"""Tests that M8 documents only real audit escalations."""

from find_failure_case import render_failure_case, select_escalated_case


def _case(mandate_id: str, priority: str, status: str = "escalated") -> dict[str, object]:
    return {
        "mandate_id": mandate_id,
        "customer_name": "Test Customer",
        "category": "emi",
        "amount": 1000,
        "failure_reason": "insufficient_balance",
        "failed_at": "2026-08-01T10:00:00+05:30",
        "urgency_priority": priority,
        "attempts_already_made": 2,
        "escalate_after_attempts": 2,
        "scheduled_retries": [],
        "attempts_made": [],
        "final_status": status,
        "stop_reason": "Recorded stop reason.",
    }


def test_selection_prefers_actual_high_priority_escalation() -> None:
    entries = [_case("LOW", "low"), _case("HIGH", "high"), _case("OTHER", "high", "recovered")]
    assert select_escalated_case(entries)["mandate_id"] == "HIGH"


def test_selection_refuses_to_fabricate_when_no_escalation_exists() -> None:
    try:
        select_escalated_case([_case("RECOVERED", "high", "recovered")])
    except ValueError as error:
        assert "refusing to fabricate" in str(error)
    else:
        raise AssertionError("missing escalations must not produce an example")


def test_renderer_rejects_non_escalated_entry() -> None:
    try:
        render_failure_case(_case("RECOVERED", "high", "recovered"))
    except ValueError as error:
        assert "actual escalated" in str(error)
    else:
        raise AssertionError("renderer must reject non-escalated entries")
