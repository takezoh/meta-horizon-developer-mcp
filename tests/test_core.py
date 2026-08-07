import json

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
