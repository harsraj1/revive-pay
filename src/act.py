"""Execute deterministic retry simulations and produce an auditable report."""

from __future__ import annotations

import hashlib
import json
import random
from datetime import date as datetime_date
from datetime import time as datetime_time
from pathlib import Path
from typing import Any, Mapping

try:
    from .constants import EXECUTION_TIMEZONE, SIMULATION_SEED, SUCCESS_PROBABILITIES
    from .time_utils import combine_ist, ist_isoformat, parse_ist_datetime
except ImportError:  # Supports direct execution: python src/act.py
    from constants import (  # type: ignore[no-redef]
        EXECUTION_TIMEZONE,
        SIMULATION_SEED,
        SUCCESS_PROBABILITIES,
    )
    from time_utils import (  # type: ignore[no-redef]
        combine_ist,
        ist_isoformat,
        parse_ist_datetime,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_PATH = PROJECT_ROOT / "data" / "message_output.json"
DEFAULT_AUDIT_PATH = PROJECT_ROOT / "logs" / "audit_trail.json"
DEFAULT_REPORT_PATH = PROJECT_ROOT / "data" / "final_report.json"


def deterministic_draw(seed: int, mandate_id: str, attempt_num: int) -> float:
    """Return a stable pseudo-random draw for one mandate attempt.

    hashlib creates a stable integer seed. This avoids Python process hash
    randomization and means reordering mandates does not change their outcomes.
    """

    identity = f"{seed}:{mandate_id}:{attempt_num}".encode("utf-8")
    attempt_seed = int.from_bytes(hashlib.sha256(identity).digest()[:8], "big")
    return random.Random(attempt_seed).random()


def _normalize_scheduled_attempt(attempt: Mapping[str, Any]) -> dict[str, Any]:
    """Return a schedule entry with one canonical, explicitly IST timestamp."""

    if attempt.get("scheduled_at"):
        scheduled_at = parse_ist_datetime(str(attempt["scheduled_at"]))
    else:
        scheduled_at = combine_ist(
            datetime_date.fromisoformat(str(attempt["scheduled_date"])),
            datetime_time.fromisoformat(str(attempt["scheduled_time"])),
        )
    return {
        **attempt,
        "scheduled_date": scheduled_at.date().isoformat(),
        "scheduled_time": scheduled_at.strftime("%H:%M"),
        "scheduled_at": ist_isoformat(scheduled_at),
        "timezone": EXECUTION_TIMEZONE,
    }


def _base_audit_entry(record: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve the context required to explain every execution decision."""

    scheduled_retries = record.get("retry_schedule", [])
    if not isinstance(scheduled_retries, list):
        raise ValueError(f"Mandate {record.get('mandate_id')} has an invalid retry schedule")

    return {
        "mandate_id": record["mandate_id"],
        "customer_name": record.get("customer_name"),
        "category": record["category"],
        "amount": record["amount"],
        "failure_reason": record["failure_reason"],
        "failed_at": ist_isoformat(parse_ist_datetime(str(record["failed_at"]))),
        "audit_timezone": EXECUTION_TIMEZONE,
        "retriable": record.get("retriable", False),
        "retry_rule_explanation": record.get("rule_explanation"),
        "schedule_justification": record.get("schedule_justification"),
        "customer_message": record.get("customer_message"),
        "urgency_priority": record.get("urgency_priority"),
        "escalate_after_attempts": record.get("escalate_after_attempts"),
        "attempts_already_made": record.get("attempts_already_made", 0),
        "scheduled_retries": [
            _normalize_scheduled_attempt(attempt) for attempt in scheduled_retries
        ],
        "attempts_made": [],
        "final_status": None,
        "stop_reason": None,
    }


def simulate_mandate(
    record: Mapping[str, Any], seed: int = SIMULATION_SEED
) -> dict[str, Any]:
    """Simulate one mandate sequentially and return its complete audit entry."""

    audit = _base_audit_entry(record)
    if record.get("retriable") is not True:
        audit["final_status"] = "non_retriable"
        audit["stop_reason"] = (
            "Deterministic failure policy prohibited automatic retry."
        )
        return audit

    schedule = record.get("retry_schedule")
    if not isinstance(schedule, list):
        raise ValueError(f"Mandate {record.get('mandate_id')} has an invalid retry schedule")

    attempts_before_simulation = int(record.get("attempts_already_made", 0))
    threshold = int(record["escalate_after_attempts"])
    probability = SUCCESS_PROBABILITIES.get(str(record.get("failure_reason")), 0.0)

    for scheduled_attempt in schedule:
        normalized_attempt = _normalize_scheduled_attempt(scheduled_attempt)
        attempts_so_far = attempts_before_simulation + len(audit["attempts_made"])

        # Check before executing the next payment. Reaching the threshold means
        # support should intervene instead of allowing another blind retry.
        if attempts_so_far >= threshold:
            audit["final_status"] = "escalated"
            audit["stop_reason"] = (
                f"Escalated before attempt {normalized_attempt['attempt_num']} because "
                f"the category threshold of {threshold} attempt(s) was reached."
            )
            return audit

        attempt_num = int(normalized_attempt["attempt_num"])
        draw = deterministic_draw(seed, str(record["mandate_id"]), attempt_num)
        successful = draw < probability

        # Record both the decision inputs and outcome so an auditor can replay
        # the exact comparison rather than trusting only the final status.
        audit["attempts_made"].append(
            {
                "attempt_num": attempt_num,
                "scheduled_date": normalized_attempt["scheduled_date"],
                "scheduled_time": normalized_attempt["scheduled_time"],
                "scheduled_at": normalized_attempt["scheduled_at"],
                "executed_at": normalized_attempt["scheduled_at"],
                "timezone": EXECUTION_TIMEZONE,
                "window_label": normalized_attempt["window_label"],
                "decision": "retry_executed",
                "why_retried": record.get("rule_explanation"),
                "success_probability": probability,
                "random_draw": round(draw, 6),
                "outcome": "success" if successful else "failure",
            }
        )

        if successful:
            audit["final_status"] = "recovered"
            audit["stop_reason"] = (
                f"Payment recovered on attempt {attempt_num}; no further retries were needed."
            )
            return audit

    audit["final_status"] = "exhausted"
    audit["stop_reason"] = (
        "All scheduled retries were used without recovery; no further automatic attempt exists."
    )
    return audit


def simulate_execution(
    records: list[Mapping[str, Any]], seed: int = SIMULATION_SEED
) -> list[dict[str, Any]]:
    """Simulate all mandates while preserving their input order."""

    return [simulate_mandate(record, seed) for record in records]


def build_final_report(
    audit_entries: list[Mapping[str, Any]], seed: int = SIMULATION_SEED
) -> dict[str, Any]:
    """Aggregate portfolio recovery metrics from the audit trail."""

    total_count = len(audit_entries)
    total_amount = sum(int(entry["amount"]) for entry in audit_entries)
    recovered = [entry for entry in audit_entries if entry["final_status"] == "recovered"]
    recovered_amount = sum(int(entry["amount"]) for entry in recovered)

    # Rates are percentages. Zero guards keep the report valid for empty input.
    count_rate = (len(recovered) / total_count * 100) if total_count else 0.0
    amount_rate = (recovered_amount / total_amount * 100) if total_amount else 0.0

    return {
        "total_mandates": total_count,
        "total_at_risk_amount": total_amount,
        "recovered_count": len(recovered),
        "recovered_amount": recovered_amount,
        "recovery_rate_by_count": round(count_rate, 2),
        "recovery_rate_by_amount": round(amount_rate, 2),
        "escalated_count": sum(
            entry["final_status"] == "escalated" for entry in audit_entries
        ),
        "non_retriable_count": sum(
            entry["final_status"] == "non_retriable" for entry in audit_entries
        ),
        "exhausted_count": sum(
            entry["final_status"] == "exhausted" for entry in audit_entries
        ),
        "simulation_seed": seed,
    }


def write_outputs(
    audit_entries: list[Mapping[str, Any]],
    report: Mapping[str, Any],
    audit_path: Path = DEFAULT_AUDIT_PATH,
    report_path: Path = DEFAULT_REPORT_PATH,
) -> None:
    """Persist the audit and summary as readable UTF-8 JSON."""

    audit_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps(audit_entries, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """Simulate message-stage records and write audit and portfolio metrics."""

    records = json.loads(DEFAULT_INPUT_PATH.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"Expected a JSON array in {DEFAULT_INPUT_PATH}")

    audit_entries = simulate_execution(records)
    report = build_final_report(audit_entries)
    write_outputs(audit_entries, report)
    print(f"Simulated {len(audit_entries)} mandates; audit: {DEFAULT_AUDIT_PATH}")
    print(f"Final report: {DEFAULT_REPORT_PATH}")


if __name__ == "__main__":
    main()
