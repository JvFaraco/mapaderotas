"""Normalization is what keeps one place from becoming two cache entries."""

from __future__ import annotations

import pytest

from route_coverage.normalization import (
    match_key,
    name_variants,
    normalize_identifier,
    normalize_place,
    normalize_state,
    parent_district,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("SC", "SC"),
        ("sc", "SC"),
        ("Santa Catarina", "SC"),
        ("SÃO PAULO", "SP"),
        ("Sao Paulo", "SP"),
        (" rio grande do sul ", "RS"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_state_accepts_codes_and_full_names(value, expected):
    assert normalize_state(value) == expected


def test_normalize_state_leaves_unknown_values_visible():
    # Better a wrong-looking value the operator can spot than a silent guess.
    assert normalize_state("Atlantis") == "ATLANTIS"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("CAMPINAS", "Campinas"),
        ("campinas", "Campinas"),
        ("  são   josé ", "São José"),
        ("dom pedro II", "Dom Pedro II"),
        ("XV de novembro", "XV De Novembro"),
    ],
)
def test_normalize_place_is_canonical_and_keeps_roman_numerals(value, expected):
    assert normalize_place(value) == expected


def test_case_variants_collapse_to_the_same_cache_key():
    assert normalize_place("CAMPINAS") == normalize_place("campinas") == normalize_place("Campinas")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Jd Sta Genebra", "jardim santa genebra"),
        ("JARDIM SANTA GENEBRA", "jardim santa genebra"),
        ("Vl. Nova", "vila nova"),
        ("Parque das Águas", "parque das aguas"),
    ],
)
def test_match_key_expands_abbreviations_and_drops_accents(value, expected):
    assert match_key(value) == expected


def test_name_variants_drops_parentheses_and_roman_suffix():
    variants = name_variants("Jd Sta Genebra II (Barao Geraldo)")
    assert variants == ["Jd Sta Genebra II", "Jd Sta Genebra", "Barao Geraldo"]
    # The parent district comes last: it is a broader area, so it is only
    # worth trying after every spelling of the district itself failed.
    assert variants[-1] == "Barao Geraldo"


def test_name_variants_never_strips_meaningful_prefixes():
    # "Setor Bueno" and "Bairro Alto" are the official names, not decorations.
    assert name_variants("Setor Bueno") == []
    assert name_variants("Bairro Alto") == []


def test_name_variants_are_unique_and_exclude_the_original():
    variants = name_variants("Jardim Paulista I")
    assert variants == ["Jardim Paulista"]


def test_parent_district():
    assert parent_district("Conjunto Maria Rosa (Taquaralto)") == "Taquaralto"
    assert parent_district("Centro") is None


def test_normalize_identifier_is_case_and_space_insensitive():
    assert normalize_identifier(" route-south-01 ") == "ROUTE-SOUTH-01"
    assert normalize_identifier("tech  001") == "TECH 001"
