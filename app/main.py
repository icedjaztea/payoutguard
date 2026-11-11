"""PayoutGuard FastAPI backend (CLAUDE.md sections 2-3).

Run:  uvicorn app.main:app --reload --port 8000
Env:  AUDIT_MODE=live|cached, AI_PROVIDER/OPENAI_*, PAYPAL_CLIENT_ID/SECRET
Webhooks and appeals are deliberately not here yet.
"""
import json
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
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
    # Only money actually sent counts: a dry-run or un-sent appeal must not move the counters.
    top_ups = sum(float(a.get("top_up_amount") or 0) for a in run.get("appeals", {}).values()
                  if a.get("decision") == "TOP_UP" and (a.get("batch") or {}).get("payout_batch_id"))
    stopped = round(billed - paying, 2)
    run["totals"] = {
        "billed": round(billed, 2),
        "paying": round(paying, 2),
        "stopped": stopped,
        "topped_up": round(top_ups, 2),
        "protected": round(stopped - top_ups, 2),
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


# ----------------------------------------------------------------------------- appeals
ITEM_EVENT_STATUS = {
    "PAYMENT.PAYOUTS-ITEM.SUCCEEDED": "SUCCESS",
    "PAYMENT.PAYOUTS-ITEM.UNCLAIMED": "UNCLAIMED",
    "PAYMENT.PAYOUTS-ITEM.FAILED": "FAILED",
    "PAYMENT.PAYOUTS-ITEM.BLOCKED": "BLOCKED",
    "PAYMENT.PAYOUTS-ITEM.RETURNED": "RETURNED",
    "PAYMENT.PAYOUTS-ITEM.CANCELED": "CANCELED",
    "PAYMENT.PAYOUTS-ITEM.DENIED": "DENIED",
    "PAYMENT.PAYOUTS-ITEM.HELD": "HELD",
    "PAYMENT.PAYOUTS-ITEM.REFUNDED": "REFUNDED",
}
BATCH_EVENT_STATUS = {
    "PAYMENT.PAYOUTSBATCH.SUCCESS": "SUCCESS",
    "PAYMENT.PAYOUTSBATCH.PROCESSING": "PROCESSING",
    "PAYMENT.PAYOUTSBATCH.DENIED": "DENIED",
}


@app.post("/api/runs/{run_id}/appeals/{file}")
def run_appeal(run_id: str, file: str,
               appeal_file: Optional[str] = Query(None, description="an .eml in data/appeals; matched by name if omitted"),
               force: bool = Query(False, description="ignore the cached appeal result"),
               dry_run: bool = Query(False, description="decide, but send no top-up batch")):
    """Contractor replied with evidence -> re-check -> on TOP_UP send a one-item batch."""
    run = _run_or_404(run_id)
    inv = run["invoices"].get(file)
    if inv is None:
        raise HTTPException(404, f"{file} is not in this run")
    if inv.get("status") != "audited":
        raise HTTPException(409, f"{file} has not been audited")
    if inv.get("decision") != "SHORT_PAY":
        raise HTTPException(409, f"{file} is {inv.get('decision')}; only SHORT_PAY invoices can be appealed")
    withheld = A.withheld_amount(inv)
    if withheld <= 0:
        raise HTTPException(409, f"nothing was withheld on {file}")
    prior = (run.get("appeals") or {}).get(file)
    if prior and (prior.get("batch") or {}).get("payout_batch_id"):
        raise HTTPException(409, f"{file} was already topped up in batch {prior['batch']['payout_batch_id']}")
    if not (run.get("batch") or {}).get("payout_batch_id"):
        raise HTTPException(409, "approve the run before processing an appeal")

    try:
        path = A.find_appeal_file(inv.get("contractor_name", ""), appeal_file)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, str(e))

    try:
        result = A.audit_appeal(inv, path.read_text(errors="replace"), mode=A.audit_mode(), force=force)
    except Exception as e:  # noqa: BLE001 - missing key, cache miss in cached mode, API error
        raise HTTPException(500, f"appeal check failed: {e}")

    appeal = {
        "file": file,
        "appeal_file": path.name,
        "checked_at": store.now(),
        "withheld": withheld,
        "decision": result.get("decision"),
        "top_up_amount": round(float(result.get("top_up_amount") or 0), 2),
        "clause": result.get("clause"),
        "reasoning": result.get("reasoning"),
        "evidence": result.get("evidence"),
        "remittance_note": result.get("remittance_note"),
        "cache": result.get("_cache"),
        "batch": None,
    }
    # Never pay out more than was withheld, whatever the model says.
    appeal["top_up_amount"] = min(appeal["top_up_amount"], withheld)

    if appeal["decision"] == "TOP_UP" and appeal["top_up_amount"] > 0 and not dry_run:
        vm = A.load_vendor_master()
        c = next((x for x in vm["contractors"] if x["id"] == inv.get("contractor_id")), None)
        if not c:
            raise HTTPException(500, f"contractor {inv.get('contractor_id')} not in vendor master")
        item = {
            "file": file,
            "contractor_id": c["id"],
            "contractor_name": c["name"],
            "receiver": c["paypal_email"],
            "amount": appeal["top_up_amount"],
            "note": appeal["remittance_note"] or f"Top-up after appeal on invoice {inv.get('invoice_number', '')}.",
            "sender_item_id": f"topup-{c['id']}",
        }
        sender_batch_id = PP.new_sender_batch_id(f"pg-{run['run_id'][-6:]}-topup")
        body = PP.build_payout_batch([item], sender_batch_id,
                                     email_subject="Brightloom Studio payout (top-up)",
                                     email_message="Following your reply, the withheld amount has been released.")
        try:
            res = PP.create_payout(body)
        except PP.PayPalError as e:
            raise HTTPException(502, {"step": e.step, "status": e.status, "paypal": e.payload})
        batch = PP.summarize_batch(res)
        batch["sender_batch_id"] = sender_batch_id
        batch["items_sent"] = [item]
        appeal["batch"] = batch

    run.setdefault("appeals", {})[file] = appeal
    inv["appeal"] = {k: appeal[k] for k in ("decision", "top_up_amount", "clause", "reasoning", "evidence")}
    if appeal["decision"] == "TOP_UP" and appeal["batch"]:
        inv["paid_total"] = round(float(inv["approved_amount"]) + appeal["top_up_amount"], 2)
    store.save(recompute_totals(run))
    return {"run_id": run["run_id"], "appeal": appeal, "totals": run["totals"]}


# ----------------------------------------------------------------------------- webhooks
def apply_webhook_event(event: dict) -> dict:
    """Update stored item/batch status from one Payouts webhook event. Returns what changed."""
    kind = event.get("event_type") or ""
    res = event.get("resource") or {}
    changed = {"event_type": kind, "matched": False}

    if kind in BATCH_EVENT_STATUS:
        bh = res.get("batch_header") or res
        batch_id = bh.get("payout_batch_id")
        run = store.find_run_for_batch(batch_id) if batch_id else None
        if run:
            for b in store.batches(run):
                if b.get("payout_batch_id") == batch_id:
                    b["batch_status"] = bh.get("batch_status") or BATCH_EVENT_STATUS[kind]
            changed.update(matched=True, run_id=run["run_id"], payout_batch_id=batch_id,
                           batch_status=BATCH_EVENT_STATUS[kind])
            _record_event(run, event)
            store.save(recompute_totals(run))
        return changed

    if kind in ITEM_EVENT_STATUS:
        item_id = res.get("payout_item_id")
        sender_item_id = (res.get("payout_item") or {}).get("sender_item_id")
        batch_id = res.get("payout_batch_id")
        run = (store.find_run_for_batch(batch_id) if batch_id else None) \
            or store.find_run_for_item(item_id or "", sender_item_id or "")
        if run:
            status = res.get("transaction_status") or ITEM_EVENT_STATUS[kind]
            hit = False
            for b in store.batches(run):
                for it in b.get("items") or []:
                    if (item_id and it.get("payout_item_id") == item_id) or \
                       (sender_item_id and it.get("sender_item_id") == sender_item_id):
                        it["transaction_status"] = status
                        it["errors"] = res.get("errors") or it.get("errors")
                        hit = True
            changed.update(matched=hit, run_id=run["run_id"], payout_item_id=item_id,
                           sender_item_id=sender_item_id, transaction_status=status)
            _record_event(run, event)
            store.save(recompute_totals(run))
        return changed

    return changed


def _record_event(run: dict, event: dict) -> None:
    log = run.setdefault("webhook_events", [])
    log.append({"id": event.get("id"), "event_type": event.get("event_type"),
                "create_time": event.get("create_time"), "received_at": store.now()})
    del log[:-50]


@app.post("/webhooks/paypal")
async def paypal_webhook(request: Request):
    """PayPal posts here. Verified with /v1/notifications/verify-webhook-signature when
    PAYPAL_WEBHOOK_ID is set; a FAILURE is rejected and changes nothing.

    With no PAYPAL_WEBHOOK_ID (local dev / before the webhook is registered) the event cannot
    be verified: it is applied only if PAYPAL_ALLOW_UNVERIFIED_WEBHOOKS=1, and marked unverified.
    """
    raw = await request.body()
    try:
        event = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "body is not JSON")
    if not isinstance(event, dict) or not event.get("event_type"):
        raise HTTPException(400, "not a webhook event")

    try:
        status = PP.verify_webhook_signature(request.headers, event)
    except PP.PayPalError as e:
        raise HTTPException(502, {"step": e.step, "status": e.status, "paypal": e.payload})

    if status == "FAILURE":
        raise HTTPException(400, "webhook signature verification failed")
    if status == "UNVERIFIED" and os.environ.get("PAYPAL_ALLOW_UNVERIFIED_WEBHOOKS") != "1":
        raise HTTPException(400, "webhook could not be verified (set PAYPAL_WEBHOOK_ID, or "
                                 "PAYPAL_ALLOW_UNVERIFIED_WEBHOOKS=1 for local testing)")

    changed = apply_webhook_event(event)
    changed["verification"] = status
    return changed


# Serve the built frontend if it exists (web/dist), so one process serves everything.
_dist = A.ROOT / "web" / "dist"
if _dist.is_dir():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="web")


@app.exception_handler(ValueError)
def value_error_handler(request, exc):  # pragma: no cover
    return JSONResponse(status_code=400, content={"detail": str(exc)})
