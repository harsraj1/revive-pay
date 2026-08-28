"""Tests for category-aware escalation policy."""

from src.constants import MAX_RETRIES
from src.escalate import apply_escalation_policy


def _record(category: str) -> dict[str, object]:
    return {
        "mandate_id": f"TEST-{category}",
        "category": category,
        "retry_schedule": [],
    }


def test_high_risk_categories_escalate_before_subscription() -> None:
    subscription = apply_escalation_policy(_record("subscription"))

    for category in ("emi", "insurance", "credit_card"):
        high_risk = apply_escalation_policy(_record(category))
        assert high_risk["urgency_priority"] == "high"
        assert high_risk["escalate_after_attempts"] < subscription[
            "escalate_after_attempts"
        ]


def test_subscription_can_use_full_retry_runway() -> None:
    result = apply_escalation_policy(_record("subscription"))
    assert result["urgency_priority"] == "low"
    assert result["escalate_after_attempts"] == MAX_RETRIES


def test_sip_has_medium_urgency() -> None:
    result = apply_escalation_policy(_record("sip"))
    assert result["urgency_priority"] == "medium"
    assert result["escalate_after_attempts"] == MAX_RETRIES


def test_unknown_category_is_rejected() -> None:
    try:
        apply_escalation_policy(_record("unknown_category"))
    except ValueError as error:
        assert "unsupported payment category" in str(error)
    else:
        raise AssertionError("unknown categories must be rejected")
