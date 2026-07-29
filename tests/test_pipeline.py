"""Pipeline behaviour with mocked providers: the cascade and the cache contract."""

from __future__ import annotations

from route_coverage.cache import GeocodeCache
from route_coverage.geocoding.base import Place
from route_coverage.geocoding.cascade import BBoxCache, locate_specialist
from route_coverage.geometry.voronoi import fill_missing_polygons
from route_coverage.models import RouteArea, Specialist
from route_coverage.pipeline import route_labels, run_geocoding

SQUARE = {"type": "Polygon", "coordinates": [[[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]]]}


class ScriptedProvider:
    """Answers only the queries it was told about; records every call."""

    def __init__(self, answers: dict[str, Place]):
        self.answers = answers
        self.calls: list[str] = []

    def search(self, query, *, geometry=False, viewbox=None, bounded=False):
        self.calls.append(query)
        return self.answers.get(query)


def test_specialist_cascade_gives_up_detail_one_level_at_a_time():
    provider = ScriptedProvider({"Centro, Porto Aurora, SC, Brasil": Place(-27.9, -46.3)})
    specialist = Specialist("TECH-001", street="Rua A", number="10", district="Centro", city="Porto Aurora", state="SC")

    result = locate_specialist(provider, specialist, BBoxCache(provider))

    assert result.precision == "district"
    assert provider.calls[0].startswith("Rua A, 10,")  # most specific first
    assert provider.calls[1].startswith("Rua A, Centro,")


def test_explicit_coordinates_skip_the_geocoder_entirely():
    provider = ScriptedProvider({})
    specialist = Specialist("TECH-001", city="Porto Aurora", state="SC", latitude=-27.9, longitude=-46.3)

    result = locate_specialist(provider, specialist, BBoxCache(provider))

    assert (result.latitude, result.longitude, result.precision) == (-27.9, -46.3, "manual")
    assert provider.calls == []


def test_address_level_hits_are_jittered_and_relabelled(tmp_path):
    address = "Rua A, 10, Centro, Porto Aurora, SC, Brasil"
    provider = ScriptedProvider({address: Place(-27.9, -46.3)})

    class Boundaries:
        def municipality_code(self, city, state):
            return "1"

        def municipality_geometry(self, city, state):
            return SQUARE

        def district_geometries(self, state):
            return []

    cache = GeocodeCache(tmp_path / "cache.json", save_interval=0)
    specialist = Specialist("TECH-001", street="Rua A", number="10", district="Centro", city="Porto Aurora", state="SC")
    run_geocoding(cache, [specialist], [], provider, Boundaries())

    entry = cache.specialists["TECH-001"]
    assert entry["location_precision"] == "block"
    assert (entry["latitude"], entry["longitude"]) != (-27.9, -46.3)
    assert "street" not in entry and "number" not in entry


def test_second_run_reuses_the_cache_and_still_refreshes_route_metadata(settings, demo_inputs, providers):
    areas, specialists = demo_inputs
    provider, boundaries = providers
    cache = GeocodeCache(settings.cache_path, save_interval=0)

    first, _ = run_geocoding(cache, specialists, areas, provider, boundaries)
    calls_after_first = len(provider.calls)

    # A route changes hands in the spreadsheet; no geometry needs re-fetching.
    moved = [
        RouteArea(area.route_id, "TECH-007", area.region, area.city, area.state, area.district)
        if area.route_id == "ROUTE-SOUTH-01"
        else area
        for area in areas
    ]
    second, _ = run_geocoding(cache, specialists, moved, provider, boundaries)

    assert first.geocoded > 0
    assert second.geocoded == 0
    assert second.cache_hits > 0
    assert second.cache_hit_rate == 1.0
    assert len(provider.calls) == calls_after_first  # nothing re-geocoded
    assert cache.districts["Centro Historico|Porto Aurora|SC"]["specialist_id"] == "TECH-007"


def test_route_labels_join_multiple_routes_per_specialist():
    areas = [
        RouteArea("ROUTE-B", "TECH-001", city="A", state="SC"),
        RouteArea("ROUTE-A", "TECH-001", city="B", state="SC"),
        RouteArea("ROUTE-C", "", city="C", state="SC"),
    ]
    assert route_labels(areas) == {"TECH-001": "ROUTE-A / ROUTE-B"}


def test_voronoi_fills_every_point_without_a_polygon():
    districts = {
        "A": {"city": "X", "state": "SC", "latitude": -0.4, "longitude": -0.4, "geometry": None},
        "B": {"city": "X", "state": "SC", "latitude": 0.4, "longitude": 0.4, "geometry": None},
        "C": {"city": "X", "state": "SC", "latitude": 0.0, "longitude": 0.0, "geometry": SQUARE},
    }
    filled = fill_missing_polygons(districts, "X", "SC", SQUARE)

    assert filled == 2
    assert districts["A"]["location_precision"] == "voronoi"
    assert districts["C"]["geometry"] == SQUARE  # anchors keep their own outline


def test_voronoi_separates_districts_sharing_one_coordinate():
    """Identical points would collapse the diagram into a single cell."""
    districts = {
        "A": {"city": "X", "state": "SC", "latitude": 0.1, "longitude": 0.1, "geometry": None},
        "B": {"city": "X", "state": "SC", "latitude": 0.1, "longitude": 0.1, "geometry": None},
    }
    assert fill_missing_polygons(districts, "X", "SC", SQUARE) == 2
    assert districts["A"]["geometry"] != districts["B"]["geometry"]


def test_voronoi_without_a_municipal_boundary_does_nothing():
    districts = {"A": {"city": "X", "state": "SC", "latitude": 0.1, "longitude": 0.1, "geometry": None}}
    assert fill_missing_polygons(districts, "X", "SC", None) == 0
