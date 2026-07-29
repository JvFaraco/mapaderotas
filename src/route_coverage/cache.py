"""Geocoding cache: crash-safe persistence plus canonical-key deduplication.

Geocoding is slow (Nominatim caps anonymous clients at ~1 req/s) and the
providers are free public services, so a resolved place must never be asked
for twice. The cache is the pipeline's memory: metadata coming from the input
files is refreshed on every run, geometry is not.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .models import CoverageStats
from .normalization import normalize_place, normalize_state

CACHE_VERSION = 2
SECTIONS = ("specialists", "cities", "districts", "support_cities")

#: How trustworthy each precision level is, used to pick a winner when two
#: cache entries collapse onto the same canonical key.
PRECISION_RANK: dict[str, int] = {
    "address": 6,
    "block": 6,
    "street": 6,
    "district": 6,
    "ibge_district": 6,
    "osm_variant": 5,
    "district_area": 4,
    "parent_district": 3,
    "voronoi": 2,
    "city": 1,
}


def empty_cache() -> dict[str, Any]:
    return {"version": CACHE_VERSION, **{section: {} for section in SECTIONS}}


class GeocodeCache:
    """A JSON document on disk, written atomically and throttled."""

    def __init__(self, path: Path, data: dict[str, Any] | None = None, save_interval: float = 5.0) -> None:
        self.path = Path(path)
        self.data = data if data is not None else empty_cache()
        for section in SECTIONS:
            self.data.setdefault(section, {})
        self.save_interval = save_interval
        self._last_save = 0.0
        self._backup_done = False

    # ── persistence ────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path, **kwargs: Any) -> GeocodeCache:
        """Load the cache, falling back to the ``.bak`` copy if it is corrupt.

        A cache truncated by an interrupted write (Ctrl+C, power loss) used to
        mean re-geocoding everything from scratch; the backup avoids that.
        """
        path = Path(path)
        if not path.exists():
            return cls(path, empty_cache(), **kwargs)
        try:
            return cls(path, json.loads(path.read_text(encoding="utf-8")), **kwargs)
        except json.JSONDecodeError as exc:
            backup = path.with_suffix(path.suffix + ".bak")
            if backup.exists():
                return cls(path, json.loads(backup.read_text(encoding="utf-8")), **kwargs)
            raise SystemExit(
                f"{path} is corrupt ({exc}) and no {backup.name} is available. "
                "Restore a valid copy or delete the file to geocode from scratch."
            ) from exc

    def save(self, force: bool = False) -> bool:
        """Write the cache atomically. Returns whether it actually wrote.

        Throttled to one write every ``save_interval`` seconds: the file grows
        to megabytes and rewriting it per item was both slow and a wider window
        for corruption. ``force=True`` bypasses the throttle.
        """
        now = time.time()
        if not force and now - self._last_save < self.save_interval:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        if not self._backup_done and self.path.exists():
            with contextlib.suppress(OSError):
                os.replace(self.path, self.path.with_suffix(self.path.suffix + ".bak"))
            self._backup_done = True
        os.replace(tmp, self.path)
        self._last_save = now
        return True

    # ── sections ───────────────────────────────────────────────────────────

    @property
    def specialists(self) -> dict[str, Any]:
        return self.data["specialists"]

    @property
    def cities(self) -> dict[str, Any]:
        return self.data["cities"]

    @property
    def districts(self) -> dict[str, Any]:
        return self.data["districts"]

    @property
    def support_cities(self) -> dict[str, Any]:
        """Municipal boundaries fetched only to clip Voronoi cells.

        Kept apart from ``cities`` so they are never rendered as coverage.
        """
        return self.data["support_cities"]


def refresh_metadata(entry: dict[str, Any], metadata: dict[str, Any]) -> bool:
    """Update input-derived fields on a cached entry. Returns whether it changed.

    Geometry costs a network round trip and never changes; route ownership
    changes in the spreadsheet all the time. Only the latter is refreshed.
    """
    changed = any(entry.get(key) != value for key, value in metadata.items())
    if changed:
        entry.update(metadata)
    return changed


def _geometry_rank(entry: dict[str, Any]) -> tuple[int, int]:
    return (1 if entry.get("geometry") else 0, PRECISION_RANK.get(entry.get("location_precision", ""), 0))


def deduplicate(cache: GeocodeCache) -> CoverageStats:
    """Merge entries that differ only by casing/accents into one canonical key.

    Before names were normalized, ``"Campinas"`` and ``"CAMPINAS"`` became two
    entries for one place, each with its own polygon — drawn on top of each
    other with semi-transparent fills, which reads as a colour smear on the
    map. Merging keeps the best geometry and drops the rest; no re-geocoding.
    """
    stats = CoverageStats()

    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in cache.cities.values():
        key = f"{normalize_place(entry.get('city'))}|{normalize_state(entry.get('state'))}"
        grouped.setdefault(key, []).append(entry)
    merged_cities: dict[str, Any] = {}
    for key, entries in grouped.items():
        entries.sort(key=_geometry_rank, reverse=True)
        winner = dict(entries[0])
        winner["city"] = normalize_place(winner.get("city"))
        winner["state"] = normalize_state(winner.get("state"))
        merged_cities[key] = winner
        stats.duplicates_removed += len(entries) - 1
    cache.data["cities"] = merged_cities

    grouped = {}
    for entry in cache.districts.values():
        key = (
            f"{normalize_place(entry.get('district'))}|"
            f"{normalize_place(entry.get('city'))}|{normalize_state(entry.get('state'))}"
        )
        grouped.setdefault(key, []).append(entry)
    merged_districts: dict[str, Any] = {}
    for key, entries in grouped.items():
        entries.sort(key=_geometry_rank, reverse=True)
        winner = dict(entries[0])
        winner["district"] = normalize_place(winner.get("district"))
        winner["city"] = normalize_place(winner.get("city"))
        winner["state"] = normalize_state(winner.get("state"))
        merged_districts[key] = winner
        stats.duplicates_removed += len(entries) - 1
    cache.data["districts"] = merged_districts

    stats.cities = len(merged_cities)
    stats.districts = len(merged_districts)
    stats.specialists = len(cache.specialists)
    return stats
