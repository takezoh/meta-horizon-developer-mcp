import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from meta_horizon_developer import core

APP_ID = "1234567890"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Keep results independent of environment variables set by the caller."""
    for name in (
        "META_HORIZON_APP_ID",
        "META_HORIZON_OUTPUT_DIR",
        "META_HORIZON_PROFILE_DIR",
        "META_HORIZON_CSV_DIR",
        "META_HORIZON_LOOKBACK_DAYS",
    ):
        monkeypatch.delenv(name, raising=False)


def _settings(tmp_path, app_id: str = APP_ID) -> core.Settings:
    return core.load_settings(
        app_id=app_id,
        snapshot_dir=tmp_path / "snapshots",
        profile_dir=tmp_path / "profile",
        csv_dir=tmp_path / "csv",
    )


def _write_snapshot(settings: core.Settings, *, app_id: str, name: str = "ExampleQuery") -> None:
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    target = settings.output_dir / "meta-store-2026-08-01T00-00-00Z.json"
    target.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "collected_at": "2026-08-01T00:00:00Z",
                "source": "meta-horizon-dashboard",
                "app_id": app_id,
                "operations": [{"name": name, "doc_id": "1", "data": {}, "errors": None}],
                "missing_operations": [],
                "missing_required_operations": [],
            }
        ),
        encoding="utf-8",
    )


class _FakeResponse:
    def __init__(self, name: str, payload: dict):
        self.url = core.GRAPHQL_URL
        self.ok = True
        self.request = SimpleNamespace(
            method="POST", post_data_json={"operationName": name, "doc_id": "test-doc"}
        )
        self._body = json.dumps(payload)

    async def text(self):
        return self._body


class _FakePage:
    def __init__(self, context):
        self.context = context

    async def goto(self, url, **_kwargs):
        marker = f"/applications/{self.context.app_id}/"
        route = urlsplit(url).path.partition(marker)[2]
        self.context.goto_paths.append(route)
        for response in self.context.responses.get(route, ()):
            for handler in self.context.handlers.get("response", ()):
                handler(response)

    async def wait_for_timeout(self, _milliseconds):
        pass


class _FakeContext:
    def __init__(self, app_id: str, responses: dict[str, list[_FakeResponse]]):
        self.app_id = app_id
        self.responses = responses
        self.handlers = {}
        self.goto_paths = []
        self.closed = False

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    async def new_page(self):
        return _FakePage(self)

    async def close(self):
        self.closed = True


class _FakePlaywright:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


def _mock_browser(monkeypatch, settings, responses):
    context = _FakeContext(settings.app_id, responses)
    playwright = _FakePlaywright()

    async def open_context(*_args, **_kwargs):
        return playwright, context

    monkeypatch.setattr(core, "_context", open_context)
    return context, playwright


def _sidenav_response(app_id: str, permissions, *, errors=None):
    node = {
        "id": "private-organization-id",
        "name": "Private Organization",
        "viewer_permissions": permissions,
        "applications": {
            "nodes": [{"id": app_id, "name": "Private Application"}],
        },
        "members": [{"email": "private@example.test"}],
    }
    payload = {"data": {"node": node}}
    if errors is not None:
        payload["errors"] = errors
    return _FakeResponse(core._SIDENAV_HEADER_QUERY, payload)


def _required_responses(settings, *, omit=None, error=None):
    responses = []
    for name in sorted(settings.required_operations):
        if name == omit:
            continue
        payload = {"data": {"node": {"count": 1}}}
        if name == error:
            payload["errors"] = [{"message": "PRIVATE_SERVER_ERROR_TEXT"}]
        responses.append(_FakeResponse(name, payload))
    return responses


def test_dashboard_contract_contains_only_read_queries_and_all_analytics_sections(tmp_path):
    settings = _settings(tmp_path)

    assert len(settings.operations) >= 50
    assert all(name.endswith("Query") for name in settings.operations)
    assert not any("Mutation" in name for name in settings.operations)
    assert settings.required_operations <= settings.operations
    assert {
        "analytics/",
        "analytics/benchmarking/",
        "analytics/funnel/",
        "analytics/revenue/",
        "analytics/add-on-content/",
        "analytics/subscriptions/",
        "analytics/user-engagement/",
        "analytics/user-demographics/",
    } == set(settings.pages)

    assert "OCDevManageApplicationSidenavHeaderQuery" in settings.operations
    assert settings.operation_permissions == {
        "OCDevManageOverviewAnalyticsLifetimeInstallsCardQuery": frozenset(
            {"FINANCE_ANALYTICS"}
        ),
        "OCDevManageOverviewAnalyticsRealtimeInstallsChartCardQuery": frozenset(
            {"FINANCE_ANALYTICS"}
        ),
        "OCDevManageRevenueRealTimeAnalyticsTotalRevenueChartQuery": frozenset(
            {"FINANCE_ANALYTICS"}
        ),
    }


def test_load_settings_rejects_permission_mapping_outside_allowlist(tmp_path):
    dashboard = tmp_path / "dashboard.json"
    dashboard.write_text(
        json.dumps(
            {
                "pages": ["analytics/"],
                "operations": ["GoodQuery"],
                "operation_permissions": {"AbsentQuery": ["FINANCE_ANALYTICS"]},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="operation_permissions operations must be allow-listed"):
        core.load_settings(app_id=APP_ID, dashboard_path=dashboard, snapshot_dir=tmp_path)


def test_permission_diagnostic_is_scoped_to_the_target_app_and_known_flags():
    payload = json.loads(_sidenav_response(APP_ID, ["ANALYTICS"])._body)

    assert core._sidenav_permissions(payload, APP_ID) == {
        "ANALYTICS": True,
        "FINANCE_ANALYTICS": False,
    }
    assert core._sidenav_permissions(payload, "another-app") == {
        "ANALYTICS": None,
        "FINANCE_ANALYTICS": None,
    }
    assert core._sidenav_permissions(
        {**payload, "errors": [{"message": "private error"}]}, APP_ID
    ) == {"ANALYTICS": None, "FINANCE_ANALYTICS": None}
    assert core._sidenav_permissions(
        {"data": {"node": {"applications": {"nodes": [{"id": APP_ID}]}}}}, APP_ID
    ) == {"ANALYTICS": None, "FINANCE_ANALYTICS": None}


def test_reviews_are_collected_by_scrolling_and_not_as_a_period_page(tmp_path):
    """Reviews carry no period parameter, so they are not one of the analytics pages."""
    settings = _settings(tmp_path)

    reviews = next(page for page in settings.scroll_pages if page.path == "reviews/")

    assert reviews.operation == "OCDevManageApplicationReviewsTableQuery"
    assert reviews.connection == ("node", "user_reviews2")
    assert "reviews/" not in settings.pages


def test_scrolled_operations_must_be_allow_listed(tmp_path):
    dashboard = tmp_path / "dashboard.json"
    dashboard.write_text(
        json.dumps(
            {
                "pages": ["analytics/"],
                "operations": ["GoodQuery"],
                "scroll_pages": [
                    {"path": "reviews/", "operation": "AbsentQuery", "connection": ["node"]}
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be allow-listed"):
        core.load_settings(app_id=APP_ID, dashboard_path=dashboard, snapshot_dir=tmp_path)


def test_reviews_are_not_required_so_a_missing_table_cannot_block_analytics(tmp_path):
    """Reviews are a diagnostic surface; losing them must not fail an analytics run."""
    settings = _settings(tmp_path)

    assert "OCDevManageApplicationReviewsTableQuery" not in settings.required_operations


def test_known_finance_denial_fails_after_the_first_page_and_closes_browser(
    tmp_path, monkeypatch
):
    settings = replace(
        _settings(tmp_path), pages=("analytics/", "analytics/revenue/"), scroll_pages=()
    )
    _write_snapshot(settings, app_id=APP_ID)
    existing = list(settings.output_dir.glob("meta-store-*.json"))
    existing_bytes = {path: path.read_bytes() for path in existing}
    context, playwright = _mock_browser(
        monkeypatch,
        settings,
        {"analytics/": [_sidenav_response(APP_ID, ["ANALYTICS"])]},
    )

    with pytest.raises(core.CollectionPermissionError) as caught:
        asyncio.run(core.collect(settings))

    assert set(caught.value.blocked_operations) == set(settings.operation_permissions)
    assert "FINANCE_ANALYTICS" in str(caught.value)
    assert all(name in str(caught.value) for name in settings.operation_permissions)
    assert "Private Organization" not in str(caught.value)
    assert context.goto_paths == ["analytics/"]
    assert context.closed and playwright.stopped
    assert list(settings.output_dir.glob("meta-store-*.json")) == existing
    assert {path: path.read_bytes() for path in existing} == existing_bytes


def test_optional_operation_permission_cannot_block_required_collection(tmp_path, monkeypatch):
    settings = replace(
        _settings(tmp_path),
        pages=("analytics/",),
        scroll_pages=(),
        operation_permissions={
            "OCDevManageApplicationReviewsTableQuery": frozenset({"FINANCE_ANALYTICS"})
        },
    )
    responses = [_sidenav_response(APP_ID, ["ANALYTICS"]), *_required_responses(settings)]
    _mock_browser(monkeypatch, settings, {"analytics/": responses})

    snapshot = asyncio.run(core.collect(settings))

    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["missing_required_operations"] == []


def test_sidenav_snapshot_discards_errors_and_unrelated_payload(tmp_path, monkeypatch):
    settings = replace(_settings(tmp_path), pages=("analytics/",), scroll_pages=())
    diagnostic = _sidenav_response(
        APP_ID,
        ["ANALYTICS", "FINANCE_ANALYTICS"],
        errors=[{"message": "PRIVATE_SERVER_ERROR_TEXT"}],
    )
    responses = [diagnostic, *_required_responses(settings)]
    _mock_browser(monkeypatch, settings, {"analytics/": responses})

    snapshot = asyncio.run(core.collect(settings))

    text = snapshot.read_text(encoding="utf-8")
    payload = json.loads(text)
    entry = next(
        operation
        for operation in payload["operations"]
        if operation["name"] == core._SIDENAV_HEADER_QUERY
    )
    assert entry["data"] == {
        "permissions": {"ANALYTICS": None, "FINANCE_ANALYTICS": None}
    }
    assert entry["errors"] is None
    assert all(
        secret not in text
        for secret in (
            "Private Organization",
            "private-organization-id",
            "Private Application",
            "private@example.test",
            "PRIVATE_SERVER_ERROR_TEXT",
        )
    )


def test_granted_finance_permission_collects_required_queries_and_sanitizes_diagnostic(
    tmp_path, monkeypatch
):
    settings = replace(_settings(tmp_path), pages=("analytics/",), scroll_pages=())
    responses = [
        _sidenav_response(APP_ID, ["ANALYTICS", "FINANCE_ANALYTICS"]),
        *_required_responses(settings),
    ]
    _mock_browser(monkeypatch, settings, {"analytics/": responses})

    snapshot = asyncio.run(core.collect(settings))

    text = snapshot.read_text(encoding="utf-8")
    payload = json.loads(text)
    diagnostic = next(
        operation
        for operation in payload["operations"]
        if operation["name"] == core._SIDENAV_HEADER_QUERY
    )
    assert payload["missing_required_operations"] == []
    assert diagnostic["data"] == {
        "permissions": {"ANALYTICS": True, "FINANCE_ANALYTICS": True}
    }
    assert diagnostic["errors"] is None
    assert all(
        secret not in text
        for secret in (
            "Private Organization",
            "private-organization-id",
            "private@example.test",
        )
    )


def test_unmatched_app_diagnostic_is_unknown_and_does_not_block_collection(
    tmp_path, monkeypatch
):
    settings = replace(_settings(tmp_path), pages=("analytics/",), scroll_pages=())
    responses = [
        _sidenav_response("another-app", ["ANALYTICS"]),
        *_required_responses(settings),
    ]
    _mock_browser(monkeypatch, settings, {"analytics/": responses})

    snapshot = asyncio.run(core.collect(settings))

    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    diagnostic = next(
        operation
        for operation in payload["operations"]
        if operation["name"] == core._SIDENAV_HEADER_QUERY
    )
    assert payload["missing_required_operations"] == []
    assert diagnostic["data"] == {
        "permissions": {"ANALYTICS": None, "FINANCE_ANALYTICS": None}
    }


def test_missing_required_query_does_not_write_a_snapshot(tmp_path, monkeypatch):
    settings = replace(_settings(tmp_path), pages=("analytics/",), scroll_pages=())
    _write_snapshot(settings, app_id=APP_ID)
    existing = list(settings.output_dir.glob("meta-store-*.json"))
    existing_bytes = {path: path.read_bytes() for path in existing}
    missing = sorted(settings.required_operations)[0]
    responses = [
        _sidenav_response(APP_ID, ["ANALYTICS", "FINANCE_ANALYTICS"]),
        *_required_responses(settings, omit=missing),
    ]
    _mock_browser(monkeypatch, settings, {"analytics/": responses})

    with pytest.raises(core.IncompleteCollectionError) as caught:
        asyncio.run(core.collect(settings))

    assert caught.value.missing_operations == (missing,)
    assert caught.value.errored_operations == ()
    assert list(settings.output_dir.glob("meta-store-*.json")) == existing
    assert {path: path.read_bytes() for path in existing} == existing_bytes


def test_required_graphql_error_does_not_write_or_expose_response_text(tmp_path, monkeypatch):
    settings = replace(_settings(tmp_path), pages=("analytics/",), scroll_pages=())
    _write_snapshot(settings, app_id=APP_ID)
    existing = list(settings.output_dir.glob("meta-store-*.json"))
    existing_bytes = {path: path.read_bytes() for path in existing}
    errored = sorted(settings.required_operations)[0]
    responses = [
        _sidenav_response(APP_ID, ["ANALYTICS", "FINANCE_ANALYTICS"]),
        *_required_responses(settings, error=errored),
    ]
    _mock_browser(monkeypatch, settings, {"analytics/": responses})

    with pytest.raises(core.IncompleteCollectionError) as caught:
        asyncio.run(core.collect(settings))

    assert caught.value.missing_operations == ()
    assert caught.value.errored_operations == (errored,)
    assert "PRIVATE_SERVER_ERROR_TEXT" not in str(caught.value)
    assert list(settings.output_dir.glob("meta-store-*.json")) == existing
    assert {path: path.read_bytes() for path in existing} == existing_bytes


def test_zero_captured_operations_still_raise_empty_collection_error(tmp_path, monkeypatch):
    settings = replace(_settings(tmp_path), pages=("analytics/",), scroll_pages=())
    _mock_browser(monkeypatch, settings, {})

    with pytest.raises(core.EmptyCollectionError):
        asyncio.run(core.collect(settings))

    assert list(settings.output_dir.glob("meta-store-*.json")) == []


def test_cli_returns_runtime_error_for_collection_permission_failure(tmp_path, monkeypatch, capsys):
    from meta_horizon_developer import cli

    async def fail_collection(*_args, **_kwargs):
        raise core.CollectionPermissionError(
            {"RequiredQuery": ("FINANCE_ANALYTICS",)}
        )

    monkeypatch.setattr(core, "collect", fail_collection)
    result = cli.main(
        [
            "--app-id",
            APP_ID,
            "--snapshot-dir",
            str(tmp_path / "snapshots"),
            "--profile-dir",
            str(tmp_path / "profile"),
            "collect",
        ]
    )

    assert result == cli.EXIT_RUNTIME
    assert "CollectionPermissionError" in capsys.readouterr().err


def test_review_node_drops_the_author_and_keeps_the_review(tmp_path):
    """The author names a person and no secret-key pattern would catch `alias` / `id`."""
    row = core.review_node(
        {
            "id": "1086229280595890",
            "author": {"alias": "Astra_Zeneki", "id": "104037732148388"},
            "date": 1786041310,
            "score": 5,
            "review_title": "great",
            "review_description": "very fun",
            "review_helpful_count": 1,
            "is_down_ranked": False,
            "developer_tags": [],
            "moderation_request": None,
            "developer_response": {"body": "Thanks for playing!"},
            "rated_binary": {
                "version": "0.1.10283",
                "binary_application": {"organization": {"viewer_permissions": ["KEYS"]}},
            },
        }
    )

    assert "author" not in row
    assert "Astra_Zeneki" not in json.dumps(row)
    assert "104037732148388" not in json.dumps(row)
    # The permission list is repeated on every row and describes the viewer, not the review.
    assert "KEYS" not in json.dumps(row)
    assert row["score"] == 5
    assert row["description"] == "very fun"
    assert row["app_version"] == "0.1.10283"
    assert row["developer_response"] == "Thanks for playing!"


def test_absorb_merges_pages_and_deduplicates_rows():
    """A scrolled table arrives as many responses; keeping only the last would lose rows."""
    scroll = core.ScrollPage(
        path="reviews/", operation="ReviewsQuery", connection=("node", "user_reviews2")
    )
    state: dict = {}

    def response(ids: list[str], has_next: bool) -> dict:
        return {
            "data": {
                "node": {
                    "user_reviews2": {
                        "edges": [{"node": {"id": i, "date": int(i), "score": 5}} for i in ids],
                        "page_info": {"has_next_page": has_next},
                    }
                }
            }
        }

    core._absorb(state, scroll, response(["1", "2"], True), "doc")
    core._absorb(state, scroll, response(["2", "3"], False), "doc")

    assert set(state["ReviewsQuery"]["rows"]) == {"1", "2", "3"}
    assert state["ReviewsQuery"]["has_next_page"] is False
    assert state["ReviewsQuery"]["responses"] == 2


def test_scrolled_row_counts_expose_a_short_collection():
    payload = {
        "operations": [
            {"name": "ReviewsQuery", "data": {"rows": [{"id": "1"}, {"id": "2"}]}},
            {"name": "OtherQuery", "data": {"node": {}}},
        ]
    }

    assert core.scrolled_row_counts(payload) == {"ReviewsQuery": 2}


def test_dashboard_contract_is_not_bound_to_one_application():
    """The allowlist is the Dashboard contract and does not identify an application."""
    payload = json.loads(core._DASHBOARD_PATH.read_text(encoding="utf-8"))

    assert "app_id" not in payload


def test_load_settings_requires_an_app_id(tmp_path):
    with pytest.raises(ValueError, match="app_id is required"):
        core.load_settings(snapshot_dir=tmp_path)


def test_load_settings_requires_a_snapshot_dir(tmp_path):
    """MCP starts with an unpredictable cwd, so storage locations have no default."""
    with pytest.raises(ValueError, match=r"--snapshot-dir is required"):
        core.load_settings(app_id=APP_ID, profile_dir=tmp_path)


def test_reading_resolves_without_a_profile(tmp_path):
    """One server serves several applications, so reads must not demand a profile.

    Cookies live in a single profile regardless of how many processes exist, so binding
    a server to one application would isolate nothing but `app_id`, which is not secret.
    """
    settings = core.load_settings(app_id=APP_ID, snapshot_dir=tmp_path)

    assert settings.profile_dir is None
    assert settings.csv_dir is None


def test_status_works_without_a_profile(tmp_path):
    settings = core.load_settings(app_id=APP_ID, snapshot_dir=tmp_path)
    _write_snapshot(settings, app_id=APP_ID)

    result = core.status(settings)

    assert result["authenticated_profile_present"] is False
    assert result["profile_dir"] is None
    assert result["operation_count"] == 1


def test_browser_operations_require_a_profile(tmp_path):
    """auth / collect / capture-csv drive a browser and cannot proceed without one."""
    settings = core.load_settings(app_id=APP_ID, snapshot_dir=tmp_path)

    with pytest.raises(ValueError, match=r"--profile-dir is required"):
        with core.profile_lock(settings):
            pass


def test_csv_dir_defaults_next_to_the_profile(tmp_path):
    settings = core.load_settings(
        app_id=APP_ID, snapshot_dir=tmp_path / "snap", profile_dir=tmp_path / "state" / "profile"
    )

    assert settings.csv_dir == tmp_path / "state" / "csv"


def test_load_settings_falls_back_to_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("META_HORIZON_APP_ID", "999")
    monkeypatch.setenv("META_HORIZON_OUTPUT_DIR", str(tmp_path / "from-env"))
    monkeypatch.setenv("META_HORIZON_PROFILE_DIR", str(tmp_path / "profile"))

    settings = core.load_settings()

    assert settings.app_id == "999"
    assert settings.output_dir == tmp_path / "from-env"


def test_load_settings_prefers_arguments_over_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("META_HORIZON_APP_ID", "999")

    settings = core.load_settings(app_id="111", snapshot_dir=tmp_path, profile_dir=tmp_path)

    assert settings.app_id == "111"


def test_load_settings_rejects_non_query(tmp_path):
    dashboard = tmp_path / "dashboard.json"
    dashboard.write_text(
        json.dumps({"pages": ["analytics/"], "operations": ["BadMutation"]}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match=r"only \*Query"):
        core.load_settings(app_id=APP_ID, dashboard_path=dashboard, snapshot_dir=tmp_path)


def test_load_settings_rejects_required_outside_the_allowlist(tmp_path):
    dashboard = tmp_path / "dashboard.json"
    dashboard.write_text(
        json.dumps(
            {
                "pages": ["analytics/"],
                "operations": ["GoodQuery"],
                "required_operations": ["AbsentQuery"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be a subset"):
        core.load_settings(app_id=APP_ID, dashboard_path=dashboard, snapshot_dir=tmp_path)


def test_page_url_carries_the_custom_window_and_the_application(tmp_path):
    """Pass the period as a custom URL window rather than a preset (maximum 28 days)."""
    from datetime import date

    settings = _settings(tmp_path)

    url = core.page_url(settings, "analytics/funnel/", today=date(2026, 8, 6))

    assert url.startswith(f"https://developers.meta.com/horizon/manage/applications/{APP_ID}/")
    assert "analytics/funnel/?" in url
    assert "start=2026-04-08" in url
    assert "end=2026-08-06" in url


def test_redact_removes_secrets_recursively():
    payload = {
        "cookie": "secret",
        "nested": {"csrf_token": "secret", "active_user_count": 42},
    }

    assert core.redact(payload) == {
        "cookie": "[REDACTED]",
        "nested": {"csrf_token": "[REDACTED]", "active_user_count": 42},
    }


def test_proxy_settings_splits_credentials_out_of_the_url():
    """Chromium returns 407 when proxy credentials remain embedded in the URL.

    Passing the username and password separately is the only working form, so lock it
    down here.
    """
    proxy = core.proxy_settings(
        {
            "HTTPS_PROXY": "http://user%40corp:p%40ss@localhost:3128",
            "NO_PROXY": "localhost,127.0.0.1",
        }
    )

    assert proxy == {
        "server": "http://localhost:3128",
        # Decode URL encoding before passing credentials; otherwise authentication fails.
        "username": "user@corp",
        "password": "p@ss",
        "bypass": "localhost,127.0.0.1",
    }


def test_proxy_settings_is_absent_without_configuration():
    """Return None without proxy configuration, preserving direct connection."""
    assert core.proxy_settings({}) is None


def test_proxy_settings_accepts_a_bare_host_and_no_credentials():
    proxy = core.proxy_settings({"HTTP_PROXY": "proxy.internal:8080"})

    assert proxy == {"server": "http://proxy.internal:8080"}


def test_profile_lock_refuses_a_second_holder(tmp_path):
    """A profile can be opened by only one process; fail without waiting."""
    settings = _settings(tmp_path)

    with core.profile_lock(settings):
        with pytest.raises(core.ProfileBusyError):
            with core.profile_lock(settings):
                pass


def test_profile_lock_is_reusable_after_release(tmp_path):
    settings = _settings(tmp_path)

    with core.profile_lock(settings):
        pass
    with core.profile_lock(settings):
        pass


def test_collect_refuses_a_directory_owned_by_another_application(tmp_path):
    """One application per directory; mixing them combines freshness and missing data."""
    settings = _settings(tmp_path, app_id="111")
    _write_snapshot(settings, app_id="222")

    with pytest.raises(core.AppIdMismatchError, match="app_id=222"):
        core.assert_app_id_matches(settings)


def test_app_id_guard_passes_for_the_same_application(tmp_path):
    settings = _settings(tmp_path, app_id="111")
    _write_snapshot(settings, app_id="111")

    core.assert_app_id_matches(settings)


def test_app_id_guard_passes_on_an_empty_directory(tmp_path):
    core.assert_app_id_matches(_settings(tmp_path))


def test_read_operation_rejects_name_outside_allowlist(tmp_path):
    with pytest.raises(ValueError, match="not allow-listed"):
        core.read_operation("ArbitraryQuery", _settings(tmp_path))


def test_read_operation_returns_an_allow_listed_operation(tmp_path):
    settings = _settings(tmp_path)
    name = next(iter(sorted(settings.operations)))
    _write_snapshot(settings, app_id=APP_ID, name=name)

    result = core.read_operation(name, settings)

    assert result["collected_at"] == "2026-08-01T00:00:00Z"
    assert result["operation"]["name"] == name


def test_status_reports_the_application_and_the_snapshot(tmp_path):
    settings = _settings(tmp_path)
    _write_snapshot(settings, app_id=APP_ID)

    result = core.status(settings)

    assert result["app_id"] == APP_ID
    assert result["operation_count"] == 1
    assert result["authenticated_profile_present"] is False
    assert result["snapshot_dir"] == str(settings.output_dir)


def test_graphql_stream_preserves_initial_and_deferred_fields():
    text = '\n'.join([
        json.dumps({"data": {"node": {"series": None, "summary": 3}}}),
        json.dumps({"path": ["node"], "data": {"series": [1, 2]}, "is_final": True}),
    ])
    assert core._graphql_payload(text)["data"] == {"node": {"series": [1, 2], "summary": 3}}


def test_graphql_incremental_preserves_errors_and_nested_data():
    text = json.dumps({"data": {"node": {"count": 4}}, "incremental": [
        {"path": ["node"], "data": {"chart": [8]}, "errors": [{"message": "partial"}]}
    ]})
    result = core._graphql_payload(text)
    assert result["data"] == {"node": {"count": 4, "chart": [8]}}
    assert result["errors"] == [{"message": "partial"}]


def test_graphql_stream_rejects_malformed_tail():
    with pytest.raises(json.JSONDecodeError):
        core._graphql_payload('{"data": {"node": {}}}\nnot-json')


def test_graphql_single_document_is_unchanged():
    payload = {"data": {"node": {"values": [1, 2]}}}
    assert core._graphql_payload(json.dumps(payload)) == payload
