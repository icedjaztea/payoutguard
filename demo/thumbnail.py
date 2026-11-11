"""Render demo/thumbnail.html to submission/thumbnail.png (1200x675, Devpost gallery size).

    ../.venv/bin/python demo/thumbnail.py

Rendered at 2x and downscaled by the viewport setting, so the text stays sharp on Devpost's card.
"""
import pathlib
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "submission" / "thumbnail.png"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1200, "height": 675}, device_scale_factor=2)
    page.goto((ROOT / "demo" / "thumbnail.html").as_uri(), wait_until="networkidle")
    page.wait_for_timeout(1500)                       # webfonts
    page.screenshot(path=str(OUT))
    browser.close()
print(OUT)
