# What broke, and what we changed

Real incidents from building this, in order. Each changed the code.

## 1. Invented UTR format (caught by reading the docs)

The first generator produced 12-digit numeric UTRs and `setl_0001`-style ids.
Razorpay's actual settlements entity uses **alphanumeric** UTRs
(`1597813219e1pq6w`) and `setl_7IZKKI4Pnt2kEe`.

A payments engineer would have spotted the fake data instantly. Worse, it
quietly made the problem *easier*: a `\d{12}` extractor works fine on invented
UTRs and fails completely on real ones.

**Changed:** generator rewritten to the verified contract; the extractor now
matches a mostly-numeric alphanumeric shape.

## 2. Subtracting fees that Razorpay had already deducted

The generator computed the bank credit as `amount - fees - tax`. The docs say
*"in case of a normal settlement the fee charge will be 0"* — fees come off per
payment, not per settlement, so `amount` **is** the expected credit.

We had manufactured an entire variance class that does not exist.

**Changed:** `expected_credit` subtracts only genuinely non-zero fees, and the
variance class now models the documented non-normal case.

## 3. The extractor ate the `UTR` label

`bare_refs("...-UTR33942321851u3z9j")` returned `utr33942321851u3z9j` — label
fused to the reference, so every exact comparison against the settlement file
would have silently failed. Found by printing extractor output on five
narrations instead of assuming the regex was right.

**Changed:** leading label stripped when the remainder still matches the shape.

## 4. A docstring that described coverage the code did not have

The same function's docstring claimed it would return batch numbers alongside
the real reference. It does not — the 12-character floor already excludes them.
Small, but `CLAUDE.md` says documentation must match implementation, and a
confidently wrong comment is worse than none.

**Changed:** docstring corrected to what the code does.

## 5. The model turned out to be the wrong tool (the big one)

The design had a model reading messy narrations. The rules-only baseline came
back at **86.9% match rate, 100% precision** — and every messy-narration row was
already resolved. The remaining rows were credits that *should* stay unmatched,
and two whose identifying information does not exist in the text.

Adding a model there would have added false positives, not recall.

**Changed:** the model was repointed from matching to exception triage. Recorded
as `docs/decisions.md` D2.

## 6. An unlucky seed hid two whole classes

Six foreign credits drawn randomly from five narration families all landed on
`FOREIGN_VENDOR_CREDIT`, leaving `TAX_REFUND` and `CASH_OR_BRANCH_DEPOSIT`
unmeasurable. The evaluation would have reported a clean number while silently
testing two-thirds of the classes.

**Changed:** stratified sampling, two per class, documented as test-set design.

## 7. Reporting a calibrated threshold that was an artefact

Calibration returned `0.59` with zero errors — because the offline classifier
never errs on data whose narrations were written to match its own rules. The
cost curve had no false-accept term at all, so the optimum collapsed to "accept
everything" and the number was just below the lowest confidence in the sample.

Presenting that as a calibrated threshold would have been fabricating a metric.

**Changed:** `is_degenerate` checks the *inputs* for both error types. On a
flat curve the run refuses to calibrate, falls back to a stated default, and
prints why. The report now discloses the circularity on every offline run.

**First attempt at this fix was also wrong** — it tested the output curve rather
than the input proposals, so it never fired. Caught by running it.

## 8. A test that asserted the wrong thing

`test_hallucinated_high_confidence_is_still_gated` failed. The code was right:
that row is terminally rejected by the matcher on amount and never reaches the
gate at all.

**Changed:** the test now builds a row that genuinely reaches triage, and the
terminal path got a test of its own — it turned out to be a safety property
worth pinning.

## Failure modes handled in the running system

| Failure | Behaviour |
| --- | --- |
| No `ANTHROPIC_API_KEY` | Labelled offline classifier; run completes; report says so |
| API rate limited / 5xx / connection lost | `TriageFailure`, row → suspense, run continues |
| Model refuses | Same path |
| Unparseable response body | `LLM_MALFORMED`, row → suspense |
| Label outside the closed set | Rejected before the gate |
| Confidence missing / non-numeric / out of range | Rejected before the gate |
| Confident contradiction of the evidence | Gate rejects, → suspense |
| Duplicate claim on one settlement | `Index.claim` raises; second row → `ALREADY_CLAIMED` |
| Empty dataset | Runs, reports zero, does not crash |
