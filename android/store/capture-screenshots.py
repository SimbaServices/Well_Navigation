"""Capture Google Play screenshot sizes from the live website.

Play Console's asset picker wants an exact 9:16 (or 16:9) crop. Phone and
tablet shots here are portrait 9:16, 24-bit PNG, 1080-3840px on each side.
"""

from __future__ import annotations

import socket
import time
from pathlib import Path

from PIL import Image
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

# Viewport * device_scale_factor must be an exact 9:16 pixel size.
# width multiple of 9, height = width * 16 / 9.
SIZES = {
    "phone": {
        "viewport": {"width": 540, "height": 960},
        "device_scale_factor": 2,
        "pixels": (1080, 1920),
    },
    "tablet-7": {
        "viewport": {"width": 720, "height": 1280},
        "device_scale_factor": 2,
        "pixels": (1440, 2560),
    },
    "tablet-10": {
        "viewport": {"width": 900, "height": 1600},
        "device_scale_factor": 2,
        "pixels": (1800, 3200),
    },
}

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36 "
    "WellNavigation/1.0 (Android; store)"
)


def chromium_args() -> list[str]:
    args = ["--disable-ipv6"]
    try:
        socket.getaddrinfo(LIVE_HOST, 443)
    except OSError:
        args.append(f"--host-resolver-rules=MAP {LIVE_HOST} {LIVE_IP}")
    return args


def login(page) -> None:
    page.goto(f"https://{LIVE_HOST}/login", wait_until="domcontentloaded")
    page.locator('input[name="email"]').fill(EMAIL)
    page.locator('input[name="password"]').fill(PASSWORD)
    page.locator("button.primary").click()
    page.wait_for_selector("#q", timeout=30000)


def hide_account_extras(page) -> None:
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
        page.goto(FALLBACK_WELL, wait_until="domcontentloaded")
        page.wait_for_selector("tr.well-row", timeout=45000)
        if not pin_first_located():
            raise SystemExit("no well with coordinates to pin for the map screenshot")
    tab = page.locator("#tab-map")
    if tab.count() and tab.is_visible():
        tab.click()
    page.wait_for_selector(".leaflet-container", timeout=20000)
    page.wait_for_selector("a.route.apple", timeout=20000)
    page.wait_for_selector("a.route.google", timeout=20000)
    page.wait_for_timeout(3500)
    page.evaluate(
        """() => {
          ['#disposal-legend', '#disposal-status', '#pipeline-status', '#offline-status']
            .forEach((sel) => {
              const el = document.querySelector(sel);
              if (el) el.style.display = 'none';
            });
          const nav = document.getElementById('nav-links');
          if (nav) nav.scrollIntoView({ block: 'nearest', inline: 'nearest' });
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


def save_play_png(path: Path, pixels: tuple[int, int]) -> None:
    image = Image.open(path).convert("RGB")
    if image.size != pixels:
        image = image.resize(pixels, Image.Resampling.LANCZOS)
    image.save(path, format="PNG", optimize=True)


def run_flow(page, prefix: str, pixels: tuple[int, int]) -> None:
    login(page)
    if page.locator("#q").count() == 0:
        page.goto(f"https://{LIVE_HOST}/", wait_until="domcontentloaded")
    page.wait_for_selector("#q", timeout=30000)
    shot = OUT / f"{prefix}-01-search.png"
    write_screenshot(page, shot)
    save_play_png(shot, pixels)

    page.goto(RESULTS, wait_until="domcontentloaded")
    page.wait_for_selector("#results", timeout=45000)
    page.wait_for_function(
        "() => (document.getElementById('results')||{}).innerText.includes('OXY')",
        timeout=45000,
    )
    shot = OUT / f"{prefix}-02-results.png"
    write_screenshot(page, shot)
    save_play_png(shot, pixels)

    pin_well_with_routes(page)
    shot = OUT / f"{prefix}-03-map.png"
    write_screenshot(page, shot)
    save_play_png(shot, pixels)

    page.locator("#tab-search").click()
    page.locator("#account-nav a[href='/account']").click()
    page.wait_for_selector(".account-card", state="attached", timeout=20000)
    page.evaluate(
        """() => {
          const ws = document.querySelector('.workspace');
          if (ws) ws.setAttribute('data-pane', 'search');
        }"""
    )
    hide_account_extras(page)
    page.wait_for_timeout(300)
    shot = OUT / f"{prefix}-04-account.png"
    write_screenshot(page, shot)
    save_play_png(shot, pixels)


def assert_play_size(path: Path, pixels: tuple[int, int]) -> None:
    image = Image.open(path)
    width, height = image.size
    if image.mode != "RGB":
        raise SystemExit(f"{path.name}: mode {image.mode} (need 24-bit RGB PNG)")
    if (width, height) != pixels:
        raise SystemExit(f"{path.name}: {width}x{height} != {pixels[0]}x{pixels[1]}")
    if width * 16 != height * 9:
        raise SystemExit(f"{path.name}: {width}x{height} is not exact 9:16")
    print(f"  {path.name} {width}x{height}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=chromium_args(),
        )
        for name, opts in SIZES.items():
            context = browser.new_context(
                viewport=opts["viewport"],
                device_scale_factor=opts["device_scale_factor"],
                user_agent=USER_AGENT,
                color_scheme="dark",
                service_workers="block",
            )
            page = context.new_page()
            run_flow(page, name, opts["pixels"])
            context.close()
        browser.close()
    for name, opts in SIZES.items():
        for path in sorted(OUT.glob(f"{name}-*.png")):
            assert_play_size(path, opts["pixels"])
    print("ok", [path.name for path in sorted(OUT.glob("*.png"))])


if __name__ == "__main__":
    main()
