"""Deterministic offline providers used by the demo and by the tests.

They implement the same ports as Nominatim/IBGE and return plausible, entirely
fictional geography derived from a hash of the place name. That keeps
``build --demo`` reproducible, network-free and free of any real address,
while still exercising the real cascade: OSM-style hits, official polygons for
some districts, points only for the rest, and Voronoi to fill the gaps.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import GeoJSON
from ..normalization import match_key, normalize_place, normalize_state
from .base import BBox, Place

EARTH_KM_PER_DEGREE = 111.32


def _hash_unit(*parts: str) -> float:
    """A stable float in ``[0, 1)`` derived from the given strings."""
    digest = hashlib.sha256("|".join(parts).lower().encode("utf-8")).digest()
    return int.from_bytes(digest[:6], "big") / float(1 << 48)


def _offset(latitude: float, longitude: float, distance_km: float, bearing_deg: float) -> tuple[float, float]:
    d_lat = distance_km * math.cos(math.radians(bearing_deg)) / EARTH_KM_PER_DEGREE
    d_lng = distance_km * math.sin(math.radians(bearing_deg)) / (
        EARTH_KM_PER_DEGREE * math.cos(math.radians(latitude))
    )
    return latitude + d_lat, longitude + d_lng


def _ring(latitude: float, longitude: float, radius_km: float, sides: int, seed: str) -> list[list[float]]:
    """An irregular convex ring, so demo polygons do not look machine-perfect."""
    points: list[list[float]] = []
    for index in range(sides):
        bearing = 360.0 * index / sides
        wobble = 0.75 + 0.5 * _hash_unit(seed, str(index))
        lat, lng = _offset(latitude, longitude, radius_km * wobble, bearing)
        points.append([round(lng, 6), round(lat, 6)])
    points.append(points[0])
    return points


def _polygon(latitude: float, longitude: float, radius_km: float, sides: int, seed: str) -> GeoJSON:
    return {"type": "Polygon", "coordinates": [_ring(latitude, longitude, radius_km, sides, seed)]}


@dataclass
class SyntheticCity:
    city: str
    state: str
    latitude: float
    longitude: float
    radius_km: float
    code: str
    districts: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{match_key(self.city)}|{normalize_state(self.state)}"

    def geometry(self) -> GeoJSON:
        return _polygon(self.latitude, self.longitude, self.radius_km, 18, f"city:{self.key}")

    def bbox(self) -> BBox:
        span = self.radius_km / EARTH_KM_PER_DEGREE
        return ((self.latitude + span, self.longitude - span), (self.latitude - span, self.longitude + span))

    def district_point(self, district: str) -> tuple[float, float]:
        seed = f"{self.key}|{match_key(district)}"
        bearing = 360.0 * _hash_unit(seed, "bearing")
        distance = self.radius_km * (0.15 + 0.6 * _hash_unit(seed, "distance"))
        return _offset(self.latitude, self.longitude, distance, bearing)

    def has_district_geometry(self, district: str, source: str) -> bool:
        """Whether a given source knows this district's outline.

        Each source covers a different ~35% of districts, mirroring reality:
        OSM has outlines where mappers drew them, IBGE where the city formally
        delimited them, and neither covers everything — which is exactly why
        the Voronoi fallback and the overlap clipping exist.
        """
        return _hash_unit(self.key, match_key(district), source) < 0.35

    def district_geometry(self, district: str, source: str = "osm") -> GeoJSON | None:
        if not self.has_district_geometry(district, source):
            return None
        lat, lng = self.district_point(district)
        radius = self.radius_km * (0.12 + 0.10 * _hash_unit(self.key, district, "size"))
        return _polygon(lat, lng, radius, 9, f"district:{self.key}:{match_key(district)}:{source}")


def load_cities(path: Path) -> list[SyntheticCity]:
    """Load the fictional gazetteer shipped in ``data/demo/places.json``."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        SyntheticCity(
            city=normalize_place(item["city"]),
            state=normalize_state(item["state"]),
            latitude=float(item["latitude"]),
            longitude=float(item["longitude"]),
            radius_km=float(item.get("radius_km", 8.0)),
            code=str(item.get("code", "")),
        )
        for item in payload["cities"]
    ]


class SyntheticGazetteer:
    """Index of fictional cities and the district names each one contains."""

    def __init__(self, cities: Iterable[SyntheticCity]) -> None:
        self.cities = list(cities)
        self._by_key = {city.key: city for city in self.cities}

    def register_districts(self, entries: Iterable[tuple[str, str, str]]) -> None:
        """Teach the gazetteer about ``(district, city, state)`` triples."""
        for district, city, state in entries:
            found = self.city(city, state)
            if found and district and district not in found.districts:
                found.districts.append(district)

    def city(self, city: str, state: str) -> SyntheticCity | None:
        return self._by_key.get(f"{match_key(city)}|{normalize_state(state)}")

    def find_in_query(self, query: str) -> tuple[SyntheticCity | None, str | None]:
        """Locate the city and (optionally) the district mentioned in a query."""
        key = match_key(query)
        city = max(
            (candidate for candidate in self.cities if match_key(candidate.city) in key),
            key=lambda candidate: len(match_key(candidate.city)),
            default=None,
        )
        if city is None:
            return None, None
        districts = [name for name in city.districts if match_key(name) in key]
        district = max(districts, key=lambda name: len(match_key(name)), default=None)
        return city, district


class SyntheticGeocodingProvider:
    """``GeocodingProvider`` over the fictional gazetteer."""

    def __init__(self, gazetteer: SyntheticGazetteer) -> None:
        self.gazetteer = gazetteer
        self.calls: list[str] = []

    def search(
        self,
        query: str,
        *,
        geometry: bool = False,
        viewbox: BBox | None = None,
        bounded: bool = False,
    ) -> Place | None:
        self.calls.append(query)
        city, district = self.gazetteer.find_in_query(query)
        if city is None:
            return None
        if district:
            lat, lng = city.district_point(district)
            geom = city.district_geometry(district, "osm") if geometry else None
            return Place(latitude=lat, longitude=lng, geometry=geom, bbox=city.bbox(), display_name=district)
        return Place(
            latitude=city.latitude,
            longitude=city.longitude,
            geometry=city.geometry() if geometry else None,
            bbox=city.bbox(),
            display_name=city.city,
        )


class SyntheticBoundaryProvider:
    """``BoundaryProvider`` over the fictional gazetteer."""

    def __init__(self, gazetteer: SyntheticGazetteer) -> None:
        self.gazetteer = gazetteer

    def municipality_code(self, city: str, state: str) -> str | None:
        found = self.gazetteer.city(city, state)
        return found.code if found else None

    def municipality_geometry(self, city: str, state: str) -> GeoJSON | None:
        found = self.gazetteer.city(city, state)
        return found.geometry() if found else None

    def district_geometries(self, state: str) -> list[dict[str, Any]]:
        state = normalize_state(state)
        records: list[dict[str, Any]] = []
        for city in self.gazetteer.cities:
            if city.state != state:
                continue
            for district in city.districts:
                geometry = city.district_geometry(district, "ibge")
                if geometry is None:
                    continue
                records.append(
                    {
                        "name": district,
                        "match_key": match_key(district),
                        "city_code": city.code,
                        "geometry": geometry,
                    }
                )
        return records
