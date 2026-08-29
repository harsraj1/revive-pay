"""Tests for the deterministic intervention policy gate."""

import pytest

from src.constants import (
    CREATE_SUPPORT_CASE,
    MAX_RETRIES,
    PERMITTED_INTERVENTION_ACTIONS,
    REQUEST_MANDATE_RENEWAL,
    RETRY_AUTOPAY,
    SEND_BALANCE_REMINDER,
    SEND_PAYMENT_LINK,
    STOP_AUTOMATION,
    WAIT_FOR_DAILY_RESET,
)
from src.intervention_router import (
    InterventionPolicyError,
    route_intervention,
    validate_intervention_actions,
)


def _record(
    failure_reason: str,
    *,
    category: str = "subscription",
    amount: int = 999,
    attempts: int = 0,
) -> dict[str, object]:
    return {
        "mandate_id": "ROUTER-TEST-001",
        "failure_reason": failure_reason,
        "category": category,
        "amount": amount,
        "attempts_already_made": attempts,
    }


def test_insufficient_balance_reminds_then_allows_delayed_retry() -> None:
    decision = route_intervention(_record("insufficient_balance"))

    assert decision["chosen_actions"] == [SEND_BALANCE_REMINDER, RETRY_AUTOPAY]
    assert decision["customer_message_required"] is True
    assert "delayed" in decision["intervention_reason"]


def test_bank_server_down_is_quiet_retry_only() -> None:
    decision = route_intervention(_record("bank_server_down"))

    assert decision["chosen_actions"] == [RETRY_AUTOPAY]
    assert decision["customer_message_required"] is False


def test_daily_limit_waits_for_reset_before_retry() -> None:
    decision = route_intervention(_record("daily_limit_exceeded"))

    assert decision["chosen_actions"] == [WAIT_FOR_DAILY_RESET, RETRY_AUTOPAY]


@pytest.mark.parametrize(
    ("category", "amount", "attempts"),
    [
        ("subscription", 499, 0),
        ("emi", 25_000, 1),
        ("insurance", 75_000, MAX_RETRIES),
        ("credit_card", 150_000, MAX_RETRIES + 5),
    ],
)
def test_expired_mandate_never_returns_retry_autopay(
    category: str, amount: int, attempts: int
) -> None:
    record = _record(
        "mandate_expired",
        category=category,
        amount=amount,
        attempts=attempts,
    )
    decision = route_intervention(record)

    assert decision["chosen_actions"] == [REQUEST_MANDATE_RENEWAL]
    assert RETRY_AUTOPAY not in decision["chosen_actions"]


def test_expired_mandate_manual_retry_request_fails_closed() -> None:
    with pytest.raises(InterventionPolicyError, match="forbidden"):
        validate_intervention_actions(
            _record("mandate_expired"),
            [REQUEST_MANDATE_RENEWAL, RETRY_AUTOPAY],
        )


@pytest.mark.parametrize("category", ["emi", "insurance"])
def test_high_value_category_has_limited_retries_then_support(category: str) -> None:
    before_limit = route_intervention(
        _record("technical_decline", category=category, amount=10_000, attempts=1)
    )
    at_limit = route_intervention(
        _record("technical_decline", category=category, amount=10_000, attempts=2)
    )

    assert RETRY_AUTOPAY in before_limit["chosen_actions"]
    assert CREATE_SUPPORT_CASE in at_limit["chosen_actions"]
    assert SEND_PAYMENT_LINK in at_limit["chosen_actions"]
    assert STOP_AUTOMATION in at_limit["chosen_actions"]
    assert RETRY_AUTOPAY not in at_limit["chosen_actions"]


def test_repeated_failure_past_cap_gets_manual_payment_path_and_stops() -> None:
    decision = route_intervention(
        _record("insufficient_balance", amount=2_000, attempts=MAX_RETRIES)
    )

    assert decision["chosen_actions"] == [SEND_PAYMENT_LINK, STOP_AUTOMATION]
    assert RETRY_AUTOPAY not in decision["chosen_actions"]


def test_high_amount_past_cap_fails_to_support_instead_of_another_retry() -> None:
    decision = route_intervention(
        _record("technical_decline", amount=25_000, attempts=MAX_RETRIES)
    )

    assert decision["amount_band"] == "high"
    assert decision["chosen_actions"] == [CREATE_SUPPORT_CASE, STOP_AUTOMATION]


def test_router_can_emit_only_centrally_permitted_actions() -> None:
    for failure_reason in (
        "insufficient_balance",
        "bank_server_down",
        "technical_decline",
        "daily_limit_exceeded",
        "mandate_expired",
        "user_declined",
        "account_frozen",
        "unknown_new_reason",
    ):
        actions = route_intervention(_record(failure_reason))["chosen_actions"]
        assert set(actions) <= PERMITTED_INTERVENTION_ACTIONS

