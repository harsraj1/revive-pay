"""Tests for the deterministic retry scheduling engine."""

from datetime import date, datetime

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
    return [
        datetime.fromisoformat(
            f"{attempt['scheduled_date']}T{attempt['scheduled_time']}:00"
        )
        for attempt in schedule
    ]


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
    first_retry = datetime.fromisoformat(
        f"{schedule[0]['scheduled_date']}T{schedule[0]['scheduled_time']}:00+05:30"
    )
    assert (first_retry - failed_at).total_seconds() < 12 * 60 * 60


def test_daily_limit_never_retries_on_failure_day() -> None:
    failure_day = date.fromisoformat("2026-08-03")
    schedule = build_retry_schedule(_record("daily_limit_exceeded"))
    assert schedule
    assert all(date.fromisoformat(item["scheduled_date"]) > failure_day for item in schedule)
