"""`meta-horizon-developer` コマンド。収集の owner が使う入口。

読み取り専用。Mutation も任意 URL の取得も持たない。
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
meta-horizon-developer — Meta Horizon Dashboard の集計値を読み取り専用で収集する

  meta-horizon-developer status                     認証の有無と最新スナップショットの健全性
  meta-horizon-developer auth                       専用ブラウザを開いて人手でログインする
  meta-horizon-developer collect                    許可された Query を収集して不変 JSON へ保存
  meta-horizon-developer capture-csv                チャート CSV を人手でダウンロードして保存
  meta-horizon-developer read <operation>           最新スナップショットから 1 operation を読む
  meta-horizon-developer migrate-profile --source D 既存の専用 profile を移設する

対象アプリと置き場は --app-id / --snapshot-dir / --profile-dir で渡す
(環境変数 META_HORIZON_APP_ID / _OUTPUT_DIR / _PROFILE_DIR でも可)。
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
    parser.add_argument("--app-id", help="対象アプリの ID (既定: META_HORIZON_APP_ID)")
    parser.add_argument("--snapshot-dir", help="不変スナップショットの保存先")
    parser.add_argument("--profile-dir", help="Chromium の専用 profile。cookie を含む")
    parser.add_argument("--csv-dir", help="capture-csv の保存先")
    parser.add_argument("--lookback-days", type=int, help="Dashboard へ渡す集計期間 (既定 120)")
    sub = parser.add_subparsers(dest="action")
    sub.add_parser("status", help="認証の有無と最新スナップショットの健全性")
    sub.add_parser("auth", help="専用ブラウザを開いて人手でログインする")
    sub.add_parser("collect", help="許可された Query を収集して不変 JSON へ保存する")
    sub.add_parser("capture-csv", help="チャート CSV を人手でダウンロードして保存する")
    read = sub.add_parser("read", help="最新スナップショットから 1 operation を読む")
    read.add_argument("operation")
    migrate = sub.add_parser("migrate-profile", help="既存の専用 profile を移設する")
    migrate.add_argument("--source", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = _settings(args)
    except (ValueError, KeyError, OSError) as exc:
        print(f"設定が解決できない: {exc}", file=sys.stderr)
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
