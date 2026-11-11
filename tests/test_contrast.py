"""Text colours keep WCAG AA (4.5:1) against the surfaces they sit on.

Reads the colour tokens straight from ``web/styles.css`` for both themes, so a
palette change that breaks contrast fails here before anyone sees it. Tinted
surfaces (a tag on its own tint, the privacy note on the blue wash) are
composited the way the browser does it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STYLES = Path(__file__).resolve().parents[1] / "web" / "styles.css"
AA = 4.5

RGB = tuple[float, float, float]


def theme_tokens(css: str, selector: str) -> dict[str, str]:
    block = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", css, re.S)
    assert block, f"{selector} not found in styles.css"
    return dict(re.findall(r"--([\w-]+):\s*([^;]+);", block.group(1)))


def themes() -> dict[str, dict[str, str]]:
    css = STYLES.read_text(encoding="utf-8")
    dark = theme_tokens(css, ":root")
    light = {**dark, **theme_tokens(css, ':root[data-theme="light"]')}
    return {"dark": dark, "light": light}


def parse(value: str) -> tuple[RGB, float]:
    value = value.strip()
    if value.startswith("#"):
        digits = value[1:]
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)), 1.0
    numbers = [float(n) for n in re.findall(r"[\d.]+", value)]
    return (numbers[0], numbers[1], numbers[2]), (numbers[3] if len(numbers) > 3 else 1.0)


def over(top: RGB, alpha: float, under: RGB) -> RGB:
    return (
        top[0] * alpha + under[0] * (1 - alpha),
        top[1] * alpha + under[1] * (1 - alpha),
        top[2] * alpha + under[2] * (1 - alpha),
    )


def luminance(rgb: RGB) -> float:
    def channel(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: RGB, b: RGB) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def solid(tokens: dict[str, str], name: str) -> RGB:
    rgb, alpha = parse(tokens[name])
    assert alpha == 1.0, f"--{name} is not opaque"
    return rgb


def tint(tokens: dict[str, str], name: str, under: str, share: float | None = None) -> RGB:
    """--name laid over --under: by its own alpha, or mixed at ``share``."""
    rgb, alpha = parse(tokens[name])
    return over(rgb, share if share is not None else alpha, solid(tokens, under))


# (what, text token, surface builder)
PAIRS = [
    ("body text on the page", "text", lambda t: solid(t, "bg")),
    ("body text on a card", "text", lambda t: solid(t, "card")),
    ("muted text on the panel", "muted", lambda t: solid(t, "bg2")),
    ("muted text on a card", "muted", lambda t: solid(t, "card")),
    ("muted text on an inner surface", "muted", lambda t: solid(t, "card2")),
    ("privacy note on its blue wash", "muted", lambda t: tint(t, "blue-dim", "bg2")),
    ("cyan links and titles on a card", "cyan", lambda t: solid(t, "card")),
    ("cyan link in the footer, on the panel", "cyan", lambda t: solid(t, "bg2")),
    ("cyan tag on its tint", "cyan", lambda t: tint(t, "cyan", "card2", share=0.12)),
    ("approx. tag on its orange tint", "warn-text", lambda t: tint(t, "orange", "card2", share=0.16)),
    ("alert title on its orange tint", "warn-text", lambda t: tint(t, "orange", "card", share=0.12)),
]


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize(("what", "token", "surface"), PAIRS, ids=[pair[0] for pair in PAIRS])
def test_text_keeps_aa_contrast(theme, what, token, surface):
    tokens = themes()[theme]
    measured = ratio(solid(tokens, token), surface(tokens))
    assert measured >= AA, f"{theme}: {what} is {measured:.2f}:1, below {AA}:1"
