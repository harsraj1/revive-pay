"""Central domain policies for the UPI recovery pipeline.

This module contains data only: compliance-like constraints, retry policy,
category urgency, and deterministic simulation settings. Keeping these values
in one place makes policy changes auditable and prevents business rules from
being scattered across pipeline stages.
"""

from typing import Final, TypedDict


class FailurePolicy(TypedDict):
    """Deterministic retry policy for a payment failure reason."""

    retriable: bool
    typical_recovery_window_days: int | None


class ExecutionWindow(TypedDict):
    """A permitted inclusive execution-time window in 24-hour format."""

    label: str
    start: str
    end: str
    timezone: str


class CategoryPolicy(TypedDict):
    """Amount generation and escalation policy for a payment category."""

    amount_min: int
    amount_max: int
    urgency_priority: str
    escalate_after_attempts: int
    urgency_note: str


CATEGORIES: Final[tuple[str, ...]] = (
    "subscription",
    "emi",
    "sip",
    "insurance",
    "credit_card",
)

FAILURE_POLICIES: Final[dict[str, FailurePolicy]] = {
    "insufficient_balance": {
        "retriable": True,
        "typical_recovery_window_days": 3,
    },
    "bank_server_down": {
        "retriable": True,
        "typical_recovery_window_days": 1,
    },
    "mandate_expired": {
        "retriable": False,
        "typical_recovery_window_days": None,
    },
    "user_declined": {
        "retriable": False,
        "typical_recovery_window_days": None,
    },
    "daily_limit_exceeded": {
        "retriable": True,
        "typical_recovery_window_days": 1,
    },
    "technical_decline": {
        "retriable": True,
        "typical_recovery_window_days": 1,
    },
    "account_frozen": {
        "retriable": False,
        "typical_recovery_window_days": None,
    },
}

# Explicit domain name used by the detection layer. The older name remains as
# an alias for compatibility; both references point to the same policy object.
FAILURE_REASONS: Final[dict[str, FailurePolicy]] = FAILURE_POLICIES

# Used by synthetic data generation; weights correspond to FAILURE_POLICIES order.
FAILURE_REASON_WEIGHTS: Final[dict[str, int]] = {
    "insufficient_balance": 35,
    "bank_server_down": 18,
    "mandate_expired": 8,
    "user_declined": 10,
    "daily_limit_exceeded": 12,
    "technical_decline": 14,
    "account_frozen": 3,
}

MAX_RETRIES: Final[int] = 3
EXECUTION_TIMEZONE: Final[str] = "Asia/Kolkata"

# Boundaries are inclusive. Windows deliberately avoid daytime banking peaks.
PERMITTED_EXECUTION_WINDOWS: Final[tuple[ExecutionWindow, ...]] = (
    {
        "label": "early_morning",
        "start": "06:00",
        "end": "08:30",
        "timezone": EXECUTION_TIMEZONE,
    },
    {
        "label": "afternoon",
        "start": "14:00",
        "end": "16:00",
        "timezone": EXECUTION_TIMEZONE,
    },
    {
        "label": "late_night",
        "start": "22:00",
        "end": "23:30",
        "timezone": EXECUTION_TIMEZONE,
    },
)

CATEGORY_POLICIES: Final[dict[str, CategoryPolicy]] = {
    "subscription": {
        "amount_min": 99,
        "amount_max": 2_999,
        "urgency_priority": "low",
        "escalate_after_attempts": MAX_RETRIES,
        "urgency_note": "Allow the full automated retry runway.",
    },
    "sip": {
        "amount_min": 500,
        "amount_max": 50_000,
        "urgency_priority": "medium",
        "escalate_after_attempts": MAX_RETRIES,
        "urgency_note": "Recover promptly to reduce investment disruption.",
    },
    "emi": {
        "amount_min": 2_000,
        "amount_max": 100_000,
        "urgency_priority": "high",
        "escalate_after_attempts": 2,
        "urgency_note": "Escalate early to limit repayment impact.",
    },
    "insurance": {
        "amount_min": 1_000,
        "amount_max": 75_000,
        "urgency_priority": "high",
        "escalate_after_attempts": 2,
        "urgency_note": "Escalate early to reduce risk of coverage disruption.",
    },
    "credit_card": {
        "amount_min": 1_000,
        "amount_max": 150_000,
        "urgency_priority": "high",
        "escalate_after_attempts": 2,
        "urgency_note": "Escalate early to reduce late-payment impact.",
    },
}

# Approximate educational probabilities, not production risk estimates.
SUCCESS_PROBABILITIES: Final[dict[str, float]] = {
    "insufficient_balance": 0.45,
    "bank_server_down": 0.80,
    "daily_limit_exceeded": 0.70,
    "technical_decline": 0.65,
}

DATA_GENERATION_SEED: Final[int] = 20_260_828
SIMULATION_SEED: Final[int] = 20_260_829
SYNTHETIC_RECORD_COUNT: Final[int] = 100
BASELINE_RETRY_INTERVAL_HOURS: Final[int] = 8
