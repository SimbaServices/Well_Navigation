"""Capture App Store screenshot sizes from the live website."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "screenshots"
EMAIL = "appreview@simba.services"
PASSWORD = Path(r"C:\Simba\wellnav-app-review-login.txt").read_text(encoding="utf-8")
PASSWORD = next(line.split("=", 1)[1] for line in PASSWORD.splitlines() if line.startswith("password="))

RESULTS = (
    "https://wellnav.simba.services/search"
    "?offset=0&scope=wells&state=tx&mode=operator&q=OXY&sort=name&dir=asc"
)


def login(page) -> None:
    page.goto("https://wellnav.simba.services/login", wait_until="domcontentloaded")
    page.locator('input[name="email"]').fill(EMAIL)
    page.locator('input[name="password"]').fill(PASSWORD)
    page.locator("button.primary").click()
    page.wait_for_url("https://wellnav.simba.services/**", timeout=30000)
    page.wait_for_selector("#q", timeout=15000)


def run_flow(page, prefix: str) -> None:
    login(page)
    page.goto("https://wellnav.simba.services/", wait_until="domcontentloaded")
    page.wait_for_selector("#q")
    page.screenshot(path=OUT / f"{prefix}-01-search.png", full_page=False)

    page.goto(RESULTS, wait_until="domcontentloaded")
    page.wait_for_selector("#results", timeout=45000)
    page.wait_for_function(
        "() => (document.getElementById('results')||{}).innerText.includes('OXY')",
        timeout=45000,
    )
    page.screenshot(path=OUT / f"{prefix}-02-results.png", full_page=False)

    page.locator("#tab-map").click()
    page.wait_for_timeout(2500)
    page.screenshot(path=OUT / f"{prefix}-03-map.png", full_page=False)

    page.goto("https://wellnav.simba.services/account", wait_until="domcontentloaded")
    page.wait_for_selector("text=Delete account", timeout=20000)
    page.evaluate(
        """() => {
          document.querySelectorAll('.search-form, .hint, #operator-suggest, .workspace-tabs')
            .forEach((el) => { el.style.display = 'none'; });
          for (const h of document.querySelectorAll('h3')) {
            if ((h.textContent || '').includes('UX recordings')) {
              let node = h;
              while (node) {
                const next = node.nextElementSibling;
                node.style.display = 'none';
                node = next;
              }
            }
          }
          const card = document.querySelector('.account-card');
          if (card) card.scrollIntoView({block: 'start'});
        }"""
    )
    page.screenshot(path=OUT / f"{prefix}-04-account.png", full_page=False)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = {
        "iphone-6.9": {"viewport": {"width": 430, "height": 932}, "device_scale_factor": 3},
        "ipad-13": {"viewport": {"width": 1024, "height": 1366}, "device_scale_factor": 2},
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for name, opts in sizes.items():
            context = browser.new_context(
                viewport=opts["viewport"],
                device_scale_factor=opts["device_scale_factor"],
                user_agent="WellNavigation/1.0 (iOS; store)",
            )
            page = context.new_page()
            run_flow(page, name)
            context.close()
        browser.close()
    print("ok", sorted(p.name for p in OUT.glob("*.png")))


if __name__ == "__main__":
    main()
