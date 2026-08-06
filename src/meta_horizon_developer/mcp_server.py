"""Read-only stdio MCP facade for the Meta Horizon collector.

対象アプリと置き場は起動引数で決まる。認証、任意 URL、任意 operation、
Mutation、Cookie/token の取得は公開しない (ADR: meta-horizon-portable-collector)。
"""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP

from meta_horizon_developer import core

INSTRUCTIONS = (
    "Meta Horizon Dashboard の集計データを読み取る。Mutation、任意URL、"
    "Cookie/tokenの取得は提供しない。認証はホスト側の meta-horizon-developer auth で行う。"
)


def build_server(settings: core.Settings) -> FastMCP:
    """1 つの `Settings` に束縛した read-only サーバを組み立てる。"""
    mcp = FastMCP(
        f"meta-horizon-developer-{settings.app_id}",
        instructions=INSTRUCTIONS,
        json_response=True,
    )

    @mcp.tool()
    def meta_horizon_developer_status() -> dict:
        """Return authentication presence and latest collection health."""
        return core.status(settings)

    @mcp.tool()
    async def meta_horizon_developer_collect() -> dict:
        """Collect allow-listed aggregate GraphQL Queries into a new snapshot."""
        path = await core.collect(settings)
        result = core.status(settings)
        result["snapshot"] = str(path)
        return result

    @mcp.tool()
    def meta_horizon_developer_latest_snapshot() -> dict:
        """Return latest snapshot metadata and collection health, not the large payload."""
        return core.status(settings)

    @mcp.tool()
    def meta_horizon_developer_read_operation(operation_name: str) -> dict:
        """Read one allow-listed aggregate Query from the latest redacted snapshot."""
        return core.read_operation(operation_name, settings)

    return mcp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meta-horizon-developer-mcp",
        description="Meta Horizon の集計値を読む read-only stdio MCP サーバ",
    )
    parser.add_argument("--app-id", help="対象アプリの ID (既定: META_HORIZON_APP_ID)")
    parser.add_argument("--snapshot-dir", help="読み取るスナップショットの置き場")
    parser.add_argument("--profile-dir", help="Chromium の専用 profile")
    parser.add_argument("--lookback-days", type=int, help="collect が渡す集計期間 (既定 120)")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = core.load_settings(
        app_id=args.app_id,
        snapshot_dir=args.snapshot_dir,
        profile_dir=args.profile_dir,
        lookback_days=args.lookback_days,
    )
    build_server(settings).run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
