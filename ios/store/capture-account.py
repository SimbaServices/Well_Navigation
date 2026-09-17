"""Account screenshots with the account card in view."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "screenshots"
EMAIL = "appreview@simba.services"
PASSWORD = Path(r"C:\Simba\wellnav-app-review-login.txt").read_text(encoding="utf-8")
PASSWORD = next(line.split("=", 1)[1] for line in PASSWORD.splitlines() if line.startswith("password="))


def login(page) -> None:
    page.goto("https://wellnav.simba.services/login", wait_until="domcontentloaded")
    page.locator('input[name="email"]').fill(EMAIL)
    page.locator('input[name="password"]').fill(PASSWORD)
    page.locator("button.primary").click()
    page.wait_for_selector("#q", timeout=30000)


def account_shot(page, dest: Path) -> None:
    page.goto("https://wellnav.simba.services/account", wait_until="domcontentloaded")
    page.wait_for_selector(".account-card", timeout=20000)
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
    page.wait_for_timeout(300)
    page.screenshot(path=dest, full_page=False)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = [
        ("iphone-6.9-04-account.png", {"width": 430, "height": 932}, 3),
        ("ipad-13-04-account.png", {"width": 1024, "height": 1366}, 2),
    ]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for name, viewport, dpr in sizes:
            context = browser.new_context(
                viewport=viewport,
                device_scale_factor=dpr,
                user_agent="WellNavigation/1.0 (iOS; store)",
            )
            page = context.new_page()
            login(page)
            account_shot(page, OUT / name)
            context.close()
        browser.close()
    print("ok")


if __name__ == "__main__":
    main()
