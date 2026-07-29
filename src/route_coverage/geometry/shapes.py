"""Conversions between GeoJSON and shapely, tolerant of what providers return."""

from __future__ import annotations

from typing import Any

from ..models import GeoJSON


def to_shape(geojson: GeoJSON) -> Any:
    """Accept a geometry, a Feature or a FeatureCollection → one shapely geometry.

    IBGE meshes arrive as FeatureCollections while OSM returns bare geometries.
    """
    from shapely.geometry import shape
    from shapely.ops import unary_union

    if geojson.get("type") == "FeatureCollection":
        return unary_union([shape(feature["geometry"]) for feature in geojson["features"]])
    if geojson.get("type") == "Feature":
        return shape(geojson["geometry"])
    return shape(geojson)


def to_geojson(geometry: Any) -> GeoJSON:
    from shapely.geometry import mapping

    return dict(mapping(geometry))


def safe_shape(geojson: GeoJSON | None) -> Any | None:
    """``to_shape`` + ``buffer(0)``, returning ``None`` instead of raising.

    ``buffer(0)`` repairs the self-intersections that are common in
    hand-digitised administrative boundaries; without it every downstream
    intersection/difference raises.
    """
    if not geojson:
        return None
    try:
        return to_shape(geojson).buffer(0)
    except Exception:
        return None


def round_coordinates(value: Any, digits: int = 5) -> Any:
    """Round every float in a nested structure (5 digits ≈ 1 m).

    Coordinates dominate the payload size; this alone cuts a typical build by
    hundreds of kilobytes without any visible difference on screen.
    """
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, list):
        return [round_coordinates(item, digits) for item in value]
    if isinstance(value, tuple):
        return tuple(round_coordinates(item, digits) for item in value)
    if isinstance(value, dict):
        return {key: round_coordinates(item, digits) for key, item in value.items()}
    return value
