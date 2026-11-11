"""Shared fixtures. Every test runs against a temp runs/ dir with the network blocked."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import audit as A  # noqa: E402
from app import paypal as PP  # noqa: E402
from app import store  # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("test tried to open a network connection")
    monkeypatch.setattr(PP.urllib.request, "urlopen", boom)
    monkeypatch.delenv("PAYOUT_TEST_RECIPIENTS", raising=False)
    monkeypatch.delenv("PAYPAL_WEBHOOK_ID", raising=False)
    monkeypatch.delenv("PAYPAL_ALLOW_UNVERIFIED_WEBHOOKS", raising=False)
    PP._token_cache.update(token=None, expires_at=0.0)


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setenv("PAYOUTGUARD_RUNS_DIR", str(tmp_path / "runs"))
    return tmp_path / "runs"


@pytest.fixture
def client(runs):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def fake_audited_run():
    """A run whose 8 invoices carry the ground-truth verdicts, as if the audit had just finished."""
    vm = A.load_vendor_master()
    by_id = {c["id"]: c for c in vm["contractors"]}
    expected = A.load_expected()
    files = list(expected)
    run = store.new_run(files, A.RUN_DATE, "cached")
    for name, exp in expected.items():
        c = by_id[exp["contractor_id"]]
        run["invoices"][name] = {
            "file": name,
            "status": "audited",
            "contractor_id": c["id"],
            "contractor_name": c["name"],
            "invoice_number": f"{c['id']}-TEST",
            "payee_email_on_invoice": c["paypal_email"],
            "line_items": [{"description": "work", "quantity": 1, "unit_price": exp["billed_total"],
                            "amount": exp["billed_total"], "approved": True, "withhold_reason": None}],
            "billed_total": exp["billed_total"],
            "approved_amount": exp["approved_amount"],
            "decision": exp["decision"],
            "findings": [],
            "remittance_note": f"Note for {c['name']}: paid {exp['approved_amount']:.2f} of "
                               f"{exp['billed_total']:.2f}.",
        }
    run["status"] = "audited"
    return store.save(run)


def approved_run():
    """...and its main Payouts batch already sent, as PayPal first answers it."""
    from app.main import payable_items
    run = fake_audited_run()
    items = payable_items(run)
    run["batch"] = {
        "payout_batch_id": "BATCHMAIN1",
        "sender_batch_id": "pg-test-main",
        "batch_status": "PENDING",
        "amount": f"{sum(i['amount'] for i in items):.2f}",
        "items": [{"payout_item_id": f"ITEM{n}", "sender_item_id": it["sender_item_id"],
                   "receiver": it["receiver"], "amount": f"{it['amount']:.2f}", "currency": "USD",
                   "note": it["note"], "transaction_status": "PENDING", "errors": None}
                  for n, it in enumerate(items, 1)],
        "items_sent": items,
    }
    run["status"] = "approved"
    return store.save(run)
