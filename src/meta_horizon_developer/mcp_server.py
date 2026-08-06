"""Read-only stdio MCP facade for the Meta Horizon collector.

The target application and storage location come from startup arguments. Authentication,
arbitrary URLs, arbitrary operations, mutations, and Cookie/token retrieval are not
exposed (ADR: meta-horizon-portable-collector).
"""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP

from meta_horizon_developer import core

INSTRUCTIONS = (
    "Read aggregate data from the Meta Horizon Dashboard. Mutations, arbitrary URLs, and "
    "Cookie/token retrieval are not provided. Authentication is handled by the host-side "
    "meta-horizon-developer auth command."
)


def build_server(settings: core.Settings) -> FastMCP:
    """Build a read-only server bound to one `Settings` instance."""
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
        description="Read Meta Horizon aggregate values through a read-only stdio MCP server",
    )
    parser.add_argument("--app-id", help="Target application ID (default: META_HORIZON_APP_ID)")
    parser.add_argument("--snapshot-dir", help="Directory containing snapshots to read")
    parser.add_argument("--profile-dir", help="Dedicated Chromium profile directory")
    parser.add_argument(
        "--lookback-days", type=int, help="Collection lookback period (default: 120)"
    )
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
