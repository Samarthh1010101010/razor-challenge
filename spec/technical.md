# Technical specification

## Invariants

1. A settlement is claimed **at most once**. `Index.claim` raises rather than
   overwriting.
2. The matcher **never** consults the classifier. Reconciliation figures are
   independent of whether a model ran.
3. The model **never** outputs an account, an amount, or a settlement id — only
   a member of a closed 6-value enum.
4. Every row produces **exactly one** audit record. Records are appended, never
   mutated.
5. Every gate rejection routes to suspense. Nothing is silently dropped.
6. Threshold calibration and reporting use **different seeds**.

## Data model

`Settlement` mirrors the verified Razorpay entity (`settlement_id`, `utr`,
`amount`, `fees`, `tax`, `status`, `settled_on`) with one derived property,
`expected_credit`, which subtracts fees and tax only when non-zero.

`BankTxn` is `SIMULATED`: `txn_id`, `value_date`, `credit_amount`, `narration`.

`Decision` carries the outcome and its whole provenance: tier, settlement,
reason, confidence, variance, evidence string, who decided, and — for triaged
rows — disposition, GL account, auto-post flag, and gate rejection code.

## Matching tiers

| Tier | Evidence | Acts alone? |
| --- | --- | --- |
| T1 | Labelled UTR + amount within ₹1 | Yes — strongest signal available |
| T1 | Unlabelled reference **+** amount corroborates | Only together |
| T2 | Labelled UTR, gap explained by non-zero fees/tax | Yes |
| T3 | Unique amount within a 3-day window | Yes, only when unique |
| — | Two or more candidates | No — escalates |

Tolerances: ±100 paise (banks credit whole rupees), ±3 days (T+1 is `INFERRED`,
not guaranteed, and weekends shift it).

## Triage contract

Messages API, `claude-opus-5`, `output_config` with a `json_schema` format and
`effort: "low"` — classification does not repay deep reasoning. The schema is
enforced server-side and **re-validated locally**, because the gate must not
trust its input.

## The gate

Runs four checks in order: label in the closed set → confidence ≥ calibrated
threshold → two-signal agreement → GL account by table lookup.

Two-signal agreement re-derives evidence the model never saw:

- A "not ours" label is refused when the narration carries a reference resolving
  to a settlement on file.
- `AWAITING_SETTLEMENT_REPORT` is refused when an unclaimed settlement already
  fits the amount and date.

## Failure model

Every model failure path returns a routable value, never an exception. A dead
API degrades the system to rules-only. Full table in `docs/failures.md`.

## Dependencies

`anthropic` (optional — the pipeline runs without it) and `pytest`. Everything
else is standard library. No web framework, no database, no queue, no agent
framework, no vector store: none of them solve a problem this system has.
