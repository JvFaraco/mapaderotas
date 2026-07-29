"""Provider adapters, exercised against mocked HTTP/geopy responses."""

from __future__ import annotations

from types import SimpleNamespace

from route_coverage.config import Settings
from route_coverage.geocoding.ibge import IbgeProvider
from route_coverage.geocoding.nominatim import NominatimProvider
from route_coverage.geometry.neighborhoods import match_district

POLYGON = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}


def fake_location(**raw):
    return SimpleNamespace(latitude=-27.9, longitude=-46.3, raw=raw)


def test_nominatim_adapter_normalizes_geometry_and_bbox():
    def geocode(query, **kwargs):
        assert kwargs["geometry"] == "geojson"
        return fake_location(geojson=POLYGON, boundingbox=["-28.0", "-27.8", "-46.4", "-46.2"], display_name="X")

    place = NominatimProvider(Settings(), geocode=geocode).search("anywhere", geometry=True)

    assert place.geometry == POLYGON
    assert place.bbox == ((-27.8, -46.4), (-28.0, -46.2))  # (lat, lon) corner pairs


def test_nominatim_adapter_drops_point_geometries():
    """A Point in the geojson field is not an outline and must not be used."""
    location = fake_location(geojson={"type": "Point", "coordinates": [0, 0]})
    place = NominatimProvider(Settings(), geocode=lambda q, **k: location).search("x", geometry=True)
    assert place.geometry is None


def test_nominatim_adapter_returns_none_when_nothing_is_found():
    assert NominatimProvider(Settings(), geocode=lambda q, **k: None).search("nowhere") is None


class FakeSession:
    def __init__(self, payloads):
        self.payloads = payloads
        self.headers = {}
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        payload = self.payloads[url]
        return SimpleNamespace(status_code=200, json=lambda: payload, raise_for_status=lambda: None, content=b"")


def test_ibge_municipality_lookup_matches_name_and_state():
    municipalities = [
        {"id": 4205407, "nome": "Florianópolis", "microrregiao": {"mesorregiao": {"UF": {"sigla": "SC"}}}},
        {"id": 3550308, "nome": "São Paulo", "microrregiao": {"mesorregiao": {"UF": {"sigla": "SP"}}}},
    ]
    from route_coverage.config import IBGE_MESH_URL, IBGE_MUNICIPALITIES_URL

    session = FakeSession({IBGE_MUNICIPALITIES_URL: municipalities, IBGE_MESH_URL.format(code="4205407"): POLYGON})
    provider = IbgeProvider(Settings(), session=session)

    # Accent-insensitive, and the state disambiguates homonymous cities.
    assert provider.municipality_code("florianopolis", "SC") == "4205407"
    assert provider.municipality_code("São Paulo", "SC") is None
    assert provider.municipality_geometry("Florianópolis", "SC") == POLYGON
    assert session.urls.count(IBGE_MUNICIPALITIES_URL) == 1  # the list is fetched once


def test_district_matching_prefers_exact_then_variant_then_fuzzy():
    records = [
        {"name": "Jardim Santa Genebra", "match_key": "jardim santa genebra", "city_code": "350950"},
        {"name": "Barão Geraldo", "match_key": "barao geraldo", "city_code": "350950"},
    ]
    exact, score, _ = match_district("Jd Sta Genebra", records, "350950")
    assert exact["name"] == "Jardim Santa Genebra" and score == 1.0

    variant, _, _ = match_district("Jd Sta Genebra II (Barao Geraldo)", records, "350950")
    assert variant["name"] == "Jardim Santa Genebra"

    missing, score, near = match_district("Bairro Inexistente", records, "350950")
    assert missing is None and near  # near misses are reported for auditing

    assert match_district("Centro", records, "999999") == (None, 0.0, [])
