#!/usr/bin/env python3
"""Recapture the README images from the demo map.

Writes ``docs/assets/overview.png`` (1440×860 at 2×) and ``docs/assets/demo.gif``
(a short walk through the map). Needs Playwright and Pillow, which are not
project dependencies, plus network access for the basemap tiles:

    pip install playwright pillow && python -m playwright install chromium
    python tools/capture_readme_assets.py

The demo is served over local HTTP, as GitHub Pages serves it, so the
captures show the same basemap as the live demo.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import io
import sys
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

from PIL import Image
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from route_coverage.cli import main as cli_main  # noqa: E402

GIF_VIEWPORT = (1180, 700)
GIF_SIZE = (944, 560)
FRAME_MS = 1900

# Zooms nowhere: returns a pixel that really hits an area of the given route.
FIND_AREA = """(want) => {
  const area = state.shown.find((a) => a.route === want.route && a.approx === want.approx && a.kind === 'district');
  if (!area) return null;
  const path = area.layer.getLayers()[0].getElement();
  const b = path.getBoundingClientRect();
  for (let fy = 0.5; fy < 0.95; fy += 0.05) for (let fx = 0.5; fx < 0.95; fx += 0.05) {
    for (const [gx, gy] of [[fx, fy], [1 - fx, 1 - fy]]) {
      const x = b.left + b.width * gx, y = b.top + b.height * gy;
      if (document.elementFromPoint(x, y) === path) return { x, y };
    }
  }
  return null;
}"""

TILES_LOADED = "Array.from(document.querySelectorAll('.leaflet-tile')).every((tile) => tile.complete)"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        pass


@contextlib.contextmanager
def serve(directory: Path) -> Iterator[str]:
    handler = functools.partial(QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()


def settle(page: Page, wait_ms: int = 1100) -> None:
    """Lets motion finish and every visible tile arrive."""
    page.wait_for_timeout(wait_ms)
    page.wait_for_function(TILES_LOADED, timeout=20000)
    page.wait_for_timeout(250)


def hover_area(page: Page, route: str, approx: bool) -> dict | None:
    point = page.evaluate(FIND_AREA, {"route": route, "approx": approx})
    if point:
        page.mouse.move(point["x"], point["y"])
        page.wait_for_timeout(350)
    return point


def capture_overview(page: Page, url: str, target: Path) -> None:
    page.goto(url)
    page.wait_for_function("document.body.classList.contains('ready')")
    # The two southern states hold most of the demo; frame them.
    page.evaluate(
        "state.map.fitBounds(boundsOf(state.areas.filter((a) => ['SC', 'PR'].includes(a.rec.state))),"
        " { padding: [40, 40], animate: false })"
    )
    settle(page, 1500)
    hover_area(page, "ROUTE-SOUTH-01", approx=True)
    # 256 colours keep the README light: the tiles make a full-colour PNG ~3 MB.
    shot = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    shot.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG).save(
        target, optimize=True
    )


def capture_gif(page: Page, url: str, target: Path) -> None:
    frames: list[Image.Image] = []

    def frame() -> None:
        shot = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
        frames.append(shot.resize(GIF_SIZE, Image.Resampling.LANCZOS))

    page.goto(url)
    page.wait_for_function("document.body.classList.contains('ready')")
    settle(page, 1500)
    frame()                                                   # 1. the whole coverage

    page.locator(".route-item[data-route='ROUTE-SOUTH-01']").click()
    settle(page, 1600)
    frame()                                                   # 2. a route: others dim, detail card

    hover_area(page, "ROUTE-SOUTH-01", approx=True)
    frame()                                                   # 3. hover tip on an approximation
    point = page.evaluate(FIND_AREA, {"route": "ROUTE-SOUTH-01", "approx": True})
    if point:
        page.mouse.click(point["x"], point["y"])
        page.wait_for_timeout(700)
        frame()                                               # 4. why it is approximated

    page.keyboard.press("Escape")
    page.locator("#search").fill("centro")
    page.wait_for_timeout(700)
    frame()                                                   # 5. search
    page.locator("#search").press("Enter")
    settle(page, 1600)
    frame()                                                   # 6. zoomed to the matches

    page.locator("#search").fill("")
    page.locator("label.chip:has(input[data-region='SOUTH'])").click()
    page.evaluate("fitAll(false)")
    settle(page, 900)
    frame()                                                   # 7. a region switched off

    page.locator("label.chip:has(input[data-region='SOUTH'])").click()
    page.locator("#btnTheme").click()
    page.locator("#basePicker [data-base='streets']").click()
    settle(page, 900)
    frame()                                                   # 8. light theme, street basemap

    # A palette per frame (GIF allows local colour tables): one shared palette
    # cannot hold both themes. Octree keeps the few saturated route colours that
    # median cut merges into the tiles' browns, and the file comes out smaller.
    quantized = [
        image.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE) for image in frames
    ]
    quantized[0].save(target, save_all=True, append_images=quantized[1:], duration=FRAME_MS, loop=0, optimize=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "assets")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp)
        if cli_main(["build", "--demo", "--out", str(site / "index.html")]) != 0:
            return 1
        with serve(site) as url, sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            # The map follows the system theme on a first visit: pin it to dark.
            overview = browser.new_page(
                viewport={"width": 1440, "height": 860}, device_scale_factor=2, color_scheme="dark"
            )
            capture_overview(overview, url, args.out / "overview.png")
            walk = browser.new_page(viewport={"width": GIF_VIEWPORT[0], "height": GIF_VIEWPORT[1]}, color_scheme="dark")
            capture_gif(walk, url, args.out / "demo.gif")
            browser.close()
    for name in ("overview.png", "demo.gif"):
        path = args.out / name
        print(f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}  {path.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
