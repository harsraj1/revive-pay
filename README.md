# UPI Recovery Agent

An educational Python backend project that models recovery of failed recurring UPI AutoPay payments. It decides whether a failure may be retried, creates legal retry schedules, applies category-aware escalation, generates customer messages, simulates recovery, and compares the result with a naive fixed-interval strategy.

The central engineering principle is:

> Deterministic rules for hard business constraints; AI only for judgment and natural-language tasks.

This is a simulation and portfolio project. It does not connect to NPCI, banks, payment service providers, or real customer accounts.

## Problem

Recurring payments fail for different reasons. A bank outage may recover within hours, insufficient balance may require several days, and an expired mandate should never be retried automatically. Payment type matters too: an EMI or insurance payment deserves earlier human attention than a low-cost subscription.

A useful recovery system must answer:

1. Is automatic retry allowed?
2. When can each retry legally run?
3. When should automation stop and involve support?
4. What should the customer be told?
5. Did the simulated retry recover the payment?
6. Does the strategy outperform a naive baseline on the same failures?

## Architecture

```text
failed_mandates.json
        │
        ▼
   detect.py       deterministic retriability + optional AI diagnosis
        │
        ▼
   decide.py       deterministic schedule + optional AI justification
        │
        ▼
  escalate.py      category-aware human escalation threshold
        │
        ▼
   message.py      constrained Hinglish generation + safe fallback
        │
        ▼
     act.py        seeded execution simulation and audit trail
        │
        ├────────► final_report.json
        └────────► audit_trail.json

failed_mandates.json ──► baseline.py ──► baseline_report.json
                                  │
final_report.json ────────────────┴──► comparison_summary.json

audit_trail.json ──► find_failure_case.py ──► failure_case_example.md
```

The baseline deliberately starts from the original raw dataset, not smart-pipeline output. This keeps the population, amounts, failure mix, seed, retry cap, and probability assumptions constant so the experiment measures strategy rather than input differences.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for stage contracts and trust boundaries.

## Core scheduling algorithm

`build_retry_schedule(record)` separates recovery strategy from execution-window enforcement:

1. Recheck centralized failure policy and fail closed for unknown or non-retriable reasons.
2. Calculate remaining attempts from `MAX_RETRIES - attempts_already_made`.
3. Select deterministic not-before offsets for the failure reason.
4. Move each candidate forward to the earliest permitted execution window.
5. Preserve strict chronological ordering if two candidates reach the same boundary.
6. Validate the complete schedule independently before returning it.

Strategies differ intentionally:

- `insufficient_balance`: retries are spaced across days to allow replenishment.
- `bank_server_down`: retries begin within hours because the failure is likely transient.
- `technical_decline`: rapid initial retry followed by more recovery time.
- `daily_limit_exceeded`: the first retry is always on a later calendar day.

`validate_schedule()` asserts retry caps, attempt ordering, timestamp ordering, recognized window labels, and window boundaries. Gemini cannot create or modify a schedule.

## AI and deterministic responsibilities

| Responsibility | Implementation | Why |
|---|---|---|
| Failure retriability | Deterministic | Hard policy must be stable, auditable, and fail closed. |
| Retry cap | Deterministic | A model must never reinterpret an attempt limit. |
| Execution windows | Deterministic | Legal/operational time validation is binary. |
| Category escalation | Deterministic | Human-review thresholds must be consistent. |
| Recovery simulation | Deterministic seeded probability | Experiments must be reproducible. |
| Short failure diagnosis | Optional Gemini | Natural-language judgment can improve explanation. |
| Schedule justification | Optional Gemini | AI explains an existing schedule but cannot alter it. |
| Customer Hinglish | Optional Gemini | Natural phrasing is a language-generation task. |

All AI stages are fallback-first. Missing SDKs, absent keys, API failures, malformed JSON, incomplete batches, or unsafe messages do not stop the pipeline. Unknown failure reasons are never sent for automatic retry.

## Setup

Requirements:

- Python 3.11+
- A Gemini API key only if AI-enhanced text is desired

Create and activate a virtual environment:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

For macOS or Linux, activation is:

```bash
source .venv/bin/activate
```

Optional Gemini configuration:

```powershell
Copy-Item .env.example .env
```

Then edit `.env`:

```env
GEMINI_API_KEY=your_actual_api_key
```

Never commit `.env`. It is excluded by `.gitignore`. Without a key, the entire pipeline still runs using deterministic diagnosis, justification, and message templates.

## Running the project

Run every stage in fail-fast order:

```powershell
python src/run_all.py
```

Run stages individually from the repository root:

```powershell
python src/generate_data.py
python src/detect.py
python src/decide.py
python src/escalate.py
python src/message.py
python src/act.py
python src/baseline.py
python src/find_failure_case.py
```

Each stage reads the previous stage's JSON and writes a new artifact. If a stage fails, `run_all.py` prints its name, stops immediately, and returns a non-zero exit status.

## Testing

Run the complete suite:

```powershell
python -m pytest -q
```

The tests cover reproducible data generation, schema validity, fail-closed detection, fallback behavior, retry caps, permitted windows, reason-specific spacing, category escalation, messaging safety, deterministic simulation, baseline rejection, real-case extraction, and fail-fast orchestration.

Current verified result:

```text
29 passed
```

## Reproducible results

Using the committed policy constants and current seeds, the 100-mandate synthetic portfolio produced:

| Metric | Smart strategy | Naive baseline |
|---|---:|---:|
| Total at-risk amount | ₹4,223,068 | ₹4,223,068 |
| Recovered mandates | 42 | 15 |
| Recovery rate by count | 42.00% | 15.00% |
| Recovered amount | ₹1,414,448 | ₹650,672 |
| Recovery rate by amount | 33.49% | 15.41% |

The smart strategy achieved a **27 percentage-point uplift** and **₹763,776 additional simulated recovery**. The naive strategy produced 149 rejected attempts because fixed retry times landed outside permitted windows.

These figures demonstrate the behavior of the chosen synthetic seed and illustrative probabilities; they are not claims about real-world recovery performance.

## Baseline design

The deliberately naive strategy:

- retries every failure, including non-retriable types;
- uses a fixed eight-hour interval;
- ignores failure-reason recovery behavior;
- ignores category escalation;
- does not move attempts into legal windows;
- records out-of-window attempts as rejected.

Valid baseline attempts use the same failure-specific probability assumptions and deterministic per-attempt draws as the smart simulation wherever logically possible.

## Failure handling and auditability

- Unknown failure reasons fail closed and require human review.
- Unsupported categories stop processing instead of receiving a guessed risk policy.
- Invalid schedules fail validation before execution.
- Non-retriable mandates execute zero retries.
- Recovery stops subsequent attempts immediately.
- Category thresholds stop another blind retry and mark the mandate escalated.
- Exhausted schedules are distinguished from escalated and non-retriable cases.
- Every executed attempt records its schedule, window, probability, deterministic draw, outcome, and stopping reason.
- `find_failure_case.py` documents an actual audit escalation or explicitly states that the seed produced none.

## Project structure

```text
upi-recovery-agent/
├── src/
│   ├── __init__.py
│   ├── constants.py
│   ├── generate_data.py
│   ├── detect.py
│   ├── decide.py
│   ├── escalate.py
│   ├── message.py
│   ├── act.py
│   ├── baseline.py
│   ├── find_failure_case.py
│   └── run_all.py
├── data/                     # stage outputs and reports
├── logs/
│   └── audit_trail.json
├── docs/
│   ├── ARCHITECTURE.md
│   ├── comparison_summary.json
│   └── failure_case_example.md
├── tests/
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

Root-level `run_all.py` and `find_failure_case.py` are retained as convenience/compatibility entry points; the documented canonical implementations are under `src/`.

## Limitations

- All mandates and customer names are synthetic.
- Recovery probabilities are illustrative constants, not trained estimates.
- No real UPI, bank, mandate, notification, or support APIs are called.
- JSON files are suitable for a learning pipeline, not concurrent production workloads.
- Execution windows are simplified daily windows and do not model holidays or provider-specific rules.
- The simulation does not model fees, settlement, idempotency keys, network timeouts, or reconciliation.
- AI safety checks are intentionally lightweight and would require stronger policy evaluation in production.

## Future improvements

- Introduce provider-specific calendars and configurable regional time zones.
- Add idempotent command handling and payment-provider adapters behind interfaces.
- Store events in an append-only database with correlation IDs and immutable audit metadata.
- Add queue-based execution, retries for infrastructure failures, and observability metrics.
- Calibrate recovery probabilities from privacy-safe historical aggregates.
- Add experiment confidence intervals rather than comparing only one synthetic seed.
- Add approval workflows and support-ticket integrations for escalated payments.
- Strengthen message evaluation with locale, accessibility, and compliance review.
