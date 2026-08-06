"""Read-only Meta Horizon Dashboard collector (CLI + MCP).

The caller chooses the target application (`app_id`) and storage location. Downstream
handling of collected aggregate values (such as DuckDB registration) is outside this
package's responsibility.
"""

from meta_horizon_developer.core import (
    AppIdMismatchError,
    EmptyCollectionError,
    MetaHorizonError,
    ProfileBusyError,
    Settings,
    authenticate,
    capture_csv,
    collect,
    latest_snapshot,
    load_settings,
    migrate_legacy_profile,
    page_url,
    profile_lock,
    proxy_settings,
    read_operation,
    redact,
    status,
)

__all__ = [
    "AppIdMismatchError",
    "EmptyCollectionError",
    "MetaHorizonError",
    "ProfileBusyError",
    "Settings",
    "authenticate",
    "capture_csv",
    "collect",
    "latest_snapshot",
    "load_settings",
    "migrate_legacy_profile",
    "page_url",
    "profile_lock",
    "proxy_settings",
    "read_operation",
    "redact",
    "status",
]
