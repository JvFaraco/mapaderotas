"""The build: one HTML file, self-contained, with verified vendored assets."""

from __future__ import annotations

import json
import re

import pytest

from route_coverage.cli import main
from route_coverage.rendering.standalone import BundleError, render_standalone, verify_vendor_checksums


def test_demo_build_produces_a_self_contained_file(demo_map_html):
    assert demo_map_html.startswith("<!DOCTYPE html>")
    # No local asset reference may survive: the file must open from disk.
    assert 'href="./' not in demo_map_html
    assert 'src="./' not in demo_map_html
    assert "L.tileLayer" in demo_map_html  # the app code is inlined
    assert "Leaflet 1.9.4" in demo_map_html  # the vendored library is inlined
    # The fonts travel inside the file, so it opens offline with its typography.
    assert demo_map_html.count("data:font/woff2;base64,") == 2
    assert 'url("./' not in demo_map_html


def test_demo_payload_is_complete(demo_map_html):
    payload = re.search(r"/\*__PAYLOAD_START__\*/(.*?)/\*__PAYLOAD_END__\*/", demo_map_html, re.S)
    data = json.loads(payload.group(1).replace("<\\/", "</"))

    assert data["specialists"] and data["districts"] and data["cities"]
    assert all(record["geometry"] for record in data["districts"])
    assert {"specialists", "cities", "districts", "generated_at", "stats"} <= set(data)
    # The cascade must have produced both real outlines and approximations,
    # otherwise the demo is not exercising the fallback path at all.
    precisions = {record["location_precision"] for record in data["districts"]}
    assert {"voronoi"} & precisions
    assert precisions - {"voronoi", "city", "parent_district"}


def test_build_is_deterministic(tmp_path):
    def build(name):
        output = tmp_path / name
        assert main(["build", "--demo", "--out", str(output)]) == 0
        html = output.read_text(encoding="utf-8")
        return re.sub(r'"generated_at":"[^"]+"', "", html)

    assert build("a.html") == build("b.html")


def test_vendored_assets_match_their_checksums(project_root):
    verified = verify_vendor_checksums(project_root / "web")
    assert "vendor/leaflet/leaflet.js" in verified
    assert "vendor/fonts/inter-latin-wght.woff2" in verified
    assert "vendor/fonts/montserrat-latin-wght.woff2" in verified


def test_a_stylesheet_url_that_cannot_be_inlined_fails_the_build(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text(
        '<!DOCTYPE html><link rel="stylesheet" href="./a.css">/*__PAYLOAD_START__*/null/*__PAYLOAD_END__*/',
        encoding="utf-8",
    )
    (web / "a.css").write_text('body { background: url("./missing.png"); }', encoding="utf-8")
    with pytest.raises(BundleError, match="not found: missing.png"):
        render_standalone({}, web, tmp_path / "out.html")


def test_a_tampered_vendor_file_fails_the_build(tmp_path, project_root):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!DOCTYPE html>/*__PAYLOAD_START__*/null/*__PAYLOAD_END__*/", encoding="utf-8")
    vendor = web / "vendor" / "lib"
    vendor.mkdir(parents=True)
    (vendor / "lib.js").write_text("console.log('tampered');", encoding="utf-8")
    (vendor / "CHECKSUMS.txt").write_text(f"{'0' * 64}  lib.js\n", encoding="utf-8")

    with pytest.raises(BundleError, match="checksum mismatch"):
        render_standalone({}, web, tmp_path / "out.html")


def test_missing_payload_markers_fail_the_build(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!DOCTYPE html><body></body>", encoding="utf-8")
    with pytest.raises(BundleError, match="payload markers"):
        render_standalone({}, web, tmp_path / "out.html")


def test_build_without_a_cache_exits_with_an_error(tmp_path, capsys):
    assert main(["build", "--cache", str(tmp_path / "missing.json"), "--out", str(tmp_path / "out.html")]) == 1
    assert "cache not found" in capsys.readouterr().out


def test_stats_json_is_written(tmp_path):
    stats_path = tmp_path / "stats.json"
    assert main(["demo", "--out", str(tmp_path / "map.html"), "--stats-json", str(stats_path)]) == 0
    stats = json.loads(stats_path.read_text())
    assert stats["districts"] > 0
    assert 0.0 <= stats["cache_hit_rate"] <= 1.0
