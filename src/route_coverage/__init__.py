"""Route Coverage Map — geospatial route coverage pipeline.

Turns two tabular inputs (routes and specialists) into a privacy-safe,
self-contained HTML map: progressive geocoding, IBGE boundaries, Voronoi
fallback and overlap clipping.
"""

__version__ = "1.0.0"

__all__ = ["__version__"]
