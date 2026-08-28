# Actual Escalated Failure Case

> Source: `logs/audit_trail.json`. This example is extracted from an actual recorded simulation entry and is not fabricated.

## Mandate

- Mandate ID: `MANDATE-0004`
- Customer: Saanvi Joshi
- Category: `credit_card`
- Amount: 57960
- Failure reason: `daily_limit_exceeded`
- Failed at: `2026-07-05T21:59:00+05:30`
- Urgency priority: `high`
- Attempts already made: 2
- Escalation threshold: 2

## Scheduled Retries

- Attempt 3: 2026-07-08 at 22:00 (late_night).

## Executed Attempts

- No retry was executed during the simulation before escalation.

## Recorded Outcome

- Final status: `escalated`
- Stop reason: Escalated before attempt 3 because the category threshold of 2 attempt(s) was reached.
