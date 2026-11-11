"""Kill test #1 — can we send a PayPal Payouts batch from the sandbox?

Usage
  python scripts/payouts_test.py --dry-run     # print the request, call nothing
  python scripts/payouts_test.py               # send a 3-item batch to sandbox and poll status

Needs in .env (or the environment):
  PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET      sandbox REST app credentials (Developer Dashboard > Apps & Credentials)
  PAYOUT_TEST_RECIPIENTS                      comma-separated sandbox PERSONAL account emails (optional)

PASS = batch reaches SUCCESS (or PROCESSING then SUCCESS) and items are SUCCESS or UNCLAIMED.
UNCLAIMED just means the email has no sandbox account yet — the API works; create the account to see money land.

The PayPal calls live in app/paypal.py; this script is just the kill test around them.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audit import load_env  # noqa: E402
from app.paypal import (  # noqa: E402
    PayPalError, ROOT, api_base, build_payout_batch, call, create_payout, get_payout, get_token, new_sender_batch_id,
)

BASE = api_base()


def build_batch(recipients):
    amounts = ["4200.00", "3400.00", "1350.00"]
    names = ["Priya Nair (short-paid PN-2026-044)", "Maya Chen MC-0311", "Lena Fischer LF-0092"]
    items = [
        {
            "receiver": email,
            "amount": amounts[i % 3],
            "sender_item_id": f"item-{i + 1}",
            "note": f"PayoutGuard kill test - {names[i % 3]}",
        }
        for i, email in enumerate(recipients)
    ]
    # build_payout_batch honours PAYOUT_TEST_RECIPIENTS; recipients already come from there when set.
    return build_payout_batch(items, new_sender_batch_id("pg-killtest"), email_subject="Brightloom Studio payout",
                              email_message="Your weekly Brightloom payout has been sent.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_env()

    recips = [r.strip() for r in os.environ.get("PAYOUT_TEST_RECIPIENTS", "").split(",") if r.strip()]
    if not recips:
        vm = json.loads((ROOT / "data" / "vendor_master.json").read_text())
        by = {c["id"]: c for c in vm["contractors"]}
        recips = [by[i]["paypal_email"] for i in ("C003", "C001", "C005")]
    batch = build_batch(recips[:3])

    if args.dry_run:
        print("DRY RUN — would POST", api_base() + "/v1/payments/payouts")
        print(json.dumps(batch, indent=2))
        return 0

    try:
        token = get_token()
        print("OK  token")
        res = create_payout(batch, token)
    except PayPalError as e:
        if e.step == "token":
            sys.exit(f"FAIL token ({e.status}): {json.dumps(e.payload)}")
        print(f"FAIL create batch ({e.status}):\n{json.dumps(e.payload, indent=2)}")
        print("\nIf the error mentions authorization/permission: Developer Dashboard > Apps & Credentials >"
              " your sandbox app > Features > make sure 'Payouts' is ticked, then retry.")
        print("If it mentions INSUFFICIENT_FUNDS: log into the sandbox BUSINESS account and check its balance.")
        return 1

    batch_id = res["batch_header"]["payout_batch_id"]
    print(f"OK  batch created: {batch_id} ({res['batch_header']['batch_status']})")

    status = res["batch_header"]["batch_status"]
    for _ in range(12):
        time.sleep(5)
        res = get_payout(batch_id, token)
        status = res.get("batch_header", {}).get("batch_status")
        print(f"..  batch status: {status}")
        if status in ("SUCCESS", "DENIED", "CANCELED"):
            break

    items = res.get("items", [])
    for it in items:
        print(f"    {it['payout_item']['receiver']:<55} {it['payout_item']['amount']['value']:>9}  "
              f"{it.get('transaction_status')}")
    ok = status == "SUCCESS" and all(it.get("transaction_status") in ("SUCCESS", "UNCLAIMED") for it in items)
    print("\nPASS — Payouts works in sandbox." if ok else "\nNOT YET PASSING — see statuses above.")
    if any(it.get("transaction_status") == "UNCLAIMED" for it in items):
        print("Note: UNCLAIMED = no sandbox account for that email yet. Create sandbox personal accounts with those "
              "emails to show money landing in the demo.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
