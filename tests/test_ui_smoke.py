"""The map in a real browser: boot, hover, detail card, link to the view.

Skipped where Playwright is not installed; CI runs it in its own job. No test
touches the network: basemap tiles are answered with a blank PNG, so the page
behaves as it does online without depending on a tile server. Set
``PW_CHANNEL=msedge`` (or ``chrome``) to run against an installed browser
instead of Playwright's Chromium.
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from route_coverage.cli import main  # noqa: E402
from route_coverage.rendering.standalone import render_standalone  # noqa: E402

BLANK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


@pytest.fixture(scope="module")
def demo_map(tmp_path_factory) -> Path:
    output = tmp_path_factory.mktemp("ui") / "map.html"
    assert main(["build", "--demo", "--out", str(output)]) == 0
    return output


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        launched = playwright.chromium.launch(channel=os.environ.get("PW_CHANNEL") or None)
        yield launched
        launched.close()


@pytest.fixture
def open_map(browser):
    """Opens a map file and returns (page, console problems)."""
    contexts = []

    def _open(
        path: Path,
        *,
        width: int = 1440,
        height: int = 900,
        hash_: str = "",
        reduced_motion: bool = True,
        color_scheme: str = "dark",
    ):
        context = browser.new_context(
            viewport={"width": width, "height": height},
            reduced_motion="reduce" if reduced_motion else "no-preference",
            color_scheme=color_scheme,
        )
        contexts.append(context)
        context.route(
            re.compile(r"^https?://"),
            lambda route: route.fulfill(status=200, content_type="image/png", body=BLANK_PNG),
        )
        page = context.new_page()
        problems: list[str] = []
        page.on(
            "console",
            lambda message: problems.append(message.text) if message.type in ("error", "warning") else None,
        )
        page.on("pageerror", lambda error: problems.append(str(error)))
        page.goto(path.as_uri() + hash_)
        page.wait_for_function("document.body.classList.contains('ready')")
        return page, problems

    yield _open
    for context in contexts:
        context.close()


def payload_of(path: Path) -> dict:
    html = path.read_text(encoding="utf-8")
    raw = re.search(r"/\*__PAYLOAD_START__\*/(.*?)/\*__PAYLOAD_END__\*/", html, re.S).group(1)
    return json.loads(raw.replace("<\\/", "</"))


FIND_AREA = """(want) => {
  // Zooms to an area and returns a pixel that really hits its path.
  const area = state.shown.find((a) => a.kind === want.kind && a.approx === want.approx);
  state.map.setView(area.layer.getBounds().getCenter(), 12, { animate: false });
  const box = state.map.getContainer().getBoundingClientRect();
  const path = area.layer.getLayers()[0].getElement();
  const b = path.getBoundingClientRect();
  for (let fy = 0.5; fy < 0.95; fy += 0.05) for (let fx = 0.5; fx < 0.95; fx += 0.05) {
    for (const [gx, gy] of [[fx, fy], [1 - fx, 1 - fy]]) {
      const x = b.left + b.width * gx, y = b.top + b.height * gy;
      if (document.elementFromPoint(x, y) === path) return { x, y, name: area.name, route: area.route };
    }
  }
  return null;
}"""


def test_the_map_boots_clean_and_counts_everything(demo_map, open_map):
    page, problems = open_map(demo_map)
    data = payload_of(demo_map)
    routes = {record["route_id"] for record in data["cities"] + data["districts"]}

    assert page.locator("#kpiAreas").inner_text() == str(len(data["cities"]) + len(data["districts"]))
    assert page.locator("#routeList .route-item").count() == len(routes)
    assert page.locator("#boot").count() == 0 or page.locator("#boot.leaving").count() == 1
    # The vendored fonts are embedded and actually usable from a file:// page.
    assert page.evaluate("document.fonts.check('900 22px Montserrat') && document.fonts.check('600 14px Inter')")
    # Approximated areas are hatched, not flat.
    assert page.locator("path.area-approx[fill^='url(#hatch-']").count() > 0
    assert problems == []


def test_hover_shows_a_tip_and_click_opens_the_area(demo_map, open_map):
    page, problems = open_map(demo_map)
    target = page.evaluate(FIND_AREA, {"kind": "district", "approx": False})
    assert target, "no clickable pixel found on a real district outline"

    page.mouse.move(target["x"], target["y"])
    expect = sync_api.expect
    expect(page.locator("#tip.on")).to_contain_text(target["name"])

    page.mouse.click(target["x"], target["y"])
    expect(page.locator("#detail")).to_be_visible()
    expect(page.locator("#detail .detail-title")).to_have_text(target["name"])
    assert page.locator("path.area.is-selected").count() > 0
    page.wait_for_function("location.hash.includes('route=')")
    assert target["route"] in page.evaluate("decodeURIComponent(location.hash)")

    page.keyboard.press("Escape")
    expect(page.locator("#detail")).to_be_hidden()
    assert page.locator("path.area.is-selected").count() == 0
    assert problems == []


def test_an_approximated_area_says_so(demo_map, open_map):
    page, _ = open_map(demo_map)
    target = page.evaluate(FIND_AREA, {"kind": "district", "approx": True})
    assert target
    page.mouse.click(target["x"], target["y"])
    sync_api.expect(page.locator("#detail .detail-alert")).to_contain_text("Approximated area")


def test_the_link_reopens_the_same_view(demo_map, open_map):
    page, problems = open_map(demo_map)
    first = page.locator("#routeList .route-item").first
    route_id = first.get_attribute("data-route")
    first.click()
    sync_api.expect(page.locator("#detail .detail-title")).to_have_text(route_id)
    page.locator("#basePicker [data-base='streets']").click()
    page.locator("label.chip:has(input[data-layer='origins'])").click()
    page.wait_for_function("location.hash.includes('base=streets') && location.hash.includes('hide=origins')")
    hash_ = page.evaluate("location.hash")

    again, again_problems = open_map(demo_map, hash_=hash_)
    sync_api.expect(again.locator(f".route-item.active[data-route='{route_id}']")).to_have_count(1)
    sync_api.expect(again.locator("#basePicker [data-base='streets'].on")).to_have_count(1)
    assert not again.locator("input[data-layer='origins']").is_checked()
    assert again.locator("path.origin, .leaflet-interactive.origin").count() == 0
    assert problems == [] and again_problems == []


def test_search_with_no_match_offers_a_way_back(demo_map, open_map):
    page, problems = open_map(demo_map)
    page.locator("#search").fill("no such place")
    sync_api.expect(page.locator("#mapBanner")).to_be_visible()
    sync_api.expect(page.locator("#searchMsg.error")).to_be_visible()

    page.locator("#btnShowAll").click()
    sync_api.expect(page.locator("#mapBanner")).to_be_hidden()
    assert page.locator("#search").input_value() == ""
    total = page.locator("#kpiAreas").inner_text()
    page.locator("#search").fill("centro")
    sync_api.expect(page.locator("#searchMsg.ok")).to_contain_text("area")
    assert int(page.locator("#kpiAreas").inner_text()) < int(total)
    assert problems == []


def test_the_theme_choice_survives_a_reload(demo_map, open_map):
    page, _ = open_map(demo_map)
    page.locator("#btnTheme").click()
    assert page.evaluate("document.documentElement.dataset.theme") == "light"
    page.reload()
    page.wait_for_function("document.body.classList.contains('ready')")
    assert page.evaluate("document.documentElement.dataset.theme") == "light"


@pytest.mark.parametrize("system", ["light", "dark"])
def test_a_first_visit_follows_the_system_theme(demo_map, open_map, system):
    page, _ = open_map(demo_map, color_scheme=system)
    assert page.evaluate("document.documentElement.dataset.theme") == system
    # A choice made with the button beats the system from then on.
    page.locator("#btnTheme").click()
    page.reload()
    page.wait_for_function("document.body.classList.contains('ready')")
    assert page.evaluate("document.documentElement.dataset.theme") != system


def test_the_keyboard_goes_into_the_card_and_back(demo_map, open_map):
    page, problems = open_map(demo_map)
    item = page.locator("#routeList .route-item").first
    route_id = item.get_attribute("data-route")
    item.focus()
    page.keyboard.press("Enter")
    sync_api.expect(page.locator("#detail")).to_be_visible()
    assert page.evaluate("document.activeElement.id") == "detailTitle"
    assert page.locator("#detail").get_attribute("aria-labelledby") == "detailTitle"

    page.keyboard.press("Escape")
    sync_api.expect(page.locator("#detail")).to_be_hidden()
    assert page.evaluate("document.activeElement.dataset.route") == route_id
    assert problems == []


def test_decorative_motion_stops_by_itself(demo_map, open_map):
    """WCAG 2.2.2: nothing that starts on its own may move for over 5 s."""
    page, problems = open_map(demo_map, reduced_motion=False)
    page.wait_for_function("!document.getElementById('boot')")
    timings = page.evaluate(
        "document.getAnimations().map((a) => ({ name: a.animationName || 'waapi',"
        " end: a.effect.getComputedTiming().endTime }))"
    )
    assert timings, "the opening animations did not run"
    assert all(timing["end"] <= 5000 for timing in timings), timings
    assert problems == []


def test_hovering_a_route_in_the_list_lights_it_up(demo_map, open_map):
    page, problems = open_map(demo_map)
    item = page.locator("#routeList .route-item").nth(1)
    route_id = item.get_attribute("data-route")
    other_opacity = (
        "(r) => state.shown.find((a) => a.route !== r && !a.approx && a.kind === 'district')"
        ".layer.getLayers()[0].getElement().getAttribute('stroke-opacity')"
    )
    before = float(page.evaluate(other_opacity, route_id))
    item.hover()
    assert float(page.evaluate(other_opacity, route_id)) < before  # the other routes dim
    page.mouse.move(900, 450)  # back over the map
    page.wait_for_function(f"(() => {{ const f = {other_opacity}; return +f({route_id!r}) === {before}; }})()")
    assert page.locator("#detail").is_hidden()  # a preview is not a selection
    assert problems == []


def test_collapsing_the_panel_does_not_move_the_map(demo_map, open_map):
    page, problems = open_map(demo_map)
    position = "(() => { const r = state.shown[5].layer.getLayers()[0].getElement().getBoundingClientRect();" \
               " return [Math.round(r.left), Math.round(r.top)]; })()"
    before = page.evaluate(position)
    page.locator("#btnPanel").click()
    page.wait_for_function("getComputedStyle(document.getElementById('panel')).visibility === 'hidden'")
    assert page.evaluate("document.getElementById('map').offsetWidth") == 1440
    assert page.evaluate(position) == before
    page.locator("#btnPanel").click()
    page.wait_for_function("!document.getElementById('app').classList.contains('panel-floating')")
    assert page.evaluate(position) == before
    assert page.locator("#btnPanel").get_attribute("aria-expanded") == "true"
    assert problems == []


def test_phone_layout_fits_and_the_panel_is_a_drawer(demo_map, open_map):
    page, problems = open_map(demo_map, width=375, height=812)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    panel = page.locator("#panel")
    assert panel.bounding_box()["x"] + panel.bounding_box()["width"] <= 0  # off-screen until asked for

    page.locator("#btnPanel").click()
    page.wait_for_function("document.getElementById('panel').getBoundingClientRect().left >= 0")
    page.locator("#routeList .route-item").first.click()
    page.wait_for_function("document.getElementById('panel').getBoundingClientRect().right <= 0")
    sync_api.expect(page.locator("#detail")).to_be_visible()
    # iOS zooms into fields under 16 px.
    assert page.evaluate("getComputedStyle(document.getElementById('search')).fontSize") == "16px"
    # A tap on the dimmed map beside the open drawer closes it.
    page.locator("#btnPanel").click()
    page.wait_for_function("document.getElementById('panel').getBoundingClientRect().left >= 0")
    page.mouse.click(365, 400)
    page.wait_for_function("document.getElementById('panel').getBoundingClientRect().right <= 0")
    assert page.evaluate("getComputedStyle(document.getElementById('panel')).visibility") == "hidden"
    assert problems == []


def test_names_from_the_spreadsheet_are_shown_as_text(tmp_path, project_root, open_map):
    hostile = '<img src=x onerror="window.__pwned=1">'
    ring = [[-49.0, -27.0], [-48.9, -27.0], [-48.9, -26.9], [-49.0, -26.9], [-49.0, -27.0]]
    square = {"type": "Polygon", "coordinates": [ring]}
    payload = {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "specialists": [],
        "cities": [],
        "districts": [{
            "district": hostile, "city": hostile, "state": "SC", "geometry": square,
            "location_precision": "district", "region": hostile, "route_id": hostile, "specialist_id": "TECH-1",
        }],
        "stats": {},
    }
    output = render_standalone(payload, project_root / "web", tmp_path / "hostile.html")
    page, _ = open_map(output)
    page.locator("#routeList .route-item").first.click()
    sync_api.expect(page.locator("#detail .detail-title")).to_have_text(hostile)
    assert page.evaluate("window.__pwned") is None
    assert page.locator("img[src='x']").count() == 0
