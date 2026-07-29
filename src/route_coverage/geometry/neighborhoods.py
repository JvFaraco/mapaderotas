"""Matching an input district name against an official district gazetteer.

Input spreadsheets carry informal names ("Jd Sta Genebra II (Barao Geraldo)")
while the census file carries the legal one, so an exact match is the
exception. Matching is: exact on the normalized key, then on any safe name
variant, then fuzzy above a deliberately high threshold.
"""

from __future__ import annotations

from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import Any

from ..normalization import match_key, name_variants

#: Below this similarity a match is more likely wrong than right; the caller
#: falls back to Voronoi, which is honest about being an approximation.
FUZZY_THRESHOLD = 0.88


def match_district(
    district: str,
    records: Sequence[dict[str, Any]],
    city_code: str | None = None,
    threshold: float = FUZZY_THRESHOLD,
) -> tuple[dict[str, Any] | None, float, list[str]]:
    """Return ``(record, score, near_misses)`` for the best candidate.

    ``near_misses`` is what a human needs to audit a failure, and is written
    to the run report rather than silently dropped.
    """
    candidates = [r for r in records if not city_code or str(r.get("city_code", "")).startswith(str(city_code))]
    if not candidates:
        return None, 0.0, []

    targets = [match_key(district)] + [match_key(variant) for variant in name_variants(district)]
    for target in targets:
        for record in candidates:
            if record.get("match_key") == target:
                return record, 1.0, []

    best: dict[str, Any] | None = None
    best_score = 0.0
    for record in candidates:
        score = max(SequenceMatcher(None, target, record.get("match_key", "")).ratio() for target in targets)
        if score > best_score:
            best, best_score = record, score
    if best is not None and best_score >= threshold:
        return best, round(best_score, 3), []

    near = sorted(
        candidates,
        key=lambda record: -SequenceMatcher(None, targets[0], record.get("match_key", "")).ratio(),
    )[:3]
    return None, round(best_score, 3), [str(record.get("name", "")) for record in near]
