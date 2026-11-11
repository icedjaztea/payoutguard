"""Guardrails are deterministic and can only RAISE severity. No network."""
import pytest

from app.audit import SEVERITY, apply_guardrails, guardrails, jaccard, norm_no

VM = {
    "contractors": [
        {"id": "C003", "name": "Priya Nair", "paypal_email": "priya.nair.brightloom@personal.example.com"},
        {"id": "C002", "name": "Diego Ramos", "paypal_email": "diego.ramos.brightloom@personal.example.com"},
        {"id": "C004", "name": "Tom Okafor", "paypal_email": "tom.okafor.brightloom@personal.example.com"},
    ]
}

HISTORY = [
    {"contractor_id": "C002", "invoice_number": "INV-117", "amount_usd": "1440.00", "payout_date": "2026-10-30",
     "description": "4 blog posts - launch series"},
    {"contractor_id": "C003", "invoice_number": "PN-2026-039", "amount_usd": "4200.00", "payout_date": "2026-10-30",
     "description": "Frontend development 50 hours"},
]


def audit(**over):
    base = {
        "contractor_id": "C003",
        "contractor_name": "Priya Nair",
        "invoice_number": "PN-2026-044",
        "payee_email_on_invoice": "priya.nair.brightloom@personal.example.com",
        "line_items": [{"description": "Checkout rebuild sprint 44", "quantity": 50, "unit_price": 84,
                        "amount": 4200.0, "approved": True, "withhold_reason": None}],
        "billed_total": 4200.0,
        "approved_amount": 4200.0,
        "decision": "PAY",
        "findings": [],
    }
    base.update(over)
    return base


# ----------------------------------------------------------------- helpers
def test_norm_no_ignores_formatting_and_leading_zeros():
    assert norm_no("INV-0117") == norm_no("INV-117") == "117"
    assert norm_no("PN-2026-044") == "2026044"


def test_jaccard_same_description_scores_high():
    assert jaccard("4 blog posts - launch series", "4 blog posts launch series") >= 0.5
    assert jaccard("Frontend development", "Illustration set") == 0.0


# ----------------------------------------------------------------- payee check
def test_clean_invoice_raises_nothing():
    assert guardrails(audit(), VM, HISTORY) == []


def test_payee_mismatch_raises_quarantine():
    a = audit(contractor_id="C004", contractor_name="Tom Okafor", invoice_number="TO-2026-19",
              payee_email_on_invoice="accounts@okafor-edits.co",
              line_items=[{"description": "Video edits", "quantity": 1, "unit_price": 9400, "amount": 9400.0,
                           "approved": True, "withhold_reason": None}],
              billed_total=9400.0, approved_amount=9400.0)
    raised = guardrails(a, VM, HISTORY)
    assert [d for d, _ in raised] == ["QUARANTINE_PAYEE"]
    assert "okafor-edits.co" in raised[0][1]


def test_payee_check_is_case_insensitive_and_trims():
    a = audit(payee_email_on_invoice="  PRIYA.NAIR.BRIGHTLOOM@Personal.Example.COM ")
    assert guardrails(a, VM, HISTORY) == []


def test_unknown_contractor_raises_hold_review_only():
    assert guardrails(audit(contractor_id="C999"), VM, HISTORY) == [
        ("HOLD_REVIEW", "Unknown contractor id C999")
    ]


# ----------------------------------------------------------------- duplicate check
def test_duplicate_same_amount_and_renumbered_invoice():
    a = audit(contractor_id="C002", contractor_name="Diego Ramos", invoice_number="INV-0117",
              payee_email_on_invoice="diego.ramos.brightloom@personal.example.com",
              line_items=[{"description": "4 blog posts - launch series", "quantity": 4, "unit_price": 360,
                           "amount": 1440.0, "approved": True, "withhold_reason": None}],
              billed_total=1440.0, approved_amount=1440.0)
    raised = guardrails(a, VM, HISTORY)
    assert [d for d, _ in raised] == ["HOLD_DUPLICATE"]
    assert "2026-10-30" in raised[0][1]


def test_same_amount_different_work_is_not_a_duplicate():
    """Regular contractors bill the same total every week — amount alone must not trip the rule.

    Priya was paid 4,200.00 on 30 Oct for "Frontend development 50 hours"; this week's
    4,200.00 is different work under a different invoice number, so nothing is raised.
    """
    assert guardrails(audit(), VM, HISTORY) == []


def test_same_description_different_invoice_number_is_a_duplicate():
    a = audit(invoice_number="PN-2026-099",
              line_items=[{"description": "Frontend development 50 hours", "quantity": 50, "unit_price": 84,
                           "amount": 4200.0, "approved": True, "withhold_reason": None}])
    assert [d for d, _ in guardrails(a, VM, HISTORY)] == ["HOLD_DUPLICATE"]


# ----------------------------------------------------------------- math check
def test_line_sum_mismatch_raises_hold_review():
    a = audit(billed_total=4850.0)  # lines sum to 4200
    raised = guardrails(a, VM, HISTORY)
    assert [d for d, _ in raised] == ["HOLD_REVIEW"]
    assert "4200" in raised[0][1]


# ----------------------------------------------------------------- severity escalation
def test_apply_guardrails_raises_severity_and_zeroes_amount():
    a = audit(decision="SHORT_PAY", approved_amount=4200.0)
    out = apply_guardrails(a, [("QUARANTINE_PAYEE", "payee changed")])
    assert out["decision"] == "QUARANTINE_PAYEE"
    assert out["ai_decision"] == "SHORT_PAY"
    assert out["approved_amount"] == 0.0
    assert out["guardrail_flags"] == ["payee changed"]


def test_apply_guardrails_never_lowers_severity():
    a = audit(decision="QUARANTINE_PAYEE", approved_amount=0.0)
    out = apply_guardrails(a, [("SHORT_PAY", "minor"), ("PAY", "clean")])
    assert out["decision"] == "QUARANTINE_PAYEE"
    assert "ai_decision" not in out
    assert out["approved_amount"] == 0.0


def test_apply_guardrails_keeps_short_pay_amount():
    """SHORT_PAY raised from PAY must keep the AI's approved amount, not zero it."""
    a = audit(decision="PAY", approved_amount=4850.0, billed_total=4850.0)
    out = apply_guardrails(a, [("SHORT_PAY", "surcharge unapproved")])
    assert out["decision"] == "SHORT_PAY"
    assert out["approved_amount"] == 4850.0


def test_apply_guardrails_no_flags_is_a_noop_decision():
    a = audit()
    out = apply_guardrails(a, [])
    assert out["decision"] == "PAY"
    assert out["guardrail_flags"] == []


@pytest.mark.parametrize("worse,better", list(zip(SEVERITY[1:], SEVERITY[:-1])))
def test_severity_order_is_monotonic(worse, better):
    a = apply_guardrails(audit(decision=better), [(worse, "r")])
    assert a["decision"] == worse
