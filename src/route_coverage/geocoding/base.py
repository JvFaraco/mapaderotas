"""Ports for the two external data sources.

The pipeline depends on these protocols only, which is what makes the tests
run offline and the demo build possible without any network at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..models import GeoJSON

#: ``((north, west), (south, east))`` — the shape geopy expects for a viewbox.
BBox = tuple[tuple[float, float], tuple[float, float]]


@dataclass
class Place:
    """A geocoding hit, normalized across providers."""

    latitude: float
    longitude: float
    geometry: GeoJSON | None = None
    bbox: BBox | None = None
    display_name: str = ""


@runtime_checkable
class GeocodingProvider(Protocol):
    """Free-text geocoding (OpenStreetMap/Nominatim in production)."""

    def search(
        self,
        query: str,
        *,
        geometry: bool = False,
        viewbox: BBox | None = None,
        bounded: bool = False,
    ) -> Place | None: ...


@runtime_checkable
class BoundaryProvider(Protocol):
    """Official boundaries (IBGE in production)."""

    def municipality_code(self, city: str, state: str) -> str | None: ...

    def municipality_geometry(self, city: str, state: str) -> GeoJSON | None: ...

    def district_geometries(self, state: str) -> list[dict[str, object]]: ...
