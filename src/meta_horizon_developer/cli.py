"""`meta-horizon-developer` command used by the collection owner.

Read-only. It does not support mutations or fetching arbitrary URLs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from meta_horizon_developer import core

USAGE = """\
meta-horizon-developer — Collect Meta Horizon Dashboard aggregates in read-only mode

  meta-horizon-developer status                     Show auth status and latest snapshot health
  meta-horizon-developer auth                       Open the dedicated browser for manual login
  meta-horizon-developer collect                    Collect allowed Queries into immutable JSON
  meta-horizon-developer capture-csv                Manually download and save chart CSV files
  meta-horizon-developer read <operation>           Read one operation from the latest snapshot
  meta-horizon-developer migrate-profile --source D Move an existing dedicated profile

Pass the target application and locations with --app-id / --snapshot-dir / --profile-dir
(or use the META_HORIZON_APP_ID / _OUTPUT_DIR / _PROFILE_DIR environment variables).
"""

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_CONFIG = 2


def _dump(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _settings(args: argparse.Namespace) -> core.Settings:
    return core.load_settings(
        app_id=args.app_id,
        snapshot_dir=args.snapshot_dir,
        profile_dir=args.profile_dir,
        csv_dir=args.csv_dir,
        lookback_days=args.lookback_days,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meta-horizon-developer",
        description=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--app-id", help="Target application ID (default: META_HORIZON_APP_ID)")
    parser.add_argument("--snapshot-dir", help="Directory for immutable snapshots")
    parser.add_argument(
        "--profile-dir", help="Dedicated Chromium profile directory containing cookies"
    )
    parser.add_argument("--csv-dir", help="Directory for capture-csv output")
    parser.add_argument(
        "--lookback-days", type=int, help="Dashboard lookback period (default: 120)"
    )
    sub = parser.add_subparsers(dest="action")
    sub.add_parser("status", help="Show auth status and latest snapshot health")
    sub.add_parser("auth", help="Open the dedicated browser for manual login")
    sub.add_parser("collect", help="Collect allowed Queries into immutable JSON")
    sub.add_parser("capture-csv", help="Manually download and save chart CSV files")
    read = sub.add_parser("read", help="Read one operation from the latest snapshot")
    read.add_argument("operation")
    migrate = sub.add_parser("migrate-profile", help="Move an existing dedicated profile")
    migrate.add_argument("--source", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = _settings(args)
    except (ValueError, KeyError, OSError) as exc:
        print(f"Could not resolve settings: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    try:
        if args.action == "auth":
            asyncio.run(core.authenticate(settings))
            return EXIT_OK
        if args.action == "collect":
            print(asyncio.run(core.collect(settings)))
            return EXIT_OK
        if args.action == "capture-csv":
            paths = asyncio.run(core.capture_csv(settings))
            _dump({"download_count": len(paths), "files": [str(path) for path in paths]})
            return EXIT_OK if paths else EXIT_RUNTIME
        if args.action == "read":
            _dump(core.read_operation(args.operation, settings))
            return EXIT_OK
        if args.action == "migrate-profile":
            core.migrate_legacy_profile(Path(args.source), settings.profile_dir)
            print(settings.profile_dir)
            return EXIT_OK
        _dump(core.status(settings))
        return EXIT_OK
    except core.MetaHorizonError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUNTIME


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
