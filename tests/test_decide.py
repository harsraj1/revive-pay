"""Tests for the deterministic retry scheduling engine."""

import os
import time
from datetime import date, datetime, timedelta

from src.constants import MAX_RETRIES, PERMITTED_EXECUTION_WINDOWS
from src.decide import build_retry_schedule, validate_schedule


def _record(reason: str, attempts: int = 0) -> dict[str, object]:
    return {
        "mandate_id": f"TEST-{reason}",
        "category": "subscription",
        "amount": 499,
        "failure_reason": reason,
        "failed_at": "2026-08-03T10:00:00+05:30",
        "attempts_already_made": attempts,
        "retriable": True,
    }


def _as_datetimes(schedule: list[dict[str, object]]) -> list[datetime]:
    return [datetime.fromisoformat(str(attempt["scheduled_at"])) for attempt in schedule]


def test_retry_cap_accounts_for_attempts_already_made() -> None:
    full = build_retry_schedule(_record("bank_server_down"))
    partial = build_retry_schedule(_record("bank_server_down", attempts=2))
    assert len(full) == MAX_RETRIES
    assert len(partial) == 1
    assert partial[0]["attempt_num"] == 3


def test_every_retry_is_inside_a_permitted_window() -> None:
    for reason in (
        "insufficient_balance",
        "bank_server_down",
        "technical_decline",
        "daily_limit_exceeded",
    ):
        schedule = build_retry_schedule(_record(reason))
        validate_schedule(schedule)
        for attempt in schedule:
            assert attempt["timezone"] == "Asia/Kolkata"
            assert datetime.fromisoformat(str(attempt["scheduled_at"])).utcoffset() == timedelta(
                hours=5, minutes=30
            )
            window = next(
                item
                for item in PERMITTED_EXECUTION_WINDOWS
                if item["label"] == attempt["window_label"]
            )
            assert window["start"] <= attempt["scheduled_time"] <= window["end"]


def test_insufficient_balance_retries_are_widely_spaced() -> None:
    times = _as_datetimes(build_retry_schedule(_record("insufficient_balance")))
    gaps = [later - earlier for earlier, later in zip(times, times[1:])]
    assert all(gap.total_seconds() >= 24 * 60 * 60 for gap in gaps)


def test_transient_bank_failure_retries_rapidly() -> None:
    failed_at = datetime.fromisoformat(str(_record("bank_server_down")["failed_at"]))
    schedule = build_retry_schedule(_record("bank_server_down"))
    first_retry = datetime.fromisoformat(str(schedule[0]["scheduled_at"]))
    assert (first_retry - failed_at).total_seconds() < 12 * 60 * 60


def test_daily_limit_never_retries_on_failure_day() -> None:
    failure_day = date.fromisoformat("2026-08-03")
    schedule = build_retry_schedule(_record("daily_limit_exceeded"))
    assert schedule
    assert all(date.fromisoformat(item["scheduled_date"]) > failure_day for item in schedule)


def test_non_ist_host_setting_still_produces_ist_schedule(monkeypatch) -> None:
    """Scheduling must never inherit the process or machine local timezone."""

    original_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "America/New_York")
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        record = _record("bank_server_down")
        record["failed_at"] = "2026-08-03T10:00:00"  # Explicitly interpreted as IST.
        first = build_retry_schedule(record)[0]
        scheduled_at = datetime.fromisoformat(str(first["scheduled_at"]))

        assert first["timezone"] == "Asia/Kolkata"
        assert first["scheduled_at"] == "2026-08-03T14:00:00+05:30"
        assert scheduled_at.utcoffset() == timedelta(hours=5, minutes=30)
    finally:
        if hasattr(time, "tzset"):
            if original_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original_tz
            time.tzset()
