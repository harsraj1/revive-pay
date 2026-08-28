"""Tests for deterministic execution simulation and stopping behavior."""

from src.act import deterministic_draw, simulate_execution, simulate_mandate
from src.constants import SUCCESS_PROBABILITIES


def _record(reason: str = "bank_server_down") -> dict[str, object]:
    return {
        "mandate_id": "TEST-EXECUTION",
        "customer_name": "Test Customer",
        "category": "subscription",
        "amount": 1000,
        "failure_reason": reason,
        "failed_at": "2026-08-03T10:00:00+05:30",
        "retriable": True,
        "rule_explanation": "Configured policy permits retry.",
        "attempts_already_made": 0,
        "escalate_after_attempts": 3,
        "retry_schedule": [
            {
                "attempt_num": attempt,
                "scheduled_date": f"2026-08-0{3 + attempt}",
                "scheduled_time": "14:00",
                "window_label": "afternoon",
            }
            for attempt in (1, 2, 3)
        ],
    }


def test_seeded_simulation_is_reproducible() -> None:
    records = [_record("bank_server_down"), {**_record("technical_decline"), "mandate_id": "TEST-2"}]
    assert simulate_execution(records, seed=12345) == simulate_execution(records, seed=12345)
    assert simulate_execution(records, seed=12345) != simulate_execution(records, seed=54321)


def test_success_stops_remaining_attempts() -> None:
    record = _record("bank_server_down")
    probability = SUCCESS_PROBABILITIES["bank_server_down"]
    seed = next(
        candidate
        for candidate in range(10_000)
        if deterministic_draw(candidate, "TEST-EXECUTION", 1) < probability
    )
    result = simulate_mandate(record, seed)
    assert result["final_status"] == "recovered"
    assert len(result["attempts_made"]) == 1


def test_threshold_escalates_before_another_attempt() -> None:
    record = {**_record("insufficient_balance"), "escalate_after_attempts": 1}
    probability = SUCCESS_PROBABILITIES["insufficient_balance"]
    seed = next(
        candidate
        for candidate in range(10_000)
        if deterministic_draw(candidate, "TEST-EXECUTION", 1) >= probability
    )
    result = simulate_mandate(record, seed)
    assert result["final_status"] == "escalated"
    assert len(result["attempts_made"]) == 1
    assert "threshold of 1" in result["stop_reason"]


def test_non_retriable_mandate_never_executes() -> None:
    record = {**_record("mandate_expired"), "retriable": False}
    result = simulate_mandate(record)
    assert result["final_status"] == "non_retriable"
    assert result["attempts_made"] == []
