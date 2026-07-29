"""Shared fixtures. Everything here is synthetic; no test touches the network."""

from __future__ import annotations

from pathlib import Path

import pytest

from route_coverage.config import Settings
from route_coverage.demo import build_gazetteer
from route_coverage.geocoding.synthetic import SyntheticBoundaryProvider, SyntheticGeocodingProvider
from route_coverage.inputs import read_routes, read_specialists

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = PROJECT_ROOT / "data" / "demo"


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=PROJECT_ROOT / "data",
        cache_path=tmp_path / "cache.json",
        boundaries_dir=tmp_path / "boundaries",
        output_path=tmp_path / "map.html",
        web_dir=PROJECT_ROOT / "web",
    )


@pytest.fixture
def demo_inputs():
    areas = read_routes(DEMO_DIR / "routes.csv")
    specialists = read_specialists(DEMO_DIR / "specialists.csv")
    return areas, specialists


@pytest.fixture(scope="session")
def demo_map_html(tmp_path_factory) -> str:
    """The demo map, built once per session — the artefact CI publishes."""
    from route_coverage.cli import main

    output = tmp_path_factory.mktemp("build") / "map.html"
    assert main(["build", "--demo", "--out", str(output)]) == 0
    return output.read_text(encoding="utf-8")


@pytest.fixture
def providers(demo_inputs):
    areas, specialists = demo_inputs
    gazetteer = build_gazetteer(DEMO_DIR / "places.json", areas, specialists)
    return SyntheticGeocodingProvider(gazetteer), SyntheticBoundaryProvider(gazetteer)
