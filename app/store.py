"""Run state as one JSON file per run in runs/. One active run at a time.

runs/{run_id}.json   full state
runs/active.json     {"run_id": ...} pointer to the current run
"""
import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
RUNS = Path(os.environ.get("PAYOUTGUARD_RUNS_DIR") or (ROOT / "runs"))
ACTIVE = "active.json"

_lock = threading.Lock()


def runs_dir() -> Path:
    # Re-read the env var each call so tests can point it at a temp dir.
    d = Path(os.environ.get("PAYOUTGUARD_RUNS_DIR") or (ROOT / "runs"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(run_id: str) -> Path:
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        raise ValueError(f"bad run_id {run_id!r}")
    return runs_dir() / f"{run_id}.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def new_run(files: list, run_date: str, audit_mode: str) -> dict:
    run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    run = {
        "run_id": run_id,
        "created_at": now(),
        "updated_at": now(),
        "status": "created",          # created -> audited -> approved
        "run_date": run_date,
        "audit_mode": audit_mode,
        "files": files,
        "invoices": {name: {"file": name, "status": "pending"} for name in files},
        "totals": {"billed": 0.0, "paying": 0.0, "stopped": 0.0},
        "batch": None,
        "appeals": {},
        "errors": [],
    }
    with _lock:
        _write_json(_path(run_id), run)
        _write_json(runs_dir() / ACTIVE, {"run_id": run_id, "created_at": run["created_at"]})
    return run


def save(run: dict) -> dict:
    run["updated_at"] = now()
    with _lock:
        _write_json(_path(run["run_id"]), run)
    return run


def load(run_id: str) -> Optional[dict]:
    if run_id in ("active", "current"):
        run_id = active_run_id() or ""
        if not run_id:
            return None
    try:
        p = _path(run_id)
    except ValueError:
        return None
    if not p.exists():
        return None
    return json.loads(p.read_text())


def active_run_id() -> Optional[str]:
    p = runs_dir() / ACTIVE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("run_id")
    except json.JSONDecodeError:
        return None


def active() -> Optional[dict]:
    rid = active_run_id()
    return load(rid) if rid else None


def list_runs() -> list:
    out = []
    for p in sorted(runs_dir().glob("run-*.json")):
        try:
            r = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        out.append({k: r.get(k) for k in ("run_id", "created_at", "status", "totals")})
    return out


def all_runs() -> list:
    out = []
    for p in sorted(runs_dir().glob("run-*.json"), reverse=True):
        try:
            out.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            continue
    return out


def batches(run: dict) -> list:
    """Every Payouts batch this run has sent: the main one plus any top-up batches."""
    out = [run["batch"]] if run.get("batch") else []
    out += [a["batch"] for a in (run.get("appeals") or {}).values() if a.get("batch")]
    return out


def find_run_for_batch(payout_batch_id: str) -> Optional[dict]:
    for run in all_runs():
        for b in batches(run):
            if b.get("payout_batch_id") == payout_batch_id:
                return run
    return None


def find_run_for_item(payout_item_id: str = "", sender_item_id: str = "") -> Optional[dict]:
    for run in all_runs():
        for b in batches(run):
            for it in b.get("items") or []:
                if payout_item_id and it.get("payout_item_id") == payout_item_id:
                    return run
                if sender_item_id and it.get("sender_item_id") == sender_item_id:
                    return run
    return None


def clear_active() -> None:
    p = runs_dir() / ACTIVE
    with _lock:
        if p.exists():
            p.unlink()
