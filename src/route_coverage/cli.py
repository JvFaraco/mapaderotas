"""Command line interface: ``geocode``, ``dedupe``, ``build`` and ``demo``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .cache import GeocodeCache, deduplicate
from .config import Settings, load_settings
from .demo import run_demo_geocoding
from .inputs import InputError, read_routes, read_specialists
from .models import CoverageStats
from .pipeline import run_geocoding
from .rendering.payload import build_payload
from .rendering.standalone import BundleError, render_standalone


def _log(message: str) -> None:
    print(message, flush=True)


def _report_stats(stats: CoverageStats) -> None:
    data = stats.as_dict()
    _log("-" * 60)
    _log(
        f"specialists={data['specialists']} cities={data['cities']} districts={data['districts']} "
        f"geocoded={data['geocoded']} cache_hits={data['cache_hits']} "
        f"cache_hit_rate={data['cache_hit_rate']:.0%}"
    )
    _log(
        f"real geometries={data['real_geometries']} fallback={data['fallback_geometries']} "
        f"overlaps clipped={data['overlaps_clipped']} duplicates removed={data['duplicates_removed']}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="route_coverage",
        description="Geospatial route coverage pipeline: geocode, deduplicate and render a privacy-safe map.",
    )
    parser.add_argument("--version", action="version", version=f"route-coverage {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    geocode = subparsers.add_parser("geocode", help="resolve coordinates and polygons into the cache")
    geocode.add_argument("--routes", type=Path, required=True, help="routes file (.csv or .xlsx)")
    geocode.add_argument("--specialists", type=Path, required=True, help="specialists file (.csv or .xlsx)")
    geocode.add_argument("--cache", type=Path, default=None, help="cache path (default: CACHE_PATH)")
    geocode.add_argument("--report", type=Path, default=None, help="write the per-district audit CSV here")

    dedupe = subparsers.add_parser("dedupe", help="merge cache entries that differ only by casing/accents")
    dedupe.add_argument("--cache", type=Path, default=None)

    build = subparsers.add_parser("build", help="render the standalone HTML map from a cache")
    build.add_argument("--cache", type=Path, default=None)
    build.add_argument("--out", type=Path, default=None, help="output HTML (default: OUTPUT_PATH)")
    build.add_argument("--demo", action="store_true", help="use the synthetic demo dataset, no network")
    build.add_argument("--no-clip", action="store_true", help="skip overlap clipping (debugging aid)")
    build.add_argument("--stats-json", type=Path, default=None, help="also write build metrics as JSON")

    demo = subparsers.add_parser("demo", help="shortcut for 'build --demo'")
    demo.add_argument("--out", type=Path, default=None)
    demo.add_argument("--stats-json", type=Path, default=None)

    return parser


def _cache_path(settings: Settings, override: Path | None) -> Path:
    return override or settings.cache_path


def command_geocode(args: argparse.Namespace, settings: Settings) -> int:
    from .geocoding.ibge import IbgeProvider
    from .geocoding.nominatim import NominatimProvider

    areas = read_routes(args.routes)
    specialists = read_specialists(args.specialists)
    _log(f"routes: {len(areas)} covered area(s) | specialists: {len(specialists)}")

    cache = GeocodeCache.load(_cache_path(settings, args.cache))
    stats, report = run_geocoding(
        cache, specialists, areas, NominatimProvider(settings), IbgeProvider(settings), _log
    )
    if args.report:
        report.to_csv(args.report)
        _log(f"audit report written to {args.report}")
    _report_stats(stats)
    _log(f"cache written to {cache.path}")
    return 0


def command_dedupe(args: argparse.Namespace, settings: Settings) -> int:
    cache = GeocodeCache.load(_cache_path(settings, args.cache))
    stats = deduplicate(cache)
    cache.save(force=True)
    _log(
        f"[dedupe] {stats.duplicates_removed} duplicate(s) merged; "
        f"{stats.cities} city key(s), {stats.districts} district key(s) remain"
    )
    return 0


def command_build(args: argparse.Namespace, settings: Settings) -> int:
    if getattr(args, "demo", False):
        _log("building from the synthetic demo dataset (no network access)")
        cache, geocoding_stats, _ = run_demo_geocoding(settings, _log)
        _report_stats(geocoding_stats)
    else:
        path = _cache_path(settings, args.cache)
        if not path.exists():
            _log(f"ERROR: cache not found at {path}. Run 'geocode' first, or use --demo.")
            return 1
        cache = GeocodeCache.load(path)

    payload, stats = build_payload(cache, clip=not getattr(args, "no_clip", False))
    output = render_standalone(payload, settings.web_dir, args.out or settings.output_path)
    _report_stats(stats)
    _log(f"map written to {output} ({output.stat().st_size / 1e6:.2f} MB)")

    if args.stats_json:
        args.stats_json.parent.mkdir(parents=True, exist_ok=True)
        args.stats_json.write_text(json.dumps(stats.as_dict(), indent=2), encoding="utf-8")
        _log(f"metrics written to {args.stats_json}")
    return 0


def command_demo(args: argparse.Namespace, settings: Settings) -> int:
    args.demo = True
    args.cache = None
    args.no_clip = False
    return command_build(args, settings)


COMMANDS = {
    "geocode": command_geocode,
    "dedupe": command_dedupe,
    "build": command_build,
    "demo": command_demo,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    try:
        return COMMANDS[args.command](args, settings)
    except (InputError, BundleError, FileNotFoundError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
