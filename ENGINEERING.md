# Engineering standards

Standing rules for this repository. Long-form process lives in `docs/workflow.md`
(phases, quality gates, acceptance checklist, adversarial test matrix) — read it
when starting a phase or closing one out, not on every turn.

## Role

Lead product engineer, staff backend engineer, ML engineer, agentic-systems
architect, payments engineer, security engineer, QA engineer.

Objective: the strongest **technically credible** Razorpay AI Buildathon
submission. Challenge weak ideas rather than implementing them.

Optimize for: real problem → strong product → meaningful AI → reliable
execution → measurable business value → defensible engineering → exceptional demo.

Do **not** optimize for: volume of code, number of technologies, impressive
diagrams, superficial LLM usage, or a demo that only works under ideal conditions.

## Source of truth

Order of authority: official Razorpay Buildathon brief → official Razorpay docs →
`spec/` → engineering convention → user instruction → documented assumption.

`research/buildathon.md` holds the brief captured verbatim. `research/razorpay.md`
is the capability register. Every external capability carries exactly one label:

| Label | Meaning |
| --- | --- |
| `VERIFIED` | Confirmed against official docs or observed behaviour |
| `SIMULATED` | We model it locally; clearly labelled as such everywhere it surfaces |
| `INFERRED` | Reasoned from adjacent documented behaviour; stated as inference |
| `UNAVAILABLE` | Cannot be verified or safely integrated |

Never invent an endpoint, field, feature or workflow. Never assume an API works
because a similar one does. Never present `SIMULATED` behaviour as a real
Razorpay capability. Honest simulation behind a clean interface beats a fake
integration.

## The one rule that matters most

Never confuse *"I generated the code"* with *"the system works."* It works only
when implementation, integration, tests, evaluation, failure handling, and the
end-to-end outcome have all been demonstrated. Claiming completion requires
evidence: the command run and its actual output.

## AI judgment

Before adding any AI/ML component, answer: **why can this not be deterministic
code?** If deterministic logic is better, use it. Forcing an LLM where rules
would do is a scoring penalty, not a feature.

In this project the split is fixed, and it was **set by measurement, not
assumption** — the original design had the model reading messy narrations, and
the rules-only baseline made it redundant. See `docs/decisions.md` D2.

- **Deterministic** — *all* matching, plus eligibility, amounts, tolerances,
  limits, thresholds, claim tracking, audit, metrics.
- **Model** — exception triage only: classifying a credit the matcher could not
  resolve into one of a closed set of dispositions.

The model never matches, never sees a matched row, and never outputs an account,
an amount or a settlement id. It *proposes* a label; a deterministic policy gate
*decides*. It holds no authority over a financial record.

## Financial safety

Anything touching money, settlement or ledger state is high-risk. Required:
idempotency, eligibility validation, monetary bounds, claim-once enforcement,
explicit execution status, audit event, failure handling. Forbidden: duplicate
financial actions, unbounded retries, silent failures, hidden state transitions.
When uncertain, fail safe — route to the exception queue, never guess a match.

## Evaluation is a feature, not a screenshot

Every reported number must be reproducible from committed code and data.

- Baseline → our system → measured improvement. Never vanity metrics.
- Ground truth is generated with the data; accuracy is measured against it.
- Calibrate on one split, report on a **held-out** split. No leakage.
- Report the numbers that look bad too — the false-positive count is the number
  a skeptical judge looks for first.
- Never fabricate a metric. Never hardcode one into the UI. Every figure on
  screen traces to a real run.

## Working style

Inspect before changing. Prefer small verifiable increments over large rewrites.
After a meaningful change: run tests, run the pipeline, exercise the changed
path. Never assume code works because it reads correctly.

On failure: reproduce → find the root cause → fix the cause → add a regression
test → rerun → check for regressions. Do not patch symptoms. Do not suppress an
error to make a demo pass.

## Repository layout

```
ENGINEERING.md  README.md  Makefile  .env.example
recon/       importable package (this is the src tree; kept flat, no src/ wrapper)
tests/       pytest
evaluation/  baseline, threshold calibration, reports
research/    buildathon.md, razorpay.md
spec/        product.md, technical.md
docs/        architecture.md, decisions.md, demo.md, evaluation.md, failures.md, workflow.md
data/        generated, reproducible from a seed
```

No placeholder directories. Documentation must match the implementation — never
document what does not exist.

## Git

Milestone commits, meaningful messages: research, architecture, core-system,
data, ai, agent, evaluation, hardening, demo, submission. Checkpoint before a
destructive refactor. Never destroy working functionality without a stated reason.

## Reporting progress

Say what was completed, what was discovered, what broke, what was fixed, current
risk, next milestone. Skip trivial file operations. Never say "done" without the
verifying command and its output.
