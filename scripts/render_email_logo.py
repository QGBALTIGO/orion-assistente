"""Render the existing official vector unchanged to an email-compatible PNG.

Development-only: requires the browser test dependency, never runs on startup.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    root = Path(__file__).resolve().parents[1]
    vector = (root/'app/static/orion-brand-light.svg').read_text(encoding='utf-8')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width':800,'height':275},device_scale_factor=1)
            page.route('**/*',lambda route: route.abort())
            page.set_content('<style>html,body{margin:0;padding:0;background:transparent}svg{display:block}</style>'+vector)
            page.screenshot(path=str(root/'app/static/orion-email-logo.png'),omit_background=True)
        finally:
            browser.close()


if __name__=='__main__':
    main()
