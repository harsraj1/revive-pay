"""Evaluate smart and naive recovery strategies across multiple seeds."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, stdev
from typing import Any, Mapping, Sequence

try:
    from .act import build_final_report, simulate_execution
    from .baseline import run_baseline
    from .constants import SIMULATION_SEED
except ImportError:  # Supports direct execution: python src/multi_seed_eval.py
    from act import build_final_report, simulate_execution  # type: ignore[no-redef]
    from baseline import run_baseline  # type: ignore[no-redef]
    from constants import SIMULATION_SEED  # type: ignore[no-redef]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SMART_INPUT_PATH = PROJECT_ROOT / "data" / "message_output.json"
RAW_INPUT_PATH = PROJECT_ROOT / "data" / "failed_mandates.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "multi_seed_report.json"
DEFAULT_SEED_COUNT = 20
DEFAULT_SEEDS = tuple(range(SIMULATION_SEED, SIMULATION_SEED + DEFAULT_SEED_COUNT))


def _metric_summary(values: Sequence[float | int]) -> dict[str, float]:
    """Summarize one metric, using sample standard deviation across seeds."""

    if not values:
        raise ValueError("cannot summarize an empty metric sequence")
    numeric_values = [float(value) for value in values]
    return {
        "mean": round(mean(numeric_values), 2),
        "standard_deviation": round(stdev(numeric_values), 2)
        if len(numeric_values) > 1
        else 0.0,
        "min": round(min(numeric_values), 2),
        "max": round(max(numeric_values), 2),
    }


def _selected_metrics(report: Mapping[str, Any]) -> dict[str, float | int]:
    """Keep the comparable outcome metrics required for each seed."""

    return {
        "recovery_rate_by_count": float(report["recovery_rate_by_count"]),
        "recovery_rate_by_amount": float(report["recovery_rate_by_amount"]),
        "recovered_count": int(report["recovered_count"]),
        "recovered_amount": int(report["recovered_amount"]),
    }


def evaluate_seeds(
    smart_records: list[Mapping[str, Any]],
    raw_records: list[Mapping[str, Any]],
    seeds: Sequence[int] = DEFAULT_SEEDS,
) -> dict[str, Any]:
    """Run both existing simulators for every seed and aggregate their metrics."""

    if not seeds:
        raise ValueError("at least one simulation seed is required")
    if len(set(seeds)) != len(seeds):
        raise ValueError("simulation seeds must be unique")
    if len(smart_records) != len(raw_records):
        raise ValueError("smart and baseline inputs must contain the same record count")

    per_seed: list[dict[str, Any]] = []
    for seed in seeds:
        # Reuse the production simulation functions. The evaluator contains no
        # duplicate probability, retry, escalation, or baseline outcome logic.
        smart_audit = simulate_execution(smart_records, seed=seed)
        smart_report = build_final_report(smart_audit, seed=seed)
        naive_report = run_baseline(raw_records, seed=seed)
        smart_metrics = _selected_metrics(smart_report)
        naive_metrics = _selected_metrics(naive_report)

        per_seed.append(
            {
                "seed": seed,
                "smart": smart_metrics,
                "naive": naive_metrics,
                "uplift": {
                    "recovery_rate_by_count_percentage_points": round(
                        float(smart_metrics["recovery_rate_by_count"])
                        - float(naive_metrics["recovery_rate_by_count"]),
                        2,
                    ),
                    "recovery_rate_by_amount_percentage_points": round(
                        float(smart_metrics["recovery_rate_by_amount"])
                        - float(naive_metrics["recovery_rate_by_amount"]),
                        2,
                    ),
                    "recovered_amount": int(smart_metrics["recovered_amount"])
                    - int(naive_metrics["recovered_amount"]),
                },
            }
        )

    metric_names = (
        "recovery_rate_by_count",
        "recovery_rate_by_amount",
        "recovered_amount",
    )
    strategy_summary = {
        strategy: {
            metric: _metric_summary(
                [row[strategy][metric] for row in per_seed]
            )
            for metric in metric_names
        }
        for strategy in ("smart", "naive")
    }
    uplift_summary = {
        metric: _metric_summary([row["uplift"][metric] for row in per_seed])
        for metric in (
            "recovery_rate_by_count_percentage_points",
            "recovery_rate_by_amount_percentage_points",
            "recovered_amount",
        )
    }

    return {
        "seed_count": len(seeds),
        "seeds": list(seeds),
        "mandates_per_seed": len(smart_records),
        "per_seed": per_seed,
        "summary": {
            "smart": strategy_summary["smart"],
            "naive": strategy_summary["naive"],
            "uplift": uplift_summary,
        },
    }


def print_report_table(report: Mapping[str, Any]) -> None:
    """Print per-seed count recovery and a compact statistical summary."""

    print("\nMulti-seed smart vs naive recovery evaluation")
    print("=" * 91)
    print(
        f"{'Seed':>10}  {'Smart count%':>12}  {'Naive count%':>12}  "
        f"{'Smart amount%':>13}  {'Naive amount%':>13}  "
        f"{'Smart INR':>12}  {'Naive INR':>12}"
    )
    print("-" * 91)
    for row in report["per_seed"]:
        print(
            f"{row['seed']:>10}  "
            f"{row['smart']['recovery_rate_by_count']:>12.2f}  "
            f"{row['naive']['recovery_rate_by_count']:>12.2f}  "
            f"{row['smart']['recovery_rate_by_amount']:>13.2f}  "
            f"{row['naive']['recovery_rate_by_amount']:>13.2f}  "
            f"{row['smart']['recovered_amount']:>12,.0f}  "
            f"{row['naive']['recovered_amount']:>12,.0f}"
        )

    print("\nAggregate statistics")
    print(f"{'Metric':<23} {'Strategy':<9} {'Mean':>12} {'Std dev':>12} {'Min':>12} {'Max':>12}")
    for metric, label in (
        ("recovery_rate_by_count", "Count rate %"),
        ("recovery_rate_by_amount", "Amount rate %"),
        ("recovered_amount", "Recovered INR"),
    ):
        for strategy in ("smart", "naive"):
            stats = report["summary"][strategy][metric]
            print(
                f"{label:<23} {strategy:<9} {stats['mean']:>12,.2f} "
                f"{stats['standard_deviation']:>12,.2f} {stats['min']:>12,.2f} "
                f"{stats['max']:>12,.2f}"
            )

    print("\nMean smart-minus-naive uplift and observed range")
    for metric, label in (
        ("recovery_rate_by_count_percentage_points", "Count rate (pp)"),
        ("recovery_rate_by_amount_percentage_points", "Amount rate (pp)"),
        ("recovered_amount", "Recovered INR"),
    ):
        stats = report["summary"]["uplift"][metric]
        print(
            f"{label:<18}: mean {stats['mean']:>12,.2f}; "
            f"range {stats['min']:>12,.2f} to {stats['max']:>12,.2f}"
        )


def main() -> None:
    """Load the existing batch, evaluate 20 seeds, and write JSON plus table."""

    smart_records = json.loads(SMART_INPUT_PATH.read_text(encoding="utf-8"))
    raw_records = json.loads(RAW_INPUT_PATH.read_text(encoding="utf-8"))
    if not isinstance(smart_records, list) or not isinstance(raw_records, list):
        raise ValueError("multi-seed inputs must both be JSON arrays")

    report = evaluate_seeds(smart_records, raw_records)
    DEFAULT_OUTPUT_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print_report_table(report)
    print(f"\nJSON report: {DEFAULT_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
