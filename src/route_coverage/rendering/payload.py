"""Turning the cache into the exact JSON the map receives.

This is the privacy boundary: every record passes through an allowlist, the
result is checked for forbidden fields, and only then is it serialised.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..cache import GeocodeCache
from ..geometry.overlaps import clip_overlaps
from ..geometry.shapes import round_coordinates
from ..models import CoverageStats
from ..privacy import (
    CITY_FIELDS,
    DISTRICT_FIELDS,
    SPECIALIST_FIELDS,
    apply_allowlist,
    assert_payload_is_safe,
)


def build_payload(
    cache: GeocodeCache, clip: bool = True, coordinate_digits: int = 5
) -> tuple[dict[str, Any], CoverageStats]:
    """Build the published payload from a cache. Never mutates the cache."""
    specialists = [
        apply_allowlist(entry, SPECIALIST_FIELDS)
        for entry in cache.specialists.values()
        if entry.get("latitude") is not None
    ]
    cities = [apply_allowlist(entry, CITY_FIELDS) for entry in cache.cities.values() if entry.get("geometry")]
    districts = [
        apply_allowlist(entry, DISTRICT_FIELDS) for entry in cache.districts.values() if entry.get("geometry")
    ]

    stats = CoverageStats(
        specialists=len(specialists),
        cities=len(cities),
        districts=len(districts),
        real_geometries=sum(
            1
            for record in districts
            if record.get("location_precision") in ("district", "ibge_district", "osm_variant")
        ),
        fallback_geometries=sum(
            1 for record in districts if record.get("location_precision") in ("voronoi", "parent_district", "city")
        ),
    )

    if clip:
        clipping = clip_overlaps(districts, cities)
        stats.overlaps_clipped = clipping["clipped"]
        stats.duplicates_removed = clipping["duplicates"]
        stats.extra.update(
            {"fully_covered": clipping["fully_covered"], "cities_adjusted": clipping["cities_adjusted"]}
        )
        cities = [record for record in cities if record.get("geometry")]
        districts = [record for record in districts if record.get("geometry")]

    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "specialists": sorted(specialists, key=lambda record: record.get("specialist_id", "")),
        "cities": sorted(cities, key=lambda record: (record.get("state", ""), record.get("city", ""))),
        "districts": sorted(
            districts, key=lambda record: (record.get("state", ""), record.get("city", ""), record.get("district", ""))
        ),
        "stats": stats.as_dict(),
    }
    assert_payload_is_safe(payload)
    return round_coordinates(payload, coordinate_digits), stats
