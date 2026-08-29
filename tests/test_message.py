"""Tests for deterministic customer-message fallback behavior."""

from src.constants import (
    CREATE_SUPPORT_CASE,
    REQUEST_MANDATE_RENEWAL,
    RETRY_AUTOPAY,
    SEND_BALANCE_REMINDER,
    SEND_PAYMENT_LINK,
    STOP_AUTOMATION,
)
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
    assert message is not None
    assert "mandate renew" in message


def test_messages_follow_router_action_instead_of_only_failure_reason() -> None:
    renewal = {
        **_record("insurance", "mandate_expired"),
        "chosen_actions": [REQUEST_MANDATE_RENEWAL],
        "customer_message_required": True,
    }
    payment_link = {
        **_record("subscription"),
        "chosen_actions": [SEND_PAYMENT_LINK, STOP_AUTOMATION],
        "customer_message_required": True,
    }
    balance = {
        **_record("emi"),
        "chosen_actions": [SEND_BALANCE_REMINDER, RETRY_AUTOPAY],
        "customer_message_required": True,
    }

    assert "mandate renew" in str(fallback_message(renewal))
    assert "payment link" in str(fallback_message(payment_link))
    assert "sufficient balance" in str(fallback_message(balance))


def test_terminal_action_message_explains_stop_link_and_support_path() -> None:
    record = {
        **_record("insurance"),
        "chosen_actions": [CREATE_SUPPORT_CASE, SEND_PAYMENT_LINK, STOP_AUTOMATION],
        "customer_message_required": True,
    }

    message = str(fallback_message(record)).casefold()
    assert "automatic retries rok diye" in message
    assert "payment link" in message
    assert "customer support" in message


def test_quiet_retry_produces_no_customer_message() -> None:
    quiet = {
        **_record("subscription", "bank_server_down"),
        "chosen_actions": [RETRY_AUTOPAY],
        "customer_message_required": False,
    }

    output = add_customer_messages([quiet], api_key=None)
    assert output[0]["customer_message"] is None
