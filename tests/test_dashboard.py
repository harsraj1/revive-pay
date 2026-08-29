"""Tests for the dashboard's read-only artifact projections."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.dashboard import (
    build_decision_trace,
    escalation_queue,
    failure_reason_distribution,
    find_explicit_policy_gate_case,
    multi_seed_summary_rows,
)


def test_policy_panel_does_not_fabricate_a_suggestion() -> None:
    audit = [
        {
            "mandate_id": "MANDATE-1",
            "failure_reason": "mandate_expired",
            "primary_action": "REQUEST_MANDATE_RENEWAL",
            "intervention_reason": "The mandate expired; retry is forbidden.",
        }
    ]

    assert find_explicit_policy_gate_case(audit) is None


def test_policy_panel_formats_explicit_blocked_evidence() -> None:
    audit = [
        {
            "mandate_id": "MANDATE-2",
            "suggested_action": "RETRY_AUTOPAY",
            "policy_status": "BLOCKED",
            "policy_reason": "expired mandate cannot auto-retry",
            "final_action": "REQUEST_MANDATE_RENEWAL",
        }
    ]

    assert find_explicit_policy_gate_case(audit) == {
        "mandate_id": "MANDATE-2",
        "suggested": "RETRY_AUTOPAY",
        "policy": "BLOCKED",
        "reason": "expired mandate cannot auto-retry",
        "final_action": "REQUEST_MANDATE_RENEWAL",
    }


def test_failure_distribution_and_escalation_queue_use_audit_fields() -> None:
    audit = [
        {
            "mandate_id": "M-1",
            "failure_reason": "technical_decline",
            "chosen_actions": ["RETRY_AUTOPAY"],
        },
        {
            "mandate_id": "M-2",
            "category": "insurance",
            "amount": 5000,
            "failure_reason": "technical_decline",
            "urgency_priority": "high",
            "chosen_actions": ["CREATE_SUPPORT_CASE", "STOP_AUTOMATION"],
            "intervention_reason": "Category threshold reached.",
            "final_status": "escalated",
        },
    ]

    assert failure_reason_distribution(audit) == [
        {"Failure reason": "Technical Decline", "Mandates": 2}
    ]
    queue = escalation_queue(audit)
    assert len(queue) == 1
    assert queue[0]["Mandate"] == "M-2"
    assert "CREATE_SUPPORT_CASE" in queue[0]["Policy actions"]


def test_decision_trace_has_all_five_pipeline_stages() -> None:
    entry = {
        "failure_reason": "bank_server_down",
        "retriable": True,
        "chosen_actions": ["RETRY_AUTOPAY"],
        "scheduled_retries": [],
        "attempts_made": [],
        "final_status": "recovered",
    }

    trace = build_decision_trace(entry)
    assert [step["stage"] for step in trace] == [
        "Detect",
        "Decide",
        "Escalate",
        "Message",
        "Act",
    ]


def test_multi_seed_panel_uses_mean_and_range() -> None:
    def stats(mean: float, minimum: float, maximum: float) -> dict[str, float]:
        return {"mean": mean, "min": minimum, "max": maximum}

    report = {
        "summary": {
            "smart": {
                "recovery_rate_by_count": stats(43.5, 38, 49),
                "recovery_rate_by_amount": stats(39.45, 33.49, 46.68),
                "recovered_amount": stats(1_665_904, 1_414_448, 1_971_495),
            },
            "naive": {
                "recovery_rate_by_count": stats(14.85, 13, 17),
                "recovery_rate_by_amount": stats(17.4, 13.54, 21.71),
                "recovered_amount": stats(734_717, 571_879, 916_787),
            },
            "uplift": {
                "recovery_rate_by_count_percentage_points": stats(28.65, 24, 33),
                "recovery_rate_by_amount_percentage_points": stats(22.05, 14.1, 27.79),
                "recovered_amount": stats(931_187, 595_340, 1_173_704),
            },
        }
    }

    rows = multi_seed_summary_rows(report)
    assert len(rows) == 3
    assert "43.50%" in rows[0]["Smart strategy"]
    assert "38.00%–49.00%" in rows[0]["Smart strategy"]
    assert "28.65 pp" in rows[0]["Smart uplift"]


def test_dashboard_renders_current_pipeline_artifacts_without_exceptions() -> None:
    dashboard_path = Path(__file__).resolve().parents[1] / "src" / "dashboard.py"
    app = AppTest.from_file(str(dashboard_path)).run(timeout=20)

    assert not app.exception
    assert len(app.metric) >= 8
    assert len(app.selectbox) == 1
    assert any("will not fabricate" in info.value for info in app.info)
