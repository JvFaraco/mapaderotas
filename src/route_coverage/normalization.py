"""Text normalization for place names and identifiers.

City and district names are part of the cache key, so ``"CAMPINAS"`` and
``"Campinas"`` must collapse to the same entry — otherwise the same place is
geocoded twice and both polygons are drawn on top of each other.
"""

from __future__ import annotations

import re
import unicodedata

STATE_BY_FULL_NAME: dict[str, str] = {
    "ACRE": "AC",
    "ALAGOAS": "AL",
    "AMAPA": "AP",
    "AMAZONAS": "AM",
    "BAHIA": "BA",
    "CEARA": "CE",
    "DISTRITO FEDERAL": "DF",
    "ESPIRITO SANTO": "ES",
    "GOIAS": "GO",
    "MARANHAO": "MA",
    "MATO GROSSO": "MT",
    "MATO GROSSO DO SUL": "MS",
    "MINAS GERAIS": "MG",
    "PARA": "PA",
    "PARAIBA": "PB",
    "PARANA": "PR",
    "PERNAMBUCO": "PE",
    "PIAUI": "PI",
    "RIO DE JANEIRO": "RJ",
    "RIO GRANDE DO NORTE": "RN",
    "RIO GRANDE DO SUL": "RS",
    "RONDONIA": "RO",
    "RORAIMA": "RR",
    "SANTA CATARINA": "SC",
    "SAO PAULO": "SP",
    "SERGIPE": "SE",
    "TOCANTINS": "TO",
}

#: Abbreviations expanded before fuzzy matching against official gazetteers.
ABBREVIATIONS: dict[str, str] = {
    "jd": "jardim",
    "pq": "parque",
    "vl": "vila",
    "res": "residencial",
    "cj": "conjunto",
    "conj": "conjunto",
    "lot": "loteamento",
    "sta": "santa",
    "sto": "santo",
    "ns": "nossa senhora",
    "nsa": "nossa senhora",
    "pca": "praca",
    "av": "avenida",
}

_RE_ROMAN = re.compile(r"\b[IVXivx]{1,4}\b")
_RE_PARENT_DISTRICT = re.compile(r"\(([^)]+)\)\s*$")
_RE_TRAILING_ROMAN = re.compile(r"\s+[IVXivx]{1,4}$")


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value or "")
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def normalize_state(value: str | None) -> str:
    """Accept a code (``"SC"``) or a full name (``"Santa Catarina"``) → code."""
    text = (value or "").strip().upper()
    if len(text) == 2:
        return text
    return STATE_BY_FULL_NAME.get(strip_accents(text), text)


def normalize_place(value: str | None) -> str:
    """Canonical display form of a city/district name.

    Title case, collapsed whitespace, and roman numerals kept upper — plain
    ``.title()`` turns ``"Pedro II"`` into ``"Pedro Ii"``.
    """
    titled = re.sub(r"\s+", " ", (value or "").strip()).title()
    return _RE_ROMAN.sub(lambda m: m.group(0).upper(), titled)


def match_key(value: str | None) -> str:
    """Aggressive form used only for *matching*, never for display."""
    text = strip_accents(value or "").lower()
    text = re.sub(r"[^a-z0-9() ]+", " ", text)
    words = [ABBREVIATIONS.get(w.strip("."), w) for w in text.split()]
    return " ".join(words)


def normalize_identifier(value: str | None) -> str:
    """Technical ids are compared case-insensitively and without stray spaces."""
    return re.sub(r"\s+", " ", (value or "").strip()).upper()


def name_variants(name: str) -> list[str]:
    """Alternative spellings worth a second geocoding attempt.

    Only safe transformations: dropping a parenthesised parent district, using
    the parent district alone, and dropping a trailing roman numeral. Prefixes
    such as ``Setor``/``Bairro`` are *not* stripped — in Goiânia the official
    name really is "Setor Bueno", and in Curitiba it really is "Bairro Alto".
    """
    base = (name or "").strip()
    if not base:
        return []
    candidates: list[str] = []
    parent = _RE_PARENT_DISTRICT.search(base)
    without_parens = _RE_PARENT_DISTRICT.sub("", base).strip() if parent else base
    if parent:
        candidates.append(without_parens)
    for candidate in (base, without_parens):
        without_roman = _RE_TRAILING_ROMAN.sub("", candidate).strip()
        if without_roman != candidate:
            candidates.append(without_roman)
    if parent:
        # Last, and on purpose: the parent district is a broader area, so it
        # is only worth using once every spelling of the district itself has
        # failed.
        candidates.append(parent.group(1).strip())

    seen = {match_key(base)}
    unique: list[str] = []
    for candidate in candidates:
        key = match_key(candidate)
        if candidate and key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def parent_district(name: str) -> str | None:
    """``"Conjunto Maria Rosa (Taquaralto)"`` → ``"Taquaralto"``."""
    found = _RE_PARENT_DISTRICT.search(name or "")
    return found.group(1).strip() if found else None
