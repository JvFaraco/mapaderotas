"""The demo build: the whole pipeline, synthetic data, zero network calls.

``python -m route_coverage build --demo`` runs the real cascade — OSM-style
hits, official polygons, Voronoi fallback, overlap clipping — against the
fictional gazetteer in ``data/demo/``. It is what CI builds on every push and
what anyone without access to internal spreadsheets can run.
"""

from __future__ import annotations

from pathlib import Path

from .cache import GeocodeCache
from .config import Settings
from .geocoding.synthetic import (
    SyntheticBoundaryProvider,
    SyntheticGazetteer,
    SyntheticGeocodingProvider,
    load_cities,
)
from .inputs import read_routes, read_specialists
from .models import CoverageStats, RouteArea, Specialist
from .pipeline import Logger, RunReport, _silent, run_geocoding


def demo_paths(settings: Settings) -> dict[str, Path]:
    demo = settings.demo_dir
    return {
        "routes": demo / "routes.csv",
        "specialists": demo / "specialists.csv",
        "places": demo / "places.json",
    }


def build_gazetteer(places: Path, areas: list[RouteArea], specialists: list[Specialist]) -> SyntheticGazetteer:
    """Index the fictional cities and every district the inputs mention."""
    gazetteer = SyntheticGazetteer(load_cities(places))
    gazetteer.register_districts((area.district, area.city, area.state) for area in areas if area.district)
    gazetteer.register_districts(
        (specialist.district, specialist.city, specialist.state) for specialist in specialists if specialist.district
    )
    return gazetteer


def run_demo_geocoding(settings: Settings, log: Logger = _silent) -> tuple[GeocodeCache, CoverageStats, RunReport]:
    """Geocode the demo dataset in memory, against the synthetic providers."""
    paths = demo_paths(settings)
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"demo dataset incomplete, missing: {', '.join(missing)}")

    areas = read_routes(paths["routes"])
    specialists = read_specialists(paths["specialists"])
    gazetteer = build_gazetteer(paths["places"], areas, specialists)

    # An in-memory cache: a demo run must not leave state behind, and it is
    # fast enough to redo from scratch every time.
    cache = GeocodeCache(settings.cache_path.parent / "demo_cache.json", save_interval=float("inf"))
    cache.save = lambda force=False: False  # type: ignore[method-assign]

    stats, report = run_geocoding(
        cache,
        specialists,
        areas,
        SyntheticGeocodingProvider(gazetteer),
        SyntheticBoundaryProvider(gazetteer),
        log,
    )
    return cache, stats, report
