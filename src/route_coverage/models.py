"""Domain models.

The pipeline only ever handles technical identifiers (``TECH-001``,
``ROUTE-SOUTH-01``). Street level fields exist on the *input* record because
geocoding needs them, and are dropped the moment a coordinate is produced —
they never reach the cache or the rendered map.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

GeoJSON = dict[str, Any]

#: How exact a coordinate is, from best to worst. ``block`` is what an
#: ``address``/``street`` hit becomes after jittering (see ``privacy``).
Precision = Literal[
    "manual",
    "block",
    "address",
    "street",
    "district",
    "district_area",
    "parent_district",
    "city",
    "ibge_district",
    "osm_variant",
    "voronoi",
]


@dataclass(frozen=True)
class Specialist:
    """A field specialist, keyed by a technical id with no personal meaning."""

    specialist_id: str
    region: str = ""
    street: str = ""
    number: str = ""
    district: str = ""
    city: str = ""
    state: str = ""
    latitude: float | None = None
    longitude: float | None = None

    @property
    def has_fixed_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


@dataclass(frozen=True)
class RouteArea:
    """One area covered by a route: a whole city (no district) or a district."""

    route_id: str
    specialist_id: str = ""
    region: str = ""
    city: str = ""
    state: str = ""
    district: str = ""

    @property
    def is_whole_city(self) -> bool:
        return not self.district

    @property
    def city_key(self) -> str:
        return f"{self.city}|{self.state}"

    @property
    def district_key(self) -> str:
        return f"{self.district}|{self.city}|{self.state}"


@dataclass
class GeocodeResult:
    """What a geocoding provider returns, independent of which provider it was."""

    latitude: float
    longitude: float
    precision: Precision
    geometry: GeoJSON | None = None
    matched_name: str = ""
    method: str = ""
    score: float | None = None
    bbox: tuple[tuple[float, float], tuple[float, float]] | None = None


@dataclass
class CoverageStats:
    """Numbers worth reporting on a build — all derived, none identifying."""

    specialists: int = 0
    cities: int = 0
    districts: int = 0
    geocoded: int = 0
    cache_hits: int = 0
    real_geometries: int = 0
    fallback_geometries: int = 0
    overlaps_clipped: int = 0
    duplicates_removed: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def cache_hit_rate(self) -> float:
        total = self.geocoded + self.cache_hits
        return self.cache_hits / total if total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "specialists": self.specialists,
            "cities": self.cities,
            "districts": self.districts,
            "geocoded": self.geocoded,
            "cache_hits": self.cache_hits,
            "cache_hit_rate": round(self.cache_hit_rate, 4),
            "real_geometries": self.real_geometries,
            "fallback_geometries": self.fallback_geometries,
            "overlaps_clipped": self.overlaps_clipped,
            "duplicates_removed": self.duplicates_removed,
            **self.extra,
        }
