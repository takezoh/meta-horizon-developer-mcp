"""Read-only stdio MCP facade for the Meta Horizon developer Dashboard collector.

The target application and storage location are **tool arguments**, not startup
arguments. Splitting the server per application would not reduce credential exposure —
every instance opens the same Chromium profile and the same cookies — and the only
thing it would isolate is `app_id`, which is not a secret. One process serves all
applications, and the host that launches it needs no knowledge of its callers.

Authentication, arbitrary URLs, arbitrary operations, mutations, and Cookie/token
retrieval are not exposed (ADR: meta-horizon-portable-collector).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from meta_horizon_developer import core

INSTRUCTIONS = (
    "Read aggregate data from the Meta Horizon developer Dashboard. Every tool takes the "
    "target app_id and snapshot directory as arguments. Mutations, arbitrary URLs, and "
    "Cookie/token retrieval are not provided. Authentication is handled by the host-side "
    "meta-horizon-developer auth command."
)

mcp = FastMCP("meta-horizon-developer", instructions=INSTRUCTIONS, json_response=True)


def _settings(
    app_id: str,
    snapshot_dir: str,
    profile_dir: str | None = None,
    lookback_days: int | None = None,
) -> core.Settings:
    return core.load_settings(
        app_id=app_id,
        snapshot_dir=snapshot_dir,
        profile_dir=profile_dir,
        lookback_days=lookback_days,
    )


@mcp.tool()
def meta_horizon_developer_status(app_id: str, snapshot_dir: str) -> dict:
    """Return latest collection health for one application.

    app_id: target application ID.
    snapshot_dir: directory holding that application's snapshots.
    """
    return core.status(_settings(app_id, snapshot_dir))


@mcp.tool()
async def meta_horizon_developer_collect(
    app_id: str,
    snapshot_dir: str,
    profile_dir: str,
    lookback_days: int | None = None,
) -> dict:
    """Collect allow-listed aggregate GraphQL Queries into a new snapshot.

    Requires profile_dir because it drives an authenticated browser. Fails immediately
    with ProfileBusyError when another process already holds that profile.
    """
    settings = _settings(app_id, snapshot_dir, profile_dir, lookback_days)
    path = await core.collect(settings)
    result = core.status(settings)
    result["snapshot"] = str(path)
    return result


@mcp.tool()
def meta_horizon_developer_latest_snapshot(app_id: str, snapshot_dir: str) -> dict:
    """Return latest snapshot metadata and collection health, not the large payload."""
    return core.status(_settings(app_id, snapshot_dir))


@mcp.tool()
def meta_horizon_developer_read_operation(
    operation_name: str, app_id: str, snapshot_dir: str
) -> dict:
    """Read one allow-listed aggregate Query from the latest redacted snapshot."""
    return core.read_operation(operation_name, _settings(app_id, snapshot_dir))


def main() -> None:
    """Start the server. It takes no arguments: every target is chosen per call."""
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
