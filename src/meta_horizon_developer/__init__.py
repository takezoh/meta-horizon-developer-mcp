"""Read-only Meta Horizon Dashboard collector (CLI + MCP).

対象アプリ (`app_id`) と保存先は呼び出し側が決める。取り込んだ集計値の
下流での扱い (DuckDB 登録など) はこのパッケージの責務ではない。
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
