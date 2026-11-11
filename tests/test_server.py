"""Server wiring: current run, cache modes, background polling, startup webhook, SPA. No network."""
import pytest

from app import audit as A
from app import main as M
from app import paypal as PP
from app import store
from tests.conftest import approved_run


# ----------------------------------------------------------------- current run
def test_current_run_is_created_on_demand(client, runs):
    r = client.get("/api/runs/current")
    assert r.status_code == 200
    body = r.json()
    assert len(body["files"]) == 8
    assert body["status"] == "created"
    assert client.get("/api/runs/current").json()["run_id"] == body["run_id"]


def test_current_run_follows_reset(client, runs):
    first = client.get("/api/runs/current").json()["run_id"]
    second = client.post("/api/reset").json()["run_id"]
    assert first != second
    assert client.get("/api/runs/current").json()["run_id"] == second


def test_current_run_carries_counters(client, runs):
    approved_run()
    totals = client.get("/api/runs/current").json()["totals"]
    assert set(totals) == {"billed", "paying", "stopped", "topped_up", "protected"}
    assert totals["protected"] == 11490.0


def test_health_reports_mode_and_polling(client, runs):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["audit_mode"] in ("cached", "live")
    assert body["polling"] == []
    assert body["webhook_id"] is None


# ----------------------------------------------------------------- cache modes
def test_cached_is_the_default_mode(monkeypatch):
    monkeypatch.delenv("AUDIT_MODE", raising=False)
    assert A.audit_mode() == "cached"
    monkeypatch.setenv("AUDIT_MODE", "live")
    assert A.audit_mode() == "live"
    monkeypatch.setenv("AUDIT_MODE", "nonsense")
    assert A.audit_mode() == "cached"


def test_cached_mode_uses_the_committed_cache_without_calling_the_ai(monkeypatch, tmp_path):
    """A fresh deploy has no out/cache; data/audit_cache.json must carry the demo."""
    path = A.INBOX / "scan_0042.pdf"
    seeded = {"file": "scan_0042.pdf", "decision": "SHORT_PAY", "approved_amount": 4200.0,
              "billed_total": 4850.0, "contractor_id": "C003", "line_items": [], "findings": []}
    monkeypatch.setattr(A, "CACHE", tmp_path / "empty-cache")
    monkeypatch.setattr(A, "committed_read", lambda key: dict(seeded) if key == A.file_hash(path) else None)
    monkeypatch.setattr(A, "make_provider", lambda *a, **k: pytest.fail("cached mode called the AI"))
    out = A.audit_file(path, mode="cached", ctx="ctx")
    assert out["decision"] == "SHORT_PAY"
    assert out["_cache"] == {"hit": True, "source": "data/audit_cache.json",
                             "key": A.file_hash(path), "mode": "cached"}


def test_cached_mode_calls_the_ai_on_a_miss_and_caches_it(monkeypatch, tmp_path):
    path = A.INBOX / "scan_0042.pdf"
    calls = []

    class FakeProvider:
        name, model = "openai", "test-model"

        def generate(self, parts, schema, retries=2):
            calls.append(schema.__name__)
            return {"contractor_id": "C003", "contractor_name": "Priya Nair", "invoice_number": "PN-2026-044",
                    "invoice_date": "2026-11-04", "payee_email_on_invoice": "priya.nair.brightloom@personal.example.com",
                    "line_items": [{"description": "dev", "quantity": 50, "unit_price": 84, "amount": 4200.0,
                                    "approved": True, "withhold_reason": None}],
                    "billed_total": 4200.0, "approved_amount": 4200.0, "decision": "PAY", "findings": [],
                    "remittance_note": "note", "confidence": 0.9}

    monkeypatch.setattr(A, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(A, "COMMITTED_CACHE", tmp_path / "none.json")
    out = A.audit_file(path, FakeProvider(), mode="cached", ctx="ctx")
    assert calls == ["InvoiceAudit"]
    assert out["_cache"]["hit"] is False
    assert out["file"] == "scan_0042.pdf"
    # second call is served from the cache it just wrote
    again = A.audit_file(path, FakeProvider(), mode="cached", ctx="ctx")
    assert calls == ["InvoiceAudit"]
    assert again["_cache"]["hit"] is True


def test_live_mode_ignores_the_cache(monkeypatch, tmp_path):
    path = A.INBOX / "scan_0042.pdf"
    monkeypatch.setattr(A, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(A, "committed_read", lambda key: {"decision": "PAY", "line_items": [], "findings": []})
    hit = []

    class FakeProvider:
        name, model = "openai", "test-model"

        def generate(self, parts, schema, retries=2):
            hit.append(1)
            return {"contractor_id": "C003", "contractor_name": "Priya Nair", "invoice_number": "x",
                    "invoice_date": "2026-11-04", "payee_email_on_invoice": "priya.nair.brightloom@personal.example.com",
                    "line_items": [], "billed_total": 0.0, "approved_amount": 0.0, "decision": "HOLD_REVIEW",
                    "findings": [], "remittance_note": "", "confidence": 0.1}

    A.audit_file(path, FakeProvider(), mode="live", ctx="ctx")
    assert hit == [1]


def test_committed_cache_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "COMMITTED_CACHE", tmp_path / "audit_cache.json")
    A.committed_write({"abc": {"decision": "PAY"}})
    A.committed_write({"def": {"decision": "SHORT_PAY"}})
    assert A.committed_read("abc")["decision"] == "PAY"
    assert A.committed_read("def")["decision"] == "SHORT_PAY"
    assert A.committed_read("missing") is None


# ----------------------------------------------------------------- polling
def test_refresh_batch_writes_paypals_status_back(runs, monkeypatch):
    run = approved_run()

    def fake_get(batch_id, token=None):
        return {"batch_header": {"payout_batch_id": batch_id, "batch_status": "SUCCESS",
                                 "amount": {"value": "14090.00", "currency": "USD"}},
                "items": [{"payout_item_id": "ITEM1", "transaction_status": "SUCCESS",
                           "payout_item": {"sender_item_id": "01-C003", "amount": {"value": "4200.00"}}}]}

    monkeypatch.setattr("app.main.PP.get_payout", fake_get)
    assert M.refresh_batch(run["run_id"], "BATCHMAIN1") == "SUCCESS"
    assert store.load(run["run_id"])["batch"]["batch_status"] == "SUCCESS"


def test_refresh_batch_tolerates_a_paypal_error(runs, monkeypatch):
    run = approved_run()
    monkeypatch.setattr("app.main.PP.get_payout",
                        lambda b, token=None: (_ for _ in ()).throw(PP.PayPalError(500, {}, "get batch")))
    assert M.refresh_batch(run["run_id"], "BATCHMAIN1") is None
    assert store.load(run["run_id"])["batch"]["batch_status"] == "PENDING"


def test_polling_can_be_disabled(runs, monkeypatch):
    monkeypatch.setenv("PAYOUTGUARD_DISABLE_POLLING", "1")
    assert M.start_polling("run-x", "BATCH1") is False
    assert M._polling == set()


def test_polling_runs_until_the_batch_is_final(runs, monkeypatch):
    monkeypatch.delenv("PAYOUTGUARD_DISABLE_POLLING", raising=False)
    monkeypatch.setattr(M, "POLL_SECONDS", 0.01)
    seen = []

    def fake_refresh(run_id, batch_id):
        seen.append(batch_id)
        return "PENDING" if len(seen) < 3 else "SUCCESS"

    monkeypatch.setattr(M, "refresh_batch", fake_refresh)
    assert M.start_polling("run-x", "BATCH1") is True
    assert M.start_polling("run-x", "BATCH1") is False      # already polling
    for _ in range(200):
        if "BATCH1" not in M._polling:
            break
        import time as _t
        _t.sleep(0.01)
    assert seen == ["BATCH1", "BATCH1", "BATCH1"]
    assert M._polling == set()


def test_approve_starts_polling(client, runs, monkeypatch):
    started = []
    monkeypatch.setattr(M, "start_polling", lambda run_id, bid: started.append(bid) or True)
    monkeypatch.setattr("app.main.PP.create_payout", lambda batch, token=None: {
        "batch_header": {"payout_batch_id": "B1", "batch_status": "PENDING",
                         "sender_batch_header": batch["sender_batch_header"]}})
    from tests.conftest import fake_audited_run
    run = fake_audited_run()
    client.post(f"/api/runs/{run['run_id']}/approve")
    assert started == ["B1"]


# ----------------------------------------------------------------- startup webhook
def test_no_public_url_means_no_webhook(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    assert M.register_webhook_on_startup() is None


def test_render_url_registers_the_webhook(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://payoutguard.onrender.com")
    asked = {}
    def fake_ensure(base, ev=None):
        asked["base"] = base
        return "WH-9"

    monkeypatch.setattr("app.main.PP.ensure_webhook", fake_ensure)
    assert M.register_webhook_on_startup() == "WH-9"
    assert asked["base"] == "https://payoutguard.onrender.com"


def test_webhook_failure_does_not_break_startup(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test")

    def boom(base, ev=None):
        raise PP.PayPalError(403, {"name": "NOT_AUTHORIZED"}, "create webhook")

    monkeypatch.setattr("app.main.PP.ensure_webhook", boom)
    assert M.register_webhook_on_startup() is None


def test_ensure_webhook_reuses_an_existing_one(monkeypatch):
    monkeypatch.setattr(PP, "get_token", lambda force=False: "tok")
    monkeypatch.setattr(PP, "list_webhooks", lambda token=None: [
        {"id": "WH-OLD", "url": "https://example.test/webhooks/paypal"}])
    monkeypatch.setattr(PP, "create_webhook", lambda *a, **k: pytest.fail("should not create a second webhook"))
    assert PP.ensure_webhook("https://example.test") == "WH-OLD"
    assert PP.webhook_id() == "WH-OLD"


def test_ensure_webhook_creates_one_with_payout_events(monkeypatch):
    monkeypatch.setattr(PP, "get_token", lambda force=False: "tok")
    monkeypatch.setattr(PP, "list_webhooks", lambda token=None: [])
    seen = {}

    def fake_create(url, event_types=None, token=None):
        seen.update(url=url, events=event_types or PP.PAYOUT_EVENT_TYPES)
        return {"id": "WH-NEW"}

    monkeypatch.setattr(PP, "create_webhook", fake_create)
    assert PP.ensure_webhook("https://example.test/") == "WH-NEW"
    assert seen["url"] == "https://example.test/webhooks/paypal"
    assert "PAYMENT.PAYOUTSBATCH.SUCCESS" in seen["events"]
    assert "PAYMENT.PAYOUTS-ITEM.SUCCEEDED" in seen["events"]


def test_registered_webhook_id_is_used_for_verification(monkeypatch):
    monkeypatch.delenv("PAYPAL_WEBHOOK_ID", raising=False)
    monkeypatch.setattr(PP, "_registered_webhook_id", "WH-MEM")
    body = {}
    monkeypatch.setattr(PP, "get_token", lambda force=False: "tok")

    def fake_call(method, path, token=None, body=None, **kw):
        body and body.update()
        return 200, {"verification_status": "SUCCESS"}

    monkeypatch.setattr(PP, "call", lambda *a, **k: (body.update(k.get("body") or {}), (200, {"verification_status": "SUCCESS"}))[1])
    hdrs = {h: "v" for h in PP.WEBHOOK_HEADERS.values()}
    assert PP.verify_webhook_signature(hdrs, {"id": "x"}) == "SUCCESS"
    assert body["webhook_id"] == "WH-MEM"


# ----------------------------------------------------------------- SPA
@pytest.mark.skipif(not (A.ROOT / "web" / "dist" / "index.html").is_file(),
                    reason="web/dist is not built")
def test_spa_fallback_serves_index_for_unknown_paths(client, runs):
    r = client.get("/some/deep/route")
    assert r.status_code == 200
    assert "<html" in r.text.lower()


@pytest.mark.skipif(not (A.ROOT / "web" / "dist" / "index.html").is_file(),
                    reason="web/dist is not built")
def test_spa_does_not_shadow_the_api(client, runs):
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/api/runs/run-nope").status_code == 404
