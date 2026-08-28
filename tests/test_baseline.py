"""Tests for the deliberately naive M7 baseline."""

from datetime import datetime

from src.baseline import (
    build_comparison,
    is_permitted_execution_time,
    run_baseline,
    simulate_naive_mandate,
)


def _record(failed_at: str = "2026-08-03T00:00:00+05:30") -> dict[str, object]:
    return {
        "mandate_id": "TEST-BASELINE",
        "customer_name": "Test Customer",
        "category": "subscription",
        "amount": 1000,
        "failure_reason": "mandate_expired",
        "failed_at": failed_at,
        "attempts_already_made": 0,
    }


def test_baseline_retries_non_retriable_failure_reasons() -> None:
    result = simulate_naive_mandate(_record())
    assert len(result["attempts"]) == 3
    assert {attempt["scheduled_at"][11:16] for attempt in result["attempts"]} == {
        "08:00",
        "16:00",
        "00:00",
    }


def test_restricted_attempt_is_rejected() -> None:
    result = simulate_naive_mandate(_record("2026-08-03T01:00:00+05:30"))
    assert result["attempts"][0]["scheduled_at"][11:16] == "09:00"
    assert result["attempts"][0]["outcome"] == "rejected_restricted_execution_period"


def test_execution_window_boundaries_are_inclusive() -> None:
    assert is_permitted_execution_time(datetime.fromisoformat("2026-08-03T06:00:00+05:30"))
    assert is_permitted_execution_time(datetime.fromisoformat("2026-08-03T23:30:00+05:30"))
    assert not is_permitted_execution_time(datetime.fromisoformat("2026-08-03T12:00:00+05:30"))


def test_baseline_is_deterministic() -> None:
    records = [_record("2026-08-03T22:00:00+05:30")]
    assert run_baseline(records, seed=123) == run_baseline(records, seed=123)


def test_comparison_calculates_requested_uplift() -> None:
    smart = {
        "total_mandates": 100,
        "total_at_risk_amount": 10_000,
        "recovery_rate_by_count": 42.0,
        "recovered_amount": 5000,
    }
    baseline = {
        "total_mandates": 100,
        "total_at_risk_amount": 10_000,
        "recovery_rate_by_count": 30.0,
        "recovered_amount": 3000,
    }
    comparison = build_comparison(smart, baseline)
    assert comparison["percentage_point_uplift"] == 12.0
    assert comparison["incremental_recovered_amount"] == 2000
