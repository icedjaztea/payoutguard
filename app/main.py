"""PayoutGuard FastAPI backend (CLAUDE.md sections 2-3).

Run:  uvicorn app.main:app --reload --port 8000
Env:  AUDIT_MODE=live|cached, AI_PROVIDER/OPENAI_*, PAYPAL_CLIENT_ID/SECRET
Webhooks and appeals are deliberately not here yet.
"""
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app import audit as A
from app import paypal as PP
from app import store

A.load_env()

app = FastAPI(title="PayoutGuard", version="0.1.0",
              description="Audits contractor invoices before a PayPal Payouts batch leaves.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SERVE_DIRS = [A.INBOX, A.DATA / "contracts", A.DATA / "appeals"]


# ----------------------------------------------------------------------------- helpers
def _run_or_404(run_id: str) -> dict:
    run = store.load(run_id)
    if run is None:
        raise HTTPException(404, f"run {run_id} not found")
    return run


def recompute_totals(run: dict) -> dict:
    billed = paying = 0.0
    for inv in run["invoices"].values():
        if inv.get("status") != "audited":
            continue
        billed += float(inv.get("billed_total") or 0)
        if inv.get("decision") in A.PAYABLE:
            paying += float(inv.get("approved_amount") or 0)
    top_ups = sum(float(a.get("top_up_amount") or 0) for a in run.get("appeals", {}).values()
                  if a.get("decision") == "TOP_UP")
    run["totals"] = {
        "billed": round(billed, 2),
        "paying": round(paying, 2),
        "stopped": round(billed - paying, 2),
        "topped_up": round(top_ups, 2),
    }
    return run


def payable_items(run: dict) -> list:
    """PAY + SHORT_PAY items for one Payouts batch. Receiver = payee email on file, never the invoice's."""
    vm = A.load_vendor_master()
    by_id = {c["id"]: c for c in vm["contractors"]}
    items = []
    for i, name in enumerate(run["files"]):
        inv = run["invoices"].get(name, {})
        if inv.get("status") != "audited" or inv.get("decision") not in A.PAYABLE:
            continue
        amount = round(float(inv.get("approved_amount") or 0), 2)
        if amount <= 0:
            continue
        c = by_id.get(inv.get("contractor_id"))
        if not c:
            run["errors"].append(f"{name}: contractor {inv.get('contractor_id')} not in vendor master, item skipped")
            continue
        items.append({
            "file": name,
            "contractor_id": c["id"],
            "contractor_name": c["name"],
            "receiver": c["paypal_email"],
            "amount": amount,
            "note": inv.get("remittance_note") or f"Brightloom payout for invoice {inv.get('invoice_number', '')}",
            "sender_item_id": f"{i + 1:02d}-{c['id']}",
        })
    return items


# ----------------------------------------------------------------------------- endpoints
@app.get("/api/health")
def health():
    return {"ok": True, "audit_mode": A.audit_mode(), "provider": os.environ.get("AI_PROVIDER", "openai"),
            "active_run": store.active_run_id(), "paypal_base": PP.api_base()}


@app.post("/api/runs")
def create_run():
    files = [p.name for p in A.inbox_files()]
    if not files:
        raise HTTPException(500, f"no invoice files in {A.INBOX}")
    run = store.new_run(files, A.RUN_DATE, A.audit_mode())
    return run


@app.get("/api/runs")
def list_runs():
    return {"active": store.active_run_id(), "runs": store.list_runs()}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, refresh: bool = Query(False, description="re-fetch the Payouts batch from PayPal")):
    run = recompute_totals(_run_or_404(run_id))
    batch = run.get("batch")
    if refresh and batch and batch.get("payout_batch_id"):
        try:
            run["batch"] = PP.summarize_batch(PP.get_payout(batch["payout_batch_id"]))
            store.save(run)
        except PP.PayPalError as e:
            run["errors"] = run.get("errors", []) + [str(e)]
    return run


@app.post("/api/runs/{run_id}/audit")
def audit_run(run_id: str,
              only: Optional[str] = Query(None, description="audit a single inbox file name"),
              force: bool = Query(False, description='bypass the cache ("Re-audit live")'),
              guardrails: bool = Query(True)):
    run = _run_or_404(run_id)
    mode = A.audit_mode()
    run["audit_mode"] = mode
    targets = [p for p in A.inbox_files() if p.name in run["files"]]
    if only:
        targets = [p for p in targets if p.name == only]
        if not targets:
            raise HTTPException(404, f"{only} is not in this run")

    provider = None
    if mode == "live":
        try:
            provider = A.make_provider()
        except Exception as e:  # noqa: BLE001 - missing key / bad provider name
            raise HTTPException(500, f"AI provider unavailable: {e}")

    ctx, vm, history = A.context_text(), A.load_vendor_master(), A.load_history()
    expected = A.load_expected()
    run["errors"] = []
    for p in targets:
        try:
            result = A.audit_file(p, provider, use_guardrails=guardrails, mode=mode,
                                  ctx=ctx, vm=vm, history=history, force=force)
        except Exception as e:  # noqa: BLE001 - one bad file must not kill the run
            run["invoices"][p.name] = {"file": p.name, "status": "error", "error": str(e)}
            run["errors"].append(f"{p.name}: {e}")
            store.save(recompute_totals(run))
            continue
        inv = dict(result)
        inv["file"] = p.name
        inv["status"] = "audited"
        inv["cache"] = inv.pop("_cache", None)
        inv["expected"] = expected.get(p.name)  # demo-only: ground truth for the scoreboard
        run["invoices"][p.name] = inv
        store.save(recompute_totals(run))

    if all(run["invoices"][n].get("status") == "audited" for n in run["files"]):
        if run["status"] == "created":
            run["status"] = "audited"
    store.save(recompute_totals(run))
    return run


@app.post("/api/runs/{run_id}/approve")
def approve_run(run_id: str,
                dry_run: bool = Query(False, description="build the Payouts body, call nothing")):
    run = _run_or_404(run_id)
    if run["status"] == "created":
        raise HTTPException(409, "run has not been audited yet")
    if run.get("batch") and run["batch"].get("payout_batch_id"):
        raise HTTPException(409, f"run already paid in batch {run['batch']['payout_batch_id']}")

    items = payable_items(run)
    if not items:
        raise HTTPException(400, "no payable (PAY / SHORT_PAY) items in this run")
    sender_batch_id = PP.new_sender_batch_id(f"pg-{run['run_id'][-6:]}")
    body = PP.build_payout_batch(items, sender_batch_id)

    if dry_run:
        return {"dry_run": True, "sender_batch_id": sender_batch_id, "items": items, "request_body": body}

    try:
        res = PP.create_payout(body)
    except PP.PayPalError as e:
        raise HTTPException(502, {"step": e.step, "status": e.status, "paypal": e.payload})

    batch = PP.summarize_batch(res)
    batch["sender_batch_id"] = sender_batch_id
    batch["items_sent"] = items
    run["batch"] = batch
    run["status"] = "approved"
    run["approved_at"] = store.now()
    store.save(recompute_totals(run))
    return run


@app.post("/api/reset")
def reset():
    store.clear_active()
    files = [p.name for p in A.inbox_files()]
    run = store.new_run(files, A.RUN_DATE, A.audit_mode())
    return run


@app.get("/files/{name}")
def get_file(name: str):
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "bad file name")
    for d in SERVE_DIRS:
        p = (d / name)
        if p.is_file() and p.resolve().parent == d.resolve():
            return FileResponse(p, filename=name)
    raise HTTPException(404, f"{name} not found")


# Serve the built frontend if it exists (web/dist), so one process serves everything.
_dist = A.ROOT / "web" / "dist"
if _dist.is_dir():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="web")


@app.exception_handler(ValueError)
def value_error_handler(request, exc):  # pragma: no cover
    return JSONResponse(status_code=400, content={"detail": str(exc)})
