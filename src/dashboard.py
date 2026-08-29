"""Single-page Streamlit pitch dashboard for existing RevivePay artifacts.

Run with: ``streamlit run src/dashboard.py``

The dashboard is intentionally read-only. It does not run the pipeline, mutate
reports, or persist UI state outside Streamlit's normal in-memory session.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FINAL_REPORT_PATH = PROJECT_ROOT / "data" / "final_report.json"
COMPARISON_PATH = PROJECT_ROOT / "docs" / "comparison_summary.json"
AUDIT_PATH = PROJECT_ROOT / "logs" / "audit_trail.json"
MULTI_SEED_PATH = PROJECT_ROOT / "data" / "multi_seed_report.json"

SUPPORT_ACTIONS = {"CREATE_SUPPORT_CASE", "STOP_AUTOMATION"}


class DashboardDataError(ValueError):
    """Raised when a required dashboard artifact is missing or malformed."""


def load_json(path: Path) -> Any:
    """Read one pipeline artifact without modifying or caching it."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise DashboardDataError(
            f"Missing {path.relative_to(PROJECT_ROOT)}. Run python src/run_all.py first."
        ) from error
    except json.JSONDecodeError as error:
        raise DashboardDataError(
            f"Invalid JSON in {path.relative_to(PROJECT_ROOT)}: {error}"
        ) from error


def load_dashboard_data() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Load and minimally validate all four read-only dashboard inputs."""

    final_report = load_json(FINAL_REPORT_PATH)
    comparison = load_json(COMPARISON_PATH)
    audit = load_json(AUDIT_PATH)
    multi_seed = load_json(MULTI_SEED_PATH)

    if not isinstance(final_report, dict):
        raise DashboardDataError("data/final_report.json must contain a JSON object.")
    if not isinstance(comparison, dict):
        raise DashboardDataError("docs/comparison_summary.json must contain a JSON object.")
    if not isinstance(audit, list) or not all(isinstance(item, dict) for item in audit):
        raise DashboardDataError("logs/audit_trail.json must contain a JSON array of objects.")
    if not isinstance(multi_seed, dict):
        raise DashboardDataError("data/multi_seed_report.json must contain a JSON object.")
    return final_report, comparison, audit, multi_seed


def format_inr(value: object, *, decimals: int = 0) -> str:
    """Format numeric values as Indian-rupee display strings."""

    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "Unavailable"
    return f"₹{amount:,.{decimals}f}"


def format_percent(value: object, *, suffix: str = "%") -> str:
    try:
        return f"{float(value):.2f}{suffix}"
    except (TypeError, ValueError):
        return "Unavailable"


def _first_action(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        return next(
            (str(action) for action in value if isinstance(action, str) and action),
            None,
        )
    return None


def find_explicit_policy_gate_case(
    audit_entries: Sequence[Mapping[str, Any]],
) -> dict[str, str] | None:
    """Find an explicitly logged blocked/redirected suggestion.

    A final action alone does not prove that another action was suggested. The
    dashboard therefore requires an explicit suggestion/blocked field instead
    of manufacturing a dramatic policy story from a non-retriable record.
    """

    for entry in audit_entries:
        suggested = (
            _first_action(entry.get("suggested_action"))
            or _first_action(entry.get("suggested_actions"))
            or _first_action(entry.get("blocked_action"))
        )
        status = str(entry.get("policy_status", "")).upper()
        explicitly_blocked = (
            status in {"BLOCKED", "REDIRECTED"}
            or entry.get("action_blocked") is True
            or bool(entry.get("blocked_action"))
        )
        if not suggested or not explicitly_blocked:
            continue

        final_action = (
            _first_action(entry.get("final_action"))
            or _first_action(entry.get("primary_action"))
            or _first_action(entry.get("chosen_actions"))
        )
        if not final_action:
            continue

        reason = (
            entry.get("policy_reason")
            or entry.get("intervention_reason")
            or entry.get("stop_reason")
        )
        if not isinstance(reason, str) or not reason.strip():
            continue
        return {
            "mandate_id": str(entry.get("mandate_id", "Unknown mandate")),
            "suggested": suggested,
            "policy": "BLOCKED" if status != "REDIRECTED" else "REDIRECTED",
            "reason": reason.strip(),
            "final_action": final_action,
        }
    return None


def failure_reason_distribution(
    audit_entries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return stable chart rows sorted by mandate count, then reason."""

    counts = Counter(
        str(entry.get("failure_reason") or "unknown") for entry in audit_entries
    )
    return [
        {"Failure reason": reason.replace("_", " ").title(), "Mandates": count}
        for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def escalation_queue(
    audit_entries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build the support/stop queue directly from audited chosen actions."""

    rows: list[dict[str, Any]] = []
    for entry in audit_entries:
        raw_actions = entry.get("chosen_actions", [])
        actions = {
            str(action) for action in raw_actions if isinstance(action, str)
        } if isinstance(raw_actions, list) else set()
        matched = sorted(actions & SUPPORT_ACTIONS)
        if not matched:
            continue
        rows.append(
            {
                "Mandate": entry.get("mandate_id"),
                "Category": str(entry.get("category", "unknown")).upper(),
                "Amount": format_inr(entry.get("amount")),
                "Failure reason": str(entry.get("failure_reason", "unknown")).replace(
                    "_", " "
                ),
                "Priority": str(entry.get("urgency_priority", "unknown")).upper(),
                "Policy actions": " + ".join(matched),
                "Reason": entry.get("intervention_reason") or entry.get("stop_reason"),
                "Final status": str(entry.get("final_status", "unknown")).upper(),
            }
        )

    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNKNOWN": 3}
    rows.sort(
        key=lambda row: (
            priority_order.get(str(row["Priority"]), 3),
            str(row["Mandate"]),
        )
    )
    return rows


def build_decision_trace(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project one combined audit entry into the five pipeline stages."""

    scheduled = entry.get("scheduled_retries", [])
    attempts = entry.get("attempts_made", [])
    message = entry.get("customer_message")
    return [
        {
            "stage": "Detect",
            "headline": (
                "Retriable" if entry.get("retriable") is True else "Automatic retry blocked"
            ),
            "details": [
                f"Failure: {str(entry.get('failure_reason', 'unknown')).replace('_', ' ')}",
                str(entry.get("retry_rule_explanation") or "No detection rationale logged."),
            ],
        },
        {
            "stage": "Decide",
            "headline": " + ".join(entry.get("chosen_actions", []))
            if isinstance(entry.get("chosen_actions"), list)
            else "No action logged",
            "details": [
                str(entry.get("intervention_reason") or "No intervention rationale logged."),
                f"Amount band: {entry.get('amount_band') or 'not logged'}",
                f"Scheduled retries: {len(scheduled) if isinstance(scheduled, list) else 0}",
            ],
            "table": scheduled if isinstance(scheduled, list) else [],
        },
        {
            "stage": "Escalate",
            "headline": f"{str(entry.get('urgency_priority', 'unknown')).title()} priority",
            "details": [
                f"Escalate after {entry.get('escalate_after_attempts', 'unknown')} attempt(s)",
                f"Attempts already made: {entry.get('attempts_already_made', 0)}",
            ],
        },
        {
            "stage": "Message",
            "headline": "Customer message prepared" if message else "Message suppressed by policy",
            "details": [str(message)] if message else ["No customer communication was sent."],
        },
        {
            "stage": "Act",
            "headline": str(entry.get("final_status", "unknown")).replace("_", " ").title(),
            "details": [str(entry.get("stop_reason") or "No stopping reason logged.")],
            "table": attempts if isinstance(attempts, list) else [],
        },
    ]


def _stat(summary: Mapping[str, Any], strategy: str, metric: str) -> Mapping[str, Any]:
    strategy_data = summary.get(strategy, {})
    if not isinstance(strategy_data, Mapping):
        return {}
    metric_data = strategy_data.get(metric, {})
    return metric_data if isinstance(metric_data, Mapping) else {}


def multi_seed_summary_rows(report: Mapping[str, Any]) -> list[dict[str, str]]:
    """Format mean and range rows from multi_seed_eval.py output."""

    summary = report.get("summary", {})
    if not isinstance(summary, Mapping):
        return []

    definitions = (
        ("Recovery rate by count", "recovery_rate_by_count", "%"),
        ("Recovery rate by amount", "recovery_rate_by_amount", "%"),
        ("Recovered amount", "recovered_amount", "inr"),
    )
    rows: list[dict[str, str]] = []
    for label, key, unit in definitions:
        smart = _stat(summary, "smart", key)
        naive = _stat(summary, "naive", key)
        uplift_key = (
            f"{key}_percentage_points"
            if key.startswith("recovery_rate")
            else key
        )
        uplift = _stat(summary, "uplift", uplift_key)

        def display(stats: Mapping[str, Any], *, uplift_value: bool = False) -> str:
            if not stats:
                return "Unavailable"
            if unit == "inr":
                mean = format_inr(stats.get("mean"), decimals=0)
                low = format_inr(stats.get("min"), decimals=0)
                high = format_inr(stats.get("max"), decimals=0)
            else:
                suffix = " pp" if uplift_value else "%"
                mean = format_percent(stats.get("mean"), suffix=suffix)
                low = format_percent(stats.get("min"), suffix=suffix)
                high = format_percent(stats.get("max"), suffix=suffix)
            return f"{mean}  ·  range {low}–{high}"

        rows.append(
            {
                "Metric": label,
                "Smart strategy": display(smart),
                "Naive baseline": display(naive),
                "Smart uplift": display(uplift, uplift_value=unit != "inr"),
            }
        )
    return rows


def _render_timeline(st: Any, entry: Mapping[str, Any]) -> None:
    """Render the combined audit record as a compact vertical stage trace."""

    for index, step in enumerate(build_decision_trace(entry), start=1):
        with st.container(border=True):
            left, right = st.columns([1, 7])
            left.markdown(f"### {index}")
            right.markdown(f"#### {step['stage']} · {step['headline']}")
            for detail in step.get("details", []):
                right.write(detail)
            table = step.get("table")
            if isinstance(table, list) and table:
                right.dataframe(table, width="stretch", hide_index=True)


def render_dashboard() -> None:
    """Render the six requested sections in one Streamlit page."""

    import streamlit as st

    st.set_page_config(
        page_title="RevivePay · Revenue Recovery",
        page_icon="↻",
        layout="wide",
    )
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1500px;}
        div[data-testid="stMetric"] {border: 1px solid rgba(120,120,120,.22); border-radius: 14px; padding: 16px;}
        div[data-testid="stMetricValue"] {color: #17a673;}
        .policy-line {font-size: 1.05rem; padding: 1rem; border-left: 4px solid #f4b942; background: rgba(244,185,66,.09); border-radius: 6px;}
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("RevivePay · AI Revenue Recovery")
    st.caption(
        "Deterministic policy controls money movement. AI is limited to explanation and language."
    )

    try:
        final_report, comparison, audit_entries, multi_seed = load_dashboard_data()
    except DashboardDataError as error:
        st.error(str(error))
        st.stop()

    # 1. Header metrics
    metric_columns = st.columns(4)
    metric_columns[0].metric(
        "Total amount at risk",
        format_inr(final_report.get("total_at_risk_amount")),
        help="Sum of failed mandate amounts in the simulated batch.",
    )
    metric_columns[1].metric(
        "Simulated recovered amount",
        format_inr(final_report.get("recovered_amount")),
    )
    metric_columns[2].metric(
        "Recovery rate",
        format_percent(final_report.get("recovery_rate_by_count")),
        help="Single seeded run, measured by mandate count.",
    )
    metric_columns[3].metric(
        "Smart vs baseline uplift",
        format_percent(comparison.get("percentage_point_uplift"), suffix=" pp"),
        help="Single-seed comparison from docs/comparison_summary.json.",
    )

    # 2. Policy in action
    st.header("Policy in action")
    policy_case = find_explicit_policy_gate_case(audit_entries)
    if policy_case is None:
        st.info(
            "No explicitly logged blocked or redirected suggested action exists in the "
            "current audit trail. The audit records final authorized actions, but not a "
            "pre-policy suggestion, so this dashboard will not fabricate a policy-gate case."
        )
    else:
        st.caption(f"Mandate {policy_case['mandate_id']}")
        st.markdown(
            '<div class="policy-line">'
            f"Suggested: <b>{policy_case['suggested']}</b> | "
            f"Policy: <b>{policy_case['policy']}</b> | "
            f"Reason: {policy_case['reason']} | "
            f"Final action: <b>{policy_case['final_action']}</b>"
            "</div>",
            unsafe_allow_html=True,
        )

    # 3. Failure distribution
    st.header("Failure-reason distribution")
    chart_rows = failure_reason_distribution(audit_entries)
    if chart_rows:
        st.bar_chart(chart_rows, x="Failure reason", y="Mandates", color="#17a673")
    else:
        st.info("No audited mandates are available for the distribution chart.")

    # 4. Escalation queue
    st.header("Escalation queue")
    queue_rows = escalation_queue(audit_entries)
    st.caption(
        "Mandates whose audited policy actions include CREATE_SUPPORT_CASE or STOP_AUTOMATION."
    )
    if queue_rows:
        st.dataframe(queue_rows, width="stretch", hide_index=True)
    else:
        st.info("No mandates currently require support or an automation stop.")

    # 5. Decision trace
    st.header("Mandate decision trace")
    audit_by_id = {
        str(entry.get("mandate_id")): entry
        for entry in audit_entries
        if entry.get("mandate_id") is not None
    }
    mandate_ids = sorted(audit_by_id)
    if mandate_ids:
        default_id = (
            str(queue_rows[0]["Mandate"])
            if queue_rows and str(queue_rows[0]["Mandate"]) in audit_by_id
            else mandate_ids[0]
        )
        selected_id = st.selectbox(
            "Choose mandate ID",
            mandate_ids,
            index=mandate_ids.index(default_id),
        )
        selected = audit_by_id[selected_id]
        top_columns = st.columns(4)
        top_columns[0].metric("Amount", format_inr(selected.get("amount")))
        top_columns[1].metric("Category", str(selected.get("category", "unknown")).upper())
        top_columns[2].metric(
            "Failure", str(selected.get("failure_reason", "unknown")).replace("_", " ").title()
        )
        top_columns[3].metric(
            "Final status", str(selected.get("final_status", "unknown")).replace("_", " ").title()
        )
        _render_timeline(st, selected)
        with st.expander("View complete raw audit record"):
            st.json(selected)
    else:
        st.info("No mandate audit records are available.")

    # 6. Multi-seed evidence
    st.header("Multi-seed evaluation")
    seed_count = multi_seed.get("seed_count", 0)
    mandates_per_seed = multi_seed.get("mandates_per_seed", "unknown")
    st.caption(
        f"Mean and observed range across {seed_count} deterministic seeds, "
        f"using {mandates_per_seed} mandates per seed."
    )
    summary_rows = multi_seed_summary_rows(multi_seed)
    if summary_rows:
        st.dataframe(summary_rows, width="stretch", hide_index=True)
        count_uplift = _stat(
            multi_seed.get("summary", {}),
            "uplift",
            "recovery_rate_by_count_percentage_points",
        )
        st.success(
            "Multi-seed mean uplift: "
            f"{format_percent(count_uplift.get('mean'), suffix=' pp')} "
            f"(observed range {format_percent(count_uplift.get('min'), suffix=' pp')}–"
            f"{format_percent(count_uplift.get('max'), suffix=' pp')})."
        )
    else:
        st.warning(
            "No valid multi-seed summary is available. Run python src/multi_seed_eval.py."
        )


if __name__ == "__main__":
    render_dashboard()
