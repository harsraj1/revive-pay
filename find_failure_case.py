"""Extract a real escalated payment case from the execution audit trail."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_AUDIT_PATH = PROJECT_ROOT / "logs" / "audit_trail.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "docs" / "failure_case_example.md"


def select_escalated_case(
    audit_entries: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Select an actual escalation, preferring high-priority audit entries."""

    escalated = [
        entry for entry in audit_entries if entry.get("final_status") == "escalated"
    ]
    if not escalated:
        raise ValueError("Audit trail contains no escalated case; refusing to fabricate one")

    return next(
        (
            entry
            for entry in escalated
            if entry.get("urgency_priority") == "high"
        ),
        escalated[0],
    )


def _attempt_lines(attempts: object) -> list[str]:
    """Render recorded attempts without adding inferred events."""

    if not isinstance(attempts, list) or not attempts:
        return ["- No retry was executed during the simulation before escalation."]

    lines: list[str] = []
    for attempt in attempts:
        if not isinstance(attempt, Mapping):
            raise ValueError("Selected audit case contains an invalid attempt entry")
        lines.append(
            "- Attempt {attempt_num} at {scheduled_date} {scheduled_time} "
            "({window_label}): {outcome}.".format(**attempt)
        )
    return lines


def render_failure_case(case: Mapping[str, Any]) -> str:
    """Render one verified escalated audit entry as Markdown."""

    if case.get("final_status") != "escalated":
        raise ValueError("Only an actual escalated audit entry can be rendered")

    required = (
        "mandate_id",
        "customer_name",
        "category",
        "amount",
        "failure_reason",
        "failed_at",
        "urgency_priority",
        "attempts_already_made",
        "escalate_after_attempts",
        "scheduled_retries",
        "attempts_made",
        "final_status",
        "stop_reason",
    )
    missing = [field for field in required if field not in case]
    if missing:
        raise ValueError(f"Selected audit case is missing required fields: {missing}")

    scheduled = case["scheduled_retries"]
    if not isinstance(scheduled, list):
        raise ValueError("Selected audit case contains an invalid retry schedule")
    scheduled_lines = [
        "- Attempt {attempt_num}: {scheduled_date} at {scheduled_time} "
        "({window_label}).".format(**attempt)
        for attempt in scheduled
    ] or ["- No retry was scheduled."]

    lines = [
        "# Actual Escalated Failure Case",
        "",
        "> Source: `logs/audit_trail.json`. This example is extracted from an "
        "actual recorded simulation entry and is not fabricated.",
        "",
        "## Mandate",
        "",
        f"- Mandate ID: `{case['mandate_id']}`",
        f"- Customer: {case['customer_name']}",
        f"- Category: `{case['category']}`",
        f"- Amount: {case['amount']}",
        f"- Failure reason: `{case['failure_reason']}`",
        f"- Failed at: `{case['failed_at']}`",
        f"- Urgency priority: `{case['urgency_priority']}`",
        f"- Attempts already made: {case['attempts_already_made']}",
        f"- Escalation threshold: {case['escalate_after_attempts']}",
        "",
        "## Scheduled Retries",
        "",
        *scheduled_lines,
        "",
        "## Executed Attempts",
        "",
        *_attempt_lines(case["attempts_made"]),
        "",
        "## Recorded Outcome",
        "",
        f"- Final status: `{case['final_status']}`",
        f"- Stop reason: {case['stop_reason']}",
        "",
    ]
    return "\n".join(lines)


def generate_failure_case(
    audit_path: Path = DEFAULT_AUDIT_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
) -> Mapping[str, Any]:
    """Load the audit, select a real escalation, and write its documentation."""

    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected a JSON array in {audit_path}")

    selected = select_escalated_case(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_failure_case(selected), encoding="utf-8")
    return selected


def main() -> None:
    selected = generate_failure_case()
    print(
        f"Documented actual escalated case {selected['mandate_id']} at "
        f"{DEFAULT_OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
