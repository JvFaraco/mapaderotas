"""Nominatim (OpenStreetMap) client.

Wraps geopy so the rest of the codebase sees a ``Place``, never a geopy
object, and so the rate limit required by Nominatim's usage policy lives in
exactly one place.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config import Settings
from .base import BBox, Place


class NominatimProvider:
    """Rate-limited free-text geocoder."""

    def __init__(self, settings: Settings, geocode: Callable[..., Any] | None = None) -> None:
        self.settings = settings
        self._geocode = geocode or self._build_geocode(settings)

    @staticmethod
    def _build_geocode(settings: Settings) -> Callable[..., Any]:
        from geopy.extra.rate_limiter import RateLimiter
        from geopy.geocoders import Nominatim

        locator = Nominatim(user_agent=settings.user_agent, timeout=settings.request_timeout_seconds)
        return RateLimiter(locator.geocode, min_delay_seconds=settings.geocoder_min_delay_seconds)

    def search(
        self,
        query: str,
        *,
        geometry: bool = False,
        viewbox: BBox | None = None,
        bounded: bool = False,
    ) -> Place | None:
        kwargs: dict[str, Any] = {}
        if geometry:
            kwargs["geometry"] = "geojson"
        if viewbox:
            kwargs["viewbox"] = viewbox
            kwargs["bounded"] = bounded
        location = self._geocode(query, **kwargs)
        if not location:
            return None
        raw = getattr(location, "raw", {}) or {}
        geojson = raw.get("geojson")
        if geojson and geojson.get("type") not in ("Polygon", "MultiPolygon"):
            geojson = None
        return Place(
            latitude=location.latitude,
            longitude=location.longitude,
            geometry=geojson,
            bbox=_parse_bbox(raw.get("boundingbox")),
            display_name=raw.get("display_name", ""),
        )


def _parse_bbox(bounding_box: list[str] | None) -> BBox | None:
    """Nominatim returns ``[south, north, west, east]`` as strings.

    geopy's viewbox wants ``(lat, lon)`` corner pairs, not ``(lon, lat)``.
    """
    if not bounding_box or len(bounding_box) != 4:
        return None
    try:
        south, north, west, east = (float(value) for value in bounding_box)
    except (TypeError, ValueError):
        return None
    return ((north, west), (south, east))
