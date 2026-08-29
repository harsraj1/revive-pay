"""Validate Razorpay payment-failure webhooks at the ingestion boundary."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

try:
    from .constants import CATEGORIES, MAX_RETRIES
    from .time_utils import ist_isoformat, localize_ist
except ImportError:  # Supports direct execution imports from src/.
    from constants import CATEGORIES, MAX_RETRIES  # type: ignore[no-redef]
    from time_utils import ist_isoformat, localize_ist  # type: ignore[no-redef]


LOGGER = logging.getLogger(__name__)


class RecoveryNotes(BaseModel):
    """Merchant metadata required to join a Razorpay payment to this pipeline."""

    model_config = ConfigDict(extra="ignore", strict=True)

    customer_name: str = Field(min_length=1, max_length=120)
    recovery_category: str
    mandate_id: str | None = Field(default=None, min_length=1, max_length=100)
    attempts_already_made: int = Field(default=0, ge=0, le=MAX_RETRIES)
    recovery_amount_paise: int | None = Field(default=None, gt=0)

    @field_validator("customer_name")
    @classmethod
    def customer_name_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("customer_name cannot be blank")
        return cleaned

    @field_validator("recovery_category")
    @classmethod
    def category_must_be_supported(cls, value: str) -> str:
        if value not in CATEGORIES:
            raise ValueError(f"unsupported recovery category {value!r}")
        return value


class RazorpayPaymentEntity(BaseModel):
    """Relevant subset of a Razorpay `payment.failed` payment entity."""

    model_config = ConfigDict(extra="ignore", strict=True)

    id: str = Field(pattern=r"^pay_[A-Za-z0-9]+$")
    entity: Literal["payment"]
    amount: int = Field(gt=0, description="Razorpay amount in paise")
    currency: Literal["INR"]
    status: Literal["failed"]
    method: Literal["upi"]
    created_at: int = Field(gt=0, description="Unix timestamp in UTC")
    order_id: str | None = None
    invoice_id: str | None = None
    error_code: str | None = None
    error_description: str | None = None
    error_reason: str | None = None
    error_source: str | None = None
    error_step: str | None = None
    notes: RecoveryNotes

    @field_validator("amount")
    @classmethod
    def amount_must_be_whole_rupees(cls, value: int) -> int:
        if value % 100 != 0:
            raise ValueError(
                "amount must be divisible by 100 because the pipeline stores whole INR"
            )
        return value


class RazorpayEntityEnvelope(BaseModel):
    """Razorpay wraps each resource inside an `entity` object."""

    model_config = ConfigDict(extra="ignore", strict=True)

    entity: RazorpayPaymentEntity


class RazorpayPayload(BaseModel):
    """Relevant resources carried by the webhook."""

    model_config = ConfigDict(extra="ignore", strict=True)

    payment: RazorpayEntityEnvelope


class RazorpayPaymentFailedWebhook(BaseModel):
    """Validated external envelope for the supported webhook event."""

    model_config = ConfigDict(extra="ignore", strict=True)

    entity: Literal["event"]
    account_id: str = Field(pattern=r"^acc_[A-Za-z0-9]+$")
    event: Literal["payment.failed"]
    contains: list[str]
    created_at: int = Field(gt=0)
    payload: RazorpayPayload

    @field_validator("contains")
    @classmethod
    def contains_must_include_payment(cls, value: list[str]) -> list[str]:
        if "payment" not in value:
            raise ValueError("contains must include 'payment'")
        return value


class RazorpaySubscriptionEntity(BaseModel):
    """Relevant subset of a Razorpay subscription entity."""

    model_config = ConfigDict(extra="ignore", strict=True)

    id: str = Field(pattern=r"^sub_[A-Za-z0-9]+$")
    entity: Literal["subscription"]
    status: Literal["active", "pending", "halted"]
    auth_attempts: int = Field(default=0, ge=0)
    notes: RecoveryNotes


class RazorpaySubscriptionPaymentEntity(BaseModel):
    """Payment subset optionally included with subscription events."""

    model_config = ConfigDict(extra="ignore", strict=True)

    id: str = Field(pattern=r"^pay_[A-Za-z0-9]+$")
    entity: Literal["payment"]
    amount: int = Field(gt=0)
    currency: Literal["INR"]
    status: Literal["failed", "captured", "authorized"]
    method: Literal["upi"]
    created_at: int = Field(gt=0)
    error_code: str | None = None
    error_description: str | None = None
    error_reason: str | None = None

    @field_validator("amount")
    @classmethod
    def amount_must_be_whole_rupees(cls, value: int) -> int:
        if value % 100 != 0:
            raise ValueError(
                "amount must be divisible by 100 because the pipeline stores whole INR"
            )
        return value


class RazorpaySubscriptionEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    entity: RazorpaySubscriptionEntity


class RazorpaySubscriptionPaymentEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    entity: RazorpaySubscriptionPaymentEntity


class RazorpaySubscriptionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    subscription: RazorpaySubscriptionEnvelope
    payment: RazorpaySubscriptionPaymentEnvelope | None = None


class RazorpaySubscriptionWebhook(BaseModel):
    """Validated envelope for pending, charged, and halted subscription events."""

    model_config = ConfigDict(extra="ignore", strict=True)

    entity: Literal["event"]
    account_id: str = Field(pattern=r"^acc_[A-Za-z0-9]+$")
    event: Literal[
        "subscription.pending",
        "subscription.charged",
        "subscription.halted",
    ]
    contains: list[str]
    created_at: int = Field(gt=0)
    payload: RazorpaySubscriptionPayload

    @field_validator("contains")
    @classmethod
    def contains_must_include_subscription(cls, value: list[str]) -> list[str]:
        if "subscription" not in value:
            raise ValueError("contains must include 'subscription'")
        return value


class NormalizedMandateRecord(BaseModel):
    """Validated internal record emitted to the existing plain-dict pipeline."""

    model_config = ConfigDict(extra="forbid", strict=True)

    mandate_id: str = Field(min_length=1, max_length=100)
    customer_name: str = Field(min_length=1, max_length=120)
    category: str
    amount: int = Field(gt=0, description="Whole INR")
    failure_reason: str = Field(min_length=1, max_length=100)
    failed_at: datetime
    attempts_already_made: int = Field(ge=0, le=MAX_RETRIES)
    source_event: Literal[
        "payment.failed",
        "subscription.pending",
        "subscription.halted",
    ]
    source_account_id: str
    source_payment_id: str | None
    source_subscription_id: str | None = None
    source_error_code: str | None = None
    source_error_reason: str | None = None

    @field_validator("failed_at")
    @classmethod
    def failed_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("failed_at must be timezone-aware")
        return localize_ist(value)

    @field_validator("category")
    @classmethod
    def category_must_be_supported(cls, value: str) -> str:
        if value not in CATEGORIES:
            raise ValueError(f"unsupported recovery category {value!r}")
        return value


class WebhookNormalizationError(ValueError):
    """Raised when an external event cannot safely enter the pipeline."""


def _normalize_failure_reason(
    payment: RazorpayPaymentEntity | RazorpaySubscriptionPaymentEntity,
) -> str:
    """Map external error evidence to known policy keys, failing closed by default."""

    evidence = " ".join(
        value.casefold()
        for value in (
            payment.error_code,
            payment.error_reason,
            payment.error_description,
        )
        if value
    )
    mappings = (
        (("insufficient", "low_balance"), "insufficient_balance"),
        (("server", "gateway", "bank_unavailable", "bank_down"), "bank_server_down"),
        (("mandate_expired", "expired_mandate", "token_expired"), "mandate_expired"),
        (("user_declined", "customer_declined", "user_cancelled"), "user_declined"),
        (("daily_limit", "limit_exceeded", "transaction_limit"), "daily_limit_exceeded"),
        (("account_frozen", "frozen_account"), "account_frozen"),
        (("technical", "processing_error", "temporary_error"), "technical_decline"),
    )
    for markers, normalized_reason in mappings:
        if any(marker in evidence for marker in markers):
            return normalized_reason

    # This value is intentionally absent from FAILURE_REASONS. detect.py will
    # block automatic retry and set requires_human_review instead of guessing.
    return "unknown_razorpay_failure"


def _whole_rupees(amount_paise: int | None) -> int:
    if amount_paise is None:
        raise ValueError(
            "subscription event needs payment.amount or notes.recovery_amount_paise"
        )
    if amount_paise % 100 != 0:
        raise ValueError(
            "amount must be divisible by 100 because the pipeline stores whole INR"
        )
    return amount_paise // 100


def normalize_event(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize one Razorpay event into the pipeline dictionary.

    Pydantic is deliberately confined to this function's external boundary.
    Downstream modules continue to receive ordinary dictionaries and remain
    independent of the webhook transport and validation library.
    """

    try:
        event_type = payload.get("event")
        if event_type == "payment.failed":
            webhook = RazorpayPaymentFailedWebhook.model_validate(payload)
            payment = webhook.payload.payment.entity
            notes = payment.notes
            failed_at = datetime.fromtimestamp(payment.created_at, tz=timezone.utc)
            normalized = NormalizedMandateRecord(
                mandate_id=notes.mandate_id
                or payment.invoice_id
                or payment.order_id
                or payment.id,
                customer_name=notes.customer_name,
                category=notes.recovery_category,
                amount=payment.amount // 100,
                failure_reason=_normalize_failure_reason(payment),
                failed_at=failed_at,
                attempts_already_made=notes.attempts_already_made,
                source_event=webhook.event,
                source_account_id=webhook.account_id,
                source_payment_id=payment.id,
                source_error_code=payment.error_code,
                source_error_reason=payment.error_reason,
            )
        elif event_type in {"subscription.pending", "subscription.halted"}:
            webhook = RazorpaySubscriptionWebhook.model_validate(payload)
            subscription = webhook.payload.subscription.entity
            payment = (
                webhook.payload.payment.entity if webhook.payload.payment is not None else None
            )
            notes = subscription.notes
            amount_paise = payment.amount if payment else notes.recovery_amount_paise
            if event_type == "subscription.halted":
                failure_reason = "subscription_halted"
            elif payment is not None:
                failure_reason = _normalize_failure_reason(payment)
            else:
                # Pending proves a charge failed, but without payment error
                # evidence we cannot safely infer why it failed or permit retry.
                failure_reason = "unknown_subscription_failure"

            normalized = NormalizedMandateRecord(
                mandate_id=notes.mandate_id or subscription.id,
                customer_name=notes.customer_name,
                category=notes.recovery_category,
                amount=_whole_rupees(amount_paise),
                # Halted and evidence-free pending reasons deliberately remain
                # absent from FAILURE_REASONS and therefore fail closed.
                failure_reason=failure_reason,
                failed_at=datetime.fromtimestamp(webhook.created_at, tz=timezone.utc),
                attempts_already_made=min(
                    max(notes.attempts_already_made, subscription.auth_attempts),
                    MAX_RETRIES,
                ),
                source_event=webhook.event,
                source_account_id=webhook.account_id,
                source_payment_id=payment.id if payment else None,
                source_subscription_id=subscription.id,
                source_error_code=payment.error_code if payment else None,
                source_error_reason=payment.error_reason if payment else None,
            )
        elif event_type == "subscription.charged":
            webhook = RazorpaySubscriptionWebhook.model_validate(payload)
            subscription = webhook.payload.subscription.entity
            if webhook.payload.payment is None:
                raise ValueError("subscription.charged must include a payment entity")
            payment = webhook.payload.payment.entity
            notes = subscription.notes
            if payment.status not in {"captured", "authorized"}:
                raise ValueError("subscription.charged payment must be captured or authorized")
            occurred_at = datetime.fromtimestamp(webhook.created_at, tz=timezone.utc)
            # A charged event is an observed success, not a failed mandate. This
            # plain dictionary is consumed by webhook_server's audit adapter.
            return {
                "mandate_id": notes.mandate_id or subscription.id,
                "customer_name": notes.customer_name,
                "category": notes.recovery_category,
                "amount": _whole_rupees(payment.amount),
                "occurred_at": ist_isoformat(occurred_at),
                "source_event": webhook.event,
                "source_account_id": webhook.account_id,
                "source_payment_id": payment.id,
                "source_subscription_id": subscription.id,
                "normalized_outcome": "recovered",
            }
        else:
            raise ValueError(f"unsupported Razorpay event type {event_type!r}")
    except (ValidationError, ValueError, TypeError) as error:
        message = f"Rejected malformed Razorpay webhook: {error}"
        LOGGER.error(message)
        raise WebhookNormalizationError(message) from error

    # JSON mode converts the aware datetime to the same ISO string format used
    # by generated records, while keeping Pydantic out of downstream stages.
    result = normalized.model_dump(mode="json")
    result["failed_at"] = ist_isoformat(normalized.failed_at)
    return result
