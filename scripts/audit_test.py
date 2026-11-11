"""Kill test #2 — can the AI audit messy invoices against the contract and history?

Usage
  pip install -r requirements.txt
  python scripts/audit_test.py                  # audit all 8 invoices + run the appeal, score vs ground truth
  python scripts/audit_test.py --no-guardrails  # pure AI, no deterministic checks
  python scripts/audit_test.py --only scan_0042.pdf
  python scripts/audit_test.py --provider gemini   # backup provider

Provider (in .env or the environment):
  AI_PROVIDER=openai (default) with OPENAI_API_KEY=...  OPENAI_MODEL=<newest model you have that reads PDFs + images>
  AI_PROVIDER=gemini with GEMINI_API_KEY=...  GEMINI_MODEL=...   (or the Vertex env vars)

PASS = all 8 decisions + approved amounts match data/expected_labels.json, and the appeal returns TOP_UP $650.
Results are saved to out/audit_results.json for the app to reuse.
"""
import argparse
import base64
import csv
import json
import os
import sys
import time
from enum import Enum
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "out"
SEVERITY = ["PAY", "SHORT_PAY", "HOLD_REVIEW", "HOLD_DUPLICATE", "QUARANTINE_PAYEE"]


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


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
            sys.exit(f"Unknown provider {name}")

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


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-guardrails", action="store_true")
    ap.add_argument("--only", help="audit a single inbox file name")
    ap.add_argument("--model", default=None)
    ap.add_argument("--provider", choices=["openai", "gemini"], default=None)
    args = ap.parse_args()
    load_env()

    provider = Provider(args.provider or os.environ.get("AI_PROVIDER", "openai"), args.model)
    terms = DATA / "contracts" / "brightloom_contractor_terms.pdf"
    ctx = context_text()
    vm = json.loads((DATA / "vendor_master.json").read_text())
    history = list(csv.DictReader(open(DATA / "history" / "payout_history.csv")))
    expected = json.loads((DATA / "expected_labels.json").read_text())["invoices"]

    files = [p for p in sorted((DATA / "inbox").iterdir()) if p.suffix.lower() in (".pdf", ".jpg", ".jpeg", ".png")]
    if args.only:
        files = [p for p in files if p.name == args.only]
    print(f"Provider: {provider.name} | model: {provider.model} | guardrails: {'off' if args.no_guardrails else 'on'} | invoices: {len(files)}\n")

    results, passed = {}, 0
    for p in files:
        t0 = time.time()
        audit = provider.generate([AUDIT_PROMPT, "CONTRACTOR TERMS:", terms, f"INVOICE FILE ({p.name}):", p, ctx],
                                  InvoiceAudit)
        if not args.no_guardrails:
            audit = apply_guardrails(audit, guardrails(audit, vm, history))
        results[p.name] = audit
        exp = expected.get(p.name)
        ok = exp and audit["decision"] == exp["decision"] and abs(audit["approved_amount"] - exp["approved_amount"]) < 1
        passed += bool(ok)
        flag = "PASS" if ok else "FAIL"
        print(f"[{flag}] {p.name:<22} {audit['contractor_name']:<14} billed {audit['billed_total']:>9,.2f}  "
              f"-> {audit['decision']:<16} {audit['approved_amount']:>9,.2f}   ({time.time() - t0:.1f}s)")
        if not ok and exp:
            print(f"       expected {exp['decision']} {exp['approved_amount']:,.2f}")
        for f in audit["findings"]:
            print(f"       - {f['code']} (cl. {f.get('clause')}): {f['explanation']}")
        for g in audit.get("guardrail_flags", []):
            print(f"       ! guardrail: {g}")

    # ---- appeal round for the short-paid invoice
    appeal_ok = None
    if "scan_0042.pdf" in results and results["scan_0042.pdf"]["decision"] == "SHORT_PAY":
        a = results["scan_0042.pdf"]
        withheld = round(a["billed_total"] - a["approved_amount"], 2)
        appeal = (DATA / "appeals" / "priya_reply.eml").read_text()
        res = provider.generate([APPEAL_PROMPT, "CONTRACTOR TERMS:", terms,
                                       f"ORIGINAL AUDIT:\n{json.dumps(a, indent=2)}\nWITHHELD AMOUNT: {withheld}",
                                       f"VENDOR MASTER:\n{json.dumps(vm, indent=2)}",
                                       f"CONTRACTOR REPLY:\n{appeal}"], AppealResult)
        results["_appeal_scan_0042.pdf"] = res
        appeal_ok = res["decision"] == "TOP_UP" and abs(res["top_up_amount"] - 650) < 1
        print(f"\n[{'PASS' if appeal_ok else 'FAIL'}] appeal (Priya)        -> {res['decision']} "
              f"{res['top_up_amount']:,.2f}\n       {res['reasoning']}")

    OUT.mkdir(exist_ok=True)
    (OUT / "audit_results.json").write_text(json.dumps(results, indent=2))
    total = len(files)
    print(f"\nScore: {passed}/{total} invoices correct; appeal: "
          f"{'n/a' if appeal_ok is None else ('PASS' if appeal_ok else 'FAIL')}")
    print("Saved out/audit_results.json")
    all_ok = passed == total and (appeal_ok in (True, None) if args.only else appeal_ok)
    print("\nKILL TEST:", "PASS" if all_ok else "NOT YET — check FAIL lines; tighten prompt or rely on guardrails")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
