"""Overlap clipping: one point on the map belongs to at most one specialist.

Polygons legitimately overlap after the cascade — a Voronoi cell spilling into
a neighbour's real outline, a large parent district swallowing smaller ones,
several districts sharing the same parent polygon, a whole-city area painted
under its own districts. Fills are semi-transparent, so every overlap reads as
a muddled colour. Clipping runs on the render payload only; the cache keeps
the untouched source geometries.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .shapes import safe_shape, to_geojson

#: Who wins a dispute: a real outline beats an approximation.
PRECISION_RANK: dict[str, int] = {
    "ibge_district": 5,
    "district": 5,
    "osm_variant": 5,
    "district_area": 4,
    "parent_district": 3,
    "voronoi": 2,
}
DEFAULT_RANK = 5


def _rank(record: dict[str, Any]) -> int:
    precision = record.get("location_precision")
    return DEFAULT_RANK if precision is None else PRECISION_RANK.get(precision, DEFAULT_RANK)


def clip_overlaps(districts: Iterable[dict[str, Any]], cities: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Clip district polygons against each other, then cities against districts.

    Returns counters for the build report. Records whose geometry is entirely
    covered by a higher-ranked one lose their geometry (``None``) instead of
    being drawn as an invisible sliver.
    """
    from shapely.ops import unary_union

    districts = [record for record in districts if record.get("geometry")]
    by_city: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in districts:
        by_city.setdefault((record.get("city", ""), record.get("state", "")), []).append(record)

    clipped = duplicates = covered = 0
    union_by_city: dict[tuple[str, str], Any] = {}

    for city_key, records in by_city.items():
        prepared = []
        for record in records:
            geometry = safe_shape(record.get("geometry"))
            if geometry is None or geometry.is_empty:
                continue
            prepared.append((record, geometry, _rank(record)))
        # Most precise decides first; on a tie the smaller polygon stays whole
        # and the container (a district that encloses smaller ones) gives up
        # the shared area.
        prepared.sort(key=lambda item: (-item[2], item[1].area))

        placed: list[tuple[dict[str, Any], Any]] = []
        for record, geometry, _ in prepared:
            owner = record.get("specialist_id")
            if any(other.get("specialist_id") == owner and shape.equals(geometry) for other, shape in placed):
                # The same polygon repeated for one specialist (N districts
                # resolved to the same parent outline): stacking it only
                # darkens the fill, so only the first occurrence is drawn.
                record["geometry"] = None
                duplicates += 1
                continue
            cutters = [
                shape
                for other, shape in placed
                if other.get("specialist_id") != owner and shape.intersects(geometry)
            ]
            if cutters:
                remainder = geometry.difference(unary_union(cutters))
                if remainder.is_empty:
                    record["geometry"] = None
                    covered += 1
                    continue
                record["geometry"] = to_geojson(remainder)
                geometry = remainder
                clipped += 1
            placed.append((record, geometry))

        if placed:
            union_by_city[city_key] = unary_union([shape for _, shape in placed])

    adjusted_cities = 0
    for record in cities:
        geometry = safe_shape(record.get("geometry"))
        if geometry is None:
            continue
        union = union_by_city.get((record.get("city", ""), record.get("state", "")))
        if union is None or not geometry.intersects(union):
            continue
        remainder = geometry.difference(union)
        record["geometry"] = None if remainder.is_empty else to_geojson(remainder)
        adjusted_cities += 1

    return {
        "clipped": clipped,
        "duplicates": duplicates,
        "fully_covered": covered,
        "cities_adjusted": adjusted_cities,
    }
