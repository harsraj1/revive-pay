"""Tests for deterministic customer-message fallback behavior."""

from src.message import MAX_MESSAGE_LENGTH, add_customer_messages, fallback_message


def _record(category: str, reason: str = "insufficient_balance") -> dict[str, object]:
    return {
        "mandate_id": f"TEST-{category}",
        "customer_name": "Aditi Rao",
        "category": category,
        "failure_reason": reason,
        "retriable": reason == "insufficient_balance",
        "retry_schedule": [{"attempt_num": 1}],
    }


def test_no_key_produces_deterministic_fallback() -> None:
    record = _record("subscription")
    first = add_customer_messages([record], api_key=None)
    second = add_customer_messages([record], api_key=None)
    assert first == second
    assert first[0]["customer_message"] == fallback_message(record)


def test_fallbacks_state_failure_and_remain_concise() -> None:
    for category in ("subscription", "emi", "sip", "insurance", "credit_card"):
        message = fallback_message(_record(category))
        assert "fail" in message.casefold()
        assert len(message) <= MAX_MESSAGE_LENGTH
        assert "penalty" not in message.casefold()


def test_non_retriable_record_gets_safe_next_action() -> None:
    message = fallback_message(_record("insurance", "mandate_expired"))
    assert "mandate renew" in message
