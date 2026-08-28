"""Run a deliberately naive retry baseline against the raw failed mandates."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

try:
    from .act import deterministic_draw
    from .constants import (
        BASELINE_RETRY_INTERVAL_HOURS,
        MAX_RETRIES,
        PERMITTED_EXECUTION_WINDOWS,
        SIMULATION_SEED,
        SUCCESS_PROBABILITIES,
    )
except ImportError:  # Supports direct execution: python src/baseline.py
    from act import deterministic_draw  # type: ignore[no-redef]
    from constants import (  # type: ignore[no-redef]
        BASELINE_RETRY_INTERVAL_HOURS,
        MAX_RETRIES,
        PERMITTED_EXECUTION_WINDOWS,
        SIMULATION_SEED,
        SUCCESS_PROBABILITIES,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_PATH = PROJECT_ROOT / "data" / "failed_mandates.json"
DEFAULT_SMART_REPORT_PATH = PROJECT_ROOT / "data" / "final_report.json"
DEFAULT_BASELINE_REPORT_PATH = PROJECT_ROOT / "data" / "baseline_report.json"
DEFAULT_COMPARISON_PATH = PROJECT_ROOT / "docs" / "comparison_summary.json"


def is_permitted_execution_time(moment: datetime) -> bool:
    """Return whether a time falls inside any shared execution window."""

    clock = moment.strftime("%H:%M")
    return any(
        window["start"] <= clock <= window["end"]
        for window in PERMITTED_EXECUTION_WINDOWS
    )


def simulate_naive_mandate(
    record: Mapping[str, Any], seed: int = SIMULATION_SEED
) -> dict[str, Any]:
    """Retry one raw failure at fixed intervals without policy intelligence."""

    failed_at = datetime.fromisoformat(str(record["failed_at"]))
    attempts_already_made = int(record.get("attempts_already_made", 0))
    attempts: list[dict[str, Any]] = []
    recovered = False

    # Every failure reason is treated identically for scheduling. Existing
    # attempts still count toward the common cap so experiment budgets match.
    for attempt_num in range(attempts_already_made + 1, MAX_RETRIES + 1):
        offset = attempt_num - attempts_already_made
        scheduled_at = failed_at + timedelta(
            hours=BASELINE_RETRY_INTERVAL_HOURS * offset
        )
        attempt: dict[str, Any] = {
            "attempt_num": attempt_num,
            "scheduled_at": scheduled_at.isoformat(),
            "strategy": f"fixed_{BASELINE_RETRY_INTERVAL_HOURS}_hour_interval",
        }

        if not is_permitted_execution_time(scheduled_at):
            attempt.update(
                {
                    "decision": "rejected",
                    "outcome": "rejected_restricted_execution_period",
                }
            )
            attempts.append(attempt)
            continue

        probability = SUCCESS_PROBABILITIES.get(str(record["failure_reason"]), 0.0)
        draw = deterministic_draw(seed, str(record["mandate_id"]), attempt_num)
        recovered = draw < probability
        attempt.update(
            {
                "decision": "retry_executed",
                "success_probability": probability,
                "random_draw": round(draw, 6),
                "outcome": "success" if recovered else "failure",
            }
        )
        attempts.append(attempt)
        if recovered:
            break

    return {
        "mandate_id": record["mandate_id"],
        "category": record["category"],
        "amount": record["amount"],
        "failure_reason": record["failure_reason"],
        "attempts_already_made": attempts_already_made,
        "attempts": attempts,
        "final_status": "recovered" if recovered else "not_recovered",
    }


def run_baseline(
    records: list[Mapping[str, Any]], seed: int = SIMULATION_SEED
) -> dict[str, Any]:
    """Run the naive strategy and aggregate its recovery results."""

    results = [simulate_naive_mandate(record, seed) for record in records]
    recovered = [result for result in results if result["final_status"] == "recovered"]
    total_amount = sum(int(result["amount"]) for result in results)
    recovered_amount = sum(int(result["amount"]) for result in recovered)
    rejected_attempts = sum(
        attempt["decision"] == "rejected"
        for result in results
        for attempt in result["attempts"]
    )
    executed_attempts = sum(
        attempt["decision"] == "retry_executed"
        for result in results
        for attempt in result["attempts"]
    )

    return {
        "strategy": "naive_fixed_interval_retry_every_failure",
        "source_dataset": "data/failed_mandates.json",
        "total_mandates": len(results),
        "total_at_risk_amount": total_amount,
        "recovered_count": len(recovered),
        "recovered_amount": recovered_amount,
        "recovery_rate_by_count": round(
            len(recovered) / len(results) * 100 if results else 0.0, 2
        ),
        "recovery_rate_by_amount": round(
            recovered_amount / total_amount * 100 if total_amount else 0.0, 2
        ),
        "executed_attempt_count": executed_attempts,
        "rejected_attempt_count": rejected_attempts,
        "retry_interval_hours": BASELINE_RETRY_INTERVAL_HOURS,
        "maximum_total_attempts": MAX_RETRIES,
        "simulation_seed": seed,
        "mandate_results": results,
    }


def build_comparison(
    smart_report: Mapping[str, Any], baseline_report: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare smart and baseline outcomes from the same mandate population."""

    if smart_report["total_mandates"] != baseline_report["total_mandates"]:
        raise ValueError("Smart and baseline reports cover different mandate counts")
    if smart_report["total_at_risk_amount"] != baseline_report["total_at_risk_amount"]:
        raise ValueError("Smart and baseline reports cover different at-risk amounts")

    smart_rate = float(smart_report["recovery_rate_by_count"])
    baseline_rate = float(baseline_report["recovery_rate_by_count"])
    smart_amount = int(smart_report["recovered_amount"])
    baseline_amount = int(baseline_report["recovered_amount"])
    return {
        "dataset": "data/failed_mandates.json",
        "total_mandates": baseline_report["total_mandates"],
        "total_at_risk_amount": baseline_report["total_at_risk_amount"],
        "smart_recovery_rate": smart_rate,
        "baseline_recovery_rate": baseline_rate,
        "percentage_point_uplift": round(smart_rate - baseline_rate, 2),
        "smart_recovered_amount": smart_amount,
        "baseline_recovered_amount": baseline_amount,
        "incremental_recovered_amount": smart_amount - baseline_amount,
        "fair_experiment_explanation": (
            "Both strategies must use the same failed mandates so they face identical "
            "customers, amounts, failure reasons, timestamps, and prior-attempt counts. "
            "Otherwise differences in dataset difficulty could be mistaken for strategy "
            "uplift. Sharing the outcome probabilities and deterministic seed further "
            "isolates retry policy as the variable being compared."
        ),
    }


def main() -> None:
    """Read raw failures, run the baseline, and write M7 comparison outputs."""

    records = json.loads(DEFAULT_INPUT_PATH.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"Expected a JSON array in {DEFAULT_INPUT_PATH}")

    smart_report = json.loads(DEFAULT_SMART_REPORT_PATH.read_text(encoding="utf-8"))
    baseline_report = run_baseline(records)
    comparison = build_comparison(smart_report, baseline_report)

    DEFAULT_BASELINE_REPORT_PATH.write_text(
        json.dumps(baseline_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    DEFAULT_COMPARISON_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_COMPARISON_PATH.write_text(
        json.dumps(comparison, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Baseline report: {DEFAULT_BASELINE_REPORT_PATH}")
    print(f"Comparison summary: {DEFAULT_COMPARISON_PATH}")


if __name__ == "__main__":
    main()
