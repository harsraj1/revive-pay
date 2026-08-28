"""Deterministically classify failures and optionally add Gemini diagnoses."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

try:
    from .constants import FAILURE_REASONS
except ImportError:  # Supports direct execution: python src/detect.py
    from constants import FAILURE_REASONS  # type: ignore[no-redef]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_PATH = PROJECT_ROOT / "data" / "failed_mandates.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "detect_output.json"
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_BATCH_SIZE = 25
VALID_CONFIDENCE_LEVELS = {"low", "medium", "high"}


def classify_retriability(record: Mapping[str, Any]) -> dict[str, Any]:
    """Apply failure policy without using AI.

    Unknown or malformed failure reasons fail closed and are explicitly marked
    for human review.
    """

    # get() handles missing input safely. Only strings can be policy keys;
    # malformed values deliberately take the same fail-closed path as unknowns.
    failure_reason = record.get("failure_reason")
    policy = FAILURE_REASONS.get(failure_reason) if isinstance(failure_reason, str) else None

    if policy is None:
        # Unknown never means retriable by default. A new bank error must first
        # be reviewed and explicitly added to constants.py.
        displayed_reason = repr(failure_reason)
        return {
            "retriable": False,
            "requires_human_review": True,
            "rule_explanation": (
                f"Failure reason {displayed_reason} has no configured policy; "
                "automatic retry is blocked and human review is required."
            ),
        }

    if policy["retriable"]:
        recovery_days = policy["typical_recovery_window_days"]
        return {
            "retriable": True,
            "requires_human_review": False,
            "rule_explanation": (
                f"Configured policy marks {failure_reason} as retriable with a "
                f"typical recovery window of {recovery_days} day(s)."
            ),
        }

    return {
        "retriable": False,
        "requires_human_review": False,
        "rule_explanation": (
            f"Configured policy marks {failure_reason} as non-retriable; "
            "automatic retry is blocked."
        ),
    }


def _fallback_diagnosis(record: Mapping[str, Any]) -> dict[str, str]:
    """Return stable diagnosis fields when AI is unavailable or inapplicable."""

    reason = record.get("failure_reason")
    policy = FAILURE_REASONS.get(reason) if isinstance(reason, str) else None

    if policy is None:
        return {
            "diagnosis": "Unrecognized failure reason; manual investigation is required.",
            "recovery_confidence": "low",
        }
    if not policy["retriable"]:
        return {
            "diagnosis": f"{reason.replace('_', ' ').capitalize()} blocks automatic retry.",
            "recovery_confidence": "low",
        }

    # These labels explain likely recovery only. They never change the
    # deterministic retriable value produced by classify_retriability().
    confidence = {
        "bank_server_down": "high",
        "technical_decline": "medium",
        "daily_limit_exceeded": "high",
        "insufficient_balance": "medium",
    }.get(reason, "medium")
    return {
        "diagnosis": (
            f"{reason.replace('_', ' ').capitalize()} is eligible for a policy-controlled retry."
        ),
        "recovery_confidence": confidence,
    }


def _parse_gemini_json(raw_text: str) -> list[dict[str, str]]:
    """Parse and validate a Gemini JSON array, tolerating Markdown fences."""

    cleaned = raw_text.strip()
    # Models sometimes wrap JSON in Markdown even when JSON was requested.
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]).strip()

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start == -1 or end <= start:
            raise ValueError("Gemini response did not contain a JSON array") from None
        payload = json.loads(cleaned[start : end + 1])

    if not isinstance(payload, list):
        raise ValueError("Gemini response must be a JSON array")

    parsed: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        mandate_id = item.get("mandate_id")
        diagnosis = item.get("diagnosis")
        confidence = item.get("recovery_confidence")
        # Invalid items are ignored individually. Their mandates retain the
        # fallback result instead of making the entire batch fail.
        if (
            isinstance(mandate_id, str)
            and isinstance(diagnosis, str)
            and diagnosis.strip()
            and confidence in VALID_CONFIDENCE_LEVELS
        ):
            parsed.append(
                {
                    "mandate_id": mandate_id,
                    "diagnosis": diagnosis.strip(),
                    "recovery_confidence": confidence,
                }
            )
    return parsed


def _gemini_batch_diagnosis(
    records: list[Mapping[str, Any]], api_key: str
) -> dict[str, dict[str, str]]:
    """Request language-only diagnoses for one batch of retriable records."""

    from google import genai
    from google.genai import types

    # Customer names and timestamps are unnecessary for diagnosis, so they are
    # intentionally excluded from the external request.
    compact_records = [
        {
            "mandate_id": record["mandate_id"],
            "category": record["category"],
            "amount": record["amount"],
            "failure_reason": record["failure_reason"],
        }
        for record in records
    ]
    prompt = (
        "Diagnose these already-approved retriable UPI AutoPay failures. "
        "Do not decide retry eligibility or suggest schedules. Return only a JSON array. "
        "Each object must contain mandate_id, a short diagnosis, and "
        "recovery_confidence (low, medium, or high). Records:\n"
        + json.dumps(compact_records, ensure_ascii=False)
    )
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    parsed = _parse_gemini_json(response.text or "")
    return {item["mandate_id"]: item for item in parsed}


def diagnose_retriable_records(
    records: list[Mapping[str, Any]],
    api_key: str | None,
    batch_size: int = GEMINI_BATCH_SIZE,
) -> dict[str, dict[str, str]]:
    """Diagnose retriable records in batches, falling back per missing result."""

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    # Populate fallbacks first. AI output may replace a fallback only after it
    # passes parsing and validation, guaranteeing complete results on failure.
    results = {
        str(record["mandate_id"]): _fallback_diagnosis(record) for record in records
    }
    if not api_key or not records:
        return results

    for start in range(0, len(records), batch_size):
        # Slicing creates small, predictable requests rather than one large API
        # call. A failure affects only this batch and processing continues.
        batch = records[start : start + batch_size]
        try:
            ai_results = _gemini_batch_diagnosis(batch, api_key)
        except Exception:
            # API/SDK failures must never prevent deterministic pipeline execution.
            continue

        for record in batch:
            mandate_id = str(record["mandate_id"])
            if mandate_id in ai_results:
                results[mandate_id] = {
                    "diagnosis": ai_results[mandate_id]["diagnosis"],
                    "recovery_confidence": ai_results[mandate_id]["recovery_confidence"],
                }
    return results


def detect_records(
    records: list[Mapping[str, Any]], api_key: str | None = None
) -> list[dict[str, Any]]:
    """Classify all records, then optionally diagnose only retriable ones."""

    # Policy classification happens for every record before any AI call. This
    # ordering enforces the separation between rules and language judgment.
    classified: list[dict[str, Any]] = []
    retriable: list[Mapping[str, Any]] = []
    for record in records:
        enriched = {**record, **classify_retriability(record)}
        classified.append(enriched)
        if enriched["retriable"]:
            # Non-retriable and unknown failures never leave this process.
            retriable.append(enriched)

    diagnoses = diagnose_retriable_records(retriable, api_key)
    for enriched in classified:
        mandate_id = str(enriched.get("mandate_id", ""))
        diagnosis = diagnoses.get(mandate_id, _fallback_diagnosis(enriched))
        enriched.update(diagnosis)
    return classified


def _load_api_key() -> str | None:
    """Load .env when python-dotenv is available, then read the API key."""

    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass
    return os.getenv("GEMINI_API_KEY") or None


def main() -> None:
    """Run detection against the standard generated dataset."""

    records = json.loads(DEFAULT_INPUT_PATH.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"Expected a JSON array in {DEFAULT_INPUT_PATH}")

    output = detect_records(records, api_key=_load_api_key())
    DEFAULT_OUTPUT_PATH.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Detected retriability for {len(output)} mandates at {DEFAULT_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
