"""Tests for Pydantic validation at the Razorpay ingestion boundary."""

from datetime import datetime, timedelta

from src.detect import classify_retriability
from src.ingest import WebhookNormalizationError, normalize_event


def _valid_payload() -> dict[str, object]:
    return {
        "entity": "event",
        "account_id": "acc_Test123",
        "event": "payment.failed",
        "contains": ["payment"],
        "created_at": 1788134401,
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_Test123",
                    "entity": "payment",
                    "amount": 250000,
                    "currency": "INR",
                    "status": "failed",
                    "method": "upi",
                    "created_at": 1788134400,
                    "order_id": "order_Test123",
                    "invoice_id": None,
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "Payment failed due to insufficient balance",
                    "error_reason": "insufficient_funds",
                    "notes": {
                        "customer_name": "Aditi Rao",
                        "recovery_category": "emi",
                        "mandate_id": "MANDATE-RZP-001",
                        "attempts_already_made": 1,
                    },
                }
            }
        },
    }


def test_valid_webhook_normalizes_to_pipeline_record() -> None:
    record = normalize_event(_valid_payload())

    assert record["mandate_id"] == "MANDATE-RZP-001"
    assert record["customer_name"] == "Aditi Rao"
    assert record["category"] == "emi"
    assert record["amount"] == 2500
    assert record["failure_reason"] == "insufficient_balance"
    assert record["attempts_already_made"] == 1
    assert datetime.fromisoformat(record["failed_at"]).utcoffset() == timedelta(
        hours=5, minutes=30
    )


def test_malformed_payload_is_logged_and_rejected(caplog) -> None:
    payload = _valid_payload()
    del payload["payload"]["payment"]["entity"]["amount"]  # type: ignore[index]

    try:
        normalize_event(payload)
    except WebhookNormalizationError as error:
        assert "Rejected malformed Razorpay" in str(error)
        assert "amount" in str(error)
    else:
        raise AssertionError("missing amount must reject the webhook")
    assert "Rejected malformed Razorpay" in caplog.text


def test_unmapped_failure_reason_flows_to_fail_closed_detection() -> None:
    payload = _valid_payload()
    payment = payload["payload"]["payment"]["entity"]  # type: ignore[index]
    payment["error_description"] = "Unrecognized issuer response"
    payment["error_reason"] = "brand_new_reason"

    record = normalize_event(payload)
    decision = classify_retriability(record)
    assert record["failure_reason"] == "unknown_razorpay_failure"
    assert decision["retriable"] is False
    assert decision["requires_human_review"] is True


def test_fractional_rupee_amount_is_rejected_not_truncated() -> None:
    payload = _valid_payload()
    payload["payload"]["payment"]["entity"]["amount"] = 250050  # type: ignore[index]

    try:
        normalize_event(payload)
    except WebhookNormalizationError as error:
        assert "divisible by 100" in str(error)
    else:
        raise AssertionError("fractional INR must not be truncated")
