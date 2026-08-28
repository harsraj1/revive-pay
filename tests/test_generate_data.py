"""Tests for the synthetic failed-mandate generator."""

from datetime import datetime

from src.constants import (
    CATEGORIES,
    CATEGORY_POLICIES,
    FAILURE_POLICIES,
    MAX_RETRIES,
    SYNTHETIC_RECORD_COUNT,
)
from src.generate_data import generate_mandates


REQUIRED_FIELDS = {
    "mandate_id",
    "customer_name",
    "category",
    "amount",
    "failure_reason",
    "failed_at",
    "attempts_already_made",
}


def test_generation_is_reproducible() -> None:
    """The same default seed must produce identical complete datasets."""

    assert generate_mandates() == generate_mandates()
    assert generate_mandates(seed=123) != generate_mandates(seed=456)


def test_generated_records_follow_schema_and_domain_rules() -> None:
    """Every record should have valid fields and centrally defined values."""

    records = generate_mandates()

    assert len(records) == SYNTHETIC_RECORD_COUNT == 100
    assert len({record["mandate_id"] for record in records}) == len(records)

    for record in records:
        assert set(record) == REQUIRED_FIELDS
        assert record["category"] in CATEGORIES
        assert record["failure_reason"] in FAILURE_POLICIES
        assert record["customer_name"].strip()
        assert 0 <= record["attempts_already_made"] < MAX_RETRIES

        category_policy = CATEGORY_POLICIES[record["category"]]
        assert category_policy["amount_min"] <= record["amount"]
        assert record["amount"] <= category_policy["amount_max"]
        assert datetime.fromisoformat(record["failed_at"]).tzinfo is not None


def test_count_validation() -> None:
    """Invalid requested counts should fail with a clear error."""

    try:
        generate_mandates(count=-1)
    except ValueError as error:
        assert str(error) == "count must be non-negative"
    else:
        raise AssertionError("negative count should raise ValueError")
