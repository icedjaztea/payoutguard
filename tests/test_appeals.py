"""Appeal loop: re-check a SHORT_PAY invoice, and on TOP_UP send a one-item batch. No network."""
import pytest

from app import audit as A
from app import paypal as PP
from app import store
from tests.conftest import approved_run, fake_audited_run

PRIYA = "scan_0042.pdf"


def appeal_result(decision="TOP_UP", amount=650.0):
    return {
        "decision": decision,
        "top_up_amount": amount,
        "clause": "4.3",
        "reasoning": "Forwarded PM email dated 30 Oct approves a flat USD 650 surcharge.",
        "evidence": "approve the flat USD 650 rush surcharge",
        "remittance_note": "Releasing the $650 rush surcharge after your PM approval (cl. 4.3).",
        "_cache": {"hit": True, "key": "out/audit_results.json", "mode": "cached"},
    }


@pytest.fixture
def stub_appeal(monkeypatch):
    """Replace the AI call; record what it was asked."""
    calls = []

    def fake(audit, text, provider=None, vm=None, *, mode=None, force=False):
        calls.append({"file": audit.get("file"), "text": text, "mode": mode, "force": force})
        return fake.result

    fake.result = appeal_result()
    monkeypatch.setattr("app.main.A.audit_appeal", fake)
    fake.calls = calls
    return fake


@pytest.fixture
def stub_payout(monkeypatch):
    sent = []

    def fake_create(batch, token=None):
        sent.append(batch)
        return {"batch_header": {
            "payout_batch_id": f"TOPUP{len(sent)}",
            "batch_status": "PENDING",
            "sender_batch_header": batch["sender_batch_header"],
            "amount": {"value": batch["items"][0]["amount"]["value"], "currency": "USD"},
        }, "items": [{"payout_item_id": "TOPUPITEM1", "transaction_status": "PENDING",
                      "payout_item": batch["items"][0]}]}

    monkeypatch.setattr("app.main.PP.create_payout", fake_create)
    fake_create.sent = sent
    return fake_create


# ----------------------------------------------------------------- the happy path
def test_top_up_sends_a_one_item_batch(client, runs, stub_appeal, stub_payout):
    run = approved_run()
    r = client.post(f"/api/runs/{run['run_id']}/appeals/{PRIYA}")
    assert r.status_code == 200, r.text
    appeal = r.json()["appeal"]
    assert appeal["decision"] == "TOP_UP"
    assert appeal["top_up_amount"] == 650.0
    assert appeal["withheld"] == 650.0
    assert appeal["clause"] == "4.3"

    assert len(stub_payout.sent) == 1
    body = stub_payout.sent[0]
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["amount"] == {"value": "650.00", "currency": "USD"}
    assert item["receiver"] == "priya.nair.brightloom@personal.example.com"
    assert item["note"].startswith("Releasing the $650")
    assert item["sender_item_id"] == "topup-C003"
    assert body["sender_batch_header"]["sender_batch_id"].startswith("pg-")
    assert body["sender_batch_header"]["sender_batch_id"] != run["batch"]["sender_batch_id"]
    assert appeal["batch"]["payout_batch_id"] == "TOPUP1"


def test_top_up_is_stored_on_the_run_and_the_invoice(client, runs, stub_appeal, stub_payout):
    run = approved_run()
    client.post(f"/api/runs/{run['run_id']}/appeals/{PRIYA}")
    saved = store.load(run["run_id"])
    assert saved["appeals"][PRIYA]["batch"]["payout_batch_id"] == "TOPUP1"
    assert saved["invoices"][PRIYA]["appeal"]["decision"] == "TOP_UP"
    assert saved["invoices"][PRIYA]["paid_total"] == 4850.0   # 4,200 + 650 = paid in full
    assert saved["appeals"][PRIYA]["appeal_file"] == "priya_reply.eml"


def test_counters_after_the_appeal(client, runs, stub_appeal, stub_payout):
    run = approved_run()
    totals = client.post(f"/api/runs/{run['run_id']}/appeals/{PRIYA}").json()["totals"]
    assert totals["billed"] == 25580.0
    assert totals["topped_up"] == 650.0
    assert totals["stopped"] == 11490.0
    assert totals["protected"] == 10840.0    # the number the UI shows after the appeal


def test_the_reply_email_is_what_gets_sent_to_the_model(client, runs, stub_appeal, stub_payout):
    run = approved_run()
    client.post(f"/api/runs/{run['run_id']}/appeals/{PRIYA}")
    call = stub_appeal.calls[0]
    assert call["file"] == PRIYA
    assert "650" in call["text"]
    assert call["mode"] == A.audit_mode()


def test_named_appeal_file_is_honoured(client, runs, stub_appeal, stub_payout):
    run = approved_run()
    r = client.post(f"/api/runs/{run['run_id']}/appeals/{PRIYA}",
                    params={"appeal_file": "priya_reply.eml"})
    assert r.status_code == 200
    assert r.json()["appeal"]["appeal_file"] == "priya_reply.eml"


def test_unknown_appeal_file_is_404(client, runs, stub_appeal, stub_payout):
    run = approved_run()
    r = client.post(f"/api/runs/{run['run_id']}/appeals/{PRIYA}", params={"appeal_file": "nope.eml"})
    assert r.status_code == 404
    assert not stub_payout.sent


def test_appeal_file_cannot_escape_the_appeals_dir(client, runs, stub_appeal, stub_payout):
    run = approved_run()
    r = client.post(f"/api/runs/{run['run_id']}/appeals/{PRIYA}", params={"appeal_file": "../.env"})
    assert r.status_code == 404
    assert not stub_payout.sent


# ----------------------------------------------------------------- the fair-in-both-directions path
def test_reject_sends_nothing(client, runs, stub_appeal, stub_payout):
    stub_appeal.result = appeal_result("REJECT", 0.0)
    run = approved_run()
    r = client.post(f"/api/runs/{run['run_id']}/appeals/{PRIYA}")
    assert r.status_code == 200
    assert r.json()["appeal"]["decision"] == "REJECT"
    assert r.json()["appeal"]["batch"] is None
    assert not stub_payout.sent
    assert r.json()["totals"]["topped_up"] == 0.0


def test_needs_human_sends_nothing(client, runs, stub_appeal, stub_payout):
    stub_appeal.result = appeal_result("NEEDS_HUMAN", 650.0)
    run = approved_run()
    r = client.post(f"/api/runs/{run['run_id']}/appeals/{PRIYA}")
    assert r.json()["appeal"]["decision"] == "NEEDS_HUMAN"
    assert not stub_payout.sent


def test_top_up_is_capped_at_the_withheld_amount(client, runs, stub_appeal, stub_payout):
    """A model that asks for more than was withheld must not be able to overpay."""
    stub_appeal.result = appeal_result("TOP_UP", 99999.0)
    run = approved_run()
    r = client.post(f"/api/runs/{run['run_id']}/appeals/{PRIYA}")
    assert r.json()["appeal"]["top_up_amount"] == 650.0
    assert stub_payout.sent[0]["items"][0]["amount"]["value"] == "650.00"


def test_dry_run_decides_but_sends_nothing(client, runs, stub_appeal, stub_payout):
    run = approved_run()
    r = client.post(f"/api/runs/{run['run_id']}/appeals/{PRIYA}", params={"dry_run": True})
    assert r.json()["appeal"]["decision"] == "TOP_UP"
    assert not stub_payout.sent
    assert store.load(run["run_id"])["appeals"][PRIYA]["batch"] is None
    assert r.json()["totals"]["topped_up"] == 0.0        # nothing sent, nothing counted
    assert r.json()["totals"]["protected"] == 11490.0


# ----------------------------------------------------------------- guards
def test_appeal_twice_is_rejected(client, runs, stub_appeal, stub_payout):
    run = approved_run()
    assert client.post(f"/api/runs/{run['run_id']}/appeals/{PRIYA}").status_code == 200
    again = client.post(f"/api/runs/{run['run_id']}/appeals/{PRIYA}")
    assert again.status_code == 409
    assert len(stub_payout.sent) == 1


def test_a_rejected_appeal_can_be_retried(client, runs, stub_appeal, stub_payout):
    """REJECT stores no batch, so new evidence may be submitted again."""
    stub_appeal.result = appeal_result("REJECT", 0.0)
    run = approved_run()
    assert client.post(f"/api/runs/{run['run_id']}/appeals/{PRIYA}").status_code == 200
    stub_appeal.result = appeal_result("TOP_UP", 650.0)
    r = client.post(f"/api/runs/{run['run_id']}/appeals/{PRIYA}")
    assert r.status_code == 200
    assert r.json()["appeal"]["decision"] == "TOP_UP"


@pytest.mark.parametrize("file", ["invoice.pdf", "Document (3).pdf", "scan_0043.pdf"])
def test_only_short_pay_invoices_can_be_appealed(client, runs, stub_appeal, stub_payout, file):
    run = approved_run()
    r = client.post(f"/api/runs/{run['run_id']}/appeals/{file}")
    assert r.status_code == 409
    assert not stub_payout.sent


def test_appeal_before_approval_is_rejected(client, runs, stub_appeal, stub_payout):
    run = fake_audited_run()          # audited, but no batch sent yet
    r = client.post(f"/api/runs/{run['run_id']}/appeals/{PRIYA}")
    assert r.status_code == 409
    assert not stub_payout.sent


def test_unknown_file_is_404(client, runs, stub_appeal, stub_payout):
    run = approved_run()
    assert client.post(f"/api/runs/{run['run_id']}/appeals/nope.pdf").status_code == 404


def test_paypal_failure_on_top_up_is_502_and_stores_no_batch(client, runs, stub_appeal, monkeypatch):
    run = approved_run()

    def fake_create(batch, token=None):
        raise PP.PayPalError(422, {"name": "INSUFFICIENT_FUNDS"}, "create batch")

    monkeypatch.setattr("app.main.PP.create_payout", fake_create)
    r = client.post(f"/api/runs/{run['run_id']}/appeals/{PRIYA}")
    assert r.status_code == 502
    assert r.json()["detail"]["paypal"]["name"] == "INSUFFICIENT_FUNDS"
    assert store.load(run["run_id"]).get("appeals") == {}


# ----------------------------------------------------------------- pure helpers
def test_withheld_amount():
    assert A.withheld_amount({"billed_total": 4850.0, "approved_amount": 4200.0}) == 650.0
    assert A.withheld_amount({"billed_total": 1050.0, "approved_amount": 1050.0}) == 0.0


def test_find_appeal_file_matches_on_name():
    assert A.find_appeal_file("Priya Nair").name == "priya_reply.eml"


def test_find_appeal_file_rejects_a_path():
    with pytest.raises(ValueError):
        A.find_appeal_file("Priya Nair", "../../.env")
