# Vendored Leaflet 1.9.4

`leaflet.js` and `leaflet.css` are copied verbatim from the official npm
tarball (`https://registry.npmjs.org/leaflet/-/leaflet-1.9.4.tgz`, `dist/`).
Leaflet is BSD-2-Clause licensed; the upstream `LICENSE` is kept next to the
files.

They are vendored rather than downloaded at build time so that:

- the build has no network dependency and is reproducible offline;
- the exact bytes that end up in every generated map are auditable in the
  repository, verified against `CHECKSUMS.txt` on every build
  (`rendering/standalone.py` refuses to bundle on a mismatch).

The published Subresource Integrity hashes for this release match these files:

| File          | SHA-256 (SRI)                                      |
| ------------- | -------------------------------------------------- |
| `leaflet.js`  | `sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=` |
| `leaflet.css` | `sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=` |

Leaflet's marker images (`dist/images/`) are not vendored: the map draws only
`circleMarker` and GeoJSON layers, so no image asset is ever requested.

## Updating

```bash
python tools/vendor_leaflet.py --version 1.9.4   # refetch + rewrite CHECKSUMS.txt
```
