"""IBGE clients: municipal meshes (API) and 2022 census district polygons (shapefile).

Coverage of the district layer is partial by design of the source — only
municipalities that formally delimited districts with IBGE are published, so a
missing state file is an expected outcome, not an error.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Any

from ..config import IBGE_DISTRICTS_ZIP_URL, IBGE_MESH_URL, IBGE_MUNICIPALITIES_URL, Settings
from ..models import GeoJSON
from ..normalization import match_key, normalize_state


class IbgeProvider:
    """Official Brazilian boundaries, cached on disk between runs."""

    def __init__(self, settings: Settings, session: Any | None = None) -> None:
        self.settings = settings
        self._session = session
        self._municipalities: list[dict[str, Any]] | None = None
        self._districts_by_state: dict[str, list[dict[str, Any]] | None] = {}

    @property
    def session(self) -> Any:
        if self._session is None:
            import requests

            self._session = requests.Session()
            self._session.headers.update({"User-Agent": self.settings.user_agent})
        return self._session

    # ── municipalities ─────────────────────────────────────────────────────

    def _load_municipalities(self) -> list[dict[str, Any]]:
        if self._municipalities is None:
            response = self.session.get(IBGE_MUNICIPALITIES_URL, timeout=self.settings.request_timeout_seconds)
            response.raise_for_status()
            self._municipalities = response.json()
        return self._municipalities

    def municipality_code(self, city: str, state: str) -> str | None:
        target = match_key(city)
        state = normalize_state(state)
        for municipality in self._load_municipalities():
            if match_key(municipality.get("nome", "")) != target:
                continue
            code = (
                municipality.get("microrregiao", {})
                .get("mesorregiao", {})
                .get("UF", {})
                .get("sigla")
            )
            if code == state:
                return str(municipality["id"])
        return None

    def municipality_geometry(self, city: str, state: str) -> GeoJSON | None:
        code = self.municipality_code(city, state)
        if not code:
            return None
        response = self.session.get(
            IBGE_MESH_URL.format(code=code),
            params={"formato": "application/vnd.geo+json"},
            timeout=self.settings.request_timeout_seconds,
        )
        if response.status_code != 200:
            return None
        return response.json()

    # ── census districts ───────────────────────────────────────────────────

    def district_geometries(self, state: str) -> list[dict[str, Any]]:
        """District polygons of a state, as ``{name, match_key, city_code, geometry}``."""
        state = normalize_state(state)
        if state in self._districts_by_state:
            return self._districts_by_state[state] or []
        archive = self._download_districts(state)
        records = _read_district_shapefile(archive) if archive else None
        self._districts_by_state[state] = records
        return records or []

    def _download_districts(self, state: str) -> Path | None:
        directory = self.settings.boundaries_dir
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{state}_bairros_CD2022.zip"
        if path.exists():
            return path
        try:
            response = self.session.get(IBGE_DISTRICTS_ZIP_URL.format(state=state), timeout=120)
        except Exception:
            return None
        # Some states publish no district mesh at all; the server answers with
        # an error page or a near-empty archive rather than a 404.
        if response.status_code != 200 or len(response.content) < 500:
            return None
        path.write_bytes(response.content)
        return path


def _read_district_shapefile(archive: Path) -> list[dict[str, Any]] | None:
    try:
        import shapefile  # pyshp
    except ImportError:  # pragma: no cover - optional at runtime
        return None

    try:
        with zipfile.ZipFile(archive) as bundle:
            shp_name = next(name for name in bundle.namelist() if name.lower().endswith(".shp"))
            base = shp_name[:-4]
            reader = shapefile.Reader(
                shp=io.BytesIO(bundle.read(base + ".shp")),
                dbf=io.BytesIO(bundle.read(base + ".dbf")),
                shx=io.BytesIO(bundle.read(base + ".shx")) if base + ".shx" in bundle.namelist() else None,
            )
            fields = [field[0] for field in reader.fields[1:]]  # skip DeletionFlag
            name_field = _find_field(fields, r"^NM_BAIRRO", r"NM_BAIR", r"^NOME")
            city_field = _find_field(fields, r"^CD_MUN", r"CD_MUNIC", r"GEOCOD")
            if not name_field or not city_field:
                return None

            records: list[dict[str, Any]] = []
            for item in reader.iterShapeRecords():
                values = item.record.as_dict()
                name = str(values.get(name_field) or "").strip()
                if not name:
                    continue
                records.append(
                    {
                        "name": name,
                        "match_key": match_key(name),
                        "city_code": str(values.get(city_field) or "").strip(),
                        "geometry": item.shape.__geo_interface__,
                    }
                )
            return records
    except Exception:
        return None


def _find_field(fields: list[str], *patterns: str) -> str | None:
    for pattern in patterns:
        for field in fields:
            if re.search(pattern, field, re.IGNORECASE):
                return field
    return None
