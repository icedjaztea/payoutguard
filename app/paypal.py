"""PayPal Payouts (sandbox): OAuth token, create batch, get batch.

Moved here from scripts/payouts_test.py. Stdlib only — no SDK.

Env: PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET
     PAYPAL_API_BASE          (default https://api-m.sandbox.paypal.com)
     PAYOUT_TEST_RECIPIENTS   optional comma-separated sandbox personal emails;
                              payable items are routed to these round-robin so money
                              actually lands in accounts you own during the demo.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE = "https://api-m.sandbox.paypal.com"


def api_base() -> str:
    return os.environ.get("PAYPAL_API_BASE", DEFAULT_BASE).rstrip("/")


class PayPalError(RuntimeError):
    def __init__(self, status: int, payload: dict, step: str):
        self.status = status
        self.payload = payload
        self.step = step
        super().__init__(f"PayPal {step} failed ({status}): {json.dumps(payload)[:600]}")


def call(method, path, token=None, body=None, basic=None, form=None, base=None):
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
    req = urllib.request.Request((base or api_base()) + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"{}")
        except json.JSONDecodeError:
            payload = {}
        return e.code, payload


_token_cache = {"token": None, "expires_at": 0.0}


def get_token(force: bool = False) -> str:
    """Client-credentials token, cached until ~1 minute before expiry."""
    if not force and _token_cache["token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["token"]
    cid, secret = os.environ.get("PAYPAL_CLIENT_ID"), os.environ.get("PAYPAL_CLIENT_SECRET")
    if not cid or not secret:
        raise PayPalError(0, {"error": "Missing PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET (sandbox). Put them in .env"},
                          "token")
    st, tok = call("POST", "/v1/oauth2/token", basic=f"{cid}:{secret}", form="grant_type=client_credentials")
    if st != 200 or "access_token" not in tok:
        raise PayPalError(st, tok, "token")
    _token_cache["token"] = tok["access_token"]
    _token_cache["expires_at"] = time.time() + max(float(tok.get("expires_in", 300)) - 60, 30)
    return _token_cache["token"]


def new_sender_batch_id(prefix: str = "pg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def test_recipients() -> list:
    return [r.strip() for r in os.environ.get("PAYOUT_TEST_RECIPIENTS", "").split(",") if r.strip()]


def build_payout_batch(items: list, sender_batch_id: Optional[str] = None,
                       email_subject: str = "Brightloom Studio payout",
                       email_message: str = "Your weekly Brightloom payout has been sent.") -> dict:
    """items: [{receiver, amount (float|str), note, sender_item_id}] -> Payouts request body.

    Amounts are rendered as 2-decimal strings; notes are trimmed to PayPal's 4000-char limit.
    """
    recips = test_recipients()
    out_items = []
    for i, it in enumerate(items):
        receiver = recips[i % len(recips)] if recips else it["receiver"]
        out_items.append({
            "recipient_type": "EMAIL",
            "receiver": receiver,
            "amount": {"value": f"{float(it['amount']):.2f}", "currency": it.get("currency", "USD")},
            "sender_item_id": it["sender_item_id"],
            "note": (it.get("note") or "")[:4000],
        })
    return {
        "sender_batch_header": {
            "sender_batch_id": sender_batch_id or new_sender_batch_id(),
            "email_subject": email_subject,
            "email_message": email_message,
        },
        "items": out_items,
    }


def create_payout(batch: dict, token: Optional[str] = None) -> dict:
    token = token or get_token()
    st, res = call("POST", "/v1/payments/payouts", token=token, body=batch)
    if st not in (200, 201):
        raise PayPalError(st, res, "create batch")
    return res


def get_payout(batch_id: str, token: Optional[str] = None) -> dict:
    token = token or get_token()
    st, res = call("GET", f"/v1/payments/payouts/{batch_id}", token=token)
    if st != 200:
        raise PayPalError(st, res, "get batch")
    return res


def summarize_batch(res: dict) -> dict:
    """Trim a Payouts response down to what the UI needs."""
    bh = res.get("batch_header", {}) or {}
    items = []
    for it in res.get("items", []) or []:
        po = it.get("payout_item", {}) or {}
        items.append({
            "payout_item_id": it.get("payout_item_id"),
            "sender_item_id": po.get("sender_item_id"),
            "receiver": po.get("receiver"),
            "amount": (po.get("amount", {}) or {}).get("value"),
            "currency": (po.get("amount", {}) or {}).get("currency"),
            "note": po.get("note"),
            "transaction_status": it.get("transaction_status"),
            "errors": it.get("errors"),
        })
    return {
        "payout_batch_id": bh.get("payout_batch_id"),
        "sender_batch_id": (bh.get("sender_batch_header", {}) or {}).get("sender_batch_id"),
        "batch_status": bh.get("batch_status"),
        "time_created": bh.get("time_created"),
        "amount": (bh.get("amount", {}) or {}).get("value"),
        "items": items,
    }


# ----------------------------------------------------------------------------- webhooks
WEBHOOK_HEADERS = {
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}


def webhook_id() -> str:
    return (os.environ.get("PAYPAL_WEBHOOK_ID") or "").strip()


def verify_webhook_signature(headers, event: dict, token: Optional[str] = None) -> str:
    """POST /v1/notifications/verify-webhook-signature. Returns SUCCESS / FAILURE / UNVERIFIED.

    UNVERIFIED means we could not even ask: no PAYPAL_WEBHOOK_ID, or a header PayPal
    always sends is missing. The caller decides what to do with that.
    """
    wid = webhook_id()
    if not wid:
        return "UNVERIFIED"
    body = {"webhook_id": wid, "webhook_event": event}
    for field, header in WEBHOOK_HEADERS.items():
        value = headers.get(header) or headers.get(header.title())
        if not value:
            return "UNVERIFIED"
        body[field] = value
    st, res = call("POST", "/v1/notifications/verify-webhook-signature", token=token or get_token(), body=body)
    if st != 200:
        raise PayPalError(st, res, "verify webhook signature")
    return res.get("verification_status", "FAILURE")
