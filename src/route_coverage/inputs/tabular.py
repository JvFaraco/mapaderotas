"""Reading CSV and XLSX with the same interface, plus loose header matching.

Real spreadsheets never agree on column names ("Município" vs "Cidade",
"Logradouro" vs "Rua"), so columns are located by regex instead of by exact
title, and a missing required column is an error with a readable message.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Sequence
from pathlib import Path

Row = list[str]


class InputError(ValueError):
    """Raised when an input file cannot be interpreted."""


def read_table(path: Path, sheet: str | None = None) -> tuple[list[str], list[Row]]:
    """Return ``(header, rows)`` from a ``.csv``/``.tsv``/``.xlsx`` file."""
    path = Path(path)
    if not path.exists():
        raise InputError(f"input file not found: {path}")
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv", ".txt"):
        return _read_delimited(path, "\t" if suffix == ".tsv" else ",")
    if suffix in (".xlsx", ".xlsm"):
        return _read_xlsx(path, sheet)
    raise InputError(f"unsupported input format: {path.suffix} ({path.name})")


def _read_delimited(path: Path, delimiter: str) -> tuple[list[str], list[Row]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [[(cell or "").strip() for cell in row] for row in csv.reader(handle, delimiter=delimiter)]
    rows = [row for row in rows if any(cell for cell in row)]
    if not rows:
        raise InputError(f"empty input file: {path}")
    return rows[0], rows[1:]


def _read_xlsx(path: Path, sheet: str | None) -> tuple[list[str], list[Row]]:
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise InputError("reading .xlsx requires openpyxl (pip install openpyxl)") from exc

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = workbook[sheet] if sheet else workbook.worksheets[0]
        raw = [
            ["" if cell is None else str(cell).strip() for cell in row]
            for row in worksheet.iter_rows(values_only=True)
        ]
    finally:
        workbook.close()
    raw = [row for row in raw if any(cell for cell in row)]
    if not raw:
        raise InputError(f"empty input file: {path}")
    return raw[0], raw[1:]


def column_index(header: Sequence[str], *patterns: str) -> int | None:
    """Index of the first column matching any pattern (case-insensitive regex)."""
    for pattern in patterns:
        for index, name in enumerate(header):
            if re.search(pattern, name or "", re.IGNORECASE):
                return index
    return None


def require_columns(header: Sequence[str], required: dict[str, int | None], source: str) -> None:
    missing = [name for name, index in required.items() if index is None]
    if missing:
        raise InputError(f"{source}: required column(s) not found: {', '.join(missing)}. Header was: {list(header)}")


def cell(row: Sequence[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    value = row[index]
    return value.strip() if isinstance(value, str) else ("" if value is None else str(value).strip())
