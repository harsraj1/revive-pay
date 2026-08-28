"""Run a deliberately naive retry strategy for fair comparison."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

try:
    from .act import deterministic_draw
    from .constants import (
        BASELINE_RETRY_INTERVAL_HOURS,
        EXECUTION_TIMEZONE,
        MAX_RETRIES,
        PERMITTED_EXECUTION_WINDOWS,
        SIMULATION_SEED,
        SUCCESS_PROBABILITIES,
    )
    from .time_utils import ist_isoformat, localize_ist, parse_ist_datetime
except ImportError:  # Supports direct execution: python src/baseline.py
    from act import deterministic_draw  # type: ignore[no-redef]
    from constants import (  # type: ignore[no-redef]
        BASELINE_RETRY_INTERVAL_HOURS,
        EXECUTION_TIMEZONE,
        MAX_RETRIES,
        PERMITTED_EXECUTION_WINDOWS,
        SIMULATION_SEED,
        SUCCESS_PROBABILITIES,
    )
    from time_utils import (  # type: ignore[no-redef]
        ist_isoformat,
        localize_ist,
        parse_ist_datetime,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_PATH = PROJECT_ROOT / "data" / "failed_mandates.json"
SMART_REPORT_PATH = PROJECT_ROOT / "data" / "final_report.json"
BASELINE_REPORT_PATH = PROJECT_ROOT / "data" / "baseline_report.json"
COMPARISON_PATH = PROJECT_ROOT / "docs" / "comparison_summary.json"


def _permitted_window_label(scheduled_at: datetime) -> str | None:
    """Return the matching legal window without changing the naive timestamp."""

    scheduled_at = localize_ist(scheduled_at)
    clock = scheduled_at.strftime("%H:%M")
    for window in PERMITTED_EXECUTION_WINDOWS:
        if window["timezone"] != EXECUTION_TIMEZONE:
            raise ValueError("Baseline encountered a non-IST execution window")
        if window["start"] <= clock <= window["end"]:
            return window["label"]
    return None


def run_naive_mandate(
    record: Mapping[str, Any], seed: int = SIMULATION_SEED
) -> dict[str, Any]:
    """Retry one raw failure at fixed intervals with no policy intelligence."""

    failed_at = parse_ist_datetime(str(record["failed_at"]))
    attempts_already_made = int(record.get("attempts_already_made", 0))
    probability = SUCCESS_PROBABILITIES.get(str(record.get("failure_reason")), 0.0)
    attempts: list[dict[str, Any]] = []
    final_status = "exhausted"

    # The baseline still respects the common global cap, but it ignores failure
    # eligibility, category urgency, recovery timing, and window optimization.
    for attempt_num in range(attempts_already_made + 1, MAX_RETRIES + 1):
        scheduled_at = failed_at + timedelta(
            hours=BASELINE_RETRY_INTERVAL_HOURS * attempt_num
        )
        window_label = _permitted_window_label(scheduled_at)

        if window_label is None:
            attempts.append(
                {
                    "attempt_num": attempt_num,
                    "scheduled_at": ist_isoformat(scheduled_at),
                    "timezone": EXECUTION_TIMEZONE,
                    "window_label": None,
                    "decision": "rejected_restricted_time",
                    "outcome": "rejected",
                }
            )
            continue

        draw = deterministic_draw(seed, str(record["mandate_id"]), attempt_num)
        successful = draw < probability
        attempts.append(
            {
                "attempt_num": attempt_num,
                "scheduled_at": ist_isoformat(scheduled_at),
                "timezone": EXECUTION_TIMEZONE,
                "window_label": window_label,
                "decision": "retry_executed",
                "success_probability": probability,
                "random_draw": round(draw, 6),
                "outcome": "success" if successful else "failure",
            }
        )
        if successful:
            final_status = "recovered"
            break

    return {
        "mandate_id": record["mandate_id"],
        "category": record["category"],
        "amount": record["amount"],
        "failure_reason": record["failure_reason"],
        "attempts": attempts,
        "final_status": final_status,
    }


def run_baseline(
    records: list[Mapping[str, Any]], seed: int = SIMULATION_SEED
) -> dict[str, Any]:
    """Run and summarize the naive strategy over the raw mandate population."""

    results = [run_naive_mandate(record, seed) for record in records]
    recovered = [result for result in results if result["final_status"] == "recovered"]
    total_amount = sum(int(result["amount"]) for result in results)
    recovered_amount = sum(int(result["amount"]) for result in recovered)
    total_count = len(results)

    return {
        "strategy": "naive_fixed_interval",
        "simulation_seed": seed,
        "retry_interval_hours": BASELINE_RETRY_INTERVAL_HOURS,
        "total_mandates": total_count,
        "total_at_risk_amount": total_amount,
        "recovered_count": len(recovered),
        "recovered_amount": recovered_amount,
        "recovery_rate_by_count": round(
            len(recovered) / total_count * 100 if total_count else 0.0, 2
        ),
        "recovery_rate_by_amount": round(
            recovered_amount / total_amount * 100 if total_amount else 0.0, 2
        ),
        "rejected_attempt_count": sum(
            attempt["outcome"] == "rejected"
            for result in results
            for attempt in result["attempts"]
        ),
        "mandate_results": results,
    }


def build_comparison(
    smart_report: Mapping[str, Any], baseline_report: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare count and amount recovery using percentage-point differences."""

    smart_rate = float(smart_report["recovery_rate_by_count"])
    baseline_rate = float(baseline_report["recovery_rate_by_count"])
    smart_amount = int(smart_report["recovered_amount"])
    baseline_amount = int(baseline_report["recovered_amount"])
    incremental_amount = smart_amount - baseline_amount

    return {
        "smart_recovery_rate": smart_rate,
        "baseline_recovery_rate": baseline_rate,
        "percentage_point_uplift": round(smart_rate - baseline_rate, 2),
        "smart_recovered_amount": smart_amount,
        "baseline_recovered_amount": baseline_amount,
        "incremental_recovered_amount": incremental_amount,
        # Keep the architecture specification's original metric name as an alias.
        "additional_recovered_amount": incremental_amount,
        "comparison_basis": (
            "Both strategies use the same failed_mandates.json records, retry cap, "
            "success probabilities, simulation seed, and per-attempt draw function."
        ),
    }


def main() -> None:
    """Run the naive strategy and compare it with the smart final report."""

    records = json.loads(DEFAULT_INPUT_PATH.read_text(encoding="utf-8"))
    smart_report = json.loads(SMART_REPORT_PATH.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"Expected a JSON array in {DEFAULT_INPUT_PATH}")

    baseline_report = run_baseline(records)
    comparison = build_comparison(smart_report, baseline_report)
    BASELINE_REPORT_PATH.write_text(
        json.dumps(baseline_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    COMPARISON_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMPARISON_PATH.write_text(
        json.dumps(comparison, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Baseline report: {BASELINE_REPORT_PATH}")
    print(f"Comparison summary: {COMPARISON_PATH}")


if __name__ == "__main__":
    main()
