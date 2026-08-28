# Failure Case Example

> This case was extracted directly from `logs/audit_trail.json`; it is not fabricated.

## Mandate

- Mandate ID: `MANDATE-0022`
- Customer: Ishaan Verma
- Amount: ₹9,657
- Category: `insurance`
- Urgency: `high`
- Escalation threshold: 2 attempts

## Initial failure

- Failed at: `2026-07-29T03:33:00+05:30`
- Failure reason: `insufficient_balance`
- Retry rule: Configured policy marks insufficient_balance as retriable with a typical recovery window of 3 day(s).
- Schedule rationale: Retries are spaced across several days to allow balance replenishment.

## Executed retries

| Attempt | Scheduled | Window | Probability | Draw | Outcome |
|---:|---|---|---:|---:|---|
| 2 | 2026-08-01 06:00 | early_morning | 0.45 | 0.855241 | failure |

## Final result

- Status: `escalated`
- Stop reason: Escalated before attempt 3 because the category threshold of 2 attempt(s) was reached.

## Why escalation was correct

The centralized `insurance` policy says: Escalate early to reduce risk of coverage disruption. The engine therefore stopped before exceeding its configured threshold of 2 attempts and handed the case to human support.
