"""Bundling ``web/`` plus the payload into one self-contained HTML file.

The output opens by double-click, with no server and no build step: the only
thing it still fetches is the basemap tiles. Assets are inlined from the local
tree — nothing is downloaded at build time — and the vendored copies (Leaflet
and the fonts) are checksum-verified before being embedded.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

PAYLOAD_START = "/*__PAYLOAD_START__*/"
PAYLOAD_END = "/*__PAYLOAD_END__*/"
CHECKSUMS_FILE = "CHECKSUMS.txt"

_RE_STYLESHEET = re.compile(r'[ \t]*<link rel="stylesheet" href="\./([^"]+)"\s*/?>\n?')
_RE_SCRIPT = re.compile(r'[ \t]*<script src="\./([^"]+)"></script>\n?')
#: ``url("./vendor/…")`` inside a stylesheet: a font or image the CSS loads by
#: relative path, which a single file has to carry as a ``data:`` URI instead.
_RE_CSS_URL = re.compile(r"""url\((["']?)\./([^"')]+)\1\)""")
_MEDIA_TYPES = {".woff2": "font/woff2", ".woff": "font/woff", ".svg": "image/svg+xml", ".png": "image/png"}


class BundleError(RuntimeError):
    """Raised when the web assets cannot be bundled as they are."""


def js_safe(text: str) -> str:
    """``</`` inside a ``<script>`` block would end the block early."""
    return text.replace("</", "<\\/")


def verify_vendor_checksums(web_dir: Path) -> dict[str, str]:
    """Check every vendored asset against ``CHECKSUMS.txt``.

    Vendoring instead of downloading from a CDN removes a build-time network
    dependency; the checksums are what make the vendored copy auditable.
    """
    verified: dict[str, str] = {}
    for checksums in sorted(web_dir.glob(f"vendor/*/{CHECKSUMS_FILE}")):
        for line in checksums.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            expected, _, name = line.partition("  ")
            asset = checksums.parent / name.strip()
            if not asset.exists():
                raise BundleError(f"vendored asset listed in {checksums} is missing: {name}")
            actual = hashlib.sha256(asset.read_bytes()).hexdigest()
            if actual != expected.strip():
                raise BundleError(
                    f"checksum mismatch for {asset}: expected {expected.strip()}, got {actual}"
                )
            # POSIX separators, so the keys are the same on Windows and Linux.
            verified[asset.relative_to(web_dir).as_posix()] = actual
    return verified


def _read_asset(web_dir: Path, relative: str) -> str:
    path = web_dir / relative
    if not path.exists():
        raise BundleError(f"asset referenced by index.html not found: {relative}")
    return path.read_text(encoding="utf-8")


def _inline_css(web_dir: Path, relative: str) -> str:
    """A stylesheet with every relative ``url(./…)`` turned into a data URI.

    Relative URLs resolve against the stylesheet's own folder, as a browser
    would resolve them when the page is served from ``web/``.
    """
    css = _read_asset(web_dir, relative)
    base = (web_dir / relative).parent

    def embed(match: re.Match[str]) -> str:
        asset = base / match.group(2)
        media_type = _MEDIA_TYPES.get(asset.suffix.lower())
        if media_type is None:
            raise BundleError(f"cannot inline {match.group(2)} (referenced by {relative}): unknown media type")
        if not asset.exists():
            raise BundleError(f"asset referenced by {relative} not found: {match.group(2)}")
        encoded = base64.b64encode(asset.read_bytes()).decode("ascii")
        return f'url("data:{media_type};base64,{encoded}")'

    return _RE_CSS_URL.sub(embed, css)


def render_standalone(payload: dict[str, Any], web_dir: Path, output_path: Path) -> Path:
    """Write the single-file map and return its path."""
    web_dir = Path(web_dir)
    template_path = web_dir / "index.html"
    if not template_path.exists():
        raise BundleError(f"template not found: {template_path}")
    verify_vendor_checksums(web_dir)

    html = template_path.read_text(encoding="utf-8")
    html = _RE_STYLESHEET.sub(lambda m: f"<style>\n{_inline_css(web_dir, m.group(1))}\n</style>\n", html)
    html = _RE_SCRIPT.sub(lambda m: f"<script>\n{js_safe(_read_asset(web_dir, m.group(1)))}\n</script>\n", html)

    serialized = js_safe(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    pattern = re.compile(re.escape(PAYLOAD_START) + r".*?" + re.escape(PAYLOAD_END), re.S)
    if not pattern.search(html):
        raise BundleError(f"payload markers {PAYLOAD_START}…{PAYLOAD_END} not found in index.html")
    html = pattern.sub(lambda _: PAYLOAD_START + serialized + PAYLOAD_END, html, count=1)

    if '<link rel="stylesheet" href="./' in html or '<script src="./' in html or _RE_CSS_URL.search(html):
        raise BundleError("some local assets were not inlined; the output would not be self-contained")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
