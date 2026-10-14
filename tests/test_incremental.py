import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from meta_horizon_developer import core
from meta_horizon_developer.incremental import assemble, merge_daily, plan


def row(day, value):
    return {"timestamp": int(datetime(2026, 10, day, tzinfo=UTC).timestamp()), "value": value}


def test_merge_preserves_missing_dates_and_series_identity():
    old = {
        "time_series": [
            {"metric": "ALL", "events": [row(1, 1), row(2, 2)]},
            {"metric": "ANR", "events": [row(1, 8)]},
        ],
        "summary": 100,
    }
    new = {
        "time_series": [{"metric": "ANR", "events": [row(3, 9)]}, {"metric": "ALL", "events": []}],
        "summary": 10,
    }
    merged = merge_daily(
        old, new, start=date(2026, 10, 1), new_start=date(2026, 10, 3), end=date(2026, 10, 3)
    )
    assert merged["summary"] == 10
    assert merged["time_series"][0]["events"] == [row(1, 8), row(3, 9)]
    assert merged["time_series"][1]["events"] == [row(1, 1), row(2, 2)]
    # An API ignoring the requested range cannot replace acquired old values.
    assert merge_daily(
        [row(1, 1)],
        [row(1, 999), row(2, 2)],
        start=date(2026, 10, 1),
        new_start=date(2026, 10, 2),
        end=date(2026, 10, 2),
    ) == [row(1, 1), row(2, 2)]


def test_required_failure_never_falls_back():
    result = {"operations": [], "missing_required_operations": ["DailyQuery"]}
    with pytest.raises(ValueError, match="acquisition failed"):
        assemble(
            {},
            result,
            source=None,
            start=date(2026, 10, 1),
            new_start=date(2026, 10, 2),
            end=date(2026, 10, 2),
        )


def test_plan_uses_successful_requested_dates_not_last_nonempty_date(tmp_path):
    settings = SimpleNamespace(
        output_dir=tmp_path,
        app_id="123",
        lookback_days=3,
        required_operations=frozenset({"DailyQuery"}),
    )
    prior = {
        "app_id": "123",
        "collected_at": "2026-10-03T00:00:00Z",
        "operations": [{"name": "DailyQuery", "data": [row(1, 1)], "errors": None}],
        "missing_required_operations": [],
        "acquisition": {"daily_start": "2026-10-01", "daily_end": "2026-10-02"},
    }
    path = tmp_path / "meta-store-2026-10-03T00-00-00Z.json"
    path.write_text(json.dumps(prior))
    old, source, new_start = plan(settings, date(2026, 10, 4))
    assert new_start == date(2026, 10, 3)
    assert source["filename"] == path.name
    assert len(source["sha256"]) == 64


def test_same_day_reuses_file_without_starting_browser(tmp_path, monkeypatch):
    today = datetime.now(UTC).date()
    prior = {
        "app_id": "123",
        "collected_at": datetime.now(UTC).isoformat(),
        "operations": [{"name": "DailyQuery", "data": [], "errors": None}],
        "missing_required_operations": [],
        "acquisition": {
            "daily_start": str(today - timedelta(days=3)),
            "daily_end": str(today - timedelta(days=1)),
        },
    }
    path = tmp_path / "meta-store-2026-10-01T00-00-00Z.json"
    path.write_text(json.dumps(prior))
    settings = SimpleNamespace(
        output_dir=tmp_path,
        app_id="123",
        lookback_days=3,
        required_operations=frozenset({"DailyQuery"}),
    )
    monkeypatch.setattr(core, "_context", lambda *a, **k: pytest.fail("browser started"))
    assert asyncio.run(core.collect(settings, incremental=True)) == path
