"""Route failed mandates to deterministic, policy-bounded interventions.

The router is deliberately a decision table, not a scoring system. It may
select only actions declared in ``constants.py`` and it never chooses retry
dates; ``decide.py`` remains responsible for schedule construction.
"""

from __future__ import annotations

from typing import Any, Final, Mapping, TypedDict

try:
    from .constants import (
        AMOUNT_BAND_MAXIMUMS,
        CATEGORIES,
        CATEGORY_POLICIES,
        CREATE_SUPPORT_CASE,
        FAILURE_REASONS,
        MAX_RETRIES,
        PERMITTED_INTERVENTION_ACTIONS,
        REQUEST_MANDATE_RENEWAL,
        RETRY_AUTOPAY,
        SEND_BALANCE_REMINDER,
        SEND_PAYMENT_LINK,
        STOP_AUTOMATION,
        WAIT_FOR_DAILY_RESET,
    )
except ImportError:  # Supports direct execution imports from src/.
    from constants import (  # type: ignore[no-redef]
        AMOUNT_BAND_MAXIMUMS,
        CATEGORIES,
        CATEGORY_POLICIES,
        CREATE_SUPPORT_CASE,
        FAILURE_REASONS,
        MAX_RETRIES,
        PERMITTED_INTERVENTION_ACTIONS,
        REQUEST_MANDATE_RENEWAL,
        RETRY_AUTOPAY,
        SEND_BALANCE_REMINDER,
        SEND_PAYMENT_LINK,
        STOP_AUTOMATION,
        WAIT_FOR_DAILY_RESET,
    )


HIGH_VALUE_CATEGORIES: Final[frozenset[str]] = frozenset({"emi", "insurance"})


class InterventionDecision(TypedDict):
    """Serializable output attached to every record by ``decide.py``."""

    amount_band: str
    chosen_actions: list[str]
    primary_action: str
    intervention_reason: str
    customer_message_required: bool
    intervention_rule: str


class InterventionPolicyError(ValueError):
    """Raised when an action request violates a hard intervention rule."""


class BaseActionPolicy(TypedDict):
    actions: tuple[str, ...]
    customer_message_required: bool
    reason: str


# Failure reason chooses the base action set. Category, amount band, and prior
# attempts apply bounded overrides below. Keeping this table as data makes a
# policy review possible without reading branching or probabilistic code.
FAILURE_ACTION_TABLE: Final[dict[str, BaseActionPolicy]] = {
    "insufficient_balance": {
        "actions": (SEND_BALANCE_REMINDER, RETRY_AUTOPAY),
        "customer_message_required": True,
        "reason": (
            "A balance reminder is required before the delayed, "
            "policy-scheduled AutoPay retry."
        ),
    },
    "bank_server_down": {
        "actions": (RETRY_AUTOPAY,),
        "customer_message_required": False,
        "reason": (
            "The bank outage is transient, so a quiet AutoPay retry is allowed "
            "without unnecessary customer communication."
        ),
    },
    "technical_decline": {
        "actions": (RETRY_AUTOPAY,),
        "customer_message_required": True,
        "reason": "A bounded AutoPay retry is allowed for the transient technical decline.",
    },
    "daily_limit_exceeded": {
        "actions": (WAIT_FOR_DAILY_RESET, RETRY_AUTOPAY),
        "customer_message_required": True,
        "reason": (
            "Automation must wait for the next daily-limit reset before the "
            "policy-scheduled AutoPay retry."
        ),
    },
    "mandate_expired": {
        "actions": (REQUEST_MANDATE_RENEWAL,),
        "customer_message_required": True,
        "reason": (
            "The mandate is no longer valid; renewal is required and AutoPay "
            "retry is forbidden."
        ),
    },
    "user_declined": {
        "actions": (SEND_PAYMENT_LINK, STOP_AUTOMATION),
        "customer_message_required": True,
        "reason": (
            "The customer declined the mandate debit, so automatic retries stop "
            "and only a customer-initiated payment path is permitted."
        ),
    },
    "account_frozen": {
        "actions": (CREATE_SUPPORT_CASE, STOP_AUTOMATION),
        "customer_message_required": True,
        "reason": (
            "A frozen account cannot be retried safely; automation stops and a "
            "support case is required."
        ),
    },
}


def classify_amount_band(amount: object) -> str:
    """Classify a positive whole-INR amount using central band definitions."""

    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise InterventionPolicyError("amount must be a positive whole-INR integer")

    for band, maximum in AMOUNT_BAND_MAXIMUMS.items():
        if maximum is None or amount <= maximum:
            return band
    raise InterventionPolicyError("amount does not match a configured amount band")


def _validated_attempt_count(value: object) -> int:
    if isinstance(value, bool):
        raise InterventionPolicyError("attempts_already_made must be a non-negative integer")
    try:
        attempts = int(value)
    except (TypeError, ValueError) as error:
        raise InterventionPolicyError(
            "attempts_already_made must be a non-negative integer"
        ) from error
    if attempts < 0:
        raise InterventionPolicyError("attempts_already_made cannot be negative")
    return attempts


def validate_intervention_actions(
    record: Mapping[str, Any], actions: list[str] | tuple[str, ...]
) -> None:
    """Enforce the closed action set and irreversible safety constraints.

    This check is intentionally separate from routing so future adapters cannot
    bypass the expired-mandate prohibition by constructing actions manually.
    """

    unknown = set(actions) - PERMITTED_INTERVENTION_ACTIONS
    if unknown:
        raise InterventionPolicyError(f"unknown intervention action(s): {sorted(unknown)}")
    if not actions:
        raise InterventionPolicyError("at least one intervention action is required")
    if len(set(actions)) != len(actions):
        raise InterventionPolicyError("duplicate intervention actions are not permitted")

    reason = record.get("failure_reason")
    attempts = _validated_attempt_count(record.get("attempts_already_made", 0))
    if reason == "mandate_expired" and RETRY_AUTOPAY in actions:
        raise InterventionPolicyError(
            "RETRY_AUTOPAY is forbidden when the mandate has expired"
        )
    if attempts >= MAX_RETRIES and RETRY_AUTOPAY in actions:
        raise InterventionPolicyError(
            "RETRY_AUTOPAY is forbidden after the retry cap has been reached"
        )

    configured = FAILURE_REASONS.get(reason) if isinstance(reason, str) else None
    if (configured is None or not configured["retriable"]) and RETRY_AUTOPAY in actions:
        raise InterventionPolicyError(
            "RETRY_AUTOPAY requires an explicitly retriable failure policy"
        )


def _decision(
    *,
    record: Mapping[str, Any],
    amount_band: str,
    actions: tuple[str, ...],
    reason: str,
    customer_message_required: bool,
    rule: str,
) -> InterventionDecision:
    serialized_actions = list(actions)
    validate_intervention_actions(record, serialized_actions)
    return {
        "amount_band": amount_band,
        "chosen_actions": serialized_actions,
        "primary_action": serialized_actions[0],
        "intervention_reason": reason,
        "customer_message_required": customer_message_required,
        "intervention_rule": rule,
    }


def route_intervention(record: Mapping[str, Any]) -> InterventionDecision:
    """Select deterministic interventions from the full policy tuple.

    Evaluation order is part of the policy: expired mandates override every
    other dimension, followed by global retry caps, category-specific caps,
    and finally the failure-reason table. Unknown reasons fail closed.
    """

    failure_reason = record.get("failure_reason")
    category = record.get("category")
    attempts = _validated_attempt_count(record.get("attempts_already_made", 0))
    amount_band = classify_amount_band(record.get("amount"))

    if not isinstance(category, str) or category not in CATEGORIES:
        return _decision(
            record=record,
            amount_band=amount_band,
            actions=(CREATE_SUPPORT_CASE, STOP_AUTOMATION),
            reason=(
                f"Category {category!r} has no configured intervention policy; "
                "automation fails closed for human review."
            ),
            customer_message_required=False,
            rule="unknown_category_fail_closed",
        )

    # This prohibition is evaluated before attempt/category overrides so there
    # is no combination of inputs that can authorize an expired mandate retry.
    if failure_reason == "mandate_expired":
        base = FAILURE_ACTION_TABLE["mandate_expired"]
        return _decision(
            record=record,
            amount_band=amount_band,
            actions=base["actions"],
            reason=base["reason"],
            customer_message_required=base["customer_message_required"],
            rule="expired_mandate_renewal_only",
        )

    configured_failure = (
        FAILURE_REASONS.get(failure_reason) if isinstance(failure_reason, str) else None
    )
    if configured_failure is None:
        return _decision(
            record=record,
            amount_band=amount_band,
            actions=(CREATE_SUPPORT_CASE, STOP_AUTOMATION),
            reason=(
                f"Failure reason {failure_reason!r} has no intervention rule; "
                "automation fails closed for human review."
            ),
            customer_message_required=False,
            rule="unknown_failure_fail_closed",
        )

    # Known non-retriable failures keep their reason-specific safe alternative;
    # retry-cap logic applies only to failures for which retry was ever allowed.
    if not configured_failure["retriable"]:
        base = FAILURE_ACTION_TABLE[str(failure_reason)]
        return _decision(
            record=record,
            amount_band=amount_band,
            actions=base["actions"],
            reason=base["reason"],
            customer_message_required=base["customer_message_required"],
            rule=f"failure_reason_{failure_reason}",
        )

    if attempts >= MAX_RETRIES:
        if category in HIGH_VALUE_CATEGORIES or amount_band == "high":
            return _decision(
                record=record,
                amount_band=amount_band,
                actions=(CREATE_SUPPORT_CASE, STOP_AUTOMATION),
                reason=(
                    f"The global retry cap of {MAX_RETRIES} was reached for a "
                    "high-value mandate; support must take ownership and "
                    "automation must stop."
                ),
                customer_message_required=True,
                rule="retry_cap_high_value_support",
            )
        return _decision(
            record=record,
            amount_band=amount_band,
            actions=(SEND_PAYMENT_LINK, STOP_AUTOMATION),
            reason=(
                f"The global retry cap of {MAX_RETRIES} was reached; no further "
                "AutoPay attempt is permitted and a manual payment path is offered."
            ),
            customer_message_required=True,
            rule="retry_cap_payment_link",
        )

    # EMI and insurance use their existing category escalation threshold rather
    # than consuming the full global cap. This bounds automation for payments
    # whose interruption can have material repayment or coverage consequences.
    if category in HIGH_VALUE_CATEGORIES:
        threshold = CATEGORY_POLICIES[category]["escalate_after_attempts"]
        if attempts >= threshold:
            return _decision(
                record=record,
                amount_band=amount_band,
                actions=(CREATE_SUPPORT_CASE, SEND_PAYMENT_LINK, STOP_AUTOMATION),
                reason=(
                    f"The {category} intervention limit of {threshold} attempt(s) "
                    "was reached; support takes ownership, a manual payment path "
                    "is offered, and automatic retry stops."
                ),
                customer_message_required=True,
                rule="high_value_category_escalation",
            )

    base = FAILURE_ACTION_TABLE[str(failure_reason)]
    return _decision(
        record=record,
        amount_band=amount_band,
        actions=base["actions"],
        reason=base["reason"],
        customer_message_required=base["customer_message_required"],
        rule=f"failure_reason_{failure_reason}",
    )
