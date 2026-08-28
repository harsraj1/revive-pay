"""Extract a real escalated simulation case into a readable Markdown document."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

try:
    from .constants import CATEGORY_POLICIES
except ImportError:  # Supports direct execution: python src/find_failure_case.py
    from constants import CATEGORY_POLICIES  # type: ignore[no-redef]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT_PATH = PROJECT_ROOT / "logs" / "audit_trail.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "docs" / "failure_case_example.md"
PREFERRED_CATEGORIES = ("insurance", "emi", "credit_card")


def select_escalated_case(
    audit_entries: list[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Choose an actual high-priority escalation with a useful attempt history."""

    escalated = [entry for entry in audit_entries if entry.get("final_status") == "escalated"]
    if not escalated:
        return None

    # Prefer requested high-risk categories. Within each category, a longer
    # executed history makes the educational walkthrough more informative.
    for category in PREFERRED_CATEGORIES:
        matching = [entry for entry in escalated if entry.get("category") == category]
        if matching:
            return max(matching, key=lambda entry: len(entry.get("attempts_made", [])))
    return max(escalated, key=lambda entry: len(entry.get("attempts_made", [])))


def render_failure_case(case: Mapping[str, Any] | None) -> str:
    """Render one real audit entry, or explicitly report that none exists."""

    if case is None:
        return (
            "# Failure Case Example\n\n"
            "No escalated mandate was produced for the current simulation seed.\n"
        )

    category = str(case["category"])
    category_policy = CATEGORY_POLICIES.get(category)
    attempts = case.get("attempts_made", [])
    lines = [
        "# Failure Case Example",
        "",
        "> This case was extracted directly from `logs/audit_trail.json`; it is not fabricated.",
        "",
        "## Mandate",
        "",
        f"- Mandate ID: `{case['mandate_id']}`",
        f"- Customer: {case.get('customer_name', 'Not recorded')}",
        f"- Amount: ₹{int(case['amount']):,}",
        f"- Category: `{category}`",
        f"- Urgency: `{case.get('urgency_priority')}`",
        f"- Escalation threshold: {case.get('escalate_after_attempts')} attempts",
        "",
        "## Initial failure",
        "",
        f"- Failed at: `{case['failed_at']}`",
        f"- Failure reason: `{case['failure_reason']}`",
        f"- Retry rule: {case.get('retry_rule_explanation')}",
        f"- Schedule rationale: {case.get('schedule_justification')}",
        "",
        "## Executed retries",
        "",
    ]

    if attempts:
        lines.extend(
            [
                "| Attempt | Scheduled | Window | Probability | Draw | Outcome |",
                "|---:|---|---|---:|---:|---|",
            ]
        )
        for attempt in attempts:
            scheduled = f"{attempt['scheduled_date']} {attempt['scheduled_time']}"
            lines.append(
                f"| {attempt['attempt_num']} | {scheduled} | {attempt['window_label']} | "
                f"{attempt['success_probability']:.2f} | {attempt['random_draw']:.6f} | "
                f"{attempt['outcome']} |"
            )
    else:
        lines.append(
            "No new retry was executed because the escalation threshold had already been reached."
        )

    urgency_note = (
        category_policy["urgency_note"] if category_policy else "Category requires manual review."
    )
    lines.extend(
        [
            "",
            "## Final result",
            "",
            f"- Status: `{case['final_status']}`",
            f"- Stop reason: {case.get('stop_reason')}",
            "",
            "## Why escalation was correct",
            "",
            f"The centralized `{category}` policy says: {urgency_note} "
            f"The engine therefore stopped before exceeding its configured threshold of "
            f"{case.get('escalate_after_attempts')} attempts and handed the case to human support.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    """Read the audit trail and write one real escalation example."""

    entries = json.loads(DEFAULT_AUDIT_PATH.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError(f"Expected a JSON array in {DEFAULT_AUDIT_PATH}")

    selected = select_escalated_case(entries)
    DEFAULT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT_PATH.write_text(render_failure_case(selected), encoding="utf-8")
    if selected is None:
        print(f"No escalated case found; wrote notice to {DEFAULT_OUTPUT_PATH}")
    else:
        print(
            f"Wrote actual escalated case {selected['mandate_id']} to {DEFAULT_OUTPUT_PATH}"
        )


if __name__ == "__main__":
    main()
