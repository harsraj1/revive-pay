"""Tests for the deliberately naive comparison strategy."""

from src.baseline import run_baseline, run_naive_mandate


def _record(failed_at: str) -> dict[str, object]:
    return {
        "mandate_id": "BASELINE-TEST",
        "category": "subscription",
        "amount": 500,
        "failure_reason": "bank_server_down",
        "failed_at": failed_at,
        "attempts_already_made": 0,
    }


def test_baseline_is_reproducible() -> None:
    records = [_record("2026-08-03T06:00:00+05:30")]
    assert run_baseline(records, seed=123) == run_baseline(records, seed=123)


def test_restricted_fixed_attempt_is_rejected_not_moved() -> None:
    result = run_naive_mandate(_record("2026-08-03T10:00:00+05:30"))
    first = result["attempts"][0]
    assert first["scheduled_at"].startswith("2026-08-03T18:00")
    assert first["decision"] == "rejected_restricted_time"
