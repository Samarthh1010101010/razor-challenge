# Workflow, gates, and acceptance

Long-form process referenced by `CLAUDE.md`. Read when opening or closing a
phase, not on every turn.

## Phases

| # | Phase | Done when |
| --- | --- | --- |
| 0 | Research | Brief captured verbatim; every external capability labelled |
| 1 | Product | Problem, user, differentiator written down |
| 2 | Specification | Workflow, data model, safety model, metrics defined |
| 3 | Core system | Smallest complete end-to-end backbone runs |
| 4 | Data / simulation | Reproducible, seeded, ground truth emitted |
| 5 | AI | Added *and evaluated*; kept only if it beats the baseline |
| 6 | Agent / bounded action | Proposals gated; every rejection routed |
| 7 | Integration boundaries | Verified contract at one swappable seam |
| 8 | UI | Reads real run state, no hardcoded figures |
| 9 | Evaluation | Baseline, held-out split, error analysis |
| 10 | Hardening | Adversarial cases tested and fixed |
| 11 | Demo | Runs from a clean checkout, no manual edits |
| 12 | Submission | README, architecture, video, claims all defensible |

Do not build UI before the core system runs.

## Quality gates

A feature is **not** complete because a file exists, a function exists, an
endpoint returns 200, or a mock works. It is complete when it runs the intended
flow and there is evidence of correctness.

For anything important: implementation + test + integration + failure handling +
documentation.

## Adversarial checklist

Before finalising, assume a skeptical Razorpay engineer is reviewing. Try to
break it with: malformed input, missing input, duplicate events, duplicate
requests, out-of-order events, invalid states, low-confidence predictions, bad
model output, model hallucinations, unauthorised actions, excessive retries, API
failure, timeout, partial execution, stale data, inconsistent state, concurrent
execution, malicious input, misleading data, empty datasets, extreme values.

Coverage lives in `tests/test_recon.py`.

## Self-critique at milestones

- **Product** — would a real merchant use this?
- **AI** — is the model necessary, and did we *measure* that?
- **Engineering** — would an experienced engineer trust this architecture?
- **Payments** — could this alter financial state incorrectly?
- **Evaluation** — would a skeptical judge believe these numbers?
- **Differentiation** — why is this better than a competent LLM wrapper?

## Acceptance checklist

- [x] Track selected from the current official brief
- [x] Problem meaningful and clearly defined
- [x] Differentiator that is not the obvious build
- [x] AI's value measured, not assumed
- [x] Consequential actions bounded by a deterministic gate
- [x] Claim-once enforced; no duplicate financial actions
- [x] Auditable — one append-only record per row
- [x] Data generation realistic and reproducible
- [x] Baseline exists and is reported
- [x] Calibration and reporting splits are different seeds
- [x] Metrics not fabricated; none hardcoded
- [x] Failure cases tested
- [x] Razorpay contract verified against official docs
- [x] No unsupported Razorpay functionality presented as real
- [x] End-to-end golden path runs from clean checkout
- [x] Tests pass
- [x] README works for clean setup
- [x] Documentation matches implementation
- [ ] 5-minute pitch video recorded
- [ ] Submitted (form is final — no edits after)
