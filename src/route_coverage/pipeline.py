"""Orchestration: inputs + providers + cache → a complete coverage cache.

Everything here is provider-agnostic; swapping Nominatim/IBGE for the
synthetic providers is what makes the demo and the tests run offline.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .cache import GeocodeCache, refresh_metadata
from .geocoding.base import BoundaryProvider, GeocodingProvider
from .geocoding.cascade import BBoxCache, locate_district, locate_district_variants, locate_specialist
from .geometry.neighborhoods import match_district
from .geometry.shapes import safe_shape
from .geometry.voronoi import fill_missing_polygons
from .models import CoverageStats, GeocodeResult, GeoJSON, RouteArea, Specialist
from .normalization import name_variants
from .privacy import block_jitter

Logger = Callable[[str], None]


def _silent(message: str) -> None:  # pragma: no cover - default logger
    pass


@dataclass
class RunReport:
    """Per-district audit trail: which source produced each polygon."""

    rows: list[dict[str, Any]] = field(default_factory=list)

    def add(self, key: str, method: str, matched_name: str = "", score: Any = "", near_misses: str = "") -> None:
        self.rows.append(
            {
                "district_key": key,
                "method": method,
                "matched_name": matched_name,
                "score": score,
                "near_misses": near_misses,
            }
        )

    def resolve_pending(self, districts: dict[str, dict[str, Any]]) -> None:
        """Turn ``pending_voronoi`` rows into their final outcome."""
        for row in self.rows:
            if row["method"] != "pending_voronoi":
                continue
            entry = districts.get(row["district_key"], {})
            row["method"] = (entry.get("method") or "voronoi") if entry.get("geometry") else "no_polygon"

    def to_csv(self, path: Any) -> None:
        import csv
        from pathlib import Path

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["district_key", "method", "matched_name", "score", "near_misses"]
            )
            writer.writeheader()
            writer.writerows(self.rows)


def route_labels(areas: list[RouteArea]) -> dict[str, str]:
    """Public label per specialist: the route(s) they cover, never their identity."""
    routes: dict[str, set[str]] = {}
    for area in areas:
        if area.specialist_id and area.route_id:
            routes.setdefault(area.specialist_id, set()).add(area.route_id)
    return {specialist: " / ".join(sorted(labels)) for specialist, labels in routes.items()}


def _area_metadata(area: RouteArea) -> dict[str, str]:
    return {"region": area.region, "route_id": area.route_id, "specialist_id": area.specialist_id}


def geocode_specialists(
    cache: GeocodeCache,
    specialists: list[Specialist],
    labels: dict[str, str],
    provider: GeocodingProvider,
    boxes: BBoxCache,
    stats: CoverageStats,
    log: Logger = _silent,
) -> None:
    """Resolve each specialist's home base once, then keep only its metadata.

    Street and number never reach the cache: the map only ever needs
    district/city/state plus a jittered coordinate.
    """
    for specialist in specialists:
        key = specialist.specialist_id
        cached = cache.specialists.get(key)
        if cached:
            stats.cache_hits += 1
            if refresh_metadata(cached, {"route_id": labels.get(key, ""), "region": specialist.region}):
                log(f"[updated] specialist {key}: route metadata refreshed")
            continue

        result = locate_specialist(provider, specialist, boxes)
        if result is None:
            log(f"[failed]  specialist {key}: not found, not even at city level")
            continue
        if result.precision in ("address", "street"):
            # An exact address is never published. The point is displaced by
            # ~60-120 m and relabelled, so the map does not claim a precision
            # the jitter has already removed.
            result.latitude, result.longitude = block_jitter(key, result.latitude, result.longitude)
            result.precision = "block"
        cache.specialists[key] = {
            "specialist_id": key,
            "region": specialist.region,
            "district": specialist.district,
            "city": specialist.city,
            "state": specialist.state,
            "route_id": labels.get(key, ""),
            "latitude": round(result.latitude, 6),
            "longitude": round(result.longitude, 6),
            "location_precision": result.precision,
        }
        stats.geocoded += 1
        log(f"[ok]      specialist {key}: {result.precision}")
        cache.save()


def geocode_cities(
    cache: GeocodeCache,
    areas: list[RouteArea],
    boundaries: BoundaryProvider,
    stats: CoverageStats,
    log: Logger = _silent,
) -> None:
    """Whole-city coverage: the official municipal boundary, fetched once."""
    for area in areas:
        key = area.city_key
        cached = cache.cities.get(key)
        if cached:
            stats.cache_hits += 1
            if refresh_metadata(cached, _area_metadata(area)):
                log(f"[updated] city {key}: metadata refreshed from the routes file")
            continue

        geometry = boundaries.municipality_geometry(area.city, area.state)
        if not geometry:
            log(f"[failed]  city {key}: no official mesh available")
            continue
        cache.cities[key] = {
            "city": area.city,
            "state": area.state,
            "geometry": geometry,
            "location_precision": "city",
            "method": "ibge_mesh",
            **_area_metadata(area),
        }
        stats.geocoded += 1
        stats.real_geometries += 1
        log(f"[ok]      city {key}: official boundary")
        cache.save()


def geocode_districts(
    cache: GeocodeCache,
    areas: list[RouteArea],
    provider: GeocodingProvider,
    boundaries: BoundaryProvider,
    boxes: BBoxCache,
    stats: CoverageStats,
    report: RunReport,
    log: Logger = _silent,
    reprocess_without_polygon: bool = True,
) -> None:
    """District cascade: OSM → name variants → IBGE census → (later) Voronoi.

    Entries already cached *with* a polygon are never re-geocoded; entries
    cached without one are retried against the newer sources only, since
    Nominatim already failed for them once.
    """
    for area in areas:
        key = area.district_key
        cached = cache.districts.get(key)
        metadata = _area_metadata(area)

        if cached and cached.get("geometry"):
            stats.cache_hits += 1
            if refresh_metadata(cached, metadata):
                log(f"[updated] district {key}: metadata refreshed from the routes file")
            report.add(key, cached.get("method", "cache"))
            continue

        base: dict[str, Any] = dict(cached or {})
        if cached and not reprocess_without_polygon:
            refresh_metadata(cached, metadata)
            report.add(key, "cache_without_polygon")
            continue

        result = None
        if not cached:
            result = locate_district(provider, area.district, area.city, area.state, boxes)
            if result and result.geometry:
                _store_district(cache, key, area, base, result, stats)
                report.add(key, result.method, result.matched_name)
                log(f"[ok]      district {key}: OSM polygon")
                cache.save()
                continue
            if result:
                base.update({"latitude": result.latitude, "longitude": result.longitude})

        variant = locate_district_variants(provider, area.district, area.city, area.state, name_variants(area.district))
        if variant:
            _store_district(cache, key, area, base, variant, stats)
            report.add(key, "osm_variant", variant.matched_name)
            log(f"[ok]      district {key}: polygon via name variant '{variant.matched_name}'")
            cache.save()
            continue

        official = _match_official_district(area, boundaries)
        if official is not None:
            record, score = official
            centroid = safe_shape(record["geometry"])
            latitude = base.get("latitude")
            longitude = base.get("longitude")
            if latitude is None and centroid is not None and not centroid.is_empty:
                latitude, longitude = centroid.centroid.y, centroid.centroid.x
            cache.districts[key] = {
                **base,
                "district": area.district,
                "city": area.city,
                "state": area.state,
                "latitude": latitude,
                "longitude": longitude,
                "geometry": record["geometry"],
                "location_precision": "ibge_district",
                "method": "ibge_2022",
                "matched_name": record.get("name", ""),
                "score": score,
                **metadata,
            }
            stats.geocoded += 1
            stats.real_geometries += 1
            report.add(key, "ibge_2022", record.get("name", ""), score)
            log(f"[ok]      district {key}: IBGE 2022 polygon ('{record.get('name', '')}', score {score})")
            cache.save()
            continue

        if result or base.get("latitude") is not None:
            if result:
                _store_district(cache, key, area, base, result, stats, keep_geometry=False)
            else:
                cache.districts[key] = {**base, **metadata}
            report.add(key, "pending_voronoi")
            log(f"[ok]      district {key}: point only (Voronoi will close the gap)")
        else:
            fallback = _city_boundary(cache, area, boundaries)
            if fallback:
                cache.districts[key] = {
                    **base,
                    "district": area.district,
                    "city": area.city,
                    "state": area.state,
                    "geometry": fallback,
                    "location_precision": "city",
                    "method": "municipality",
                    **metadata,
                }
                stats.fallback_geometries += 1
                report.add(key, "municipality")
                log(f"[ok]      district {key}: municipal boundary (last resort)")
            else:
                report.add(key, "failed")
                log(f"[failed]  district {key}: not found at any level")
        cache.save()


def _store_district(
    cache: GeocodeCache,
    key: str,
    area: RouteArea,
    base: dict[str, Any],
    result: GeocodeResult,
    stats: CoverageStats,
    keep_geometry: bool = True,
) -> None:
    geometry = result.geometry if keep_geometry else None
    cache.districts[key] = {
        **base,
        "district": area.district,
        "city": area.city,
        "state": area.state,
        "latitude": round(result.latitude, 6),
        "longitude": round(result.longitude, 6),
        "geometry": geometry,
        "location_precision": result.precision,
        "method": result.method,
        "matched_name": result.matched_name,
        **_area_metadata(area),
    }
    stats.geocoded += 1
    if geometry:
        stats.real_geometries += 1


def _match_official_district(area: RouteArea, boundaries: BoundaryProvider) -> tuple[dict[str, Any], float] | None:
    records = boundaries.district_geometries(area.state)
    if not records:
        return None
    code = boundaries.municipality_code(area.city, area.state)
    record, score, _ = match_district(area.district, records, code)
    return (record, score) if record else None


def _city_boundary(cache: GeocodeCache, area: RouteArea, boundaries: BoundaryProvider) -> GeoJSON | None:
    """Municipal boundary, reusing the cache before asking IBGE again."""
    key = area.city_key
    cached = cache.cities.get(key)
    if cached and cached.get("geometry"):
        return cached["geometry"]
    if key in cache.support_cities:
        return cache.support_cities[key]
    geometry = boundaries.municipality_geometry(area.city, area.state)
    # Stored apart from `cities` so a boundary fetched only to clip Voronoi
    # cells never shows up on the map as covered territory.
    cache.support_cities[key] = geometry
    return geometry


def fill_voronoi(
    cache: GeocodeCache,
    boundaries: BoundaryProvider,
    stats: CoverageStats,
    log: Logger = _silent,
) -> None:
    """Close the last gaps: every district point without a polygon gets a cell."""
    pending = sorted(
        {
            (entry.get("city", ""), entry.get("state", ""))
            for entry in cache.districts.values()
            if not entry.get("geometry") and entry.get("latitude") is not None
        }
    )
    for city, state in pending:
        area = RouteArea(route_id="", city=city, state=state)
        boundary = _city_boundary(cache, area, boundaries)
        filled = fill_missing_polygons(cache.districts, city, state, boundary)
        if filled:
            stats.fallback_geometries += filled
            log(f"[voronoi] {city}|{state}: {filled} district(s) got an approximate area")
        cache.save()


def run_geocoding(
    cache: GeocodeCache,
    specialists: list[Specialist],
    areas: list[RouteArea],
    provider: GeocodingProvider,
    boundaries: BoundaryProvider,
    log: Logger = _silent,
) -> tuple[CoverageStats, RunReport]:
    """Full geocoding run: specialists, whole cities, districts, Voronoi."""
    stats = CoverageStats()
    report = RunReport()
    boxes = BBoxCache(provider)
    labels = route_labels(areas)

    city_areas = [area for area in areas if area.is_whole_city]
    district_areas = [area for area in areas if not area.is_whole_city]

    geocode_specialists(cache, specialists, labels, provider, boxes, stats, log)
    geocode_cities(cache, city_areas, boundaries, stats, log)
    geocode_districts(cache, district_areas, provider, boundaries, boxes, stats, report, log)
    fill_voronoi(cache, boundaries, stats, log)
    report.resolve_pending(cache.districts)

    stats.specialists = len(cache.specialists)
    stats.cities = len(cache.cities)
    stats.districts = len(cache.districts)
    cache.save(force=True)
    return stats, report
