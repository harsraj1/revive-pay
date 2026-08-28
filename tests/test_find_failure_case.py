"""Tests ensuring failure-case extraction never fabricates an escalation."""

from src.find_failure_case import render_failure_case, select_escalated_case


def test_no_escalation_is_reported_honestly() -> None:
    assert select_escalated_case([{"final_status": "recovered"}]) is None
    assert "No escalated mandate" in render_failure_case(None)


def test_preferred_actual_category_is_selected() -> None:
    entries = [
        {"mandate_id": "SUB", "category": "subscription", "final_status": "escalated", "attempts_made": []},
        {"mandate_id": "INS", "category": "insurance", "final_status": "escalated", "attempts_made": []},
    ]
    assert select_escalated_case(entries)["mandate_id"] == "INS"
