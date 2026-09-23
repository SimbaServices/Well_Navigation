"""Capture App Store screenshot sizes from the live website."""

from __future__ import annotations

import socket
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "screenshots"
EMAIL = "appreview@simba.services"
PASSWORD = Path(r"C:\Simba\wellnav-app-review-login.txt").read_text(encoding="utf-8")
PASSWORD = next(line.split("=", 1)[1] for line in PASSWORD.splitlines() if line.startswith("password="))

LIVE_HOST = "wellnav.simba.services"
LIVE_IP = "62.238.113.20"
RESULTS = (
    f"https://{LIVE_HOST}/search"
    "?offset=0&scope=wells&state=tx&mode=operator&q=OXY&sort=name&dir=asc"
)
FALLBACK_WELL = (
    f"https://{LIVE_HOST}/search"
    "?offset=0&scope=wells&state=tx&mode=name&q=UNIVERSITY+11&sort=name&dir=asc"
)

SIZES = {
    "iphone-6.9": {"viewport": {"width": 430, "height": 932}, "device_scale_factor": 3},
    "iphone-6.5": {"viewport": {"width": 428, "height": 926}, "device_scale_factor": 3},
    "ipad-13": {"viewport": {"width": 1024, "height": 1366}, "device_scale_factor": 2},
}


def chromium_args() -> list[str]:
    args = ["--disable-ipv6"]
    try:
        socket.getaddrinfo(LIVE_HOST, 443)
    except OSError:
        args.append(f"--host-resolver-rules=MAP {LIVE_HOST} {LIVE_IP}")
    return args


def open_page(page, url: str) -> None:
    last_error = None
    for _ in range(3):
        try:
            page.goto(url, wait_until="commit", timeout=60000)
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1.5)
    raise last_error


def login(page) -> None:
    open_page(page, f"https://{LIVE_HOST}/login")
    page.locator('input[name="email"]').fill(EMAIL)
    page.locator('input[name="password"]').fill(PASSWORD)
    with page.expect_navigation(timeout=30000):
        page.locator("form.auth-form").evaluate("form => form.submit()")
    page.wait_for_selector("#q", timeout=30000)


def wait_for_map_tiles(page) -> None:
    if page.locator(".leaflet-container").count() == 0:
        return
    page.wait_for_function(
        "() => document.querySelectorAll('.leaflet-tile-loaded').length > 4",
        timeout=25000,
    )
    page.wait_for_timeout(1200)


def pin_well_with_routes(page) -> None:
    """Pin a well that has coordinates so Apple Maps / Google Maps appear."""

    def pin_first_located() -> bool:
        return bool(
            page.evaluate(
                """() => {
                  const row = [...document.querySelectorAll('tr.well-row')].find(
                    (r) => r.dataset.lat && r.dataset.lon
                  );
                  const pin = row && row.querySelector('.map-toggle');
                  if (!pin) return false;
                  pin.click();
                  return true;
                }"""
            )
        )

    page.wait_for_selector("tr.well-row", timeout=45000)
    if not pin_first_located():
        open_page(page, FALLBACK_WELL)
        page.wait_for_selector("tr.well-row", timeout=45000)
        if not pin_first_located():
            raise SystemExit("no well with coordinates to pin for the map screenshot")
    tab = page.locator("#tab-map")
    if tab.count() and tab.is_visible():
        tab.click()
    page.wait_for_selector(".leaflet-container", timeout=20000)
    page.wait_for_selector("a.route.apple", timeout=20000)
    page.wait_for_selector("a.route.google", timeout=20000)
    wait_for_map_tiles(page)
    page.wait_for_timeout(1500)
    page.evaluate(
        """() => {
          ['#disposal-legend', '#disposal-status', '#pipeline-status', '#offline-status']
            .forEach((sel) => {
              const el = document.querySelector(sel);
              if (el) el.style.display = 'none';
            });
          document.querySelectorAll('.info-tip-pop').forEach((el) => {
            el.setAttribute('hidden', '');
          });
        }"""
    )
    page.wait_for_timeout(200)


def write_screenshot(page, path: Path) -> None:
    tmp = path.with_name(path.stem + ".tmp.png")
    page.screenshot(path=tmp, full_page=False)
    last_error = None
    for _ in range(6):
        try:
            if path.exists():
                path.unlink()
            tmp.replace(path)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.4)
    raise last_error


def hide_account_extras(page) -> None:
    page.evaluate(
        """() => {
          const ws = document.querySelector('.workspace');
          if (ws) ws.setAttribute('data-pane', 'search');
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


def run_flow(page, prefix: str) -> None:
    login(page)
    open_page(page, f"https://{LIVE_HOST}/")
    page.wait_for_selector("#q")
    page.wait_for_selector("text=Well Name", timeout=15000)
    wait_for_map_tiles(page)
    write_screenshot(page, OUT / f"{prefix}-01-search.png")

    open_page(page, RESULTS)
    page.wait_for_selector("#results", timeout=45000)
    page.wait_for_function(
        "() => (document.getElementById('results')||{}).innerText.includes('OXY')",
        timeout=45000,
    )
    write_screenshot(page, OUT / f"{prefix}-02-results.png")

    pin_well_with_routes(page)
    write_screenshot(page, OUT / f"{prefix}-03-map.png")

    open_page(page, f"https://{LIVE_HOST}/account")
    page.evaluate(
        """() => {
          const ws = document.querySelector('.workspace');
          if (ws) ws.setAttribute('data-pane', 'search');
        }"""
    )
    page.wait_for_selector("text=Delete account", state="attached", timeout=20000)
    hide_account_extras(page)
    page.wait_for_timeout(300)
    write_screenshot(page, OUT / f"{prefix}-04-account.png")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=chromium_args())
        for name, opts in SIZES.items():
            context = browser.new_context(
                viewport=opts["viewport"],
                device_scale_factor=opts["device_scale_factor"],
                user_agent="WellNavigation/1.0 (iOS; store)",
            )
            page = context.new_page()
            page.set_default_timeout(60000)
            run_flow(page, name)
            context.close()
        browser.close()
    print("ok", sorted(p.name for p in OUT.glob("iphone-*.png")) + sorted(p.name for p in OUT.glob("ipad-*.png")))


if __name__ == "__main__":
    main()
