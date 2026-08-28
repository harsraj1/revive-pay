"""Attach category-aware human escalation policy to scheduled mandates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

try:
    from .constants import CATEGORY_POLICIES, MAX_RETRIES
except ImportError:  # Supports direct execution: python src/escalate.py
    from constants import CATEGORY_POLICIES, MAX_RETRIES  # type: ignore[no-redef]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_PATH = PROJECT_ROOT / "data" / "decide_output.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "escalate_output.json"


def apply_escalation_policy(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of a mandate enriched with its category urgency policy.

    Escalation is intentionally declarative at this stage. The function says
    when automation should stop, but it does not execute or remove attempts.
    """

    category = record.get("category")
    if not isinstance(category, str) or category not in CATEGORY_POLICIES:
        # Unlike unknown payment failures, an unknown category indicates broken
        # pipeline data. Stop rather than silently applying the wrong risk policy.
        mandate_id = record.get("mandate_id", "<unknown>")
        raise ValueError(
            f"Mandate {mandate_id} has unsupported payment category {category!r}"
        )

    policy = CATEGORY_POLICIES[category]
    escalation_threshold = policy["escalate_after_attempts"]
    if not 0 <= escalation_threshold <= MAX_RETRIES:
        raise ValueError(
            f"Category {category} has invalid escalation threshold "
            f"{escalation_threshold}"
        )

    # Copying avoids mutating decide_output records held by the caller. Each
    # pipeline stage produces a new auditable representation of its input.
    return {
        **record,
        "urgency_priority": policy["urgency_priority"],
        "escalate_after_attempts": escalation_threshold,
        "urgency_note": policy["urgency_note"],
    }


def escalate_records(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Attach escalation policy to every mandate in input order."""

    return [apply_escalation_policy(record) for record in records]


def main() -> None:
    """Read scheduling output and write category-enriched escalation output."""

    records = json.loads(DEFAULT_INPUT_PATH.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"Expected a JSON array in {DEFAULT_INPUT_PATH}")

    output = escalate_records(records)
    DEFAULT_OUTPUT_PATH.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Attached escalation policy to {len(output)} mandates at {DEFAULT_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
