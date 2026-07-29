"""Configuration — everything that used to be a constant in the middle of a script.

Values come from the environment (see ``.env.example``) with safe defaults, so
no deployment-specific string is hardcoded in the source tree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parents[1]

IBGE_MUNICIPALITIES_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"
IBGE_MESH_URL = "https://servicodados.ibge.gov.br/api/v3/malhas/municipios/{code}"
IBGE_DISTRICTS_ZIP_URL = (
    "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/"
    "malhas_de_setores_censitarios__divisoes_intramunicipais/censo_2022/"
    "bairros/shp/UF/{state}_bairros_CD2022.zip"
)


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    """Runtime settings resolved from the environment."""

    #: Identifies the client to Nominatim; their usage policy requires a
    #: contact address, so it is configuration, never a literal in the code.
    geocoder_user_agent: str = field(
        default_factory=lambda: _env("GEOCODER_USER_AGENT", "route-coverage-portfolio")
    )
    geocoder_contact_email: str = field(
        default_factory=lambda: _env("GEOCODER_CONTACT_EMAIL", "contato@example.com")
    )
    #: Nominatim's usage policy caps anonymous clients at 1 request/second.
    geocoder_min_delay_seconds: float = field(
        default_factory=lambda: _env_float("GEOCODER_MIN_DELAY_SECONDS", 1.1)
    )
    request_timeout_seconds: float = field(
        default_factory=lambda: _env_float("REQUEST_TIMEOUT_SECONDS", 30.0)
    )

    data_dir: Path = field(default_factory=lambda: _env_path("DATA_DIR", PROJECT_ROOT / "data"))
    cache_path: Path = field(
        default_factory=lambda: _env_path("CACHE_PATH", PROJECT_ROOT / "data" / "private" / "geocode_cache.json")
    )
    boundaries_dir: Path = field(
        default_factory=lambda: _env_path("BOUNDARIES_DIR", PROJECT_ROOT / "data" / "private" / "ibge_districts")
    )
    output_path: Path = field(
        default_factory=lambda: _env_path("OUTPUT_PATH", PROJECT_ROOT / "dist" / "route-coverage-map.html")
    )
    web_dir: Path = field(default_factory=lambda: _env_path("WEB_DIR", PROJECT_ROOT / "web"))

    @property
    def user_agent(self) -> str:
        return f"{self.geocoder_user_agent} ({self.geocoder_contact_email})"

    @property
    def demo_dir(self) -> Path:
        return self.data_dir / "demo"


def load_settings() -> Settings:
    """Build settings from the current environment (reads ``.env`` if present)."""
    load_dotenv(PROJECT_ROOT / ".env")
    return Settings()


def load_dotenv(path: Path) -> None:
    """Minimal ``.env`` reader: ``KEY=value`` lines, existing env wins.

    A dependency-free loader keeps the runtime install limited to what the
    pipeline actually needs.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
