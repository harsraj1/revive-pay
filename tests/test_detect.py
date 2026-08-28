"""Tests for deterministic detection and optional diagnosis fallback."""

from src.detect import classify_retriability, detect_records


def _record(reason: str) -> dict[str, object]:
    return {
        "mandate_id": "MANDATE-TEST",
        "customer_name": "Test Customer",
        "category": "subscription",
        "amount": 499,
        "failure_reason": reason,
        "failed_at": "2026-07-01T10:00:00+05:30",
        "attempts_already_made": 0,
    }


def test_retriable_failure_uses_policy() -> None:
    result = classify_retriability(_record("bank_server_down"))
    assert result["retriable"] is True
    assert result["requires_human_review"] is False
    assert "Configured policy" in result["rule_explanation"]


def test_non_retriable_failure_uses_policy() -> None:
    result = classify_retriability(_record("mandate_expired"))
    assert result["retriable"] is False
    assert result["requires_human_review"] is False
    assert "non-retriable" in result["rule_explanation"]


def test_unknown_failure_fails_safely() -> None:
    result = classify_retriability(_record("new_bank_error"))
    assert result["retriable"] is False
    assert result["requires_human_review"] is True
    assert "no configured policy" in result["rule_explanation"]


def test_no_gemini_key_uses_fallback() -> None:
    results = detect_records([_record("bank_server_down")], api_key=None)
    assert len(results) == 1
    assert results[0]["diagnosis"] == (
        "Bank server down is eligible for a policy-controlled retry."
    )
    assert results[0]["recovery_confidence"] == "high"
