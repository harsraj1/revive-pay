# Architecture

## Objective

UPI Recovery Agent is a staged educational pipeline for failed recurring UPI AutoPay mandates. Its primary design constraint is that probabilistic language generation cannot control money-movement policy.

```text
Razorpay Test Mode
      │ payment/subscription webhook
      ▼
webhook_server.py
      │ HMAC verification + SQLite idempotency
      ▼
normalize_event() ── Pydantic boundary validation
      │
      ├── failed/pending/halted ─► detect → decide/router → escalate → message → simulated act
      └── charged ────────► externally confirmed recovered audit entry

generate → detect → decide → escalate → message → act
    └────────────── baseline reads raw input ──────────────┘
                                      │
                                      ▼
                              compare strategies
```

## Razorpay ingestion boundary

`POST /webhooks/razorpay` reads the raw body before JSON parsing and validates
`X-Razorpay-Signature` using HMAC-SHA256 and `RAZORPAY_WEBHOOK_SECRET`. The
`x-razorpay-event-id` is inserted into a SQLite table with a unique primary key.
Duplicates receive HTTP 200 without a second audit record. A processing failure
releases the claim so Razorpay can retry it.

Pydantic exists only in `src/ingest.py`. After validation, failed mandates are
ordinary dictionaries matching the existing pipeline contract.

### Exact field mapping

| Internal field | `payment.failed` source | `subscription.pending` / `subscription.halted` source |
|---|---|---|
| `mandate_id` | `notes.mandate_id`, else `invoice_id`, `order_id`, then payment `id` | `notes.mandate_id`, else subscription `id` |
| `customer_name` | `payment.notes.customer_name` | `subscription.notes.customer_name` |
| `category` | `payment.notes.recovery_category` | `subscription.notes.recovery_category` |
| `amount` | `payment.amount / 100` | payment `amount / 100`, else `notes.recovery_amount_paise / 100` |
| `failure_reason` | deterministic mapping of `error_code`, `error_reason`, and `error_description` | Pending maps payment error evidence; evidence-free pending and halted values intentionally fail closed |
| `failed_at` | payment `created_at` Unix UTC converted to IST | webhook `created_at` Unix UTC converted to IST |
| `attempts_already_made` | `payment.notes.attempts_already_made` | maximum of notes attempts and subscription `auth_attempts`, capped by policy |
| source IDs | account and payment IDs | account, payment, and subscription IDs |

Merchant metadata is required in Razorpay `notes`; category or customer identity
is never guessed from amount or free text. Amounts must currently represent whole
INR because that is the existing internal contract. Unmapped payment errors become
`unknown_razorpay_failure`, causing deterministic detection to block retries and
request human review.

For `subscription.charged`, the subscription/payment/customer/amount fields map
to the audit entry and the event is recorded as `recovered`. It is not normalized
into a failed mandate because doing so would corrupt the pipeline semantics.

`subscription.pending` is treated as the primary subscription charge-failure
event. If its payload carries a payment entity, the same deterministic error
mapping used for `payment.failed` applies. If no payment error evidence exists,
the reason becomes `unknown_subscription_failure`, preventing an unsafe retry.

The final payment execution remains simulated. No code in this repository claims
to trigger an actual UPI AutoPay retry through Razorpay Test Mode.

## Stage contracts

| Stage | Input | Output | Responsibility |
|---|---|---|---|
| Webhook ingestion | Signed Razorpay Test Mode event | Normalized plain dictionary | Verify, deduplicate, validate, and route one external event. |
| Generate | Policy constants and fixed seed | `data/failed_mandates.json` | Create 100 reproducible synthetic failures. |
| Detect | Raw failures | `data/detect_output.json` | Apply deterministic retriability and optional diagnosis. |
| Decide/router | Detection output | `data/decide_output.json` | Select allow-listed interventions, then build schedules only for authorized retries. |
| Escalate | Scheduling output | `data/escalate_output.json` | Attach category urgency and human-review threshold. |
| Message | Escalation output | `data/message_output.json` | Add constrained customer Hinglish with fallback. |
| Act | Messaging output | `logs/audit_trail.json`, `data/final_report.json` | Simulate attempts and aggregate recovery. |
| Baseline | Raw failures and smart report | `data/baseline_report.json`, `docs/comparison_summary.json` | Run naive fixed-time retries and compare outcomes. |
| Failure case | Audit trail | `docs/failure_case_example.md` | Document one real escalation without fabrication. |

JSON outputs intentionally preserve upstream fields. This makes each stage independently inspectable and allows the final audit to answer what failed, why retry was permitted, when execution occurred, what happened, and why automation stopped.

## Policy boundary

`src/constants.py` is the single source for:

- supported categories and amount ranges;
- retriable and non-retriable failure reasons;
- maximum retries;
- permitted execution windows;
- category escalation thresholds;
- permitted intervention actions and amount-band boundaries;
- simulation probabilities and seeds.

Unknown failure reasons fail closed. The scheduler rechecks the policy instead of trusting persisted upstream flags, providing defense in depth at the money-movement boundary.

## Intervention routing

`src/intervention_router.py` is a deterministic policy gate between detection
and scheduling. Its decision tuple is failure reason, existing category,
centrally defined amount band, and attempts already made. It returns an ordered
`chosen_actions` list, a primary action, a policy reason, and whether customer
communication is required. The closed action set is:

- `RETRY_AUTOPAY`;
- `SEND_BALANCE_REMINDER`;
- `SEND_PAYMENT_LINK`;
- `REQUEST_MANDATE_RENEWAL`;
- `WAIT_FOR_DAILY_RESET`;
- `CREATE_SUPPORT_CASE`;
- `STOP_AUTOMATION`.

No scoring, ML, or Gemini call participates. Expired mandates always select
renewal only, and an independent validator rejects any manually constructed
expired-mandate retry. EMI and insurance use their existing early-escalation
threshold, while the global cap selects a payment-link or support/stop path.
Unknown failures select support and stop automation. `decide.py` discards every
retry schedule unless the router explicitly selected `RETRY_AUTOPAY`.

## Scheduling

Failure-specific strategy produces not-before candidates. A shared normalizer moves candidates forward to the earliest permitted window. It never moves a timestamp backward. If candidates collapse onto one boundary, later attempts advance to preserve strict ordering.

Daily-limit scheduling is calendar-aware: its first attempt occurs on a later date after reset. Other retriable failures use centrally explainable offsets reflecting likely recovery behavior.

Construction and validation are separate. `validate_schedule()` independently enforces:

- `MAX_RETRIES`;
- valid and increasing attempt numbers;
- strictly increasing timestamps;
- known window labels;
- inclusive configured time boundaries.

Timezone handling is centralized in `src/time_utils.py`. Naive source values are
explicitly interpreted as Asia/Kolkata, aware values from other zones are
converted to Asia/Kolkata, and serialized schedules include both the named zone
and an ISO-8601 `+05:30` offset. Host-local timezone settings are never consulted.

## Escalation and execution

Escalation policy is attached before execution but does not alter the schedule.
During simulation, the engine checks both the routed action and category
threshold before each next attempt. Recovery stops execution immediately;
reaching the threshold or selecting a support/stop action prevents another blind
attempt and assigns the corresponding terminal state. The audit preserves the
action set, primary action, amount band, decision-table rule, and policy reason.

Each probability draw is derived from the fixed seed, mandate ID, and attempt number using a stable SHA-256-based seed. Therefore mandate outcomes do not depend on list order, and corresponding smart/baseline attempts can use identical draws.

Final states are:

- `recovered`: an executed attempt succeeded;
- `escalated`: policy stopped another automatic attempt;
- `non_retriable`: deterministic policy prohibited execution;
- `exhausted`: all available scheduled attempts failed or none remained.

## AI boundary and degradation

Gemini is optional and limited to:

- concise failure diagnosis and recovery-confidence language;
- explanation of an already validated schedule;
- customer-facing Hinglish for records whose routed action requires communication.

The model never decides retriability, caps, windows, dates, escalation, probabilities, or outcomes. Requests are batched and require JSON. Outputs are parsed and validated per record. Deterministic fallbacks are installed before API calls, so missing or invalid AI output cannot make the pipeline incomplete.

## Baseline validity

The baseline reads `failed_mandates.json` directly. It retries every failure at fixed intervals, applies no category escalation or reason-aware timing, and does not correct restricted timestamps. This deliberately weak behavior is evaluated on the same input population and deterministic outcome model, isolating the value of the smart strategy.

## Failure and orchestration semantics

`src/run_all.py` uses the active Python interpreter to run each stage as a separate process. It checks each exit code and stops at the first failure, preventing later artifacts from being built from missing or stale upstream data.

The pipeline remains operational without Gemini. Data/schema errors, invalid policies, or illegal schedules fail loudly because silently continuing would compromise auditability.
