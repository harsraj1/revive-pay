"""Build and explain deterministic, policy-compliant retry schedules."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping, TypedDict

try:
    from .constants import FAILURE_REASONS, MAX_RETRIES, PERMITTED_EXECUTION_WINDOWS
except ImportError:  # Supports direct execution: python src/decide.py
    from constants import (  # type: ignore[no-redef]
        FAILURE_REASONS,
        MAX_RETRIES,
        PERMITTED_EXECUTION_WINDOWS,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_PATH = PROJECT_ROOT / "data" / "detect_output.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "decide_output.json"
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_BATCH_SIZE = 25

# These are not execution times. They express how long each failure type should
# be allowed to recover before attempt 1, 2, or 3 becomes eligible.
RETRY_OFFSETS: dict[str, tuple[timedelta, ...]] = {
    "insufficient_balance": (
        timedelta(days=1),
        timedelta(days=3),
        timedelta(days=5),
    ),
    "bank_server_down": (
        timedelta(hours=2),
        timedelta(hours=6),
        timedelta(hours=24),
    ),
    "technical_decline": (
        timedelta(hours=4),
        timedelta(hours=12),
        timedelta(hours=30),
    ),
}


class ScheduledAttempt(TypedDict):
    """Serializable schema for a single planned retry."""

    attempt_num: int
    scheduled_date: str
    scheduled_time: str
    window_label: str


def _parse_clock(value: str) -> time:
    """Parse a centrally configured HH:MM clock value."""

    return datetime.strptime(value, "%H:%M").time()


def _round_up_to_minute(value: datetime) -> datetime:
    """Avoid formatting a candidate to an earlier minute by dropping seconds."""

    if value.second or value.microsecond:
        value += timedelta(minutes=1)
    return value.replace(second=0, microsecond=0)


def _next_permitted_datetime(candidate: datetime) -> tuple[datetime, str]:
    """Move a candidate forward to the earliest permitted execution time."""

    candidate = _round_up_to_minute(candidate)
    search_date = candidate.date()

    # Seven iterations are far more than needed because windows exist every
    # day, while still preventing an accidental infinite loop after bad config.
    for _ in range(7):
        for window in PERMITTED_EXECUTION_WINDOWS:
            start = datetime.combine(search_date, _parse_clock(window["start"]), candidate.tzinfo)
            end = datetime.combine(search_date, _parse_clock(window["end"]), candidate.tzinfo)

            if candidate <= end:
                return max(candidate, start), window["label"]

        search_date += timedelta(days=1)
        candidate = datetime.combine(search_date, time.min, candidate.tzinfo)

    raise ValueError("No permitted execution window could be found")


def _daily_limit_candidate(failed_at: datetime, attempt_num: int) -> datetime:
    """Place daily-limit retries on later calendar days after the reset."""

    retry_date = failed_at.date() + timedelta(days=attempt_num)
    preferred_windows = ("early_morning", "afternoon", "late_night")
    preferred_label = preferred_windows[min(attempt_num - 1, len(preferred_windows) - 1)]
    window = next(
        item for item in PERMITTED_EXECUTION_WINDOWS if item["label"] == preferred_label
    )
    return datetime.combine(retry_date, _parse_clock(window["start"]), failed_at.tzinfo)


def build_retry_schedule(record: Mapping[str, Any]) -> list[ScheduledAttempt]:
    """Create a deterministic retry schedule from policy and failure timestamp."""

    failure_reason = record.get("failure_reason")
    policy = FAILURE_REASONS.get(failure_reason) if isinstance(failure_reason, str) else None

    # Re-check policy locally instead of trusting upstream JSON. This prevents a
    # corrupted retriable flag from creating retries for a prohibited reason.
    if policy is None or not policy["retriable"]:
        return []

    try:
        failed_at = datetime.fromisoformat(str(record["failed_at"]))
        attempts_already_made = int(record.get("attempts_already_made", 0))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("record has invalid failed_at or attempts_already_made") from error

    if attempts_already_made < 0:
        raise ValueError("attempts_already_made cannot be negative")
    if attempts_already_made >= MAX_RETRIES:
        return []

    schedule: list[ScheduledAttempt] = []
    previous_time: datetime | None = None

    for attempt_num in range(attempts_already_made + 1, MAX_RETRIES + 1):
        if failure_reason == "daily_limit_exceeded":
            candidate = _daily_limit_candidate(failed_at, attempt_num)
        else:
            offsets = RETRY_OFFSETS.get(str(failure_reason))
            if offsets is None:
                # This fails closed if a newly retriable policy is added without
                # an explicit scheduling strategy in this module.
                return []
            candidate = failed_at + offsets[attempt_num - 1]

        # Two candidates can snap to the same window boundary. Moving the later
        # one by one minute preserves strict chronological ordering.
        if previous_time is not None:
            candidate = max(candidate, previous_time + timedelta(minutes=1))
        scheduled_at, window_label = _next_permitted_datetime(candidate)
        previous_time = scheduled_at
        schedule.append(
            {
                "attempt_num": attempt_num,
                "scheduled_date": scheduled_at.date().isoformat(),
                "scheduled_time": scheduled_at.strftime("%H:%M"),
                "window_label": window_label,
            }
        )

    validate_schedule(schedule)
    return schedule


def validate_schedule(schedule: list[ScheduledAttempt]) -> None:
    """Assert that a schedule respects caps, ordering, and execution windows."""

    assert len(schedule) <= MAX_RETRIES, "retry schedule exceeds MAX_RETRIES"
    previous: datetime | None = None
    previous_attempt = 0

    for attempt in schedule:
        attempt_num = attempt["attempt_num"]
        assert isinstance(attempt_num, int), "attempt_num must be an integer"
        assert previous_attempt < attempt_num <= MAX_RETRIES, "attempt numbers are invalid"

        scheduled_at = datetime.combine(
            date.fromisoformat(attempt["scheduled_date"]),
            _parse_clock(attempt["scheduled_time"]),
        )
        if previous is not None:
            assert scheduled_at > previous, "retry times must be strictly increasing"

        matching_window = next(
            (
                window
                for window in PERMITTED_EXECUTION_WINDOWS
                if window["label"] == attempt["window_label"]
            ),
            None,
        )
        assert matching_window is not None, "unknown execution window label"
        assert (
            _parse_clock(matching_window["start"])
            <= scheduled_at.time()
            <= _parse_clock(matching_window["end"])
        ), "retry falls outside its permitted execution window"

        previous = scheduled_at
        previous_attempt = attempt_num


def _fallback_justification(record: Mapping[str, Any]) -> str:
    """Explain the deterministic strategy without relying on Gemini."""

    reason = str(record.get("failure_reason", "unknown_failure"))
    explanations = {
        "insufficient_balance": (
            "Retries are spaced across several days to allow balance replenishment."
        ),
        "bank_server_down": (
            "Retries begin quickly because a bank server outage is usually transient."
        ),
        "technical_decline": (
            "Retries use short initial waits with more time for later technical recovery."
        ),
        "daily_limit_exceeded": (
            "Retries begin on a later calendar day so the daily limit can reset."
        ),
    }
    return explanations.get(reason, "No automatic retry schedule is permitted by policy.")


def _parse_justifications(raw_text: str) -> dict[str, str]:
    """Parse validated mandate-to-justification results from Gemini JSON."""

    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.splitlines()[1:-1]).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start == -1 or end <= start:
            raise ValueError("Gemini response did not contain a JSON array") from None
        payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, list):
        raise ValueError("Gemini response must be a JSON array")

    results: dict[str, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        mandate_id = item.get("mandate_id")
        justification = item.get("schedule_justification")
        if isinstance(mandate_id, str) and isinstance(justification, str) and justification.strip():
            results[mandate_id] = justification.strip()
    return results


def _gemini_justification_batch(
    records: list[Mapping[str, Any]], api_key: str
) -> dict[str, str]:
    """Ask Gemini to explain schedules that have already been validated."""

    from google import genai
    from google.genai import types

    immutable_schedules = [
        {
            "mandate_id": record["mandate_id"],
            "failure_reason": record["failure_reason"],
            "retry_schedule": record["retry_schedule"],
        }
        for record in records
    ]
    prompt = (
        "Explain why each deterministic UPI retry schedule fits its failure reason. "
        "Do not propose, change, remove, or add dates or times. Return only a JSON array "
        "with mandate_id and schedule_justification. Schedules:\n"
        + json.dumps(immutable_schedules, ensure_ascii=False)
    )
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return _parse_justifications(response.text or "")


def add_schedule_justifications(
    records: list[dict[str, Any]], api_key: str | None
) -> list[dict[str, Any]]:
    """Attach batched optional explanations while preserving every schedule."""

    scheduled_records = [record for record in records if record["retry_schedule"]]
    justifications = {
        str(record["mandate_id"]): _fallback_justification(record)
        for record in records
    }

    if api_key:
        for start in range(0, len(scheduled_records), GEMINI_BATCH_SIZE):
            batch = scheduled_records[start : start + GEMINI_BATCH_SIZE]
            try:
                ai_results = _gemini_justification_batch(batch, api_key)
            except Exception:
                continue
            for record in batch:
                mandate_id = str(record["mandate_id"])
                if mandate_id in ai_results:
                    justifications[mandate_id] = ai_results[mandate_id]

    for record in records:
        record["schedule_justification"] = justifications[str(record["mandate_id"])]
    return records


def decide_records(
    records: list[Mapping[str, Any]], api_key: str | None = None
) -> list[dict[str, Any]]:
    """Build all schedules first, then add optional language explanations."""

    output = [
        {**record, "retry_schedule": build_retry_schedule(record)} for record in records
    ]
    return add_schedule_justifications(output, api_key)


def _load_api_key() -> str | None:
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass
    return os.getenv("GEMINI_API_KEY") or None


def main() -> None:
    """Run deterministic scheduling against detection output."""

    records = json.loads(DEFAULT_INPUT_PATH.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"Expected a JSON array in {DEFAULT_INPUT_PATH}")

    output = decide_records(records, api_key=_load_api_key())
    DEFAULT_OUTPUT_PATH.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Built retry schedules for {len(output)} mandates at {DEFAULT_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
