#!/usr/bin/env python3
"""Refresh the vendored Leaflet copy from the npm registry.

Downloads the official tarball, extracts ``dist/leaflet.js`` and
``dist/leaflet.css`` plus the licence, and rewrites ``CHECKSUMS.txt``. Run it
only when bumping the version; the build itself never touches the network.

    python tools/vendor_leaflet.py --version 1.9.4
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import tarfile
import urllib.request
from pathlib import Path

REGISTRY = "https://registry.npmjs.org/leaflet/-/leaflet-{version}.tgz"
WANTED = {
    "package/dist/leaflet.js": "leaflet.js",
    "package/dist/leaflet.css": "leaflet.css",
    "package/LICENSE": "LICENSE",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="1.9.4")
    parser.add_argument("--dest", type=Path, default=Path(__file__).resolve().parents[1] / "web" / "vendor" / "leaflet")
    args = parser.parse_args()

    url = REGISTRY.format(version=args.version)
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=60) as response:
        blob = response.read()

    args.dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        for member, name in WANTED.items():
            extracted = archive.extractfile(member)
            if extracted is None:
                print(f"ERROR: {member} not found in the tarball", file=sys.stderr)
                return 1
            target = args.dest / name
            target.write_bytes(extracted.read())
            written.append(target)
            print(f"wrote {target}")

    checksums = args.dest / "CHECKSUMS.txt"
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in written
        if path.suffix in (".js", ".css")
    ]
    checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {checksums}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
