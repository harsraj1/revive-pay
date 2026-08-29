"""Tests for statistically aggregated smart-vs-naive evaluation."""

from src.multi_seed_eval import DEFAULT_SEED_COUNT, evaluate_seeds


def _raw_record() -> dict[str, object]:
    return {
        "mandate_id": "MULTI-SEED-TEST",
        "customer_name": "Test Customer",
        "category": "subscription",
        "amount": 1000,
        "failure_reason": "bank_server_down",
        "failed_at": "2026-08-03T06:00:00+05:30",
        "attempts_already_made": 0,
    }


def _smart_record() -> dict[str, object]:
    return {
        **_raw_record(),
        "retriable": True,
        "rule_explanation": "Configured policy permits retry.",
        "urgency_priority": "low",
        "escalate_after_attempts": 3,
        "retry_schedule": [
            {
                "attempt_num": attempt,
                "scheduled_date": f"2026-08-0{3 + attempt}",
                "scheduled_time": "14:00",
                "scheduled_at": f"2026-08-0{3 + attempt}T14:00:00+05:30",
                "timezone": "Asia/Kolkata",
                "window_label": "afternoon",
            }
            for attempt in (1, 2, 3)
        ],
    }


def test_evaluation_runs_at_least_15_seeds_and_has_expected_summary() -> None:
    assert 15 <= DEFAULT_SEED_COUNT < 20 + 1
    report = evaluate_seeds([_smart_record()], [_raw_record()], seeds=range(15))

    assert report["seed_count"] == 15
    assert len(report["per_seed"]) == 15
    assert set(report["summary"]) == {"smart", "naive", "uplift"}
    for strategy in ("smart", "naive"):
        assert set(report["summary"][strategy]) == {
            "recovery_rate_by_count",
            "recovery_rate_by_amount",
            "recovered_amount",
        }
        assert set(report["summary"][strategy]["recovery_rate_by_count"]) == {
            "mean",
            "standard_deviation",
            "min",
            "max",
        }
    assert "recovery_rate_by_count_percentage_points" in report["summary"]["uplift"]
