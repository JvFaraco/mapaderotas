"""Bundling ``web/`` plus the payload into one self-contained HTML file.

The output opens by double-click, with no server and no build step: the only
thing it still fetches is the basemap tiles. Assets are inlined from the local
tree — nothing is downloaded at build time — and the vendored Leaflet copy is
checksum-verified before being embedded.
"""

from __future__ import annotations

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
            verified[str(asset.relative_to(web_dir))] = actual
    return verified


def _read_asset(web_dir: Path, relative: str) -> str:
    path = web_dir / relative
    if not path.exists():
        raise BundleError(f"asset referenced by index.html not found: {relative}")
    return path.read_text(encoding="utf-8")


def render_standalone(payload: dict[str, Any], web_dir: Path, output_path: Path) -> Path:
    """Write the single-file map and return its path."""
    web_dir = Path(web_dir)
    template_path = web_dir / "index.html"
    if not template_path.exists():
        raise BundleError(f"template not found: {template_path}")
    verify_vendor_checksums(web_dir)

    html = template_path.read_text(encoding="utf-8")
    html = _RE_STYLESHEET.sub(lambda m: f"<style>\n{_read_asset(web_dir, m.group(1))}\n</style>\n", html)
    html = _RE_SCRIPT.sub(lambda m: f"<script>\n{js_safe(_read_asset(web_dir, m.group(1)))}\n</script>\n", html)

    serialized = js_safe(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    pattern = re.compile(re.escape(PAYLOAD_START) + r".*?" + re.escape(PAYLOAD_END), re.S)
    if not pattern.search(html):
        raise BundleError(f"payload markers {PAYLOAD_START}…{PAYLOAD_END} not found in index.html")
    html = pattern.sub(lambda _: PAYLOAD_START + serialized + PAYLOAD_END, html, count=1)

    if "<link rel=\"stylesheet\" href=\"./" in html or "<script src=\"./" in html:
        raise BundleError("some local assets were not inlined; the output would not be self-contained")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
