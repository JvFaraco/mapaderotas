# Architecture and technical decisions

This document explains *why* the pipeline looks the way it does. The README
covers what it does; this is the reasoning, including the mistakes that shaped
it.

## 1. The shape of the problem

Two tabular inputs, one map:

- `routes.csv` — one row per covered area (a district, or a whole
  municipality when the district column is empty), owned by a route.
- `specialists.csv` — one row per specialist, with the home base that becomes
  the origin marker.

Both are joined by technical identifiers: `route_id` and `specialist_id`.
Nothing in the pipeline knows a person's name, so nothing can leak one.

## 2. Pipeline stages

```
                 ┌────────────────────── run again any time ─────────────────┐
                 │                                                           │
inputs ──→ [geocode] ──→ cache ──→ [dedupe] ──→ cache ──→ [build] ──→ HTML ──┘
            │                       │                      │
     OSM + IBGE, cascaded    canonical keys      allowlist → clip → bundle
```

**geocode** is the only stage that touches the network, and it is
incremental: anything already resolved is left alone, only route metadata is
refreshed. **dedupe** is a repair stage for caches written before names were
normalized. **build** is pure computation over the cache and can be re-run
freely — it never mutates its input.

### Progressive geocoding

`address → street → district → district inside the city's bounding box →
city`. The fallback exists because address-level OpenStreetMap coverage in
Brazil is uneven: outside large cities house numbers frequently do not exist,
and a single strict query would fail for a large fraction of the rows.

The level that succeeded is recorded as `location_precision` and travels all
the way to the map legend. An approximation that says it is an approximation
is useful; one that pretends to be exact is worse than nothing.

### Three sources for district outlines

1. **OpenStreetMap, direct.** Best case: a real, mapped outline.
2. **OpenStreetMap, name variants.** Input names are informal
   (`Jd Sta Genebra II (Barao Geraldo)`). Only safe transformations are tried:
   drop the parenthesised parent, drop a trailing roman numeral, and finally
   the parent district on its own. Prefixes are never stripped — in Goiânia
   the official name really is "Setor Bueno", in Curitiba "Bairro Alto".
3. **IBGE 2022 census districts.** Official polygons, but published only for
   municipalities that formally delimited districts, so coverage is partial by
   nature. Matching is exact on the normalized key, then by variant, then
   fuzzy above 0.88 similarity — a deliberately high bar, because a wrong
   match draws a confidently incorrect polygon.

### Voronoi as the guarantee, not the goal

Whatever the cascade cannot resolve would otherwise be a lone dot. Instead,
each remaining point claims the area closer to it than to any other district
point in the same city, clipped to the municipal boundary.

Two details matter:

- Districts that already have real outlines take part in the diagram as
  anchors. Without them, generated cells would spill across their neighbours'
  real territory.
- Districts sharing an identical coordinate (which happens when the city-level
  fallback returns the same point twice) are displaced by a deterministic
  ~40–90 m before the diagram is built; identical inputs collapse a Voronoi
  diagram into a single cell. Only the point fed to the diagram moves — the
  stored coordinate does not.

### Overlap clipping

After the cascade, polygons legitimately overlap: a Voronoi cell over a
neighbour's real outline, a parent district enclosing smaller ones, several
districts resolved to the same parent polygon, a whole-city area painted under
its own districts. With semi-transparent fills, every overlap reads as a
muddled colour, and territory looks shared when it is not.

Resolution order:

1. Rank by precision — a real outline beats an approximation.
2. On a tie, the **smaller** polygon stays whole and the container gives up
   the shared area. The alternative (largest wins) erases small districts.
3. The same polygon repeated for one specialist is drawn once; stacking it
   only darkens the fill.
4. Whole-city areas are cut against the union of their districts.

In the original operational dataset this eliminated 182 overlapping pairs
between different specialists. `tests/test_overlap_clipping.py` asserts the
invariant directly: after a build, polygons of different specialists share no
area.

**Clipping happens at render time, not in the cache.** The cache stays a
faithful record of what the sources returned, so a presentation decision can
be changed or reverted without re-geocoding anything.

## 3. Ports, and why the demo is honest

The pipeline depends on two protocols (`geocoding/base.py`):

- `GeocodingProvider.search(query, geometry=..., viewbox=..., bounded=...)`
- `BoundaryProvider.municipality_code / municipality_geometry /
  district_geometries`

Production implementations are Nominatim and IBGE. The demo and the tests use
`geocoding/synthetic.py`, which generates deterministic fictional geography
from a hash of each place name. Crucially, it does not fake the *pipeline* —
it fakes only the two providers, so `build --demo` exercises the real cascade,
the real Voronoi fill and the real clipping code. Each synthetic source knows
a different ~35% of district outlines, which reproduces the situation the
fallbacks exist for.

This is what allows CI to build a real map with no network access at all.

## 4. Privacy

The interface is not the boundary; the payload is. Hiding a name on screen
while shipping it inside the embedded JSON hides nothing from anyone who opens
the page source — this was a real limitation of the earlier version of this
project, and it is the reason the data model changed.

Four mechanisms, in `privacy.py`:

| Mechanism | What it prevents |
| --- | --- |
| Technical ids as join keys | Names never enter the pipeline at all. |
| Street/number dropped after geocoding | The most sensitive field is not persisted. |
| Deterministic 60–120 m jitter, relabelled `block` | Published points are not addresses, and do not jump between rebuilds. |
| Allowlist + `assert_payload_is_safe` | New upstream fields are dropped by default; forbidden keys, e-mails and address shapes fail the build. |

An allowlist rather than a denylist is the whole point: a field nobody
anticipated is excluded automatically instead of leaking until somebody
notices.

`tools/scan_pii.py` runs the same detectors over every tracked file in CI, so
a stray address in a fixture or a doc fails the pipeline too.

## 5. The cache

A single JSON document with four sections: `specialists`, `cities`,
`districts` and `support_cities` (municipal boundaries fetched only to clip
Voronoi cells — kept apart so they are never rendered as coverage).

Design points:

- **Atomic writes.** Write to `.tmp`, `fsync`, then `os.replace`. An
  interrupted run can no longer truncate the file.
- **A rolling `.bak`.** Taken on the first write of each run; a corrupt cache
  falls back to it automatically instead of costing hours of re-geocoding.
- **Throttled saves.** At most one write every five seconds — the file reaches
  megabytes and per-item rewrites were both slow and a wider corruption
  window.
- **Geometry is permanent, metadata is not.** Route ownership changes weekly
  in the source spreadsheet; polygons do not change at all. Every run
  refreshes the former and never re-fetches the latter.
- **Canonical keys.** `Centro|Porto Aurora|SC`, normalized on write. Before
  this, `CAMPINAS` and `Campinas` produced two entries for one place, each
  with its own polygon, overlapping on the map. `dedupe` repairs caches
  written before the fix.

## 6. Output

One HTML file, opened by double-click: the audience has no Python and no local
server. The bundler inlines `web/styles.css`, `web/map.js` and the vendored
Leaflet into `web/index.html`, injects the payload between markers, and then
verifies that no local asset reference survived — if one did, the file would
not be self-contained and the build fails.

Leaflet is vendored rather than downloaded at build time (with SHA-256
checksums verified on every build) so that the build has no network
dependency and the exact bytes shipped in every map are auditable in the
repository. `tools/vendor_leaflet.py` refreshes it from the npm registry when
the version is bumped.

Coordinates are rounded to five decimals (~1 m) before serialisation, which
costs nothing visually and cuts a large share of the payload. Because that
rounding happens *after* clipping, sub-metre slivers can remain along shared
borders — the overlap test allows a relative tolerance of 1e-6 for exactly
this reason and nothing more.

## 7. Testing strategy

Tests are organised by invariant rather than by file:

| File | Invariant under protection |
| --- | --- |
| `test_normalization.py` | Case/accent variants collapse to one key; unsafe name transformations never happen. |
| `test_input_routes.py` | Loose headers, `Sum` rows, junk rows, duplicate ids. |
| `test_cache.py` | Atomicity, backup recovery, throttling, metadata-only refresh, deduplication. |
| `test_pipeline.py` | Cascade order, manual overrides, jitter, 100% cache reuse on a second run, Voronoi behaviour. |
| `test_geocoding_providers.py` | Adapter contracts against mocked HTTP/geopy. |
| `test_overlap_clipping.py` | No two specialists share area, before and after a full build. |
| `test_privacy.py` | Nothing private survives, even from a deliberately polluted cache. |
| `test_build_standalone.py` | Self-contained, deterministic, checksum-verified output. |

Everything runs offline, on synthetic fixtures.

## 8. Known limitations

- Official district coverage is partial; whole states fall back to Voronoi.
- Voronoi cells are areas of influence, not legal boundaries.
- Fuzzy matching can still pick a wrong homonym; the `--report` CSV records
  method, matched name, score and near misses so a human can audit it.
- Nominatim's ~1 request/second policy makes the first run over a large
  territory slow. Everything after that comes from the cache.
- Basemap tiles are still fetched from the network at view time.
