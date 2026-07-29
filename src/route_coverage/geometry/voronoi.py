"""Voronoi fallback: an approximate area of influence for every district point.

When neither OSM nor IBGE has an outline, a district would be a lone dot on
the map. Instead each point takes the area closer to it than to any other
district point of the same city, clipped to the municipal boundary. The result
is labelled ``voronoi`` so the map can say it is an approximation.
"""

from __future__ import annotations

import math
from typing import Any

from ..models import GeoJSON
from .shapes import safe_shape, to_geojson

#: Golden angle — spreads duplicated points around a circle without collisions.
GOLDEN_ANGLE = 2.399963


def _diagram_points(entries: list[tuple[str, dict[str, Any]]]) -> dict[str, tuple[float, float]]:
    """Point per entry, nudging exact duplicates apart.

    Two districts can share a coordinate (the city-level fallback returns the
    same point for both). Identical inputs collapse a Voronoi diagram, so
    duplicates are displaced by a deterministic ~40–90 m. Only the point used
    to build the diagram moves; the stored coordinate is untouched.
    """
    seen: dict[tuple[float, float], int] = {}
    points: dict[str, tuple[float, float]] = {}
    for key, entry in entries:
        rounded = (round(entry["latitude"], 6), round(entry["longitude"], 6))
        repeat = seen.get(rounded, 0)
        seen[rounded] = repeat + 1
        if repeat == 0:
            points[key] = (entry["longitude"], entry["latitude"])
        else:
            angle = repeat * GOLDEN_ANGLE
            radius = 0.0004 + 0.0002 * repeat
            points[key] = (
                entry["longitude"] + radius * math.cos(angle),
                entry["latitude"] + radius * math.sin(angle),
            )
    return points


def fill_missing_polygons(
    districts: dict[str, dict[str, Any]],
    city: str,
    state: str,
    municipality: GeoJSON | None,
) -> int:
    """Give every polygon-less district of one city a clipped Voronoi cell.

    Districts that already have a polygon still take part in the diagram as
    anchors — otherwise the generated cells would spill over their neighbours'
    real territory — but keep their own geometry. Returns how many were filled.
    """
    from shapely.geometry import MultiPoint, Point
    from shapely.ops import voronoi_diagram

    in_city = [
        (key, entry)
        for key, entry in districts.items()
        if entry.get("city") == city and entry.get("state") == state and entry.get("latitude") is not None
    ]
    missing = [(key, entry) for key, entry in in_city if not entry.get("geometry")]
    if not missing:
        return 0

    boundary = safe_shape(municipality)
    if boundary is None or boundary.is_empty:
        return 0

    points = _diagram_points(in_city)
    try:
        diagram = voronoi_diagram(
            MultiPoint([Point(*points[key]) for key, _ in in_city]),
            envelope=boundary.envelope,
        )
    except Exception:
        return 0

    cells = list(diagram.geoms)
    filled = 0
    for key, entry in missing:
        point = Point(*points[key])
        cell = next((candidate for candidate in cells if candidate.contains(point)), None)
        if cell is None:
            cell = min(cells, key=lambda candidate: candidate.distance(point))
        clipped = cell.intersection(boundary)
        if clipped.is_empty:
            continue
        entry["geometry"] = to_geojson(clipped)
        entry["location_precision"] = "voronoi"
        entry["method"] = "voronoi"
        filled += 1
    return filled
