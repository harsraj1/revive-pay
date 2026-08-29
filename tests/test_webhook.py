"""Flask client tests for signed and idempotent Razorpay webhook ingestion."""

from __future__ import annotations

import hashlib
import hmac
import json
from copy import deepcopy
from pathlib import Path

import pytest

from src.webhook_server import create_app


TEST_SECRET = "test-webhook-secret"


@pytest.fixture
def valid_payment_failed_payload() -> dict[str, object]:
    return {
        "entity": "event",
        "account_id": "acc_Webhook123",
        "event": "payment.failed",
        "contains": ["payment"],
        "created_at": 1788134401,
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_Webhook123",
                    "entity": "payment",
                    "amount": 250000,
                    "currency": "INR",
                    "status": "failed",
                    "method": "upi",
                    "created_at": 1788134400,
                    "order_id": "order_Webhook123",
                    "invoice_id": None,
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "Payment failed due to insufficient balance",
                    "error_reason": "insufficient_funds",
                    "notes": {
                        "customer_name": "Aditi Rao",
                        "recovery_category": "emi",
                        "mandate_id": "MANDATE-WEBHOOK-001",
                        "attempts_already_made": 1,
                    },
                }
            }
        },
    }


@pytest.fixture
def duplicate_event_payload(valid_payment_failed_payload) -> dict[str, object]:
    return deepcopy(valid_payment_failed_payload)


@pytest.fixture
def invalid_signature_payload(valid_payment_failed_payload) -> dict[str, object]:
    return deepcopy(valid_payment_failed_payload)


@pytest.fixture
def webhook_client(tmp_path: Path):
    audit_path = tmp_path / "audit_trail.json"
    app = create_app(
        webhook_secret=TEST_SECRET,
        database_path=tmp_path / "seen_events.sqlite3",
        audit_path=audit_path,
    )
    app.config["TESTING"] = True
    return app.test_client(), audit_path


def _encoded_request(payload: dict[str, object]) -> tuple[bytes, str]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(TEST_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, signature


def _post(client, payload: dict[str, object], event_id: str, signature: str | None = None):
    body, valid_signature = _encoded_request(payload)
    return client.post(
        "/webhooks/razorpay",
        data=body,
        content_type="application/json",
        headers={
            "X-Razorpay-Signature": signature or valid_signature,
            "x-razorpay-event-id": event_id,
        },
    )


def test_valid_payment_failed_is_processed_and_audited(
    webhook_client, valid_payment_failed_payload
) -> None:
    client, audit_path = webhook_client
    response = _post(client, valid_payment_failed_payload, "event-valid-001")

    assert response.status_code == 200
    assert response.get_json()["status"] == "processed"
    assert response.get_json()["execution_adapter"] == "simulated"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert len(audit) == 1
    assert audit[0]["mandate_id"] == "MANDATE-WEBHOOK-001"
    assert audit[0]["source_event"] == "payment.failed"
    assert audit[0]["source_payment_id"] == "pay_Webhook123"


def test_duplicate_event_returns_200_without_second_audit(
    webhook_client, duplicate_event_payload
) -> None:
    client, audit_path = webhook_client
    first = _post(client, duplicate_event_payload, "event-duplicate-001")
    second = _post(client, duplicate_event_payload, "event-duplicate-001")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json()["status"] == "duplicate"
    assert len(json.loads(audit_path.read_text(encoding="utf-8"))) == 1


def test_invalid_signature_returns_400_and_does_not_write_audit(
    webhook_client, invalid_signature_payload
) -> None:
    client, audit_path = webhook_client
    response = _post(
        client,
        invalid_signature_payload,
        "event-invalid-signature-001",
        signature="not-a-valid-signature",
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid webhook signature"
    assert not audit_path.exists()


@pytest.mark.parametrize("method", ["netbanking", "wallet"])
def test_signed_non_upi_failure_is_acknowledged_without_entering_pipeline(
    webhook_client, valid_payment_failed_payload, method: str
) -> None:
    client, audit_path = webhook_client
    payment = valid_payment_failed_payload["payload"]["payment"]["entity"]
    payment["method"] = method
    payment["notes"] = []

    response = _post(
        client,
        valid_payment_failed_payload,
        f"event-unsupported-{method}",
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "ignored"
    assert response.get_json()["reason"] == f"unsupported_payment_method:{method}"
    assert not audit_path.exists()


def test_signed_upi_failure_without_recovery_metadata_is_acknowledged_not_retried(
    webhook_client, valid_payment_failed_payload
) -> None:
    client, audit_path = webhook_client
    payment = valid_payment_failed_payload["payload"]["payment"]["entity"]
    payment["notes"] = []

    response = _post(
        client,
        valid_payment_failed_payload,
        "event-upi-missing-metadata",
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ignored",
        "reason": "missing_recovery_metadata",
        "event_id": "event-upi-missing-metadata",
    }
    assert not audit_path.exists()


def _subscription_payload(event: str, status: str) -> dict[str, object]:
    payment_status = "captured" if event == "subscription.charged" else "failed"
    return {
        "entity": "event",
        "account_id": "acc_Webhook123",
        "event": event,
        "contains": ["subscription", "payment"],
        "created_at": 1788134401,
        "payload": {
            "subscription": {
                "entity": {
                    "id": "sub_Webhook123",
                    "entity": "subscription",
                    "status": status,
                    "auth_attempts": 3 if status == "halted" else 0,
                    "notes": {
                        "customer_name": "Aditi Rao",
                        "recovery_category": "insurance",
                        "mandate_id": "MANDATE-SUB-001",
                    },
                }
            },
            "payment": {
                "entity": {
                    "id": "pay_Subscription123",
                    "entity": "payment",
                    "amount": 500000,
                    "currency": "INR",
                    "status": payment_status,
                    "method": "upi",
                    "created_at": 1788134400,
                    "error_reason": "insufficient_funds"
                    if payment_status == "failed"
                    else None,
                }
            },
        },
    }


def test_subscription_charged_is_recorded_as_confirmed_recovery(webhook_client) -> None:
    client, audit_path = webhook_client
    response = _post(
        client,
        _subscription_payload("subscription.charged", "active"),
        "event-subscription-charged",
    )

    assert response.status_code == 200
    assert response.get_json()["execution_adapter"] == "razorpay_confirmation"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit[0]["final_status"] == "recovered"
    assert audit[0]["source_event"] == "subscription.charged"


def test_subscription_halted_enters_fail_closed_pipeline(webhook_client) -> None:
    client, audit_path = webhook_client
    response = _post(
        client,
        _subscription_payload("subscription.halted", "halted"),
        "event-subscription-halted",
    )

    assert response.status_code == 200
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit[0]["failure_reason"] == "subscription_halted"
    assert audit[0]["final_status"] == "non_retriable"
    assert audit[0]["source_subscription_id"] == "sub_Webhook123"


def test_subscription_pending_uses_payment_failure_evidence(webhook_client) -> None:
    client, audit_path = webhook_client
    response = _post(
        client,
        _subscription_payload("subscription.pending", "pending"),
        "event-subscription-pending",
    )

    assert response.status_code == 200
    assert response.get_json()["execution_adapter"] == "simulated"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit[0]["failure_reason"] == "insufficient_balance"
    assert audit[0]["source_event"] == "subscription.pending"


def test_unrecognized_signed_event_is_acknowledged_and_ignored(webhook_client) -> None:
    client, audit_path = webhook_client
    payload = {"entity": "event", "event": "order.paid", "id": "payload-event-id"}
    response = _post(client, payload, "event-ignored")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ignored"
    assert not audit_path.exists()
