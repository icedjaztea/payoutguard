"""Audit engine: provider wrapper, schemas, prompts, guardrails, cache.

Moved here from scripts/audit_test.py so both the kill test and the FastAPI app
use exactly the same logic.

Env:
  AI_PROVIDER=openai|gemini   OPENAI_API_KEY / OPENAI_MODEL  (or GEMINI_*)
  AUDIT_MODE=live|cached      cached = reuse out/cache, never call the AI
"""
import base64
import csv
import hashlib
import json
import os
import time
from enum import Enum
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "out"
CACHE = OUT / "cache"
INBOX = DATA / "inbox"
TERMS = DATA / "contracts" / "brightloom_contractor_terms.pdf"
DOC_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png")
SEVERITY = ["PAY", "SHORT_PAY", "HOLD_REVIEW", "HOLD_DUPLICATE", "QUARANTINE_PAYEE"]
PAYABLE = ("PAY", "SHORT_PAY")
RUN_DATE = "2026-11-06"
# Bump when the prompts change so stale cache entries are not reused.
PROMPT_VERSION = "1"


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def audit_mode() -> str:
    mode = os.environ.get("AUDIT_MODE", "live").strip().lower()
    return mode if mode in ("live", "cached") else "live"


# ----------------------------------------------------------------------------- schemas
class Decision(str, Enum):
    PAY = "PAY"
    SHORT_PAY = "SHORT_PAY"
    HOLD_DUPLICATE = "HOLD_DUPLICATE"
    QUARANTINE_PAYEE = "QUARANTINE_PAYEE"
    HOLD_REVIEW = "HOLD_REVIEW"


class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    amount: float
    approved: bool = Field(description="False if this line is withheld")
    withhold_reason: Optional[str] = Field(description="Why this line is withheld; null if approved")


class Finding(BaseModel):
    code: str = Field(description="e.g. RATE_MISMATCH, UNAPPROVED_SURCHARGE, DUPLICATE, PAYEE_CHANGE, "
                                  "AMOUNT_ANOMALY, OVER_CAP, MATH_ERROR")
    clause: Optional[str] = Field(description="Clause number from the contractor terms, e.g. '4.3'")
    explanation: str
    evidence: str = Field(description="Exact text/figures from the documents that support the finding")


class InvoiceAudit(BaseModel):
    contractor_id: str
    contractor_name: str
    invoice_number: str
    invoice_date: str
    payee_email_on_invoice: str
    line_items: List[LineItem]
    billed_total: float
    approved_amount: float
    decision: Decision
    findings: List[Finding]
    remittance_note: str = Field(description="Plain-English note to the contractor (max 60 words) explaining "
                                             "what is paid and why anything is withheld, citing clauses")
    confidence: float = Field(description="0-1")


class AppealDecision(str, Enum):
    TOP_UP = "TOP_UP"
    REJECT = "REJECT"
    NEEDS_HUMAN = "NEEDS_HUMAN"


class AppealResult(BaseModel):
    decision: AppealDecision
    top_up_amount: float
    clause: str
    reasoning: str
    evidence: str
    remittance_note: str


# ----------------------------------------------------------------------------- prompts
AUDIT_PROMPT = """You are PayoutGuard, an accounts-payable auditor for Brightloom Studio's weekly PayPal Payouts run.
Audit ONE contractor invoice before money leaves. Be precise and conservative: never pay money the terms do not support,
but never withhold money that is supported.

Today's payout run date: 2026-11-06.

You are given:
1. Brightloom's contractor terms (PDF, with Schedule A rate card).
2. The invoice (PDF or a phone photo). The file name is meaningless; identify the contractor from the content.
3. Vendor master (contractors of record, rates, caps, PayPal payee emails on file).
4. Payout history (what has already been paid).
5. Other emails received in the AP inbox this week.

Rules:
- Match the contractor by name/content to the vendor master. Check every line: rate vs Schedule A, units vs weekly cap.
- PAY: everything supported. approved_amount = billed_total.
- SHORT_PAY: pay the supported lines, withhold unsupported lines (e.g. surcharges or hours needing written PM approval
  that is not in the documents you were given). approved_amount = sum of approved lines. Cite the clause.
- HOLD_DUPLICATE: the same work/period/amount was already paid in history, even if re-numbered or re-dated (clause 3).
  approved_amount = 0.
- QUARANTINE_PAYEE: the PayPal payee on the invoice or in an inbox email differs from the payee email on file (clause 5),
  especially with urgency, look-alike domains or unusual amounts. approved_amount = 0. Fraud outranks everything else.
- HOLD_REVIEW: you cannot read or match the invoice confidently.
- Check arithmetic. Use evidence quoted from the documents. Keep the remittance note friendly and factual.
"""

APPEAL_PROMPT = """You are PayoutGuard. Earlier today you short-paid an invoice. The contractor has replied with evidence.
Decide whether the withheld amount should now be paid, strictly under Brightloom's contractor terms (attached PDF).

For a surcharge under clause 4.3 to be payable, the approval must: come from the Engagement PM on file, be in writing,
be dated BEFORE the work was performed, and state the amount or a cap. If every condition is met, return TOP_UP with
top_up_amount = the lesser of the withheld amount and the approved amount. If any condition fails, return REJECT
and say which. If it is ambiguous, return NEEDS_HUMAN.
"""


# ----------------------------------------------------------------------------- helpers
def mime_for(p: Path):
    return {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}[p.suffix.lower()]


def context_text():
    vm = (DATA / "vendor_master.json").read_text()
    hist = (DATA / "history" / "payout_history.csv").read_text()
    emails = "\n\n".join(f"=== {p.name} ===\n{p.read_text()}" for p in sorted((DATA / "inbox").glob("*.eml")))
    return f"VENDOR MASTER (JSON):\n{vm}\n\nPAYOUT HISTORY (CSV):\n{hist}\n\nAP INBOX EMAILS THIS WEEK:\n{emails}"


def load_vendor_master() -> dict:
    return json.loads((DATA / "vendor_master.json").read_text())


def load_history() -> list:
    with open(DATA / "history" / "payout_history.csv") as fh:
        return list(csv.DictReader(fh))


def load_expected() -> dict:
    return json.loads((DATA / "expected_labels.json").read_text())["invoices"]


def inbox_files() -> List[Path]:
    return [p for p in sorted(INBOX.iterdir()) if p.suffix.lower() in DOC_SUFFIXES]


def norm_no(s: str) -> str:
    """'INV-0117' and 'INV-117' -> '117' ; 'PN-2026-044' -> '2026044'"""
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits.lstrip("0")


def jaccard(a: str, b: str) -> float:
    ta = {w for w in a.lower().replace("-", " ").split() if len(w) > 2}
    tb = {w for w in b.lower().replace("-", " ").split() if len(w) > 2}
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def guardrails(audit: dict, vm: dict, history: list) -> list:
    """Deterministic checks that can only RAISE severity, never lower it. Returns list of (decision, reason)."""
    raised = []
    k = next((c for c in vm["contractors"] if c["id"] == audit["contractor_id"]), None)
    if k is None:
        return [("HOLD_REVIEW", f"Unknown contractor id {audit['contractor_id']}")]
    payee = (audit.get("payee_email_on_invoice") or "").strip().lower()
    if payee and payee != k["paypal_email"].lower():
        raised.append(("QUARANTINE_PAYEE", f"Payee {payee} != on file {k['paypal_email']}"))
    # Same amount alone is NOT a duplicate (regular contractors bill the same totals every week).
    # Require same amount AND (same invoice number ignoring formatting OR clearly the same description).
    inv_desc = " ".join(li["description"] for li in audit["line_items"])
    for h in history:
        if h["contractor_id"] != k["id"] or abs(float(h["amount_usd"]) - audit["billed_total"]) > 0.01:
            continue
        same_no = norm_no(h["invoice_number"]) == norm_no(audit.get("invoice_number", ""))
        if same_no or jaccard(h["description"], inv_desc) >= 0.5:
            raised.append(("HOLD_DUPLICATE", f"Same amount and work already paid {h['payout_date']} as "
                                             f"{h['invoice_number']} ({h['description']})"))
    s = round(sum(li["amount"] for li in audit["line_items"]), 2)
    if abs(s - audit["billed_total"]) > 0.01:
        raised.append(("HOLD_REVIEW", f"Line items sum to {s}, invoice total {audit['billed_total']}"))
    return raised


def apply_guardrails(audit: dict, raised: list) -> dict:
    final = audit["decision"]
    for dec, _ in raised:
        if SEVERITY.index(dec) > SEVERITY.index(final):
            final = dec
    if final != audit["decision"]:
        audit["ai_decision"] = audit["decision"]
        audit["decision"] = final
        if final in ("HOLD_DUPLICATE", "QUARANTINE_PAYEE", "HOLD_REVIEW"):
            audit["approved_amount"] = 0.0
    audit["guardrail_flags"] = [r for _, r in raised]
    return audit


class Provider:
    """Thin wrapper so the same audit runs on OpenAI (default) or Gemini (backup)."""

    def __init__(self, name, model=None):
        self.name = name
        if name == "openai":
            from openai import OpenAI
            self.client = OpenAI()
            self.model = model or os.environ.get("OPENAI_MODEL", "gpt-5-mini")
        elif name == "gemini":
            from google import genai
            self.client = genai.Client()
            self.model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        else:
            raise ValueError(f"Unknown provider {name}")

    # parts: list of str (text) or Path (a PDF / image file)
    def _openai_content(self, parts):
        content = []
        for p in parts:
            if isinstance(p, Path):
                b64 = base64.b64encode(p.read_bytes()).decode()
                mt = mime_for(p)
                if mt == "application/pdf":
                    content.append({"type": "input_file", "filename": p.name,
                                    "file_data": f"data:application/pdf;base64,{b64}"})
                else:
                    content.append({"type": "input_image", "image_url": f"data:{mt};base64,{b64}", "detail": "high"})
            else:
                content.append({"type": "input_text", "text": p})
        return [{"role": "user", "content": content}]

    def _gemini_parts(self, parts):
        from google.genai import types
        return [types.Part.from_bytes(data=p.read_bytes(), mime_type=mime_for(p)) if isinstance(p, Path) else p
                for p in parts]

    def generate(self, parts, schema, retries=2):
        for attempt in range(retries + 1):
            try:
                if self.name == "openai":
                    r = self.client.responses.parse(model=self.model, input=self._openai_content(parts),
                                                    text_format=schema, store=False)
                    return r.output_parsed.model_dump(mode="json")
                from google.genai import types
                cfg = types.GenerateContentConfig(temperature=0, response_mime_type="application/json",
                                                  response_schema=schema)
                r = self.client.models.generate_content(model=self.model, contents=self._gemini_parts(parts), config=cfg)
                return json.loads(r.text)
            except Exception as e:  # noqa: BLE001 - surface any API error after retries
                if attempt == retries:
                    raise
                print(f"   retry after error: {e}")
                time.sleep(3)


def make_provider(name=None, model=None) -> Provider:
    load_env()
    return Provider(name or os.environ.get("AI_PROVIDER", "openai"), model)


# ----------------------------------------------------------------------------- cache
def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cache_key(path: Path, provider_name: str, model: str, ctx: str) -> str:
    """Audit result cache key: the file's own hash, scoped to the prompt+model+context it was produced with."""
    scope = hashlib.sha256("|".join([PROMPT_VERSION, provider_name, model, AUDIT_PROMPT, ctx]).encode()).hexdigest()[:12]
    return f"{file_hash(path)}-{scope}"


def cache_path(key: str) -> Path:
    return CACHE / f"{key}.json"


def cache_read(key: str) -> Optional[dict]:
    p = cache_path(key)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return None
    return None


def cache_write(key: str, audit: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_path(key).write_text(json.dumps(audit, indent=2))


def legacy_cache(name: str) -> Optional[dict]:
    """Fallback for AUDIT_MODE=cached before any cached run: reuse out/audit_results.json from the kill test."""
    p = OUT / "audit_results.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get(name)
    except json.JSONDecodeError:
        return None


# ----------------------------------------------------------------------------- audit entry points
def audit_file(path: Path, provider: Optional[Provider] = None, *, use_guardrails: bool = True,
               mode: Optional[str] = None, ctx: Optional[str] = None, vm: Optional[dict] = None,
               history: Optional[list] = None, force: bool = False) -> dict:
    """Audit one invoice file. Returns the audit dict, with cache metadata under _cache.

    mode "cached" never calls the AI; mode "live" calls it only on a cache miss
    (or when force=True, which is the "Re-audit live" button).
    """
    mode = mode or audit_mode()
    ctx = context_text() if ctx is None else ctx
    vm = load_vendor_master() if vm is None else vm
    history = load_history() if history is None else history

    if mode == "cached":
        # No AI call allowed. Use any cache entry for this file, else the kill-test results.
        for p in sorted(CACHE.glob(f"{file_hash(path)}-*.json")) if CACHE.exists() else []:
            try:
                audit = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            audit["_cache"] = {"hit": True, "key": p.stem, "mode": "cached"}
            return audit
        audit = legacy_cache(path.name)
        if audit is None:
            raise FileNotFoundError(f"AUDIT_MODE=cached but no cached audit for {path.name} "
                                    f"(run with AUDIT_MODE=live once, or run scripts/audit_test.py)")
        audit = dict(audit)
        audit["_cache"] = {"hit": True, "key": "out/audit_results.json", "mode": "cached"}
        return audit

    provider = provider or make_provider()
    key = cache_key(path, provider.name, provider.model, ctx)
    cached = None if force else cache_read(key)
    if cached is not None:
        cached["_cache"] = {"hit": True, "key": key, "mode": "live"}
        return cached

    audit = provider.generate([AUDIT_PROMPT, "CONTRACTOR TERMS:", TERMS, f"INVOICE FILE ({path.name}):", path, ctx],
                              InvoiceAudit)
    if use_guardrails:
        audit = apply_guardrails(audit, guardrails(audit, vm, history))
    cache_write(key, audit)
    audit["_cache"] = {"hit": False, "key": key, "mode": "live"}
    return audit


def appeals_dir_files() -> List[Path]:
    d = DATA / "appeals"
    return sorted(d.glob("*.eml")) if d.is_dir() else []


def find_appeal_file(contractor_name: str, explicit: Optional[str] = None) -> Path:
    """Locate the contractor reply to re-check. Name it explicitly, or we match on surname."""
    files = appeals_dir_files()
    if explicit:
        if "/" in explicit or "\\" in explicit or explicit.startswith("."):
            raise ValueError(f"bad appeal file name {explicit!r}")
        p = DATA / "appeals" / explicit
        if not p.is_file():
            raise FileNotFoundError(f"no appeal file data/appeals/{explicit}")
        return p
    parts = [w for w in (contractor_name or "").lower().split() if len(w) > 2]
    for p in files:
        hay = (p.name + " " + p.read_text(errors="replace")).lower()
        if any(w in hay for w in parts):
            return p
    if len(files) == 1:
        return files[0]
    raise FileNotFoundError(f"no appeal file in data/appeals for {contractor_name!r}")


def withheld_amount(audit: dict) -> float:
    return round(float(audit["billed_total"]) - float(audit["approved_amount"]), 2)


def appeal_cache_key(audit: dict, appeal_text: str, provider_name: str, model: str) -> str:
    seed = json.dumps({
        "file": audit.get("file"),
        "invoice": audit.get("invoice_number"),
        "decision": audit.get("decision"),
        "approved": audit.get("approved_amount"),
        "billed": audit.get("billed_total"),
    }, sort_keys=True)
    h = hashlib.sha256("|".join([PROMPT_VERSION, provider_name, model, APPEAL_PROMPT, seed, appeal_text]).encode())
    return f"appeal-{h.hexdigest()}"


def audit_appeal(audit: dict, appeal_text: str, provider: Optional[Provider] = None,
                 vm: Optional[dict] = None, *, mode: Optional[str] = None, force: bool = False) -> dict:
    """Re-check a short-paid invoice against a contractor reply.

    Cached like the audit itself, so the appeal beat in the video is instant and identical.
    """
    mode = mode or audit_mode()
    vm = load_vendor_master() if vm is None else vm
    withheld = withheld_amount(audit)

    if mode == "cached":
        for p in sorted(CACHE.glob("appeal-*.json")) if CACHE.exists() else []:
            try:
                res = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if res.get("_for_file") == audit.get("file"):
                out = dict(res)
                out["_cache"] = {"hit": True, "key": p.stem, "mode": "cached"}
                return out
        res = legacy_cache(f"_appeal_{audit.get('file')}")
        if res is None:
            raise FileNotFoundError(f"AUDIT_MODE=cached but no cached appeal for {audit.get('file')} "
                                    f"(run with AUDIT_MODE=live once, or run scripts/audit_test.py)")
        out = dict(res)
        out["_cache"] = {"hit": True, "key": "out/audit_results.json", "mode": "cached"}
        return out

    provider = provider or make_provider()
    key = appeal_cache_key(audit, appeal_text, provider.name, provider.model)
    cached = None if force else cache_read(key)
    if cached is not None:
        cached["_cache"] = {"hit": True, "key": key, "mode": "live"}
        return cached

    res = provider.generate([APPEAL_PROMPT, "CONTRACTOR TERMS:", TERMS,
                             f"ORIGINAL AUDIT:\n{json.dumps(audit, indent=2)}\nWITHHELD AMOUNT: {withheld}",
                             f"VENDOR MASTER:\n{json.dumps(vm, indent=2)}",
                             f"CONTRACTOR REPLY:\n{appeal_text}"], AppealResult)
    res["_for_file"] = audit.get("file")
    cache_write(key, res)
    res["_cache"] = {"hit": False, "key": key, "mode": "live"}
    return res

