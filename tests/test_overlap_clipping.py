"""Overlap clipping: after a build, two specialists never share a pixel."""

from __future__ import annotations

import json
import re

from shapely.geometry import box, mapping

from route_coverage.geometry.overlaps import clip_overlaps
from route_coverage.geometry.shapes import to_shape


def record(specialist, geometry, precision="district", city="Porto Aurora"):
    return {
        "specialist_id": specialist,
        "city": city,
        "state": "SC",
        "geometry": mapping(geometry),
        "location_precision": precision,
        "route_id": f"ROUTE-{specialist}",
    }


def total_pairwise_intersection(records):
    """Area shared by polygons of *different* specialists."""
    shapes = [(r.get("specialist_id", ""), to_shape(r["geometry"])) for r in records if r.get("geometry")]
    area = 0.0
    for i, (owner_a, shape_a) in enumerate(shapes):
        for owner_b, shape_b in shapes[i + 1 :]:
            if owner_a != owner_b:
                area += shape_a.intersection(shape_b).area
    return area


def test_overlapping_polygons_of_different_specialists_are_clipped():
    districts = [
        record("TECH-001", box(0, 0, 2, 2)),
        record("TECH-002", box(1, 0, 3, 2), precision="voronoi"),
    ]
    assert total_pairwise_intersection(districts) > 0

    stats = clip_overlaps(districts, [])

    assert stats["clipped"] == 1
    assert total_pairwise_intersection(districts) == 0
    # The real outline keeps its full area; the approximation gives way.
    assert to_shape(districts[0]["geometry"]).area == 4.0


def test_a_polygon_fully_covered_by_another_owner_is_dropped():
    districts = [
        record("TECH-001", box(0, 0, 4, 4)),
        record("TECH-002", box(1, 1, 2, 2), precision="voronoi"),
    ]
    stats = clip_overlaps(districts, [])
    assert stats["fully_covered"] == 1
    assert districts[1]["geometry"] is None


def test_the_same_polygon_repeated_for_one_specialist_is_drawn_once():
    """N districts resolved to the same parent outline must not stack."""
    districts = [record("TECH-001", box(0, 0, 2, 2)), record("TECH-001", box(0, 0, 2, 2))]
    stats = clip_overlaps(districts, [])
    assert stats["duplicates"] == 1
    assert [bool(r["geometry"]) for r in districts] == [True, False]


def test_a_smaller_polygon_wins_over_its_container_on_a_tie():
    districts = [
        record("TECH-001", box(0, 0, 10, 10)),  # parent district
        record("TECH-002", box(1, 1, 3, 3)),  # smaller district inside it
    ]
    clip_overlaps(districts, [])
    assert to_shape(districts[1]["geometry"]).area == 4.0
    assert to_shape(districts[0]["geometry"]).area == 100.0 - 4.0


def test_city_polygons_lose_the_area_already_painted_by_districts():
    districts = [record("TECH-001", box(0, 0, 2, 2))]
    cities = [
        {"city": "Porto Aurora", "state": "SC", "geometry": mapping(box(0, 0, 4, 4)), "specialist_id": "TECH-003"}
    ]
    stats = clip_overlaps(districts, cities)
    assert stats["cities_adjusted"] == 1
    assert to_shape(cities[0]["geometry"]).area == 16.0 - 4.0


def test_polygons_in_different_cities_are_never_compared():
    districts = [
        record("TECH-001", box(0, 0, 2, 2), city="Porto Aurora"),
        record("TECH-002", box(0, 0, 2, 2), city="Nova Bracara"),
    ]
    stats = clip_overlaps(districts, [])
    assert stats == {"clipped": 0, "duplicates": 0, "fully_covered": 0, "cities_adjusted": 0}


def test_built_demo_map_has_no_overlap_between_specialists(demo_map_html):
    payload = re.search(r"/\*__PAYLOAD_START__\*/(.*?)/\*__PAYLOAD_END__\*/", demo_map_html, re.S)
    data = json.loads(payload.group(1).replace("<\\/", "</"))

    by_city: dict[tuple[str, str], list[dict]] = {}
    for item in data["districts"]:
        by_city.setdefault((item["city"], item["state"]), []).append(item)

    for records in by_city.values():
        overlap = total_pairwise_intersection(records)
        covered = sum(to_shape(item["geometry"]).area for item in records)
        # Coordinates are rounded to ~1 m *after* clipping, which can leave
        # sub-metre slivers along a shared border; anything above that would
        # be a real overlap and would show up as a colour smear on the map.
        assert overlap / covered < 1e-6
