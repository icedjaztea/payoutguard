"""Drive the whole payout run in a real browser: reset -> audit -> approve -> appeal.

  python tools/e2e_browser.py                       # against http://localhost:8000
  python tools/e2e_browser.py --base http://... --headed --shots out/e2e

Needs the frontend built (cd web && npm run build) and the server running, and it SENDS two real
PayPal sandbox batches. Install once with: pip install playwright && playwright install chromium.
"""
import argparse
import re
import sys
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

MONEY = re.compile(r"[^0-9.]")


def amount(text: str) -> float:
    cleaned = MONEY.sub("", text or "")
    return float(cleaned) if cleaned else 0.0


def counter(page, name: str) -> float:
    """The displayed number animates, so read the exact value off aria-label, not the tween."""
    metric = page.get_by_test_id(f"counter-{name}")
    labelled = metric.locator("strong span[aria-label]")
    if labelled.count():
        return amount(labelled.first.get_attribute("aria-label") or "")
    return amount(metric.locator("strong").inner_text())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots", default="out/e2e", help="directory for screenshots")
    ap.add_argument("--slow", type=float, default=0, help="slow_mo in ms, for watching it run")
    args = ap.parse_args()
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)
    problems, console = [], []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed, slow_mo=args.slow)
        page = browser.new_page(viewport={"width": 1480, "height": 1000})
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))

        print(f"open {args.base}")
        page.goto(args.base, wait_until="networkidle")
        page.get_by_test_id("audit-run").wait_for(state="visible", timeout=30_000)

        # ---- reset: a fresh run, nothing audited
        print("reset")
        page.get_by_test_id("reset").click()
        page.wait_for_function("() => !!document.querySelector('[data-testid=\"audit-run\"]:not([disabled])')",
                               timeout=60_000)
        docs = page.locator('[data-testid^="document-"]').count()
        print(f"  {docs} documents in the inbox")
        if docs != 8:
            problems.append(f"expected 8 inbox documents, saw {docs}")
        page.screenshot(path=str(shots / "1-inbox.png"), full_page=True)

        # ---- audit
        print("audit")
        page.get_by_test_id("audit-run").click()
        page.locator('[data-testid^="verdict-"]').nth(7).wait_for(state="visible", timeout=180_000)
        page.wait_for_timeout(500)
        verdicts = {}
        for card in page.locator('[data-testid^="verdict-"]').all():
            file = (card.get_attribute("data-testid") or "")[len("verdict-"):]
            verdicts[file] = card.locator(".verdict").inner_text().strip()
        for file, label in sorted(verdicts.items()):
            print(f"  {file:<24}{label}")
        expected = {
            "scan_0042.pdf": "Short pay",
            "Document (3).pdf": "Duplicate hold",
            "invoice.pdf": "Payee quarantined",
        }
        for file, label in expected.items():
            if verdicts.get(file) != label:
                problems.append(f"{file}: expected {label!r}, saw {verdicts.get(file)!r}")
        pays = [f for f, label in verdicts.items() if label == "Pay in full"]
        if len(pays) != 5:
            problems.append(f"expected 5 'Pay in full' verdicts, saw {len(pays)}")
        billed, paying, stopped = counter(page, "billed"), counter(page, "paying"), counter(page, "stopped")
        print(f"  counters: billed {billed:,.0f} paying {paying:,.0f} stopped {stopped:,.0f}")
        for label, got, want in (("billed", billed, 25580), ("paying", paying, 14090), ("stopped", stopped, 11490)):
            if abs(got - want) > 1:
                problems.append(f"counter {label}: expected {want:,}, saw {got:,.0f}")
        page.screenshot(path=str(shots / "2-verdicts.png"), full_page=True)

        # ---- evidence drawer on the short-paid invoice
        print("open the evidence drawer")
        page.get_by_test_id("verdict-scan_0042.pdf").click()
        page.get_by_test_id("close-drawer").wait_for(state="visible", timeout=15_000)
        page.wait_for_timeout(1200)        # let the PDF preview load
        drawer_text = page.locator(".drawer-shell").inner_text()
        if "4.3" not in drawer_text:
            problems.append("the drawer does not mention clause 4.3")
        page.screenshot(path=str(shots / "3-evidence.png"), full_page=True)
        page.get_by_test_id("close-drawer").click()
        page.get_by_test_id("close-drawer").wait_for(state="detached", timeout=10_000)

        # ---- approve: one real sandbox batch, then wait for every item to settle
        print("approve & pay (sends a real sandbox batch)")
        page.get_by_test_id("approve-pay").click()
        page.get_by_test_id("payout-batch").wait_for(state="visible", timeout=120_000)
        batch_id = page.get_by_test_id("payout-batch").locator("code").inner_text().strip()
        print(f"  batch {batch_id}")
        page.wait_for_function(
            """() => {
                 const rows = document.querySelectorAll('[data-testid="payout-batch"] li .status');
                 return rows.length === 6 && [...rows].every(r => r.textContent.includes('SUCCESS'));
               }""", timeout=180_000)
        print("  6/6 items SUCCESS")
        page.screenshot(path=str(shots / "4-batch.png"), full_page=True)

        # ---- appeal: the reply tray appears once the run is paid
        print("review Priya's reply")
        try:
            page.get_by_test_id("review-reply-tray").wait_for(state="visible", timeout=60_000)
        except PWTimeout:
            problems.append("the 'Review evidence' tray button never appeared")
            page.screenshot(path=str(shots / "5-no-reply.png"), full_page=True)
            raise
        page.get_by_test_id("review-reply-tray").click()
        page.get_by_test_id("top-up-batch").wait_for(state="visible", timeout=180_000)
        top_up_id = page.get_by_test_id("top-up-batch").locator("code").inner_text().strip()
        print(f"  top-up batch {top_up_id}")
        page.wait_for_function(
            """() => {
                 const rows = document.querySelectorAll('[data-testid="top-up-batch"] li .status');
                 return rows.length === 1 && rows[0].textContent.includes('SUCCESS');
               }""", timeout=180_000)
        protected = counter(page, "protected")
        paying_after = counter(page, "paying")
        print(f"  protected {protected:,.0f} | paying {paying_after:,.0f}")
        if abs(protected - 10840) > 1:
            problems.append(f"protected: expected 10,840, saw {protected:,.0f}")
        if abs(paying_after - 14740) > 1:
            problems.append(f"paying after top-up: expected 14,740, saw {paying_after:,.0f}")

        # ---- the appeal evidence is visible in the drawer
        page.get_by_test_id("open-appeal-drawer").click()
        page.get_by_test_id("appeal-finding").wait_for(state="visible", timeout=15_000)
        appeal_text = page.get_by_test_id("appeal-finding").inner_text()
        print("  appeal finding:", " ".join(appeal_text.split())[:90])
        if "Top-up" not in appeal_text:
            problems.append("the appeal finding does not show the top-up")
        page.screenshot(path=str(shots / "6-appeal.png"), full_page=True)
        page.get_by_test_id("close-drawer").click()

        # a paid-in-full card is the closing shot
        card = page.get_by_test_id("verdict-scan_0042.pdf").inner_text()
        if "Paid in full after evidence" not in card:
            problems.append(f"Priya's card does not say paid in full: {' '.join(card.split())[:80]}")
        page.screenshot(path=str(shots / "7-final.png"), full_page=True)
        browser.close()

    if console:
        print("\nconsole:")
        for line in console[:12]:
            print(" ", line)
    print(f"\nscreenshots in {shots}/")
    if problems:
        print("\nFAIL")
        for p in problems:
            print(" -", p)
        return 1
    print("\nPASS — reset, audit, approve and appeal all work in the browser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
