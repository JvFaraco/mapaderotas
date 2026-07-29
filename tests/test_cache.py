"""Cache behaviour: crash safety, metadata refresh and deduplication."""

from __future__ import annotations

import json

import pytest

from route_coverage.cache import GeocodeCache, deduplicate, empty_cache, refresh_metadata


def test_load_returns_empty_cache_when_absent(tmp_path):
    cache = GeocodeCache.load(tmp_path / "missing.json")
    assert cache.data == empty_cache()


def test_save_is_atomic_and_creates_a_backup(tmp_path):
    path = tmp_path / "cache.json"
    cache = GeocodeCache(path, save_interval=0)
    cache.cities["A|SC"] = {"city": "A", "state": "SC"}
    cache.save(force=True)
    assert json.loads(path.read_text())["cities"]["A|SC"]["city"] == "A"

    cache.cities["B|SC"] = {"city": "B", "state": "SC"}
    cache.save(force=True)
    assert (tmp_path / "cache.json.bak").exists()
    assert not (tmp_path / "cache.json.tmp").exists()


def test_corrupt_cache_falls_back_to_the_backup(tmp_path):
    path = tmp_path / "cache.json"
    backup = tmp_path / "cache.json.bak"
    backup.write_text(json.dumps({**empty_cache(), "cities": {"A|SC": {"city": "A", "state": "SC"}}}))
    path.write_text('{"cities": {"A|SC"')  # truncated by an interrupted write

    cache = GeocodeCache.load(path)
    assert "A|SC" in cache.cities


def test_corrupt_cache_without_backup_stops_the_run(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{not json")
    with pytest.raises(SystemExit):
        GeocodeCache.load(path)


def test_save_is_throttled(tmp_path):
    cache = GeocodeCache(tmp_path / "cache.json", save_interval=60)
    assert cache.save(force=True) is True
    assert cache.save() is False  # within the interval, skipped
    assert cache.save(force=True) is True


def test_refresh_metadata_only_touches_input_derived_fields():
    entry = {"route_id": "ROUTE-A", "geometry": {"type": "Polygon"}, "location_precision": "district"}
    assert refresh_metadata(entry, {"route_id": "ROUTE-A"}) is False
    assert refresh_metadata(entry, {"route_id": "ROUTE-B"}) is True
    assert entry["route_id"] == "ROUTE-B"
    assert entry["geometry"] == {"type": "Polygon"}  # geometry is never re-fetched


def test_deduplicate_merges_case_variants_and_keeps_the_best_geometry(tmp_path):
    cache = GeocodeCache(tmp_path / "cache.json")
    cache.data["cities"] = {
        "CAMPINAS|SP": {"city": "CAMPINAS", "state": "SP", "geometry": None},
        "Campinas|SP": {"city": "Campinas", "state": "sp", "geometry": {"type": "Polygon"}},
    }
    cache.data["districts"] = {
        "CENTRO|CAMPINAS|SP": {
            "district": "CENTRO",
            "city": "CAMPINAS",
            "state": "SP",
            "geometry": {"type": "Polygon"},
            "location_precision": "voronoi",
        },
        "Centro|Campinas|SP": {
            "district": "Centro",
            "city": "Campinas",
            "state": "SP",
            "geometry": {"type": "Polygon"},
            "location_precision": "ibge_district",
        },
    }

    stats = deduplicate(cache)

    assert list(cache.cities) == ["Campinas|SP"]
    assert cache.cities["Campinas|SP"]["geometry"] is not None
    assert list(cache.districts) == ["Centro|Campinas|SP"]
    # The official outline wins over the Voronoi approximation.
    assert cache.districts["Centro|Campinas|SP"]["location_precision"] == "ibge_district"
    assert stats.duplicates_removed == 2


def test_deduplicate_is_idempotent(tmp_path):
    cache = GeocodeCache(tmp_path / "cache.json")
    cache.data["cities"] = {"CAMPINAS|SP": {"city": "CAMPINAS", "state": "SP", "geometry": {"type": "Polygon"}}}
    deduplicate(cache)
    snapshot = json.dumps(cache.data, sort_keys=True)
    assert deduplicate(cache).duplicates_removed == 0
    assert json.dumps(cache.data, sort_keys=True) == snapshot
