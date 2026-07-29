"""Specialist input: the home base that becomes the origin marker.

Street and number are read because geocoding needs them; they are dropped as
soon as a coordinate exists (see ``pipeline.geocode_specialists``).
"""

from __future__ import annotations

from pathlib import Path

from ..inputs.tabular import InputError, cell, column_index, read_table, require_columns
from ..models import Specialist
from ..normalization import normalize_identifier, normalize_place, normalize_state


def _optional_float(value: str) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def read_specialists(path: Path, sheet: str | None = None) -> list[Specialist]:
    """Read ``specialist_id, region, street, number, district, city, state``.

    ``latitude``/``longitude`` are optional and act as a manual override: when
    present the geocoder is not called at all, which is how a known bad match
    gets pinned (homonymous districts in different cities are common).
    """
    header, rows = read_table(path, sheet)
    indexes = {
        "specialist_id": column_index(header, r"^specialist[_ ]?id$", r"^tech[_ ]?id$", r"^id$"),
        "region": column_index(header, r"^region(al)?$"),
        "street": column_index(header, r"^street$", r"^rua$", r"logradouro"),
        "number": column_index(header, r"^number$", r"^n[uú]mero$", r"^n[ºo°]?$"),
        "district": column_index(header, r"^district$", r"^bairro$", r"neighbou?rhood"),
        "city": column_index(header, r"^city$", r"^cidade$", r"munic[ií]pio"),
        "state": column_index(header, r"^state$", r"^uf$", r"estado"),
        "latitude": column_index(header, r"^lat(itude)?$"),
        "longitude": column_index(header, r"^l(ng|on|ongitude)$"),
    }
    require_columns(
        header,
        {"specialist_id": indexes["specialist_id"], "city": indexes["city"], "state": indexes["state"]},
        source=Path(path).name,
    )

    specialists: list[Specialist] = []
    seen: set[str] = set()
    for row in rows:
        specialist_id = normalize_identifier(cell(row, indexes["specialist_id"]))
        city = normalize_place(cell(row, indexes["city"]))
        if not specialist_id or not city:
            continue
        if specialist_id in seen:
            raise InputError(f"{Path(path).name}: duplicated specialist_id {specialist_id!r}")
        seen.add(specialist_id)
        specialists.append(
            Specialist(
                specialist_id=specialist_id,
                region=normalize_identifier(cell(row, indexes["region"])),
                street=cell(row, indexes["street"]),
                number=cell(row, indexes["number"]),
                district=normalize_place(cell(row, indexes["district"])),
                city=city,
                state=normalize_state(cell(row, indexes["state"])),
                latitude=_optional_float(cell(row, indexes["latitude"])),
                longitude=_optional_float(cell(row, indexes["longitude"])),
            )
        )
    return specialists
