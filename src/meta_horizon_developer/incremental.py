"""Incremental daily Dashboard data. No request construction or replay."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta


def observed_day(row):
    if not isinstance(row, dict):
        return None
    value = (row.get("dimensions") or {}).get("end_time", row.get("timestamp"))
    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(value, UTC).date()
    return None


def merge_daily(old, new, *, start, new_start, end):
    """Reuse only dated records, replacing the whole requested date interval.

    In particular, an empty successful new series does not retain old values for
    that requested interval. Non-daily summaries always belong to the new request.
    """
    if isinstance(old, dict) and isinstance(new, dict):
        return {
            key: merge_daily(old.get(key), value, start=start, new_start=new_start, end=end)
            for key, value in new.items()
        }
    if isinstance(old, list) and isinstance(new, list):
        sample = old + new
        if sample and all(observed_day(row) is not None for row in sample):
            return [deepcopy(r) for r in old if start <= observed_day(r) < new_start] + [
                deepcopy(r) for r in new if new_start <= observed_day(r) <= end
            ]
        # Crash metrics have one named series per metric; do not combine ALL/ANR.
        if sample and all(isinstance(r, dict) and "metric" in r and "events" in r for r in sample):
            prior = {r["metric"]: r for r in old}
            return [
                merge_daily(
                    prior.get(r["metric"], {}), r, start=start, new_start=new_start, end=end
                )
                for r in new
            ]
    return deepcopy(new)


def plan(settings, today):
    start = today - timedelta(days=settings.lookback_days)
    files = sorted(settings.output_dir.glob("meta-store-*.json"))
    if not files:
        return None, None, start
    path = files[-1]
    raw = path.read_bytes()
    prior = json.loads(raw)
    recorded_hash = prior.get("incremental_content_hash")
    if recorded_hash:
        material = {k: v for k, v in prior.items() if k != "incremental_content_hash"}
        actual = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
        if actual != recorded_hash:
            raise ValueError("incremental snapshot integrity check failed")
    if str(prior.get("app_id")) != settings.app_id:
        raise ValueError("incremental snapshot belongs to another app")
    names = {op.get("name") for op in prior.get("operations", [])}
    if not settings.required_operations <= names:
        return None, None, start
    if prior.get("missing_required_operations") or any(
        op.get("errors")
        for op in prior.get("operations", [])
        if op.get("name") in settings.required_operations
    ):
        raise ValueError("latest snapshot acquisition failed; cannot use it as a daily checkpoint")
    collected = datetime.fromisoformat(prior["collected_at"].replace("Z", "+00:00"))
    # Legacy snapshots have no requested window. Require observed evidence at the
    # left boundary before adopting their previously successful complete request.
    days = []

    def visit(value):
        if isinstance(value, dict):
            if (d := observed_day(value)) is not None:
                days.append(d)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(prior.get("operations", []))
    previous_start = (prior.get("acquisition") or {}).get("daily_start")
    if previous_start:
        covered_start = date.fromisoformat(previous_start)
    elif days:
        covered_start = min(days)
    else:
        return None, None, start
    if covered_start > start:
        # This is an explicit longer-history request, not a normal daily run.
        return None, None, start
    requested_end = (prior.get("acquisition") or {}).get("daily_end")
    next_day = (
        date.fromisoformat(requested_end) + timedelta(days=1) if requested_end else collected.date()
    )
    if next_day > today:
        raise ValueError("snapshot checkpoint is in the future")
    source = {
        "filename": path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "collected_at": prior["collected_at"],
    }
    return prior, source, max(start, next_day)


def assemble(prior, result, *, source, start, new_start, end):
    failed = result.get("missing_required_operations") or [
        op["name"] for op in result["operations"] if op.get("errors")
    ]
    if failed:
        raise ValueError(
            "Dashboard acquisition failed; old data will not replace the failed request"
        )
    previous = {op["name"]: op for op in (prior or {}).get("operations", [])}
    for op in result["operations"]:
        if op["name"] in previous:
            op["data"] = merge_daily(
                previous[op["name"]].get("data"),
                op.get("data"),
                start=start,
                new_start=new_start,
                end=end,
            )
    result["assembled_at"] = result["collected_at"]
    result["acquisition"] = {
        "daily_start": str(start),
        "daily_end": str(end),
        "request_start": str(new_start),
        "request_end": str(end),
        "reused_source": source,
        "non_daily_scope": (
            "current request window; lifetime and undated tables follow provider scope"
        ),
        "undated_tables": "Dashboard has no date cursor; reviews are read to completion",
    }
    result["incremental_content_hash"] = hashlib.sha256(
        json.dumps(result, sort_keys=True).encode()
    ).hexdigest()
    return result
