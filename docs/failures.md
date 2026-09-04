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
Small, but `ENGINEERING.md` says documentation must match implementation, and a
confidently wrong comment is worse than none.

**Changed:** docstring corrected to what the code does.

## 5. The model turned out to be the wrong tool (the big one)

The design had a model reading messy narrations. The rules-only baseline came
back at **100% precision with zero false positives** — and every
messy-narration row was already resolved. (The match rate at the time read
86.9% on the then-current 61-row dataset; on today's stratified split the same
matcher reads 81.5%. See incident 13 — the pairings are identical, the
denominator changed.) The remaining rows were credits that *should* stay unmatched,
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

## 9. Weak evidence could steal a settlement from strong evidence

Found by a systematic review, not by a test — nothing in the suite covered it.

The matcher ran one greedy pass in **statement order**. Claims are exclusive, so
a row with no reference at all, matched on amount and date alone, could claim a
settlement that a *later* row's labelled UTR proved was its own. The later row
then reported `ALREADY_CLAIMED`, and the earlier match was a false positive
caused by nothing but the order of lines in a file.

```
weak first     bank_A = T3_AMOUNT_DATE -> setl_TARGET   bank_B = ALREADY_CLAIMED
strong first   bank_A = UNMATCHED                        bank_B = T1_UTR_EXACT
```

A shuffle test over the real dataset found **zero** order-dependent pairings —
because this generator never puts a no-reference row and a labelled-UTR row on
the same settlement. The data did not exercise the bug. That is the "synthetic
data that flatters the system" trap, arrived at by accident rather than design.

**Changed:** `match_batch` now runs **evidence-strength passes** over the whole
batch — every labelled UTR is claimed before any unlabelled reference is
considered, and both before anything is matched on amount alone. The failure
mode is removed by construction, not by hoping files arrive well-ordered. Two
regression tests pin it, plus one asserting a flagged amount discrepancy is
never re-matched on weaker evidence.

## 10. The honesty disclosure hung on a class-name string

`mode` was derived with `type(triage).__name__ == "ModelTriage"`. That string
decides whether the report prints its **SIMULATED** warning — so a wrapper, a
subclass, or a rename would have made the report claim model results for a run
that used the stand-in. A truthfulness mechanism should not depend on a name.

**Changed:** each triage implementation declares `mode` explicitly, and an
unrecognised tier fails safe to `"offline"` rather than to `"model"`.

## 11. Throughput was quoted from a cherry-picked run

The README said `~73,000 rows/sec`. Three consecutive runs measured 32,589 /
29,272 / 47,934 — the demo batch reconciles in 1–2 ms, so the figure was timing
noise, and 73,000 was the best of them. The brief's own words: *"One
cherry-picked match proves nothing."*

**Changed:** `evaluation/bench.py` scales the batch until the timer means
something and reports a **median over repeats with its range**.

## 12. The matcher was quadratic

Measuring throughput properly exposed it: 72k -> 50k -> 33k -> 20k -> 9.4k
rows/sec as settlements doubled from 500 to 8,000. `candidates_by_amount`
scanned every settlement for every row. Invisible at 65 rows; ruinous at a real
month-end.

**Changed:** settlements are bucketed by expected credit in whole rupees. The
tolerance is ±100 paise, so candidates can only lie in a row's own bucket or the
two adjacent ones — three dict lookups instead of a full scan. Verified
identical to the exhaustive scan across 3,900 row/index pairs on 60 seeds, with
a test for the tolerance-boundary case. Scaling is now flat: **137,000 rows/sec
at 20,000 settlements**, against 9,439 at 8,000 before.

## 13. The baseline was measured on a dataset that no longer existed

`docs/evaluation.md` reported 86.9% as the rules-only baseline "on the held-out
split". It was from the pre-stratification 61-row set. On the actual held-out
split the same matcher gets 81.5%. Not invented, but mislabelled as to
provenance — and for a reviewer that costs the same trust.

**Changed:** `evaluation/baseline.py` regenerates it, so the documented number
is always the number the command prints.

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
