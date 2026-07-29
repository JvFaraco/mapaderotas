#!/usr/bin/env python3
"""Fail the build if personal data shows up in the tracked tree or in a build.

Deliberately blunt: e-mail addresses and street-address shapes are never
legitimate content here, so any hit is a finding. Runs in CI over the tracked
files and over the generated demo map.

    python tools/scan_pii.py [path ...]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from route_coverage.privacy import scan_text_for_pii  # noqa: E402

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".zip", ".pdf", ".woff", ".woff2"}
SKIP_PARTS = {".git", "data/private", "web/vendor", ".venv", "node_modules", "__pycache__"}
#: Placeholders that document configuration; they are not contact details.
ALLOWED_EMAILS = {"contato@example.com", "you@example.com", "user@example.com"}
#: Files where an address-shaped string is legitimate: the fictional datasets
#: (which document the input format) and the privacy test, which must contain
#: fake personal data to prove the detector rejects it. Rather than skipping
#: these files — which would also skip anything else hiding in them — the exact
#: strings they contain are allowed everywhere, and any *other* address or
#: e-mail is still a finding, including in the README that quotes them.
FIXTURE_FILES = (
    "data/demo/specialists.csv",
    "data/input/specialists.example.csv",
    "tests/test_privacy.py",
)


def fixture_allowlist(root: Path) -> set[str]:
    """Every address-shaped string present in the synthetic fixtures."""
    allowed: set[str] = set()
    for relative in FIXTURE_FILES:
        path = root / relative
        if path.exists():
            allowed.update(scan_text_for_pii(path.read_text(encoding="utf-8")))
    return allowed


def tracked_files() -> list[Path]:
    result = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return [Path(line) for line in result.stdout.splitlines() if line]


def scan(paths: list[Path], allowed: set[str]) -> list[tuple[Path, str]]:
    findings: list[tuple[Path, str]] = []
    for path in paths:
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        if any(part in str(path) for part in SKIP_PARTS):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        findings += [(path, hit) for hit in scan_text_for_pii(text) if hit not in allowed]
    return findings


def main(argv: list[str]) -> int:
    paths = [Path(arg) for arg in argv] if argv else tracked_files()
    expanded: list[Path] = []
    for path in paths:
        expanded.extend(sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path])

    root = Path(__file__).resolve().parents[1]
    allowed = ALLOWED_EMAILS | fixture_allowlist(root)
    findings = scan(expanded, allowed)
    if findings:
        print("Possible personal data found:", file=sys.stderr)
        for path, hit in findings:
            print(f"  {path}: {hit}", file=sys.stderr)
        return 1
    print(f"No personal data patterns found in {len(expanded)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
