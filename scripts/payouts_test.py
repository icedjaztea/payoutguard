"""Kill test #1 — can we send a PayPal Payouts batch from the sandbox?

Usage
  python scripts/payouts_test.py --dry-run     # print the request, call nothing
  python scripts/payouts_test.py               # send a 3-item batch to sandbox and poll status

Needs in .env (or the environment):
  PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET      sandbox REST app credentials (Developer Dashboard > Apps & Credentials)
  PAYOUT_TEST_RECIPIENTS                      comma-separated sandbox PERSONAL account emails (optional)

PASS = batch reaches SUCCESS (or PROCESSING then SUCCESS) and items are SUCCESS or UNCLAIMED.
UNCLAIMED just means the email has no sandbox account yet — the API works; create the account to see money land.
Stdlib only, no installs needed.
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://api-m.sandbox.paypal.com"


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def call(method, path, token=None, body=None, basic=None, form=None):
    headers = {"Accept": "application/json"}
    data = None
    if basic:
        headers["Authorization"] = "Basic " + base64.b64encode(basic.encode()).decode()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = form.encode()
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"{}")
        except json.JSONDecodeError:
            payload = {}
        return e.code, payload


def build_batch(recipients):
    amounts = ["4200.00", "3400.00", "1350.00"]
    names = ["Priya Nair (short-paid PN-2026-044)", "Maya Chen MC-0311", "Lena Fischer LF-0092"]
    return {
        "sender_batch_header": {
            "sender_batch_id": f"pg-killtest-{uuid.uuid4().hex[:10]}",
            "email_subject": "Brightloom Studio payout",
            "email_message": "Your weekly Brightloom payout has been sent.",
        },
        "items": [
            {
                "recipient_type": "EMAIL",
                "receiver": email,
                "amount": {"value": amounts[i % 3], "currency": "USD"},
                "sender_item_id": f"item-{i + 1}",
                "note": f"PayoutGuard kill test - {names[i % 3]}",
            }
            for i, email in enumerate(recipients)
        ],
    }


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
        print("DRY RUN — would POST", BASE + "/v1/payments/payouts")
        print(json.dumps(batch, indent=2))
        return 0

    cid, secret = os.environ.get("PAYPAL_CLIENT_ID"), os.environ.get("PAYPAL_CLIENT_SECRET")
    if not cid or not secret:
        sys.exit("Missing PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET (sandbox). Put them in .env")

    st, tok = call("POST", "/v1/oauth2/token", basic=f"{cid}:{secret}", form="grant_type=client_credentials")
    if st != 200:
        sys.exit(f"FAIL token ({st}): {json.dumps(tok)}")
    token = tok["access_token"]
    print("OK  token")

    st, res = call("POST", "/v1/payments/payouts", token=token, body=batch)
    if st not in (200, 201):
        print(f"FAIL create batch ({st}):\n{json.dumps(res, indent=2)}")
        print("\nIf the error mentions authorization/permission: Developer Dashboard > Apps & Credentials >"
              " your sandbox app > Features > make sure 'Payouts' is ticked, then retry.")
        print("If it mentions INSUFFICIENT_FUNDS: log into the sandbox BUSINESS account and check its balance.")
        return 1
    batch_id = res["batch_header"]["payout_batch_id"]
    print(f"OK  batch created: {batch_id} ({res['batch_header']['batch_status']})")

    for _ in range(12):
        time.sleep(5)
        st, res = call("GET", f"/v1/payments/payouts/{batch_id}", token=token)
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
