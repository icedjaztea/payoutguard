"""The Payouts request body we send to PayPal. Nothing in here touches the network:
urllib is blocked for the whole module and create/get are monkeypatched.
"""
import json

import pytest

from app import audit as A
from app import paypal as PP
from app import store
from app.main import payable_items
from tests.conftest import fake_audited_run


# ----------------------------------------------------------------- build_payout_batch
def test_batch_body_shape():
    body = PP.build_payout_batch(
        [{"receiver": "a@example.com", "amount": 4200, "note": "n1", "sender_item_id": "01-C003"}],
        "pg-test-1")
    assert body["sender_batch_header"] == {
        "sender_batch_id": "pg-test-1",
        "email_subject": "Brightloom Studio payout",
        "email_message": "Your weekly Brightloom payout has been sent.",
    }
    assert body["items"] == [{
        "recipient_type": "EMAIL",
        "receiver": "a@example.com",
        "amount": {"value": "4200.00", "currency": "USD"},
        "sender_item_id": "01-C003",
        "note": "n1",
    }]
    json.dumps(body)  # must be JSON-serialisable as-is


@pytest.mark.parametrize("amount,value", [(4200, "4200.00"), (4200.5, "4200.50"), ("1350", "1350.00"),
                                          (1049.999, "1050.00"), (0.14, "0.14")])
def test_amounts_are_two_decimal_strings(amount, value):
    body = PP.build_payout_batch([{"receiver": "a@example.com", "amount": amount, "note": "",
                                   "sender_item_id": "x"}])
    assert body["items"][0]["amount"]["value"] == value


def test_sender_batch_id_is_new_each_run():
    ids = {PP.build_payout_batch([])["sender_batch_header"]["sender_batch_id"] for _ in range(5)}
    assert len(ids) == 5


def test_long_note_is_trimmed_to_paypal_limit():
    body = PP.build_payout_batch([{"receiver": "a@example.com", "amount": 1, "note": "x" * 5000,
                                   "sender_item_id": "x"}])
    assert len(body["items"][0]["note"]) == 4000


def test_test_recipients_override_receivers_round_robin(monkeypatch):
    monkeypatch.setenv("PAYOUT_TEST_RECIPIENTS", "s1@personal.example.com, s2@personal.example.com")
    items = [{"receiver": f"real{i}@example.com", "amount": 10, "note": "", "sender_item_id": str(i)}
             for i in range(3)]
    got = [it["receiver"] for it in PP.build_payout_batch(items)["items"]]
    assert got == ["s1@personal.example.com", "s2@personal.example.com", "s1@personal.example.com"]


# ----------------------------------------------------------------- item selection
def test_only_pay_and_short_pay_become_items(runs):
    run = fake_audited_run()
    items = payable_items(run)
    assert len(items) == 6
    assert {it["file"] for it in items} == {
        "scan_0042.pdf", "scan_0043.pdf", "final_FINAL_v2.pdf", "20261105_invoice.pdf",
        "IMG_1234.jpg", "IMG_1301.jpg",
    }
    assert sum(it["amount"] for it in items) == 14090.0


def test_short_pay_item_uses_approved_amount_and_remittance_note(runs):
    items = {it["file"]: it for it in payable_items(fake_audited_run())}
    priya = items["scan_0042.pdf"]
    assert priya["amount"] == 4200.0           # not the 4,850 billed
    assert priya["note"].startswith("Note for Priya Nair")
    assert priya["sender_item_id"] == "01-C003"


def test_receiver_is_the_payee_on_file_not_the_invoice(runs):
    run = fake_audited_run()
    run["invoices"]["scan_0043.pdf"]["payee_email_on_invoice"] = "attacker@look-alike.co"
    items = {it["file"]: it for it in payable_items(run)}
    assert items["scan_0043.pdf"]["receiver"] == "maya.chen.brightloom@personal.example.com"


def test_zero_and_held_items_are_skipped(runs):
    run = fake_audited_run()
    run["invoices"]["scan_0043.pdf"]["approved_amount"] = 0.0
    files = {it["file"] for it in payable_items(run)}
    assert "scan_0043.pdf" not in files
    assert "invoice.pdf" not in files          # QUARANTINE_PAYEE
    assert "Document (3).pdf" not in files     # HOLD_DUPLICATE


# ----------------------------------------------------------------- approve endpoint
def test_approve_dry_run_builds_one_batch_without_calling_paypal(client, runs):
    run = fake_audited_run()
    r = client.post(f"/api/runs/{run['run_id']}/approve", params={"dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()["request_body"]
    assert len(body["items"]) == 6
    assert body["sender_batch_header"]["sender_batch_id"].startswith("pg-")
    assert [it["amount"]["value"] for it in body["items"]] == [
        "4200.00", "3400.00", "2880.00", "1050.00", "1350.00", "1210.00"]
    assert all(it["note"] for it in body["items"])
    assert store.load(run["run_id"])["batch"] is None   # dry run changes nothing


def test_approve_sends_exactly_one_batch_and_stores_it(client, runs, monkeypatch):
    run = fake_audited_run()
    sent = []

    def fake_create(batch, token=None):
        sent.append(batch)
        return {"batch_header": {
            "payout_batch_id": "BATCH123",
            "batch_status": "PENDING",
            "sender_batch_header": batch["sender_batch_header"],
            "amount": {"value": "14090.00", "currency": "USD"},
        }}

    monkeypatch.setattr("app.main.PP.create_payout", fake_create)
    r = client.post(f"/api/runs/{run['run_id']}/approve")
    assert r.status_code == 200, r.text
    assert len(sent) == 1
    assert len(sent[0]["items"]) == 6
    state = r.json()
    assert state["status"] == "approved"
    assert state["batch"]["payout_batch_id"] == "BATCH123"
    assert len(state["batch"]["items_sent"]) == 6
    assert store.load(run["run_id"])["batch"]["payout_batch_id"] == "BATCH123"

    again = client.post(f"/api/runs/{run['run_id']}/approve")
    assert again.status_code == 409
    assert len(sent) == 1


def test_approve_before_audit_is_rejected(client, runs):
    run = store.new_run(["scan_0042.pdf"], A.RUN_DATE, "cached")
    r = client.post(f"/api/runs/{run['run_id']}/approve")
    assert r.status_code == 409


def test_approve_with_nothing_payable_is_rejected(client, runs):
    run = fake_audited_run()
    for inv in run["invoices"].values():
        inv["decision"] = "HOLD_DUPLICATE"
        inv["approved_amount"] = 0.0
    store.save(run)
    r = client.post(f"/api/runs/{run['run_id']}/approve")
    assert r.status_code == 400


def test_approve_surfaces_paypal_errors_as_502(client, runs, monkeypatch):
    run = fake_audited_run()

    def fake_create(batch, token=None):
        raise PP.PayPalError(403, {"name": "NOT_AUTHORIZED"}, "create batch")

    monkeypatch.setattr("app.main.PP.create_payout", fake_create)
    r = client.post(f"/api/runs/{run['run_id']}/approve")
    assert r.status_code == 502
    assert r.json()["detail"]["paypal"]["name"] == "NOT_AUTHORIZED"
    assert store.load(run["run_id"])["status"] == "audited"


# ----------------------------------------------------------------- run lifecycle / totals
def test_totals_and_counters(client, runs):
    run = fake_audited_run()
    state = client.get(f"/api/runs/{run['run_id']}").json()
    assert state["totals"]["billed"] == 25580.0
    assert state["totals"]["paying"] == 14090.0
    assert state["totals"]["stopped"] == 11490.0


def test_create_run_lists_the_inbox(client, runs):
    r = client.post("/api/runs")
    assert r.status_code == 200
    body = r.json()
    assert len(body["files"]) == 8
    assert body["status"] == "created"
    assert store.active_run_id() == body["run_id"]


def test_reset_makes_a_new_active_run(client, runs):
    first = client.post("/api/runs").json()["run_id"]
    second = client.post("/api/reset").json()["run_id"]
    assert first != second
    assert store.active_run_id() == second
    assert store.load("active")["run_id"] == second


def test_unknown_run_is_404(client, runs):
    assert client.get("/api/runs/run-does-not-exist").status_code == 404


# ----------------------------------------------------------------- file serving
def test_files_serves_inbox_documents(client, runs):
    r = client.get("/files/scan_0042.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"


def test_files_rejects_traversal_and_unknown(client, runs):
    import pytest as _pytest
    from fastapi import HTTPException

    from app.main import get_file

    assert client.get("/files/nope.pdf").status_code == 404
    for bad in ("../.env", "..\\.env", ".env", "sub/dir.pdf"):
        with _pytest.raises(HTTPException):
            get_file(bad)
    # and nothing that does come back is ever the project .env
    for path in ("/files/..%2F.env", "/files/.env", "/.env"):
        r = client.get(path)
        assert "PAYPAL_CLIENT_SECRET" not in r.text
