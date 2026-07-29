"""The tests that must fail if private data ever reaches the published map."""

from __future__ import annotations

import json
import math
import re

import pytest

from route_coverage.cache import GeocodeCache
from route_coverage.privacy import (
    PrivacyViolation,
    apply_allowlist,
    assert_payload_is_safe,
    block_jitter,
    scan_text_for_pii,
)
from route_coverage.rendering.payload import build_payload

FORBIDDEN_IN_OUTPUT = ("street", "rua", "number", "numero", "name", "nome", "email", "supervisor")


def haversine_m(a, b):
    lat1, lng1 = map(math.radians, a)
    lat2, lng2 = map(math.radians, b)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def test_block_jitter_moves_the_point_between_60_and_120_metres():
    origin = (-27.95, -46.30)
    moved = block_jitter("TECH-001", *origin)
    assert moved != origin
    assert 55 <= haversine_m(origin, moved) <= 125


def test_block_jitter_is_deterministic_but_differs_per_seed():
    assert block_jitter("TECH-001", -27.95, -46.30) == block_jitter("TECH-001", -27.95, -46.30)
    assert block_jitter("TECH-001", -27.95, -46.30) != block_jitter("TECH-002", -27.95, -46.30)


def test_allowlist_drops_everything_it_does_not_know():
    record = {"specialist_id": "TECH-001", "street": "Rua A", "number": "10", "region": "SOUTH", "city": ""}
    assert apply_allowlist(record, {"specialist_id", "region", "city"}) == {
        "specialist_id": "TECH-001",
        "region": "SOUTH",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"specialists": [{"name": "Someone"}]},
        {"specialists": [{"street": "Rua das Flores"}]},
        {"contact": "someone@example.org"},
        {"districts": [{"district": "Rua das Flores, 120"}]},
        {"nested": {"list": [{"supervisor": "Someone"}]}},
    ],
)
def test_assert_payload_is_safe_rejects_private_data(payload):
    with pytest.raises(PrivacyViolation):
        assert_payload_is_safe(payload)


def test_assert_payload_is_safe_accepts_a_clean_record():
    assert_payload_is_safe(
        {
            "specialist_id": "TECH-001",
            "route_id": "ROUTE-SOUTH-01",
            "region": "SOUTH",
            "latitude": -27.60,
            "longitude": -48.55,
            "location_precision": "district",
        }
    )


def test_payload_built_from_a_cache_carrying_private_fields_is_clean(tmp_path):
    """Even if the cache is polluted, the allowlist keeps the payload clean."""
    cache = GeocodeCache(tmp_path / "cache.json")
    cache.specialists["TECH-001"] = {
        "specialist_id": "TECH-001",
        "route_id": "ROUTE-SOUTH-01",
        "region": "SOUTH",
        "city": "Porto Aurora",
        "state": "SC",
        "latitude": -27.95,
        "longitude": -46.30,
        "location_precision": "block",
        # These must not survive:
        "name": "Someone Real",
        "street": "Rua das Flores",
        "number": "120",
        "supervisor": "Someone Else",
        "email": "someone@example.org",
    }
    payload, _ = build_payload(cache)
    assert payload["specialists"] == [
        {
            "specialist_id": "TECH-001",
            "route_id": "ROUTE-SOUTH-01",
            "region": "SOUTH",
            "city": "Porto Aurora",
            "state": "SC",
            "latitude": -27.95,
            "longitude": -46.3,
            "location_precision": "block",
        }
    ]


def test_demo_build_output_contains_no_private_field(demo_map_html):
    lowered = demo_map_html.lower()
    payload = re.search(r"/\*__PAYLOAD_START__\*/(.*?)/\*__PAYLOAD_END__\*/", demo_map_html, re.S)
    assert payload, "the built map must carry an inlined payload"
    data = json.loads(payload.group(1).replace("<\\/", "</"))

    for collection in ("specialists", "cities", "districts"):
        for record in data[collection]:
            for field in FORBIDDEN_IN_OUTPUT:
                assert field not in record, f"{field} leaked into {collection}"

    assert not scan_text_for_pii(json.dumps(data, ensure_ascii=False))
    # The word "email" may legitimately appear in Leaflet's bundled code, but
    # no actual address should be anywhere in the file.
    assert "@" not in lowered.split("/*__payload_start__*/")[1].split("/*__payload_end__*/")[0]
