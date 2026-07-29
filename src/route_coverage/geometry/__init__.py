"""Geometry helpers: shapely in, GeoJSON out."""

from .neighborhoods import match_district
from .overlaps import clip_overlaps
from .shapes import to_geojson, to_shape
from .voronoi import fill_missing_polygons

__all__ = ["clip_overlaps", "fill_missing_polygons", "match_district", "to_geojson", "to_shape"]
