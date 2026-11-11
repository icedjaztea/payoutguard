"""Webhook listener: verification, then item/batch status updates. No network."""
import pytest

from app import paypal as PP
from app import store
from app.main import apply_webhook_event
from tests.conftest import approved_run

HDRS = {
    "paypal-auth-algo": "SHA256withRSA",
    "paypal-cert-url": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-abc",
    "paypal-transmission-id": "tx-1",
    "paypal-transmission-sig": "sig",
    "paypal-transmission-time": "2026-11-11T12:00:00Z",
}


def item_event(event_type="PAYMENT.PAYOUTS-ITEM.SUCCEEDED", payout_item_id="ITEM1",
               sender_item_id="01-C003", status=None, batch_id="BATCHMAIN1"):
    return {
        "id": "WH-1",
        "event_type": event_type,
        "create_time": "2026-11-11T12:00:01Z",
        "resource": {
            "payout_item_id": payout_item_id,
            "payout_batch_id": batch_id,
            "transaction_status": status,
            "payout_item": {"sender_item_id": sender_item_id,
                            "receiver": "priya.nair.brightloom@personal.example.com",
                            "amount": {"value": "4200.00", "currency": "USD"}},
        },
    }


def batch_event(event_type="PAYMENT.PAYOUTSBATCH.SUCCESS", batch_id="BATCHMAIN1", status="SUCCESS"):
    return {
        "id": "WH-2",
        "event_type": event_type,
        "create_time": "2026-11-11T12:00:02Z",
        "resource": {"batch_header": {"payout_batch_id": batch_id, "batch_status": status}},
    }


@pytest.fixture
def allow_unverified(monkeypatch):
    monkeypatch.setenv("PAYPAL_ALLOW_UNVERIFIED_WEBHOOKS", "1")


# ----------------------------------------------------------------- verification
def test_unverifiable_event_is_rejected_by_default(client, runs):
    approved_run()
    r = client.post("/webhooks/paypal", json=item_event(), headers=HDRS)
    assert r.status_code == 400
    assert "PAYPAL_WEBHOOK_ID" in r.json()["detail"]
    assert store.load("active")["batch"]["items"][0]["transaction_status"] == "PENDING"


def test_unverified_is_applied_when_explicitly_allowed(client, runs, allow_unverified):
    run = approved_run()
    r = client.post("/webhooks/paypal", json=item_event(), headers=HDRS)
    assert r.status_code == 200
    assert r.json()["verification"] == "UNVERIFIED"
    assert r.json()["matched"] is True
    assert store.load(run["run_id"])["batch"]["items"][0]["transaction_status"] == "SUCCESS"


def test_signature_is_verified_against_paypal_when_configured(client, runs, monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-ID-1")
    seen = {}

    def fake_call(method, path, token=None, body=None, **kw):
        seen.update(method=method, path=path, body=body)
        return 200, {"verification_status": "SUCCESS"}

    monkeypatch.setattr("app.paypal.call", fake_call)
    monkeypatch.setattr("app.paypal.get_token", lambda force=False: "tok")
    run = approved_run()
    r = client.post("/webhooks/paypal", json=item_event(), headers=HDRS)
    assert r.status_code == 200
    assert r.json()["verification"] == "SUCCESS"
    assert seen["path"] == "/v1/notifications/verify-webhook-signature"
    assert seen["body"]["webhook_id"] == "WH-ID-1"
    assert seen["body"]["transmission_id"] == "tx-1"
    assert seen["body"]["cert_url"] == HDRS["paypal-cert-url"]
    assert seen["body"]["webhook_event"]["id"] == "WH-1"
    assert store.load(run["run_id"])["batch"]["items"][0]["transaction_status"] == "SUCCESS"


def test_failed_verification_changes_nothing(client, runs, monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-ID-1")
    monkeypatch.setattr("app.paypal.call", lambda *a, **k: (200, {"verification_status": "FAILURE"}))
    monkeypatch.setattr("app.paypal.get_token", lambda force=False: "tok")
    run = approved_run()
    r = client.post("/webhooks/paypal", json=item_event(), headers=HDRS)
    assert r.status_code == 400
    assert store.load(run["run_id"])["batch"]["items"][0]["transaction_status"] == "PENDING"


def test_missing_paypal_headers_cannot_be_verified(client, runs, monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-ID-1")
    monkeypatch.setattr("app.paypal.get_token", lambda force=False: "tok")
    approved_run()
    r = client.post("/webhooks/paypal", json=item_event(), headers={"paypal-auth-algo": "SHA256withRSA"})
    assert r.status_code == 400


def test_garbage_bodies_are_rejected(client, runs, allow_unverified):
    assert client.post("/webhooks/paypal", content=b"not json").status_code == 400
    assert client.post("/webhooks/paypal", json={"hello": "world"}).status_code == 400
    assert client.post("/webhooks/paypal", json=[1, 2, 3]).status_code == 400


# ----------------------------------------------------------------- item events
@pytest.mark.parametrize("event_type,expected", [
    ("PAYMENT.PAYOUTS-ITEM.SUCCEEDED", "SUCCESS"),
    ("PAYMENT.PAYOUTS-ITEM.UNCLAIMED", "UNCLAIMED"),
    ("PAYMENT.PAYOUTS-ITEM.FAILED", "FAILED"),
    ("PAYMENT.PAYOUTS-ITEM.RETURNED", "RETURNED"),
    ("PAYMENT.PAYOUTS-ITEM.BLOCKED", "BLOCKED"),
])
def test_each_item_event_sets_its_status(client, runs, allow_unverified, event_type, expected):
    run = approved_run()
    r = client.post("/webhooks/paypal", json=item_event(event_type), headers=HDRS)
    assert r.json()["transaction_status"] == expected
    assert store.load(run["run_id"])["batch"]["items"][0]["transaction_status"] == expected


def test_paypals_own_status_wins_over_the_event_name(client, runs, allow_unverified):
    run = approved_run()
    client.post("/webhooks/paypal", json=item_event(status="UNCLAIMED"), headers=HDRS)
    assert store.load(run["run_id"])["batch"]["items"][0]["transaction_status"] == "UNCLAIMED"


def test_item_is_matched_by_sender_item_id_without_a_batch_id(client, runs, allow_unverified):
    run = approved_run()
    ev = item_event(payout_item_id="", sender_item_id="05-C007", batch_id=None)
    client.post("/webhooks/paypal", json=ev, headers=HDRS)
    items = {i["sender_item_id"]: i for i in store.load(run["run_id"])["batch"]["items"]}
    assert items["05-C007"]["transaction_status"] == "SUCCESS"
    assert items["01-C003"]["transaction_status"] == "PENDING"


def test_only_the_named_item_changes(client, runs, allow_unverified):
    run = approved_run()
    client.post("/webhooks/paypal", json=item_event(), headers=HDRS)
    statuses = [i["transaction_status"] for i in store.load(run["run_id"])["batch"]["items"]]
    assert statuses.count("SUCCESS") == 1
    assert statuses.count("PENDING") == 5


def test_item_errors_are_kept(client, runs, allow_unverified):
    run = approved_run()
    ev = item_event("PAYMENT.PAYOUTS-ITEM.FAILED")
    ev["resource"]["errors"] = {"name": "RECEIVER_UNREGISTERED"}
    client.post("/webhooks/paypal", json=ev, headers=HDRS)
    assert store.load(run["run_id"])["batch"]["items"][0]["errors"]["name"] == "RECEIVER_UNREGISTERED"


# ----------------------------------------------------------------- batch events
def test_batch_success_updates_the_batch(client, runs, allow_unverified):
    run = approved_run()
    r = client.post("/webhooks/paypal", json=batch_event(), headers=HDRS)
    assert r.json()["matched"] is True
    assert store.load(run["run_id"])["batch"]["batch_status"] == "SUCCESS"


def test_batch_denied_updates_the_batch(client, runs, allow_unverified):
    run = approved_run()
    client.post("/webhooks/paypal", json=batch_event("PAYMENT.PAYOUTSBATCH.DENIED", status="DENIED"), headers=HDRS)
    assert store.load(run["run_id"])["batch"]["batch_status"] == "DENIED"


def test_events_for_another_batch_are_ignored(client, runs, allow_unverified):
    run = approved_run()
    r = client.post("/webhooks/paypal", json=batch_event(batch_id="SOMEONEELSE"), headers=HDRS)
    assert r.status_code == 200
    assert r.json()["matched"] is False
    assert store.load(run["run_id"])["batch"]["batch_status"] == "PENDING"


def test_unknown_event_types_are_acknowledged_and_ignored(client, runs, allow_unverified):
    run = approved_run()
    r = client.post("/webhooks/paypal", json=batch_event("CHECKOUT.ORDER.APPROVED"), headers=HDRS)
    assert r.status_code == 200
    assert r.json()["matched"] is False
    assert store.load(run["run_id"])["batch"]["batch_status"] == "PENDING"


def test_events_are_logged_on_the_run(client, runs, allow_unverified):
    run = approved_run()
    client.post("/webhooks/paypal", json=item_event(), headers=HDRS)
    client.post("/webhooks/paypal", json=batch_event(), headers=HDRS)
    log = store.load(run["run_id"])["webhook_events"]
    assert [e["event_type"] for e in log] == ["PAYMENT.PAYOUTS-ITEM.SUCCEEDED", "PAYMENT.PAYOUTSBATCH.SUCCESS"]
    assert all(e["received_at"] for e in log)


def test_a_top_up_batch_is_updated_too(client, runs, allow_unverified):
    """The appeal's one-item batch must receive status updates like the main one."""
    run = approved_run()
    run["appeals"] = {"scan_0042.pdf": {"file": "scan_0042.pdf", "decision": "TOP_UP", "top_up_amount": 650.0,
                                        "batch": {"payout_batch_id": "TOPUP1", "batch_status": "PENDING",
                                                  "items": [{"payout_item_id": "TOPUPITEM1",
                                                             "sender_item_id": "topup-C003",
                                                             "transaction_status": "PENDING"}]}}}
    store.save(run)
    client.post("/webhooks/paypal", json=item_event(payout_item_id="TOPUPITEM1", sender_item_id="topup-C003",
                                                    batch_id="TOPUP1"), headers=HDRS)
    client.post("/webhooks/paypal", json=batch_event(batch_id="TOPUP1"), headers=HDRS)
    saved = store.load(run["run_id"])["appeals"]["scan_0042.pdf"]["batch"]
    assert saved["batch_status"] == "SUCCESS"
    assert saved["items"][0]["transaction_status"] == "SUCCESS"


# ----------------------------------------------------------------- poll fallback
def test_refresh_polls_every_batch_including_top_ups(client, runs, monkeypatch):
    run = approved_run()
    run["appeals"] = {"scan_0042.pdf": {"file": "scan_0042.pdf", "decision": "TOP_UP", "top_up_amount": 650.0,
                                        "batch": {"payout_batch_id": "TOPUP1", "batch_status": "PENDING",
                                                  "items": [{"payout_item_id": "TOPUPITEM1",
                                                             "sender_item_id": "topup-C003",
                                                             "transaction_status": "PENDING"}]}}}
    store.save(run)
    asked = []

    def fake_get(batch_id, token=None):
        asked.append(batch_id)
        return {"batch_header": {"payout_batch_id": batch_id, "batch_status": "SUCCESS",
                                 "amount": {"value": "650.00", "currency": "USD"}},
                "items": [{"payout_item_id": "X", "transaction_status": "SUCCESS",
                           "payout_item": {"sender_item_id": "s", "amount": {"value": "650.00"}}}]}

    monkeypatch.setattr("app.main.PP.get_payout", fake_get)
    state = client.get(f"/api/runs/{run['run_id']}", params={"refresh": True}).json()
    assert sorted(asked) == ["BATCHMAIN1", "TOPUP1"]
    assert state["batch"]["batch_status"] == "SUCCESS"
    assert state["appeals"]["scan_0042.pdf"]["batch"]["batch_status"] == "SUCCESS"
    assert store.load(run["run_id"])["appeals"]["scan_0042.pdf"]["batch"]["batch_status"] == "SUCCESS"


def test_refresh_survives_a_paypal_error(client, runs, monkeypatch):
    run = approved_run()

    def boom(batch_id, token=None):
        raise PP.PayPalError(500, {"name": "INTERNAL"}, "get batch")

    monkeypatch.setattr("app.main.PP.get_payout", boom)
    state = client.get(f"/api/runs/{run['run_id']}", params={"refresh": True}).json()
    assert state["batch"]["batch_status"] == "PENDING"
    assert any("get batch" in e for e in state["errors"])


# ----------------------------------------------------------------- the pure applier
def test_apply_webhook_event_is_callable_without_http(runs):
    run = approved_run()
    out = apply_webhook_event(item_event())
    assert out["matched"] is True
    assert out["run_id"] == run["run_id"]


def test_apply_webhook_event_with_no_runs_at_all(runs):
    assert apply_webhook_event(item_event())["matched"] is False


def test_verify_returns_unverified_without_a_webhook_id():
    assert PP.verify_webhook_signature(HDRS, {"id": "x"}) == "UNVERIFIED"
