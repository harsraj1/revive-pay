"""Generate concise customer-facing Hinglish payment-failure messages."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

try:
    from .constants import (
        CREATE_SUPPORT_CASE,
        REQUEST_MANDATE_RENEWAL,
        RETRY_AUTOPAY,
        SEND_BALANCE_REMINDER,
        SEND_PAYMENT_LINK,
        STOP_AUTOMATION,
        WAIT_FOR_DAILY_RESET,
    )
except ImportError:  # Supports direct execution: python src/message.py
    from constants import (  # type: ignore[no-redef]
        CREATE_SUPPORT_CASE,
        REQUEST_MANDATE_RENEWAL,
        RETRY_AUTOPAY,
        SEND_BALANCE_REMINDER,
        SEND_PAYMENT_LINK,
        STOP_AUTOMATION,
        WAIT_FOR_DAILY_RESET,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_PATH = PROJECT_ROOT / "data" / "escalate_output.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "message_output.json"
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_BATCH_SIZE = 25
MAX_MESSAGE_LENGTH = 280

# Tone is a presentation concern, not retry or escalation policy.
MESSAGE_TONES = {
    "subscription": "casual and friendly",
    "emi": "respectful and formal",
    "sip": "respectful and formal",
    "insurance": "respectful and formal",
    "credit_card": "respectful and formal",
}

# Reject claims that could mislead customers when the system has no supporting
# penalty data and cannot guarantee a future payment result.
PROHIBITED_CLAIMS = ("penalty", "late fee", "fine", "guaranteed", "definitely")
FAILURE_WORDS = ("fail", "failed", "nahi ho paya", "unsuccessful")


def _next_action(record: Mapping[str, Any]) -> str:
    """Translate router actions into one truthful customer instruction."""

    raw_actions = record.get("chosen_actions")
    actions = (
        {str(action) for action in raw_actions}
        if isinstance(raw_actions, list)
        else set()
    )

    # Combined terminal actions need combined copy: omitting STOP_AUTOMATION or
    # CREATE_SUPPORT_CASE would make a payment-link message technically true but
    # incomplete and confusing to the customer.
    if {
        CREATE_SUPPORT_CASE,
        SEND_PAYMENT_LINK,
        STOP_AUTOMATION,
    }.issubset(actions):
        return (
            "No further automatic debit will be attempted; please use the payment-link "
            "option or contact customer support."
        )
    if SEND_PAYMENT_LINK in actions and STOP_AUTOMATION in actions:
        return (
            "No further automatic debit will be attempted; please complete the payment "
            "using the manual payment-link option."
        )

    # Customer-directed actions take priority for non-terminal combinations.
    # The complete action set is still passed to Gemini for context below.
    if REQUEST_MANDATE_RENEWAL in actions:
        return "Please renew the UPI mandate before trying the payment again."
    if SEND_BALANCE_REMINDER in actions:
        return "Please keep sufficient balance in your linked account for the scheduled retry."
    if WAIT_FOR_DAILY_RESET in actions:
        return "Please keep enough daily transaction limit available for the next-day retry."
    if SEND_PAYMENT_LINK in actions:
        return "Please complete the payment using the manual payment-link option."
    if CREATE_SUPPORT_CASE in actions:
        return "Please contact customer support; no further automatic debit will be attempted."
    if STOP_AUTOMATION in actions:
        return "No further automatic debit will be attempted; please contact customer support."
    if RETRY_AUTOPAY in actions:
        return "No action is needed now; we will retry at the scheduled time."

    # Compatibility fallback for old stage files that predate the router.
    reason = record.get("failure_reason")
    schedule = record.get("retry_schedule")
    has_retry = isinstance(schedule, list) and bool(schedule)

    if reason == "insufficient_balance" and has_retry:
        return "Please keep sufficient balance in your linked account for the scheduled retry."
    if reason in {"bank_server_down", "technical_decline"} and has_retry:
        return "No action is needed now; we will retry at the scheduled time."
    if reason == "daily_limit_exceeded" and has_retry:
        return "Please keep enough daily transaction limit available for the next-day retry."
    if reason == "mandate_expired":
        return "Please renew the UPI mandate before trying the payment again."
    if reason == "user_declined":
        return "Please approve a new payment only if you wish to continue."
    if reason == "account_frozen":
        return "Please contact your bank to check the account status."
    return "Please review the payment details or contact customer support for help."


def _category_label(category: str) -> str:
    """Return a customer-readable payment category label."""

    return {
        "subscription": "subscription",
        "emi": "EMI",
        "sip": "SIP",
        "insurance": "insurance premium",
        "credit_card": "credit card bill",
    }.get(category, "UPI AutoPay")


def fallback_message(record: Mapping[str, Any]) -> str | None:
    """Create deterministic Hinglish copy for all categories and failures."""

    if record.get("customer_message_required") is False:
        return None

    category = str(record.get("category", ""))
    label = _category_label(category)
    action = _next_action(record)
    first_name = str(record.get("customer_name", "Customer")).strip().split()[0]

    # The action is authored centrally in English, then mapped to fixed safe
    # Hinglish phrases. No amount, penalty, or recovery outcome is invented.
    action_hinglish = {
        "Please keep sufficient balance in your linked account for the scheduled retry.": (
            "Agle retry se pehle linked account mein sufficient balance rakh lein."
        ),
        "No action is needed now; we will retry at the scheduled time.": (
            "Abhi aapko kuch karne ki zarurat nahi; hum sahi time par dobara try karenge."
        ),
        "Please keep enough daily transaction limit available for the next-day retry.": (
            "Kal dobara try karne se pehle daily transaction limit available rakhiyega."
        ),
        "Please renew the UPI mandate before trying the payment again.": (
            "Payment continue karne ke liye apna UPI mandate renew karein."
        ),
        "Please complete the payment using the manual payment-link option.": (
            "Payment complete karne ke liye payment link option use karein."
        ),
        (
            "No further automatic debit will be attempted; please use the payment-link "
            "option or contact customer support."
        ): (
            "Automatic retries rok diye gaye hain; payment link use karein ya help ke "
            "liye customer support se baat karein."
        ),
        (
            "No further automatic debit will be attempted; please complete the payment "
            "using the manual payment-link option."
        ): (
            "Automatic retries rok diye gaye hain; payment complete karne ke liye "
            "payment link option use karein."
        ),
        "Please contact customer support; no further automatic debit will be attempted.": (
            "Automatic retries rok diye gaye hain; help ke liye customer support se baat karein."
        ),
        "No further automatic debit will be attempted; please contact customer support.": (
            "Automatic retries rok diye gaye hain; help ke liye customer support se baat karein."
        ),
        "Please approve a new payment only if you wish to continue.": (
            "Continue karna ho toh naya payment approve karein."
        ),
        "Please contact your bank to check the account status.": (
            "Account status check karne ke liye apne bank se contact karein."
        ),
        "Please review the payment details or contact customer support for help.": (
            "Payment details check karein ya help ke liye customer support se contact karein."
        ),
    }[action]

    if category == "subscription":
        return f"Hi {first_name}, aapka {label} payment fail ho gaya. {action_hinglish}"
    return f"Namaste {first_name}, aapka {label} payment fail ho gaya hai. {action_hinglish}"


def _is_safe_message(message: object) -> bool:
    """Validate minimum customer-copy constraints before accepting AI text."""

    if not isinstance(message, str):
        return False
    cleaned = message.strip()
    lowered = cleaned.casefold()
    return (
        0 < len(cleaned) <= MAX_MESSAGE_LENGTH
        and any(word in lowered for word in FAILURE_WORDS)
        and not any(claim in lowered for claim in PROHIBITED_CLAIMS)
    )


def _parse_messages(raw_text: str) -> dict[str, str]:
    """Parse a Gemini JSON array and keep only safe, complete messages."""

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
        message = item.get("customer_message")
        # Invalid model output is ignored per record, leaving its fallback intact.
        if isinstance(mandate_id, str) and _is_safe_message(message):
            results[mandate_id] = str(message).strip()
    return results


def _gemini_message_batch(
    records: list[Mapping[str, Any]], api_key: str
) -> dict[str, str]:
    """Generate language only for one batch of already-retriable mandates."""

    from google import genai
    from google.genai import types

    prompt_records = [
        {
            "mandate_id": record["mandate_id"],
            "customer_first_name": str(record.get("customer_name", "Customer")).split()[0],
            "category": record["category"],
            "failure_reason": record["failure_reason"],
            "chosen_actions": record.get("chosen_actions", []),
            "tone": MESSAGE_TONES.get(str(record["category"]), "respectful and formal"),
            "required_next_action": _next_action(record),
        }
        for record in records
    ]
    prompt = (
        "Write concise, natural Roman-script Hinglish messages for Indian fintech users. "
        "Return only a JSON array with mandate_id and customer_message. Each message must "
        "clearly say the payment failed and communicate the supplied next action. Sound like "
        "a helpful Indian payments app, not a literal translation or a bank notice. Prefer "
        "simple phrases such as 'dobara try karenge', 'rakh lein', and 'baat karein'; avoid "
        "stiff phrases such as 'action zaroori' or 'scheduled time par retry hoga'. Never add, "
        "remove, or reinterpret chosen_actions. Do not invent links, penalties, fees, deadlines, "
        "promises, or payment outcomes. Keep each message to at most two short sentences and under "
        f"{MAX_MESSAGE_LENGTH} characters. Records:\n"
        + json.dumps(prompt_records, ensure_ascii=False)
    )
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return _parse_messages(response.text or "")


def add_customer_messages(
    records: list[Mapping[str, Any]], api_key: str | None = None
) -> list[dict[str, Any]]:
    """Attach AI or fallback messages without changing upstream decisions."""

    output = [{**record, "customer_message": fallback_message(record)} for record in records]
    messageable = [
        record
        for record in output
        if record.get("customer_message_required") is not False
        and isinstance(record.get("customer_message"), str)
    ]

    if api_key:
        for start in range(0, len(messageable), GEMINI_BATCH_SIZE):
            batch = messageable[start : start + GEMINI_BATCH_SIZE]
            try:
                generated = _gemini_message_batch(batch, api_key)
            except Exception:
                # Messaging must never prevent the deterministic pipeline running.
                continue
            for record in batch:
                mandate_id = str(record["mandate_id"])
                if mandate_id in generated:
                    record["customer_message"] = generated[mandate_id]
    return output


def _load_api_key() -> str | None:
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass
    return os.getenv("GEMINI_API_KEY") or None


def main() -> None:
    """Read escalation output and write customer messaging output."""

    records = json.loads(DEFAULT_INPUT_PATH.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"Expected a JSON array in {DEFAULT_INPUT_PATH}")

    output = add_customer_messages(records, api_key=_load_api_key())
    DEFAULT_OUTPUT_PATH.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Added customer messages to {len(output)} mandates at {DEFAULT_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
