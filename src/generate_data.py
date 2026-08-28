"""Generate reproducible synthetic failed UPI AutoPay mandates."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict

try:
    from .constants import (
        CATEGORIES,
        CATEGORY_POLICIES,
        DATA_GENERATION_SEED,
        FAILURE_POLICIES,
        FAILURE_REASON_WEIGHTS,
        MAX_RETRIES,
        SYNTHETIC_RECORD_COUNT,
    )
except ImportError:  # Supports direct execution: python src/generate_data.py
    from constants import (  # type: ignore[no-redef]
        CATEGORIES,
        CATEGORY_POLICIES,
        DATA_GENERATION_SEED,
        FAILURE_POLICIES,
        FAILURE_REASON_WEIGHTS,
        MAX_RETRIES,
        SYNTHETIC_RECORD_COUNT,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "failed_mandates.json"
INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30))
# A fixed clock anchor is as important as a fixed random seed. Using
# datetime.now() here would make otherwise identical runs produce new output.
SYNTHETIC_PERIOD_START = datetime(2026, 7, 1, tzinfo=INDIA_TIMEZONE)

CUSTOMER_NAMES = (
    "Aarav Sharma",
    "Aditi Rao",
    "Ananya Iyer",
    "Arjun Mehta",
    "Diya Patel",
    "Ishaan Verma",
    "Kabir Singh",
    "Meera Nair",
    "Neha Gupta",
    "Rohan Das",
    "Saanvi Joshi",
    "Vikram Kulkarni",
)


class FailedMandate(TypedDict):
    """JSON schema for one synthetic failed mandate."""

    mandate_id: str
    customer_name: str
    category: str
    amount: int
    failure_reason: str
    failed_at: str
    attempts_already_made: int


def _choose_failure_reason(rng: random.Random) -> str:
    """Choose one known failure reason using centrally configured weights."""

    # random.choices accepts parallel value and weight sequences. A weight of
    # 35 makes a reason more likely than a weight of 3; it is not a guarantee.
    reasons = tuple(FAILURE_REASON_WEIGHTS)
    weights = tuple(FAILURE_REASON_WEIGHTS[reason] for reason in reasons)
    return rng.choices(reasons, weights=weights, k=1)[0]


def _generate_failed_at(rng: random.Random) -> str:
    """Return a reproducible timestamp within a fixed 30-day synthetic period."""

    minute_offset = rng.randrange(30 * 24 * 60)
    return (SYNTHETIC_PERIOD_START + timedelta(minutes=minute_offset)).isoformat()


def generate_mandates(
    count: int = SYNTHETIC_RECORD_COUNT,
    seed: int = DATA_GENERATION_SEED,
) -> list[FailedMandate]:
    """Build deterministic synthetic mandate records without writing to disk."""

    if count < 0:
        raise ValueError("count must be non-negative")

    # A local Random instance avoids changing Python's process-wide random
    # state and makes this function deterministic in isolation.
    rng = random.Random(seed)
    records: list[FailedMandate] = []

    for sequence in range(1, count + 1):
        category = rng.choice(CATEGORIES)
        # Amount boundaries are selected only after the category is known, so
        # subscriptions and credit-card payments use realistic different ranges.
        category_policy = CATEGORY_POLICIES[category]
        amount = rng.randint(
            category_policy["amount_min"], category_policy["amount_max"]
        )
        failure_reason = _choose_failure_reason(rng)

        # Zero padding keeps identifiers easy to scan and sort as plain text.
        records.append(
            {
                "mandate_id": f"MANDATE-{sequence:04d}",
                "customer_name": rng.choice(CUSTOMER_NAMES),
                "category": category,
                "amount": amount,
                "failure_reason": failure_reason,
                "failed_at": _generate_failed_at(rng),
                "attempts_already_made": rng.randint(0, MAX_RETRIES - 1),
            }
        )

    return records


def write_json(records: list[FailedMandate], output_path: Path) -> None:
    """Write records as readable UTF-8 JSON, creating the parent directory."""

    # Stage functions can be run independently, so they create their own output
    # directory instead of assuming the full pipeline has already run.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """Generate the configured dataset at the standard pipeline location."""

    records = generate_mandates()
    write_json(records, DEFAULT_OUTPUT_PATH)
    print(f"Generated {len(records)} failed mandates at {DEFAULT_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
