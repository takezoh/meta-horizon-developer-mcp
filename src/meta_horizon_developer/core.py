"""Read-only Meta Horizon Dashboard collector.

The dashboard owns authentication and builds persisted GraphQL requests.  This
collector observes successful allow-listed Query responses; it never replays a
request, exposes a cookie/token, or permits a Mutation.

このモジュールは `app_id` にも保存先にも依存しない。対象アプリと置き場は
`load_settings()` の引数 (または環境変数) で呼び出し側が決める。DuckDB への
登録のような下流の扱いはここには無い (ADR: meta-horizon-portable-collector)。
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import re
import shutil
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from playwright.async_api import BrowserContext, Request, async_playwright

GRAPHQL_URL = "https://developers.meta.com/api/graphql/"
BASE_URL = "https://developers.meta.com/horizon/manage/applications/{app_id}/"
SNAPSHOT_PREFIX = "meta-store-"
_SECRET_KEY = re.compile(
    r"authorization|cookie|token|secret|password|session|csrf|email|phone|"
    r"user.?id|account.?id|dtsg|jazoest|^lsd$|access.?key|signature",
    re.IGNORECASE,
)
_DASHBOARD_PATH = Path(__file__).with_name("dashboard.json")


class MetaHorizonError(RuntimeError):
    """Base class for collector failures that callers are expected to handle."""


class ProfileBusyError(MetaHorizonError):
    """Another process already holds the browser profile."""


class AppIdMismatchError(MetaHorizonError):
    """The snapshot directory already belongs to a different application."""


class EmptyCollectionError(MetaHorizonError):
    """No allow-listed Query was captured; the session is most likely expired."""


@dataclass(frozen=True)
class Settings:
    app_id: str
    pages: tuple[str, ...]
    operations: frozenset[str]
    required_operations: frozenset[str]
    profile_dir: Path
    output_dir: Path
    csv_dir: Path
    executable_path: str | None = None
    # Dashboard へ渡す集計期間の長さ。既定 (28日) は分析側の収集窓 120 日に対して
    # 短すぎ、期間推移がストア側だけ数期分しか描けなくなるため揃える。
    lookback_days: int = 120


def _required(value: Path | str | None, env_name: str, flag: str) -> Path:
    """置き場は必ず呼び出し側が決める。既定を持たない。

    MCP サーバはホスト (gateway など) から**作業ディレクトリが不定の状態で**
    起動される。cwd 起点の既定を持つと、profile やスナップショットが起動元次第で
    別の場所に作られ、認証済み profile があるのに「ログイン画面へ飛ばされる」
    という切り分けにくい失敗になる。ホーム配下の `~/.local/state` も
    実行環境によっては書き込み不可なので既定にしない。
    """
    resolved = value or os.environ.get(env_name)
    if not resolved:
        raise ValueError(f"{flag} is required (pass {flag} or set {env_name})")
    return Path(resolved)


def load_settings(
    *,
    app_id: str | None = None,
    snapshot_dir: Path | str | None = None,
    profile_dir: Path | str | None = None,
    csv_dir: Path | str | None = None,
    dashboard_path: Path | str | None = None,
    lookback_days: int | None = None,
    executable_path: str | None = None,
) -> Settings:
    """対象アプリと置き場を解決する。引数 > 環境変数 > 既定 の順。

    `dashboard.json` は pages と operations だけを持つ **Dashboard の契約**であり、
    どのアプリを見るかは含まない。画面構成はアプリによらず同じなので、
    許可リストは全アプリで共有する。
    """
    payload = json.loads(Path(dashboard_path or _DASHBOARD_PATH).read_text(encoding="utf-8"))
    operations = frozenset(payload["operations"])
    if not operations or any(not name.endswith("Query") for name in operations):
        raise ValueError("Meta Horizon allowlist may contain only *Query operations")
    required = frozenset(payload.get("required_operations", operations))
    if not required <= operations:
        raise ValueError("required_operations must be a subset of operations")

    resolved_app_id = app_id or os.environ.get("META_HORIZON_APP_ID")
    if not resolved_app_id:
        raise ValueError("app_id is required (pass --app-id or set META_HORIZON_APP_ID)")

    output = _required(snapshot_dir, "META_HORIZON_OUTPUT_DIR", "--snapshot-dir")
    profile = _required(profile_dir, "META_HORIZON_PROFILE_DIR", "--profile-dir")
    # CSV は復旧・値照合のときだけ使う補助なので、profile の隣を既定にしてよい。
    csv = csv_dir or os.environ.get("META_HORIZON_CSV_DIR") or profile.parent / "csv"
    lookback = lookback_days or int(os.environ.get("META_HORIZON_LOOKBACK_DAYS", "120"))
    return Settings(
        app_id=str(resolved_app_id),
        pages=tuple(payload["pages"]),
        operations=operations,
        required_operations=required,
        profile_dir=Path(profile),
        output_dir=Path(output),
        csv_dir=Path(csv),
        executable_path=executable_path or os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
        lookback_days=lookback,
    )


def page_url(settings: Settings, relative: str, *, today: date) -> str:
    """集計期間を明示した Analytics ページの URL。

    Dashboard の期間は URL の `start` / `end` に載る。既定のプリセットは最長 28 日
    しか選べないので、**カスタム期間を URL で渡して** より長い日次系列を出させる。
    リクエストを組み立てて投げ直すのではなく、Dashboard 自身に発行させる点は変えない。
    `end` に当日を渡すと、Dashboard は集計途中の当日を除いた前日までを返す。
    """
    start = today - timedelta(days=settings.lookback_days)
    query = urlencode({"start": start.isoformat(), "end": today.isoformat(), "preset": "custom"})
    return f"{BASE_URL.format(app_id=settings.app_id)}{relative}?{query}"


def redact(value: Any, key: str = "") -> Any:
    """Remove credentials/identifiers before a response reaches disk or MCP."""
    if _SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, list):
        return [redact(item) for item in value[:10_000]]
    if isinstance(value, dict):
        return {name: redact(item, name) for name, item in value.items()}
    if isinstance(value, str) and len(value) > 10_000:
        return f"{value[:10_000]}…"
    return value


def _request_meta(request: Request) -> dict[str, str]:
    try:
        body = request.post_data_json
    except Exception:  # Playwright raises when the form body is not JSON.
        body = None
    if isinstance(body, dict):
        return {
            "name": body.get("operationName") or body.get("fb_api_req_friendly_name") or "",
            "doc_id": str(body.get("doc_id") or ""),
        }
    form = parse_qs(request.post_data or "")
    return {
        "name": (form.get("operationName") or form.get("fb_api_req_friendly_name") or [""])[0],
        "doc_id": (form.get("doc_id") or [""])[0],
    }


def proxy_settings(environ: dict[str, str] | None = None) -> dict[str, str] | None:
    """環境変数の HTTP プロキシ設定を Playwright の形へ直す。

    Chromium は URL に埋め込まれた認証情報を CONNECT に使わない。環境変数を
    そのまま渡すと 407 が返り `ERR_TUNNEL_CONNECTION_FAILED` で全ページが
    開けないので、**ユーザー名とパスワードを分けて渡す**。
    プロキシが設定されていない環境では None を返す (既定の直接接続のまま)。
    """
    env = os.environ if environ is None else environ
    raw = next(
        (
            env[name]
            for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")
            if env.get(name)
        ),
        None,
    )
    if not raw:
        return None
    parsed = urlsplit(raw if "://" in raw else f"http://{raw}")
    if not parsed.hostname:
        return None
    port = f":{parsed.port}" if parsed.port else ""
    proxy = {"server": f"{parsed.scheme or 'http'}://{parsed.hostname}{port}"}
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    bypass = env.get("NO_PROXY") or env.get("no_proxy")
    if bypass:
        # Playwright は Chromium と同じくカンマ区切りを受ける
        proxy["bypass"] = bypass
    return proxy


# Cookie を OS キーリングではなく profile 内の basic store で扱う。
# キーリングの無い headless / WSL 環境では、これを指定しないと Chromium が
# 別の鍵を選び、**保存済みの cookie を 1 件も読めない**まま無言でログイン画面へ飛ぶ
# (profile は壊れていないのに認証切れに見える)。
_BROWSER_ARGS = ("--password-store=basic",)


@contextmanager
def profile_lock(settings: Settings) -> Generator[Path]:
    """profile を排他ロックする。取れなければ即座に失敗する。

    Chromium の persistent profile は**同時に 1 プロセスしか開けない**。
    複数プロジェクトから呼ばれうるため、排他の根拠を呼び出し側の cron
    (`flock`) ではなくここに置く。ブロックせずに落とすのは、定期実行が
    人手の `auth` を待って積み上がるのを避けるため。
    """
    settings.profile_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = settings.profile_dir.with_name(f"{settings.profile_dir.name}.lock")
    handle = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ProfileBusyError(
                f"browser profile is already in use: {settings.profile_dir}"
            ) from exc
        try:
            yield lock_path
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        os.close(handle)


def assert_app_id_matches(settings: Settings) -> None:
    """snapshot-dir が別アプリのものなら失敗する。ディレクトリ単位で 1 アプリ。

    混ざると staging の `app_id` 列だけが頼りになり、鮮度や欠損の集計が
    アプリをまたいで合算される。**書く前に**止める。
    """
    latest = latest_snapshot(settings)
    if not latest:
        return
    existing = json.loads(latest.read_text(encoding="utf-8")).get("app_id")
    if existing and str(existing) != settings.app_id:
        raise AppIdMismatchError(
            f"{settings.output_dir} holds snapshots for app_id={existing}; "
            f"refusing to write app_id={settings.app_id}"
        )


async def _context(settings: Settings, *, headless: bool) -> tuple[Any, BrowserContext]:
    settings.profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        settings.profile_dir,
        headless=headless,
        accept_downloads=True,
        viewport={"width": 1440, "height": 1000},
        executable_path=settings.executable_path,
        proxy=proxy_settings(),
        args=list(_BROWSER_ARGS),
    )
    return playwright, context


async def authenticate(settings: Settings | None = None) -> None:
    """Open the dedicated browser profile for a human Meta login."""
    settings = settings or load_settings()
    with profile_lock(settings):
        playwright, context = await _context(settings, headless=False)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(
                BASE_URL.format(app_id=settings.app_id), wait_until="domcontentloaded"
            )
            await asyncio.to_thread(input, "ログインとDashboard表示が完了したら Enter: ")
        finally:
            await context.close()
            await playwright.stop()


async def collect(settings: Settings | None = None) -> Path:
    """Collect successful allow-listed GraphQL Query responses into one snapshot."""
    settings = settings or load_settings()
    settings.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    assert_app_id_matches(settings)
    captured: dict[str, dict[str, Any]] = {}
    tasks: set[asyncio.Task[None]] = set()

    async def capture(response: Any) -> None:
        request = response.request
        if response.url != GRAPHQL_URL or request.method != "POST" or not response.ok:
            return
        meta = _request_meta(request)
        if meta["name"] not in settings.operations:
            return
        payload = await response.json()
        data = redact(payload.get("data"))
        if meta["name"] == "OCDevManageApplicationAnalyticsRootQuery":
            app = (payload.get("data") or {}).get("fetch__Application") or {}
            data = {"fetch__Application": {key: app.get(key) for key in (
                "can_download_analytics_data", "meets_min_user_threshold",
                "is_2d_app_for_analytics", "app_mode",
            )}}
        captured[meta["name"]] = {
            "name": meta["name"],
            "doc_id": meta["doc_id"],
            "data": data,
            "errors": redact(payload.get("errors")),
        }

    def on_response(response: Any) -> None:
        task = asyncio.create_task(capture(response))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    with profile_lock(settings):
        playwright, context = await _context(settings, headless=True)
        context.on("response", on_response)
        try:
            page = await context.new_page()
            today = datetime.now(UTC).date()
            for relative in settings.pages:
                await page.goto(
                    page_url(settings, relative, today=today),
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                await page.wait_for_timeout(5_000)
            if tasks:
                await asyncio.gather(*tuple(tasks))
        finally:
            await context.close()
            await playwright.stop()

    now = datetime.now(UTC)
    result = {
        "schema_version": 1,
        "collected_at": now.isoformat().replace("+00:00", "Z"),
        "source": "meta-horizon-dashboard",
        "app_id": settings.app_id,
        "operations": sorted(captured.values(), key=lambda item: item["name"]),
        "missing_operations": sorted(settings.operations - captured.keys()),
        "missing_required_operations": sorted(settings.required_operations - captured.keys()),
    }
    target = settings.output_dir / f"{SNAPSHOT_PREFIX}{now:%Y-%m-%dT%H-%M-%SZ}.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    target.chmod(0o600)
    if not captured:
        target.unlink(missing_ok=True)
        raise EmptyCollectionError(
            "no analytics Query was captured; run auth and verify the session"
        )
    return target


async def capture_csv(settings: Settings | None = None) -> list[Path]:
    """Save chart CSV Blob downloads while a human operates the dashboard."""
    settings = settings or load_settings()
    batch = settings.csv_dir / datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    batch.mkdir(parents=True, exist_ok=False, mode=0o700)
    saved: list[Path] = []

    async def save(download: Any) -> None:
        name = Path(download.suggested_filename).name
        if not name.lower().endswith(".csv"):
            return
        target = batch / f"{len(saved) + 1:02d}-{name}"
        await download.save_as(target)
        target.chmod(0o600)
        saved.append(target)

    tasks: set[asyncio.Task[None]] = set()

    def on_download(download: Any) -> None:
        task = asyncio.create_task(save(download))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    with profile_lock(settings):
        playwright, context = await _context(settings, headless=False)
        context.on("page", lambda page: page.on("download", on_download))
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            page.on("download", on_download)
            await page.goto(
                BASE_URL.format(app_id=settings.app_id) + "analytics/",
                wait_until="domcontentloaded",
            )
            await asyncio.to_thread(input, "必要なCSVをダウンロードし、完了したら Enter: ")
            if tasks:
                await asyncio.gather(*tuple(tasks))
        finally:
            await context.close()
            await playwright.stop()
    if not saved:
        batch.rmdir()
    return saved


def latest_snapshot(settings: Settings | None = None) -> Path | None:
    settings = settings or load_settings()
    files = sorted(settings.output_dir.glob(f"{SNAPSHOT_PREFIX}*.json"))
    return files[-1] if files else None


def read_operation(name: str, settings: Settings | None = None) -> dict[str, Any]:
    """Read one allow-listed operation from the latest redacted snapshot."""
    settings = settings or load_settings()
    if name not in settings.operations:
        raise ValueError("operation is not allow-listed")
    latest = latest_snapshot(settings)
    if not latest:
        raise FileNotFoundError("Meta Store snapshot is not available")
    payload = json.loads(latest.read_text(encoding="utf-8"))
    operation = next((item for item in payload.get("operations", []) if item["name"] == name), None)
    return {
        "snapshot": str(latest),
        "collected_at": payload.get("collected_at"),
        "operation": operation,
    }


def status(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    latest = latest_snapshot(settings)
    payload = json.loads(latest.read_text(encoding="utf-8")) if latest else None
    return {
        "app_id": settings.app_id,
        "authenticated_profile_present": settings.profile_dir.exists(),
        "profile_dir": str(settings.profile_dir),
        "snapshot_dir": str(settings.output_dir),
        "latest_snapshot": str(latest) if latest else None,
        "collected_at": payload.get("collected_at") if payload else None,
        "operation_count": len(payload.get("operations", [])) if payload else 0,
        "missing_operation_count": len(payload.get("missing_operations", [])) if payload else 0,
        "missing_required_operation_count": (
            len(payload.get("missing_required_operations", [])) if payload else 0
        ),
    }


def migrate_legacy_profile(source: Path, destination: Path) -> None:
    """Copy an existing dedicated profile without exposing its contents."""
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copytree(source, destination)
    for item in destination.rglob("*"):
        item.chmod(0o700 if item.is_dir() else 0o600)
