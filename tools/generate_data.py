"""Generate the synthetic PayoutGuard demo dataset.

Everything here is fictional: the company (Brightloom Studio), contractors,
emails and amounts. Re-run any time:  python tools/generate_data.py

Outputs into ./data
  contracts/brightloom_contractor_terms.pdf   human-readable terms + Schedule A rate card
  vendor_master.json                         contractors of record (incl. PayPal payee emails)
  history/payout_history.csv                  last 7 weekly payout runs
  inbox/*.pdf|*.jpg                           8 invoices for the 6 Nov 2026 run (messy filenames)
  inbox/RE_ payment details update.eml        the payee-change scam email
  appeals/priya_reply.eml                     contractor appeal with PM approval forwarded
  expected_labels.json                        ground truth for the audit test
"""
import csv
import json
import os
import random
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
random.seed(7)

COMPANY = {
    "name": "Brightloom Studio Pte. Ltd.",
    "address": "21 Carpenter Street, #04-02, Singapore 059984",
    "ap_email": "payables@brightloom.studio",
}

# PayPal payee emails are SANDBOX placeholders. Create matching sandbox personal
# accounts in the PayPal Developer Dashboard (Testing Tools > Sandbox Accounts),
# or edit these to your own sandbox personal account emails.
CONTRACTORS = [
    {"id": "C001", "name": "Maya Chen", "role": "UI designer", "rate": 85, "unit": "hour",
     "weekly_cap": 45, "paypal_email": "maya.chen.brightloom@personal.example.com",
     "contact_email": "maya@mayachen.design"},
    {"id": "C002", "name": "Diego Ramos", "role": "Copywriter", "rate": 360, "unit": "blog post",
     "weekly_cap": None, "paypal_email": "diego.ramos.brightloom@personal.example.com",
     "contact_email": "diego@ramoswrites.com"},
    {"id": "C003", "name": "Priya Nair", "role": "Frontend developer", "rate": 84, "unit": "hour",
     "weekly_cap": 50, "paypal_email": "priya.nair.brightloom@personal.example.com",
     "contact_email": "priya@nair.dev"},
    {"id": "C004", "name": "Tom Okafor", "role": "Video editor", "rate": 1200, "unit": "edited video",
     "weekly_cap": None, "paypal_email": "tom.okafor.brightloom@personal.example.com",
     "contact_email": "tom@okaforedits.com"},
    {"id": "C005", "name": "Lena Fischer", "role": "Illustrator", "rate": 450, "unit": "illustration",
     "weekly_cap": None, "paypal_email": "lena.fischer.brightloom@personal.example.com",
     "contact_email": "lena@lenafischer.art"},
    {"id": "C006", "name": "Sam Whitfield", "role": "QA tester", "rate": 55, "unit": "hour",
     "weekly_cap": 40, "paypal_email": "sam.whitfield.brightloom@personal.example.com",
     "contact_email": "sam.whitfield@qa-mail.net"},
    {"id": "C007", "name": "Kofi Mensah", "role": "Motion designer", "rate": 80, "unit": "hour",
     "weekly_cap": 45, "paypal_email": "kofi.mensah.brightloom@personal.example.com",
     "contact_email": "kofi@mensahmotion.com"},
    {"id": "C008", "name": "Aiko Tanaka", "role": "Translator (JA-EN)", "rate": 0.14, "unit": "word",
     "weekly_cap": None, "paypal_email": "aiko.tanaka.brightloom@personal.example.com",
     "contact_email": "aiko@tanaka-translate.jp"},
]
BY_ID = {c["id"]: c for c in CONTRACTORS}
PM = {"name": "Daniel Koh", "title": "Engagement PM", "email": "daniel.koh@brightloom.studio"}


# --------------------------------------------------------------------------- terms
def build_terms_pdf(path):
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    y = h - 25 * mm
    c.setFont("Helvetica-Bold", 15)
    c.drawString(20 * mm, y, "Brightloom Studio — Independent Contractor Terms (v3.2)")
    y -= 7 * mm
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, y, f"{COMPANY['name']} · {COMPANY['address']} · Effective 1 July 2026")
    y -= 10 * mm
    clauses = [
        ("1. Rates", "Contractors are paid the rates in Schedule A. Rates may only change by signed amendment."),
        ("2. Invoicing", "Invoices are submitted weekly to payables@brightloom.studio. Each invoice must carry a unique "
                         "invoice number and itemise units x rate."),
        ("3. Duplicates", "An invoice for work already paid (same work, same period, same amount) will not be paid again, "
                          "even if re-numbered or re-dated."),
        ("4.1 Weekly cap", "Hourly contractors may bill up to the weekly cap in Schedule A without further approval."),
        ("4.2 Hours above cap", "Hours above the weekly cap are payable only with a written change order from the "
                                "Engagement PM issued before the work is performed."),
        ("4.3 Rush / weekend surcharges", "Rush, weekend or out-of-hours surcharges are payable only if pre-approved in "
                                          "writing by the Engagement PM before the work is performed, and the approval "
                                          "states the amount or cap."),
        ("5. Payee details", "Changes to a contractor's PayPal payee email must be requested through the contractor "
                             "portal and verified by phone call-back to the number on file. Change requests received "
                             "by email are not actioned. The first payment to new payee details is held for 5 "
                             "business days."),
        ("6. Short payment", "Brightloom may pay the undisputed portion of an invoice and withhold the disputed portion, "
                             "with a written explanation citing the relevant clause. The contractor may submit evidence "
                             "within 10 business days; if the evidence satisfies these terms the balance is paid "
                             "promptly."),
        ("7. Payment method", "Approved amounts are paid weekly on Friday via PayPal Payouts in USD."),
    ]
    for title, body in clauses:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(20 * mm, y, title)
        y -= 5 * mm
        c.setFont("Helvetica", 9.5)
        for line in wrap(body, 105):
            c.drawString(24 * mm, y, line)
            y -= 4.6 * mm
        y -= 2.5 * mm
    y -= 4 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, y, "Schedule A — Rate card")
    y -= 7 * mm
    cols = [20, 38, 72, 112, 168]
    c.setFont("Helvetica-Bold", 9)
    for x, t in zip(cols, ["ID", "Contractor", "Role", "Rate (USD)", "Weekly cap"]):
        c.drawString(x * mm, y, t)
    y -= 2 * mm
    c.line(20 * mm, y, 190 * mm, y)
    y -= 5 * mm
    c.setFont("Helvetica", 9)
    for k in CONTRACTORS:
        rate = f"${k['rate']:,.2f} / {k['unit']}" if k["rate"] < 1 else f"${k['rate']:,.0f} / {k['unit']}"
        cap = f"{k['weekly_cap']} h" if k["weekly_cap"] else "—"
        for x, t in zip(cols, [k["id"], k["name"], k["role"], rate, cap]):
            c.drawString(x * mm, y, t)
        y -= 5.2 * mm
    y -= 6 * mm
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(20 * mm, y, "Fictional document generated for a hackathon demo. Not a real company or contract.")
    c.save()


def wrap(text, n):
    words, lines, cur = text.split(), [], ""
    for wd in words:
        if len(cur) + len(wd) + 1 > n:
            lines.append(cur)
            cur = wd
        else:
            cur = f"{cur} {wd}".strip()
    if cur:
        lines.append(cur)
    return lines


# --------------------------------------------------------------------------- invoices
def money(v):
    return f"${v:,.2f}"


def invoice_pdf(path, style, *, contractor, inv_no, date, period, lines, notes=None, remit=None, extra_header=None):
    """lines = [(description, qty, unit_label, unit_price)]"""
    k = contractor
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    total = round(sum(q * p for _, q, _, p in lines), 2)
    accent = {"a": colors.HexColor("#1f3a5f"), "b": colors.HexColor("#7a2e2e"), "c": colors.black}[style]

    if style == "a":
        c.setFillColor(accent)
        c.rect(0, h - 38 * mm, w, 38 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 22)
        c.drawString(20 * mm, h - 22 * mm, "INVOICE")
        c.setFont("Helvetica", 10)
        c.drawString(20 * mm, h - 30 * mm, f"{k['name']} · {k['role']}")
        c.drawRightString(190 * mm, h - 22 * mm, f"No. {inv_no}")
        c.drawRightString(190 * mm, h - 30 * mm, f"Date: {date}")
        c.setFillColor(colors.black)
        y = h - 52 * mm
    elif style == "b":
        c.setFont("Times-Bold", 20)
        c.setFillColor(accent)
        c.drawString(20 * mm, h - 25 * mm, k["name"])
        c.setFillColor(colors.black)
        c.setFont("Times-Roman", 10)
        c.drawString(20 * mm, h - 31 * mm, f"{k['role']} · {k['contact_email']}")
        c.setFont("Times-Bold", 13)
        c.drawRightString(190 * mm, h - 25 * mm, "Tax Invoice")
        c.setFont("Times-Roman", 10)
        c.drawRightString(190 * mm, h - 31 * mm, f"Invoice # {inv_no}   |   {date}")
        c.line(20 * mm, h - 35 * mm, 190 * mm, h - 35 * mm)
        y = h - 47 * mm
    else:
        c.setFont("Courier-Bold", 14)
        c.drawString(20 * mm, h - 25 * mm, f"invoice {inv_no}")
        c.setFont("Courier", 10)
        c.drawString(20 * mm, h - 32 * mm, f"from: {k['name']} <{k['contact_email']}>")
        c.drawString(20 * mm, h - 37 * mm, f"date: {date}")
        y = h - 50 * mm

    body_font = {"a": "Helvetica", "b": "Times-Roman", "c": "Courier"}[style]
    bold_font = {"a": "Helvetica-Bold", "b": "Times-Bold", "c": "Courier-Bold"}[style]
    if extra_header:
        c.setFont(bold_font, 10)
        c.setFillColor(colors.HexColor("#b00020"))
        c.drawString(20 * mm, y, extra_header)
        c.setFillColor(colors.black)
        y -= 8 * mm
    c.setFont(bold_font, 10)
    c.drawString(20 * mm, y, "Bill to")
    c.setFont(body_font, 10)
    c.drawString(20 * mm, y - 5 * mm, COMPANY["name"])
    c.drawString(20 * mm, y - 10 * mm, COMPANY["address"])
    c.setFont(bold_font, 10)
    c.drawString(120 * mm, y, "Service period")
    c.setFont(body_font, 10)
    c.drawString(120 * mm, y - 5 * mm, period)
    y -= 22 * mm

    c.setFont(bold_font, 9.5)
    c.drawString(20 * mm, y, "Description")
    c.drawRightString(132 * mm, y, "Qty")
    c.drawRightString(160 * mm, y, "Rate")
    c.drawRightString(190 * mm, y, "Amount")
    y -= 2.5 * mm
    c.line(20 * mm, y, 190 * mm, y)
    y -= 6 * mm
    c.setFont(body_font, 9.5)
    for desc, q, unit, p in lines:
        qty = f"{q:,.0f} {unit}" if unit else (f"{q:,.0f}" if q != 1 else "1")
        rate = f"${p:,.2f}" if p < 1 else money(p)
        c.drawString(20 * mm, y, desc)
        c.drawRightString(132 * mm, y, qty)
        c.drawRightString(160 * mm, y, rate)
        c.drawRightString(190 * mm, y, money(q * p))
        y -= 6.5 * mm
    y -= 1 * mm
    c.line(120 * mm, y, 190 * mm, y)
    y -= 6.5 * mm
    c.setFont(bold_font, 11)
    c.drawRightString(160 * mm, y, "TOTAL DUE (USD)")
    c.drawRightString(190 * mm, y, money(total))
    y -= 14 * mm

    c.setFont(bold_font, 9.5)
    c.drawString(20 * mm, y, "Payment")
    c.setFont(body_font, 9.5)
    c.drawString(20 * mm, y - 5 * mm, f"PayPal: {remit or k['paypal_email']}")
    c.drawString(20 * mm, y - 10 * mm, "Terms: Brightloom weekly payout run")
    y -= 20 * mm
    if notes:
        c.setFont(bold_font, 9.5)
        c.drawString(20 * mm, y, "Notes")
        c.setFont(body_font, 9.5)
        y -= 5 * mm
        for ln in notes:
            for part in wrap(ln, 95):
                c.drawString(20 * mm, y, part)
                y -= 4.8 * mm
    c.setFont(body_font, 7.5)
    c.setFillColor(colors.grey)
    c.drawString(20 * mm, 12 * mm, "Fictional invoice generated for a hackathon demo.")
    c.save()
    return total


def photo_of(pdf_path, out_jpg, angle):
    """Turn a PDF page into a 'phone photo' JPG: rotated, warm, slightly soft, on a desk."""
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", "110", "-png", "-singlefile", str(pdf_path), f"{td}/p"], check=True)
        page = Image.open(f"{td}/p.png").convert("RGB")
    page = page.rotate(angle, expand=True, fillcolor=(92, 78, 64), resample=Image.BICUBIC)
    desk = Image.new("RGB", (page.width + 160, page.height + 200), (92, 78, 64))
    desk.paste(page, (80, 90))
    desk = ImageEnhance.Color(desk).enhance(0.85)
    r, g, b = desk.split()
    desk = Image.merge("RGB", (r.point(lambda v: min(255, v + 6)), g, b.point(lambda v: max(0, v - 10))))
    desk = desk.filter(ImageFilter.GaussianBlur(0.6))
    desk = ImageEnhance.Brightness(desk).enhance(0.96)
    desk.save(out_jpg, "JPEG", quality=78)


# --------------------------------------------------------------------------- main
def main():
    for d in ["contracts", "inbox", "appeals", "history"]:
        (DATA / d).mkdir(parents=True, exist_ok=True)
    for f in (DATA / "inbox").glob("*"):
        f.unlink()

    build_terms_pdf(DATA / "contracts" / "brightloom_contractor_terms.pdf")

    vm = {"company": COMPANY, "engagement_pm": PM, "contractors": CONTRACTORS,
          "payee_change_requests_on_file": []}
    (DATA / "vendor_master.json").write_text(json.dumps(vm, indent=2))

    # ---- payout history: 7 Fridays, 18 Sep - 30 Oct 2026
    fridays = ["2026-09-18", "2026-09-25", "2026-10-02", "2026-10-09", "2026-10-16", "2026-10-23", "2026-10-30"]
    typical = {"C001": (34, 42), "C003": (38, 48), "C006": (14, 26), "C007": (28, 40)}
    rows = []
    for i, day in enumerate(fridays):
        for k in CONTRACTORS:
            cid = k["id"]
            if cid in typical:
                units = random.randint(*typical[cid])
            elif cid == "C002":
                units = 4 if day == "2026-10-30" else random.choice([2, 3, 4])
            elif cid == "C004":
                units = [2, 3, 1, 2, 2, 3, 2][i]
            elif cid == "C005":
                units = random.choice([2, 3, 4])
            else:  # C008 words
                units = random.choice([5000, 6200, 7400, 8000])
            amount = round(units * k["rate"], 2)
            initials = "".join(p[0] for p in k["name"].split())
            inv = f"{initials}-{280 + i * 3 + int(cid[-1])}"
            if cid == "C002" and day == "2026-10-30":
                inv = "INV-117"
                desc = "Blog series - October (4 posts)"
            else:
                desc = f"{units:,} x {k['unit']}"
            rows.append({"payout_date": day, "contractor_id": cid, "contractor_name": k["name"],
                         "invoice_number": inv, "description": desc, "amount_usd": f"{amount:.2f}",
                         "paypal_email": k["paypal_email"], "status": "SUCCESS"})
    with open(DATA / "history" / "payout_history.csv", "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)

    period = "2 Nov - 6 Nov 2026"
    inbox = DATA / "inbox"
    tmp = DATA / "_tmp"
    tmp.mkdir(exist_ok=True)

    # 1. Priya — rush surcharge without pre-approval -> SHORT_PAY 4,200 (withhold 650)
    invoice_pdf(inbox / "scan_0042.pdf", "a", contractor=BY_ID["C003"], inv_no="PN-2026-044", date="6 Nov 2026",
                period=period, lines=[
                    ("Frontend development - Sprint 14", 45, "h", 84),
                    ("Weekend release support (Sat 7 Nov)", 5, "h", 84),
                    ("Rush surcharge - weekend release", 1, "", 650),
                ], notes=["Weekend release support as discussed with Daniel."])
    # 2. Diego — fuzzy duplicate of INV-117 already paid 30 Oct -> HOLD_DUPLICATE
    invoice_pdf(inbox / "Document (3).pdf", "b", contractor=BY_ID["C002"], inv_no="INV-0117", date="3 Nov 2026",
                period="October 2026", lines=[("Blog series - October (4 posts)", 4, "posts", 360)],
                notes=["Resending in case this one got lost - thanks!"])
    # 3. Tom — payee change to look-alike domain + 3x+ normal amount -> QUARANTINE_PAYEE
    invoice_pdf(inbox / "invoice.pdf", "a", contractor=BY_ID["C004"], inv_no="TO-1188", date="5 Nov 2026",
                period=period, lines=[
                    ("Edited video - product launch series", 7, "videos", 1200),
                    ("Raw footage archive export", 1, "", 1000),
                ], remit="tom.okafor.payments@okafor-edits.co",
                extra_header="UPDATED PAYMENT DETAILS - please use the new PayPal address below",
                notes=["My old PayPal is being closed, please pay the new address this week.",
                       "Urgent: needed before the weekend."])
    # 4-8 clean invoices
    invoice_pdf(inbox / "scan_0043.pdf", "b", contractor=BY_ID["C001"], inv_no="MC-0311", date="6 Nov 2026",
                period=period, lines=[("UI design - onboarding flow v2", 40, "h", 85)])
    invoice_pdf(inbox / "final_FINAL_v2.pdf", "c", contractor=BY_ID["C007"], inv_no="KM-2026-19", date="6 Nov 2026",
                period=period, lines=[("Motion design - launch teaser", 30, "h", 80),
                                      ("Motion design - social cutdowns", 6, "h", 80)])
    invoice_pdf(inbox / "20261105_invoice.pdf", "c", contractor=BY_ID["C008"], inv_no="AT-118", date="5 Nov 2026",
                period=period, lines=[("Translation JA>EN - help centre articles", 7500, "words", 0.14)])
    invoice_pdf(tmp / "lena.pdf", "b", contractor=BY_ID["C005"], inv_no="LF-0092", date="6 Nov 2026",
                period=period, lines=[("Editorial illustration - blog headers", 3, "illus.", 450)])
    photo_of(tmp / "lena.pdf", inbox / "IMG_1234.jpg", angle=-2.2)
    invoice_pdf(tmp / "sam.pdf", "a", contractor=BY_ID["C006"], inv_no="SW-77", date="6 Nov 2026",
                period=period, lines=[("Regression testing - checkout", 22, "h", 55)])
    photo_of(tmp / "sam.pdf", inbox / "IMG_1301.jpg", angle=1.6)
    for f in tmp.glob("*"):
        f.unlink()
    tmp.rmdir()

    # ---- scam email (look-alike domain okafor-edits.co vs okaforedits.com)
    (inbox / "RE_ payment details update.eml").write_text(
        "From: Tom Okafor <tom@okafor-edits.co>\n"
        "To: payables@brightloom.studio\n"
        "Date: Thu, 05 Nov 2026 21:14:09 +0800\n"
        "Subject: RE: payment details update - URGENT\n\n"
        "Hi team,\n\n"
        "Quick one - my PayPal account is being closed this week, so please send this Friday's payment\n"
        "to my new PayPal: tom.okafor.payments@okafor-edits.co\n\n"
        "Invoice TO-1188 attached. I'm travelling and can't take calls, so email is best.\n"
        "Really need this before the weekend, thanks so much!\n\n"
        "Tom\n")

    # ---- appeal: Priya forwards PM pre-approval dated BEFORE the work
    (DATA / "appeals" / "priya_reply.eml").write_text(
        "From: Priya Nair <priya@nair.dev>\n"
        "To: payables@brightloom.studio\n"
        "Date: Fri, 06 Nov 2026 15:02:44 +0800\n"
        "Subject: RE: Short payment on PN-2026-044\n\n"
        "Hi, the weekend rush surcharge was pre-approved by Daniel - forwarding his email below.\n\n"
        "Priya\n\n"
        "---------- Forwarded message ----------\n"
        f"From: {PM['name']} <{PM['email']}>\n"
        "Date: Fri, 30 Oct 2026 18:20:11 +0800\n"
        "To: Priya Nair <priya@nair.dev>\n"
        "Subject: Weekend release support - approved\n\n"
        "Hi Priya,\n\n"
        "Confirming the weekend release support on Sat 7 Nov is approved under clause 4.3.\n"
        "Up to 5 hours at your standard rate, plus a flat rush surcharge of USD 650.\n\n"
        f"Thanks,\n{PM['name']}\n{PM['title']}, Brightloom Studio\n")

    # ---- ground truth
    expected = {
        "run_date": "2026-11-06",
        "invoices": {
            "scan_0042.pdf": {"contractor_id": "C003", "billed_total": 4850.00, "decision": "SHORT_PAY",
                              "approved_amount": 4200.00, "withheld": 650.00,
                              "why": "Rush surcharge needs written PM pre-approval stating the amount (clause 4.3); none on file at run time.",
                              "after_appeal": {"appeal_file": "appeals/priya_reply.eml", "decision": "TOP_UP",
                                               "top_up_amount": 650.00,
                                               "why": "Forwarded PM email dated 30 Oct (before the work) approves a flat USD 650 surcharge, satisfying clause 4.3."}},
            "Document (3).pdf": {"contractor_id": "C002", "billed_total": 1440.00, "decision": "HOLD_DUPLICATE",
                                 "approved_amount": 0.00,
                                 "why": "Same work, period and amount as INV-117 paid 30 Oct 2026; re-numbered INV-0117 (clause 3)."},
            "invoice.pdf": {"contractor_id": "C004", "billed_total": 9400.00, "decision": "QUARANTINE_PAYEE",
                            "approved_amount": 0.00,
                            "why": "Payee email changed to look-alike domain okafor-edits.co via email (clause 5); amount ~3-4x Tom's usual; urgency + no call-back."},
            "scan_0043.pdf": {"contractor_id": "C001", "billed_total": 3400.00, "decision": "PAY", "approved_amount": 3400.00},
            "final_FINAL_v2.pdf": {"contractor_id": "C007", "billed_total": 2880.00, "decision": "PAY", "approved_amount": 2880.00},
            "20261105_invoice.pdf": {"contractor_id": "C008", "billed_total": 1050.00, "decision": "PAY", "approved_amount": 1050.00},
            "IMG_1234.jpg": {"contractor_id": "C005", "billed_total": 1350.00, "decision": "PAY", "approved_amount": 1350.00},
            "IMG_1301.jpg": {"contractor_id": "C006", "billed_total": 1210.00, "decision": "PAY", "approved_amount": 1210.00},
        },
    }
    billed = sum(v["billed_total"] for v in expected["invoices"].values())
    paid = sum(v["approved_amount"] for v in expected["invoices"].values())
    expected["totals"] = {"billed": billed, "paid_in_batch": paid, "held_or_withheld": round(billed - paid, 2),
                          "paid_after_appeal": paid + 650}
    (DATA / "expected_labels.json").write_text(json.dumps(expected, indent=2))
    print(json.dumps(expected["totals"], indent=2))


if __name__ == "__main__":
    main()
