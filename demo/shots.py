"""Save the four submission screenshots from the hosted app, in the order the demo tells the story.

    ../.venv/bin/python demo/shots.py [--base URL] [--out submission]

It photographs the run that is already there — tray-after-top-up, the verdict cards and the scam
evidence drawer all come from the finished state, so the amounts in them agree with the video. The
inbox shot is taken last, after a reset, because resetting is what makes the inbox empty of verdicts
again.

The payout shot needs a run that has actually been paid, so if the tray has no top-up batch the
script says which shot it skipped and takes the other three rather than inventing one.
"""
import argparse
from playwright.sync_api import sync_playwright

SHOTS = "1-inbox.png", "2-verdict-cards.png", "3-scam-evidence-drawer.png", "4-tray-after-top-up.png"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://payoutguard.onrender.com")
    ap.add_argument("--out", default="submission")
    args = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=2)
        page.goto(args.base, wait_until="networkidle")
        page.wait_for_selector('[data-testid="verdict-invoice.pdf"]', timeout=60_000)
        page.wait_for_timeout(1200)

        # The finished payout tray: initial batch, Priya's appeal, and the top-up batch under it.
        taken, skipped = [], []
        if page.get_by_test_id("top-up-batch").count():
            page.screenshot(path=f"{args.out}/{SHOTS[3]}")
            taken.append(SHOTS[3])
        else:
            skipped.append(f"{SHOTS[3]} (this run has no top-up batch yet)")

        # All eight verdicts, with Priya's card reading "Paid in full after evidence" once topped up.
        page.get_by_test_id("counter-protected").scroll_into_view_if_needed()
        page.screenshot(path=f"{args.out}/{SHOTS[1]}")
        taken.append(SHOTS[1])

        # The quarantined payee, source document beside the finding that stopped it.
        page.get_by_test_id("verdict-invoice.pdf").click()
        page.wait_for_selector("dialog.drawer")
        page.wait_for_timeout(2500)                      # let the PDF preview paint
        page.screenshot(path=f"{args.out}/{SHOTS[2]}")
        taken.append(SHOTS[2])
        page.get_by_test_id("close-drawer").click()

        # Reset last: this is the only way back to an un-audited inbox.
        page.get_by_test_id("reset").click()
        page.wait_for_selector("text=Awaiting audit", timeout=60_000)
        page.wait_for_timeout(1200)
        page.screenshot(path=f"{args.out}/{SHOTS[0]}")
        taken.append(SHOTS[0])

        browser.close()
    for name in sorted(taken):
        print(f"saved    {args.out}/{name}")
    for note in skipped:
        print(f"skipped  {note}")


if __name__ == "__main__":
    main()
