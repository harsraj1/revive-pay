# Architecture

## Objective

UPI Recovery Agent is a staged educational pipeline for failed recurring UPI AutoPay mandates. Its primary design constraint is that probabilistic language generation cannot control money-movement policy.

```text
generate → detect → decide → escalate → message → act
    └────────────── baseline reads raw input ──────────────┘
                                      │
                                      ▼
                              compare strategies
```

## Stage contracts

| Stage | Input | Output | Responsibility |
|---|---|---|---|
| Generate | Policy constants and fixed seed | `data/failed_mandates.json` | Create 100 reproducible synthetic failures. |
| Detect | Raw failures | `data/detect_output.json` | Apply deterministic retriability and optional diagnosis. |
| Decide | Detection output | `data/decide_output.json` | Build and validate deterministic schedules. |
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
- simulation probabilities and seeds.

Unknown failure reasons fail closed. The scheduler rechecks the policy instead of trusting persisted upstream flags, providing defense in depth at the money-movement boundary.

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

Escalation policy is attached before execution but does not alter the schedule. During simulation, the engine checks the threshold before each next attempt. Recovery stops execution immediately; reaching the threshold stops another blind attempt and assigns human escalation.

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
- customer-facing Hinglish for retriable records.

The model never decides retriability, caps, windows, dates, escalation, probabilities, or outcomes. Requests are batched and require JSON. Outputs are parsed and validated per record. Deterministic fallbacks are installed before API calls, so missing or invalid AI output cannot make the pipeline incomplete.

## Baseline validity

The baseline reads `failed_mandates.json` directly. It retries every failure at fixed intervals, applies no category escalation or reason-aware timing, and does not correct restricted timestamps. This deliberately weak behavior is evaluated on the same input population and deterministic outcome model, isolating the value of the smart strategy.

## Failure and orchestration semantics

`src/run_all.py` uses the active Python interpreter to run each stage as a separate process. It checks each exit code and stops at the first failure, preventing later artifacts from being built from missing or stale upstream data.

The pipeline remains operational without Gemini. Data/schema errors, invalid policies, or illegal schedules fail loudly because silently continuing would compromise auditability.
