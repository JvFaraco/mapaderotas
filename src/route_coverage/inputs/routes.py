"""Route coverage input: one row per area a route covers."""

from __future__ import annotations

from pathlib import Path

from ..models import RouteArea
from ..normalization import normalize_identifier, normalize_place, normalize_state
from .tabular import cell, column_index, read_table, require_columns

#: Rows whose identifier column holds a spreadsheet total, not a route.
TOTAL_ROW_LABELS = {"sum", "soma", "total", "total geral", "grand total", "subtotal"}


def read_routes(path: Path, sheet: str | None = None) -> list[RouteArea]:
    """Read ``route_id, specialist_id, region, state, city, district``.

    An empty ``district`` means the route covers the whole municipality.
    Rows missing a city/state are skipped (trailing junk is normal in exported
    spreadsheets), and totalling rows are dropped rather than treated as a
    route with an odd name.
    """
    header, rows = read_table(path, sheet)
    indexes = {
        "route_id": column_index(header, r"^route[_ ]?id$", r"^rota$", r"^route$"),
        "specialist_id": column_index(header, r"^specialist[_ ]?id$", r"^tech[_ ]?id$", r"especialista"),
        "region": column_index(header, r"^region(al)?$"),
        "state": column_index(header, r"^state$", r"^uf$", r"estado"),
        "city": column_index(header, r"^city$", r"^cidade$", r"munic[ií]pio"),
        "district": column_index(header, r"^district$", r"^bairro$", r"neighbou?rhood"),
    }
    require_columns(
        header,
        {"route_id": indexes["route_id"], "city": indexes["city"], "state": indexes["state"]},
        source=Path(path).name,
    )

    areas: list[RouteArea] = []
    for row in rows:
        route_id = normalize_identifier(cell(row, indexes["route_id"]))
        if route_id.lower() in TOTAL_ROW_LABELS:
            continue
        city = normalize_place(cell(row, indexes["city"]))
        state = normalize_state(cell(row, indexes["state"]))
        if not city or not state:
            continue
        areas.append(
            RouteArea(
                route_id=route_id,
                specialist_id=normalize_identifier(cell(row, indexes["specialist_id"])),
                region=normalize_identifier(cell(row, indexes["region"])),
                city=city,
                state=state,
                district=normalize_place(cell(row, indexes["district"])),
            )
        )
    return areas


def split_areas(areas: list[RouteArea]) -> tuple[list[RouteArea], list[RouteArea]]:
    """Split into ``(whole cities, districts)`` — they use different sources."""
    cities = [area for area in areas if area.is_whole_city]
    districts = [area for area in areas if not area.is_whole_city]
    return cities, districts
