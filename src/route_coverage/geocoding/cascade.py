"""Progressive geocoding: try the most specific query, then give up detail.

Address-level data outside big cities is thin in OpenStreetMap — house numbers
often simply do not exist — so a single query would fail for a large share of
records. Each level down is less exact and says so, which is what
``location_precision`` carries all the way to the map legend.
"""

from __future__ import annotations

from ..models import GeocodeResult, Precision, Specialist
from ..normalization import parent_district
from .base import BBox, GeocodingProvider

COUNTRY_SUFFIX = "Brasil"


class BBoxCache:
    """Memoises a city's bounding box; every district fallback needs it."""

    def __init__(self, provider: GeocodingProvider) -> None:
        self.provider = provider
        self._boxes: dict[str, BBox | None] = {}

    def get(self, city: str, state: str) -> BBox | None:
        key = f"{city}|{state}"
        if key not in self._boxes:
            place = self.provider.search(f"{city}, {state}, {COUNTRY_SUFFIX}")
            self._boxes[key] = place.bbox if place else None
        return self._boxes[key]


def locate_specialist(provider: GeocodingProvider, specialist: Specialist, boxes: BBoxCache) -> GeocodeResult | None:
    """Locate a specialist's home base, from full address down to city centre.

    A record carrying explicit coordinates short-circuits the whole cascade:
    that is the manual override for cases the geocoder gets wrong (homonymous
    districts in different cities are the usual culprit).
    """
    if specialist.latitude is not None and specialist.longitude is not None:
        return GeocodeResult(
            latitude=specialist.latitude,
            longitude=specialist.longitude,
            precision="manual",
            method="manual",
        )

    street, number = specialist.street, specialist.number
    district, city, state = specialist.district, specialist.city, specialist.state

    attempts: list[tuple[Precision, str]] = []
    if street and number:
        attempts.append(("address", f"{street}, {number}, {district}, {city}, {state}, {COUNTRY_SUFFIX}"))
    if street:
        attempts.append(("street", f"{street}, {district}, {city}, {state}, {COUNTRY_SUFFIX}"))
    if district:
        attempts.append(("district", f"{district}, {city}, {state}, {COUNTRY_SUFFIX}"))

    for precision, query in attempts:
        place = provider.search(query)
        if place:
            return GeocodeResult(place.latitude, place.longitude, precision, method="osm")

    if district:
        bbox = boxes.get(city, state)
        if bbox:
            place = provider.search(district, viewbox=bbox, bounded=True)
            if place:
                return GeocodeResult(place.latitude, place.longitude, "district_area", method="osm")

    place = provider.search(f"{city}, {state}, {COUNTRY_SUFFIX}")
    if place:
        return GeocodeResult(place.latitude, place.longitude, "city", method="osm")
    return None


def locate_district(
    provider: GeocodingProvider, district: str, city: str, state: str, boxes: BBoxCache
) -> GeocodeResult | None:
    """Locate a district, preferring a polygon over a point at every level."""
    place = provider.search(f"{district}, {city}, {state}, {COUNTRY_SUFFIX}", geometry=True)
    if place:
        return GeocodeResult(place.latitude, place.longitude, "district", geometry=place.geometry, method="osm")

    bbox = boxes.get(city, state)
    if bbox:
        place = provider.search(district, geometry=True, viewbox=bbox, bounded=True)
        if place:
            return GeocodeResult(
                place.latitude, place.longitude, "district_area", geometry=place.geometry, method="osm"
            )

        parent = parent_district(district)
        if parent:
            # Informal sub-districts often do not exist in OSM on their own,
            # but the parent district between parentheses usually does.
            place = provider.search(parent, geometry=True, viewbox=bbox, bounded=True)
            if place:
                return GeocodeResult(
                    place.latitude,
                    place.longitude,
                    "parent_district",
                    geometry=place.geometry,
                    matched_name=parent,
                    method="osm_parent",
                )
    return None


def locate_district_variants(
    provider: GeocodingProvider, district: str, city: str, state: str, variants: list[str]
) -> GeocodeResult | None:
    """Retry the polygon search with safe spelling variants of the name."""
    for variant in variants:
        place = provider.search(f"{variant}, {city}, {state}, {COUNTRY_SUFFIX}", geometry=True)
        if place and place.geometry:
            return GeocodeResult(
                place.latitude,
                place.longitude,
                "osm_variant",
                geometry=place.geometry,
                matched_name=variant,
                method="osm_variant",
            )
    return None
