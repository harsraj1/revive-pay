"""Flask ingestion adapter for signed Razorpay Test Mode webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from flask import Flask, jsonify, request
from werkzeug.exceptions import BadRequest

try:
    from .act import simulate_mandate
    from .constants import EXECUTION_TIMEZONE, SIMULATION_SEED
    from .decide import decide_records
    from .detect import detect_records
    from .escalate import escalate_records
    from .ingest import WebhookNormalizationError, normalize_event
    from .message import add_customer_messages
    from .time_utils import ist_isoformat
except ImportError:  # Supports direct execution: python src/webhook_server.py
    from act import simulate_mandate  # type: ignore[no-redef]
    from constants import EXECUTION_TIMEZONE, SIMULATION_SEED  # type: ignore[no-redef]
    from decide import decide_records  # type: ignore[no-redef]
    from detect import detect_records  # type: ignore[no-redef]
    from escalate import escalate_records  # type: ignore[no-redef]
    from ingest import WebhookNormalizationError, normalize_event  # type: ignore[no-redef]
    from message import add_customer_messages  # type: ignore[no-redef]
    from time_utils import ist_isoformat  # type: ignore[no-redef]


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVENT_DB_PATH = PROJECT_ROOT / "data" / "webhook_events.sqlite3"
DEFAULT_AUDIT_PATH = PROJECT_ROOT / "logs" / "audit_trail.json"
SUPPORTED_EVENTS = {
    "payment.failed",
    "subscription.pending",
    "subscription.charged",
    "subscription.halted",
}
_AUDIT_LOCK = threading.Lock()


def validate_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """Compare Razorpay's signature with HMAC-SHA256 of the untouched body."""

    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@contextmanager
def _database_connection(database_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a transaction and always close its SQLite connection."""

    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=10)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS webhook_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        connection.commit()
        with connection:
            yield connection
    finally:
        connection.close()


def claim_event(database_path: Path, event_id: str, event_type: str) -> bool:
    """Atomically reserve an event ID; return False when it already exists."""

    recorded_at = ist_isoformat(datetime.now(tz=timezone.utc))
    with _database_connection(database_path) as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO webhook_events VALUES (?, ?, ?, ?)",
            (event_id, event_type, "processing", recorded_at),
        )
        return cursor.rowcount == 1


def complete_event(database_path: Path, event_id: str, status: str) -> None:
    """Mark a claimed event as processed or intentionally ignored."""

    with _database_connection(database_path) as connection:
        connection.execute(
            "UPDATE webhook_events SET status = ?, recorded_at = ? WHERE event_id = ?",
            (status, ist_isoformat(datetime.now(tz=timezone.utc)), event_id),
        )


def release_event(database_path: Path, event_id: str) -> None:
    """Release a failed claim so Razorpay can retry the event later."""

    with _database_connection(database_path) as connection:
        connection.execute("DELETE FROM webhook_events WHERE event_id = ?", (event_id,))


def append_audit_entry(audit_path: Path, entry: Mapping[str, Any]) -> None:
    """Append one entry atomically to the existing JSON audit array."""

    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with _AUDIT_LOCK:
        if audit_path.exists():
            entries = json.loads(audit_path.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                raise ValueError(f"Expected a JSON array in {audit_path}")
        else:
            entries = []
        entries.append(dict(entry))
        temporary_path = audit_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(audit_path)


def process_failed_mandate(
    normalized_record: Mapping[str, Any], seed: int = SIMULATION_SEED
) -> dict[str, Any]:
    """Run one normalized record through existing in-process stage functions."""

    detected = detect_records([normalized_record], api_key=None)[0]
    decided = decide_records([detected], api_key=None)[0]
    escalated = escalate_records([decided])[0]
    messaged = add_customer_messages([escalated], api_key=None)[0]

    # This remains a simulated execution adapter. The repository does not have
    # authenticated Razorpay Test Mode code that triggers a real UPI retry.
    return simulate_mandate(messaged, seed=seed)


def build_charged_audit(normalized: Mapping[str, Any]) -> dict[str, Any]:
    """Represent an externally confirmed subscription charge in audit format."""

    occurred_at = str(normalized["occurred_at"])
    return {
        "mandate_id": normalized["mandate_id"],
        "customer_name": normalized["customer_name"],
        "category": normalized["category"],
        "amount": normalized["amount"],
        "failure_reason": None,
        "failed_at": None,
        "audit_timezone": EXECUTION_TIMEZONE,
        "retriable": False,
        "retry_rule_explanation": None,
        "schedule_justification": None,
        "customer_message": None,
        "urgency_priority": None,
        "escalate_after_attempts": None,
        "attempts_already_made": 0,
        "scheduled_retries": [],
        "attempts_made": [
            {
                "attempt_num": 0,
                "scheduled_at": occurred_at,
                "executed_at": occurred_at,
                "timezone": EXECUTION_TIMEZONE,
                "window_label": None,
                "decision": "external_subscription_charge_confirmed",
                "why_retried": "Razorpay subscription.charged webhook confirmed payment.",
                "success_probability": None,
                "random_draw": None,
                "outcome": "success",
            }
        ],
        "final_status": "recovered",
        "stop_reason": "Razorpay confirmed the subscription charge; no recovery action needed.",
        "source_event": normalized["source_event"],
        "source_account_id": normalized["source_account_id"],
        "source_payment_id": normalized["source_payment_id"],
        "source_subscription_id": normalized["source_subscription_id"],
    }


def _event_id(payload: Mapping[str, Any]) -> str | None:
    header_id = request.headers.get("x-razorpay-event-id")
    payload_id = payload.get("id")
    candidate = header_id or (payload_id if isinstance(payload_id, str) else None)
    return candidate.strip() if candidate and candidate.strip() else None


def _out_of_scope_reason(
    payload: Mapping[str, Any], event_type: object
) -> str | None:
    """Identify valid Razorpay events that cannot enter this UPI pipeline.

    Razorpay represents empty ``notes`` as either ``[]`` or ``{}``. Those
    events and non-UPI failures are authentic but lack the business contract
    required by the recovery engine. Acknowledging them prevents retry storms;
    it does not reinterpret them as UPI mandates or let incomplete data flow.
    """

    raw_payload = payload.get("payload")
    if not isinstance(raw_payload, Mapping):
        return None  # Structural validation will return a clear 400 later.

    resource_name = "payment" if event_type == "payment.failed" else "subscription"
    envelope = raw_payload.get(resource_name)
    if not isinstance(envelope, Mapping):
        return None
    entity = envelope.get("entity")
    if not isinstance(entity, Mapping):
        return None

    payment_entity: object = entity if event_type == "payment.failed" else None
    if event_type != "payment.failed":
        payment_envelope = raw_payload.get("payment")
        if isinstance(payment_envelope, Mapping):
            payment_entity = payment_envelope.get("entity")
    if isinstance(payment_entity, Mapping):
        method = payment_entity.get("method")
        if isinstance(method, str) and method != "upi":
            return f"unsupported_payment_method:{method}"

    notes = entity.get("notes")
    has_recovery_metadata = (
        isinstance(notes, Mapping)
        and isinstance(notes.get("customer_name"), str)
        and bool(notes["customer_name"].strip())
        and isinstance(notes.get("recovery_category"), str)
        and bool(notes["recovery_category"].strip())
    )
    if not has_recovery_metadata:
        return "missing_recovery_metadata"
    return None


def create_app(
    *,
    webhook_secret: str | None = None,
    database_path: Path = DEFAULT_EVENT_DB_PATH,
    audit_path: Path = DEFAULT_AUDIT_PATH,
) -> Flask:
    """Create a configurable Flask app for production and isolated tests."""

    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass

    app = Flask(__name__)
    app.config["RAZORPAY_WEBHOOK_SECRET"] = webhook_secret
    app.config["EVENT_DATABASE_PATH"] = database_path
    app.config["AUDIT_PATH"] = audit_path

    @app.post("/webhooks/razorpay")
    def razorpay_webhook():
        raw_body = request.get_data(cache=True)
        signature = request.headers.get("X-Razorpay-Signature", "")
        secret = app.config["RAZORPAY_WEBHOOK_SECRET"] or os.getenv(
            "RAZORPAY_WEBHOOK_SECRET"
        )
        if not secret:
            LOGGER.error("RAZORPAY_WEBHOOK_SECRET is not configured")
            return jsonify(error="webhook secret is not configured"), 503
        if not signature or not validate_webhook_signature(raw_body, signature, secret):
            LOGGER.warning("Rejected Razorpay webhook with invalid signature")
            return jsonify(error="invalid webhook signature"), 400

        try:
            payload = request.get_json(force=False, silent=False)
        except BadRequest:
            return jsonify(error="request body must be valid JSON"), 400
        if not isinstance(payload, dict):
            return jsonify(error="webhook payload must be a JSON object"), 400

        event_type = payload.get("event")
        LOGGER.info("Received Razorpay webhook event=%r", event_type)
        event_id = _event_id(payload)
        if event_id is None:
            return jsonify(error="x-razorpay-event-id or payload id is required"), 400

        database = Path(app.config["EVENT_DATABASE_PATH"])
        if not claim_event(database, event_id, str(event_type)):
            LOGGER.info("Ignoring duplicate Razorpay event %s", event_id)
            return jsonify(status="duplicate", event_id=event_id), 200

        if event_type not in SUPPORTED_EVENTS:
            LOGGER.info("Ignoring unsupported Razorpay event %r", event_type)
            complete_event(database, event_id, "ignored")
            return jsonify(status="ignored", event_id=event_id), 200

        out_of_scope = _out_of_scope_reason(payload, event_type)
        if out_of_scope:
            LOGGER.info(
                "Acknowledging out-of-scope Razorpay event %s: %s",
                event_id,
                out_of_scope,
            )
            complete_event(database, event_id, f"ignored:{out_of_scope}")
            return (
                jsonify(
                    status="ignored",
                    reason=out_of_scope,
                    event_id=event_id,
                ),
                200,
            )

        try:
            normalized = normalize_event(payload)
            LOGGER.info(
                "Normalized Razorpay event=%s mandate_id=%s",
                event_type,
                normalized.get("mandate_id"),
            )
            if event_type == "subscription.charged":
                audit_entry = build_charged_audit(normalized)
            else:
                audit_entry = process_failed_mandate(normalized)
            append_audit_entry(Path(app.config["AUDIT_PATH"]), audit_entry)
            complete_event(database, event_id, "completed")
        except WebhookNormalizationError as error:
            release_event(database, event_id)
            return jsonify(error=str(error)), 400
        except Exception:
            release_event(database, event_id)
            LOGGER.exception("Razorpay webhook processing failed for %s", event_id)
            return jsonify(error="webhook processing failed"), 500

        return (
            jsonify(
                status="processed",
                event_id=event_id,
                mandate_id=audit_entry["mandate_id"],
                final_status=audit_entry["final_status"],
                execution_adapter="simulated"
                if event_type != "subscription.charged"
                else "razorpay_confirmation",
            ),
            200,
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "8000")), debug=False)
