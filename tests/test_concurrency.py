"""Two writers touching the same run must not lose each other's update.

The real case: PayPal's webhook handler, the background poller and the appeal endpoint all
read-modify-write runs/{id}.json. Before store.mutate() the last writer to finish overwrote
whatever the others had recorded in between - usually a webhook event.
"""
import threading

from app import main as M
from app import store


def test_mutate_serialises_two_writers(runs):
    run = store.new_run(["a.pdf"], "2026-11-06", "cached")
    rid = run["run_id"]
    start = threading.Barrier(2)

    def writer(key: str, n: int):
        start.wait()
        for i in range(n):
            with store.mutate(rid) as r:
                r.setdefault("marks", []).append(f"{key}{i}")

    threads = [threading.Thread(target=writer, args=(k, 25)) for k in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    marks = store.load(rid)["marks"]
    assert len(marks) == 50
    assert sorted(marks) == sorted([f"a{i}" for i in range(25)] + [f"b{i}" for i in range(25)])


def test_mutate_sees_a_concurrent_write(runs):
    """A block entered after another writer finished must read that writer's value, not a stale one."""
    run = store.new_run(["a.pdf"], "2026-11-06", "cached")
    rid = run["run_id"]
    stale = store.load(rid)                      # a handler holding an old copy
    with store.mutate(rid) as r:
        r["status"] = "approved"
    with store.mutate(rid) as r:
        assert r["status"] == "approved"
    assert stale["status"] == "created"          # the old copy is never written back


def test_mutate_unknown_run_yields_none(runs):
    with store.mutate("run-nope") as r:
        assert r is None


def test_mutate_resolves_active(runs):
    run = store.new_run(["a.pdf"], "2026-11-06", "cached")
    with store.mutate("current") as r:
        r["status"] = "audited"
    assert store.load(run["run_id"])["status"] == "audited"


def test_webhook_events_from_both_batches_survive_a_concurrent_poll(runs, monkeypatch):
    """An item event and a batch refresh landing together: both must end up on the run."""
    from tests.conftest import approved_run

    run = approved_run()
    rid = run["run_id"]
    batch_id = run["batch"]["payout_batch_id"]
    item = run["batch"]["items"][0]

    fresh = {"payout_batch_id": batch_id, "batch_status": "PROCESSING",
             "amount": run["batch"].get("amount"), "items": run["batch"]["items"]}
    monkeypatch.setattr(M.PP, "get_payout", lambda bid: {"batch_header": {}})
    monkeypatch.setattr(M.PP, "summarize_batch", lambda res: fresh)

    start = threading.Barrier(2)

    def poll():
        start.wait()
        M.refresh_batch(rid, batch_id)

    def webhook():
        start.wait()
        M.apply_webhook_event({
            "id": "WH-1", "event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
            "resource": {"payout_batch_id": batch_id,
                         "payout_item_id": item.get("payout_item_id"),
                         "transaction_status": "SUCCESS"},
        })

    threads = [threading.Thread(target=poll), threading.Thread(target=webhook)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    after = store.load(rid)
    assert [e["id"] for e in after.get("webhook_events", [])] == ["WH-1"]
    assert after["batch"]["batch_status"] == "PROCESSING"
