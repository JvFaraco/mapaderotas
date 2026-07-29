"""Privacy by design: the only module allowed to decide what leaves the pipeline.

Two rules, enforced here and asserted by ``tests/test_privacy.py``:

1. A coordinate derived from a street address is never published as-is — it is
   displaced by a deterministic ~60–120 m offset (one city block).
2. Everything rendered goes through an explicit *allowlist*. Anything not on
   the list is dropped, so a new field added upstream cannot leak by accident.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable
from typing import Any

METERS_PER_DEGREE_LAT = 111_320.0

#: Fields allowed in the published payload, per collection.
SPECIALIST_FIELDS: frozenset[str] = frozenset(
    {"specialist_id", "route_id", "region", "city", "state", "latitude", "longitude", "location_precision"}
)
CITY_FIELDS: frozenset[str] = frozenset(
    {"city", "state", "region", "route_id", "specialist_id", "geometry", "location_precision"}
)
DISTRICT_FIELDS: frozenset[str] = frozenset(
    {"district", "city", "state", "region", "route_id", "specialist_id", "geometry", "location_precision"}
)

#: Fields that must never appear in a published payload, whatever the reason.
FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "nome",
        "full_name",
        "street",
        "rua",
        "logradouro",
        "number",
        "numero",
        "address",
        "endereco",
        "email",
        "e_mail",
        "phone",
        "telefone",
        "cpf",
        "cnpj",
        "notes",
        "observacao",
        "observacoes",
        "supervisor",
        "manager",
        "comment",
    }
)

_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
#: Street-address shapes ("Rua X, 123"), which no published field should contain.
_RE_STREET_ADDRESS = re.compile(
    r"\b(?:rua|r\.|av\.|avenida|travessa|rodovia|estrada|alameda)\s+[^,;]{3,},?\s*n?[º°]?\s*\d+",
    re.IGNORECASE,
)


def block_jitter(
    seed: str, latitude: float, longitude: float, min_m: int = 60, max_m: int = 120
) -> tuple[float, float]:
    """Displace a point by a deterministic 60–120 m (about one block).

    Deterministic on ``seed`` so the marker does not jump between rebuilds, and
    irreversible in practice: the published point is not the address.
    """
    digest = hashlib.sha256(seed.strip().lower().encode("utf-8")).digest()
    angle_deg = int.from_bytes(digest[0:4], "big") % 360
    span = max(max_m - min_m, 1)
    distance_m = min_m + (int.from_bytes(digest[4:8], "big") % span)

    angle = math.radians(angle_deg)
    d_lat = (distance_m * math.cos(angle)) / METERS_PER_DEGREE_LAT
    meters_per_degree_lng = METERS_PER_DEGREE_LAT * math.cos(math.radians(latitude))
    d_lng = (distance_m * math.sin(angle)) / meters_per_degree_lng
    return round(latitude + d_lat, 6), round(longitude + d_lng, 6)


def apply_allowlist(record: dict[str, Any], allowed: Iterable[str]) -> dict[str, Any]:
    """Keep only allowed keys whose value is meaningful (``None``/``""`` dropped)."""
    allowed = set(allowed)
    return {k: v for k, v in record.items() if k in allowed and v not in (None, "")}


class PrivacyViolation(AssertionError):
    """Raised when a payload about to be published still carries private data."""


def assert_payload_is_safe(payload: Any, path: str = "payload") -> None:
    """Fail loudly if a forbidden key, an e-mail or a street address survived.

    Called by the renderer right before serialisation, so a leak breaks the
    build instead of shipping.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in FORBIDDEN_FIELDS:
                raise PrivacyViolation(f"forbidden field {key!r} at {path}")
            assert_payload_is_safe(value, f"{path}.{key}")
    elif isinstance(payload, (list, tuple)):
        for index, item in enumerate(payload):
            assert_payload_is_safe(item, f"{path}[{index}]")
    elif isinstance(payload, str):
        if _RE_EMAIL.search(payload):
            raise PrivacyViolation(f"e-mail address at {path}")
        if _RE_STREET_ADDRESS.search(payload):
            raise PrivacyViolation(f"street address at {path}")


def scan_text_for_pii(text: str) -> list[str]:
    """Report e-mail/street-address matches in arbitrary text (used by CI)."""
    findings = [m.group(0) for m in _RE_EMAIL.finditer(text)]
    findings += [m.group(0) for m in _RE_STREET_ADDRESS.finditer(text)]
    return findings
