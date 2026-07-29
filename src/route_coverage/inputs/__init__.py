"""Input adapters: tabular files in, validated domain objects out."""

from .routes import read_routes
from .specialists import read_specialists
from .tabular import InputError, read_table

__all__ = ["InputError", "read_routes", "read_specialists", "read_table"]
