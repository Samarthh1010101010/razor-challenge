# Reconciliation Exception Resolver

**Razorpay AI Buildathon — Track 04, AI Finance Controller**

A merchant's bank statement and their Razorpay settlement report never line up
by themselves. Somebody in finance opens both, and matches them by hand.

This closes that loop across a batch, reports its match rate and precision
against a known answer key, and hands a human a ranked queue of only what it
could not resolve.

```bash
make demo          # Python 3.10+, no dependencies, no network
```

The core pipeline is **standard library only**. `make setup` installs two
optional extras: `anthropic` for the live triage tier, `pytest` for the suite.

## The result

Held-out batch of 65 bank credits against 55 settlements, seed `20260905`:

```
  match rate    81.5%  (53/65)
  precision     100.0%  (53 correct of 53 claimed)
  recall        96.4%
  FALSE MATCHES 0

  by difficulty class:
    clean_utr         30/30        messy_narration   8/8
    fee_variance       8/8         no_utr_unique     7/7
    foreign_credit    10/10        ambiguous_pair    0/2
```

Precision holds at **100% with zero false positives across 100 unseen seeds**,
not just this one. Throughput is reported separately by `make bench`
(~137,000 rows/sec at 20,000 settlements, median over repeats) rather than from
this batch — 65 rows reconcile in about a millisecond, and any figure taken from
timing that is noise.

`ambiguous_pair` is 0/2 on purpose, and it is the most important row in the
table. Those two credits are identical amounts settled on the same day with no
reference in the narration. **The information needed to separate them does not
exist.** Guessing would have scored 50% and put a wrong number in the ledger;
they are escalated instead. A reconciler that never says "I don't know" is not
trustworthy, it is just unmeasured.

The other 10 unresolved rows are credits that are not settlements at all —
a GST refund, bank interest, a branch deposit, vendor reversals. Leaving them
unmatched is the correct answer, not a miss.

## Why this is not an LLM wrapper

The original design had a model read the messy bank narrations that regexes
cannot parse. We built the deterministic tiers first to establish the baseline
it would have to beat, and **the baseline made the model redundant**: every
narration written to defeat a regex was resolved by extracting an unlabelled
reference and requiring the amount to independently corroborate it.

So the model does not do the matching. Putting one there would have added false
positives, not recall. That measurement is in
[`docs/decisions.md`](docs/decisions.md) D2, and it is the central engineering
decision in the project.

The model works the **exception queue** instead — which is the bottleneck the
brief itself names ("verification capacity, not generation speed"). Deciding
that `RTGS CR/GST REFUND AY2026/...` is a tax refund and
`NEFT-ACME TRADING CO-...` is a vendor reversal is genuinely semantic. Writing
it as rules means hardcoding a vendor list that is stale on day one.

| Stage | Owner |
| --- | --- |
| Match bank credit → settlement | Deterministic. No model. |
| Classify an unresolved credit | Model proposes |
| Accept or reject that proposal | Deterministic policy gate |
| Post to a GL account | Deterministic allowlist |

## Three things that are not the obvious build

**1. The threshold is calibrated from cost, not guessed.** Everyone writes
`if confidence > 0.8`. The two errors do not cost the same — a wrongly posted
entry costs about an hour to find and unwind (₹1,200); an exception handed to a
human that the classifier had right costs three minutes (₹30). That is 40:1, so
the optimum is derivable. We sweep it on a **calibration seed** and report on a
**held-out seed**, because tuning on the data you report is leakage.

**2. Two independent signals must agree before anything is accepted.** The model
proposes a label from narration text; the gate re-derives evidence the model
never saw and refuses on contradiction. If the model calls a credit third-party
but its narration carries a reference resolving to a settlement on file, the
proposal is rejected — structurally, not by asking the prompt nicely. A
confident hallucination fails the same way an unconfident one does.

**3. The exception queue is ranked by rupee value.** An analyst working
top-down meets the ₹7.3L uncertainty before the ₹1.1L one.

## Safety

- The model chooses from a **closed 6-value enum**, enforced by the API's schema
  and re-validated locally. It never names a GL account, an amount, or a
  settlement id.
- A settlement is **claimable exactly once**. Two bank rows can never reconcile
  against the same payout; `Index.claim` raises rather than overwriting.
- Every rejection **routes to suspense for a human**. Nothing is dropped.
- Every row writes one **append-only audit record** (`out/audit.jsonl`). If a
  number in the report cannot be traced to a line there, the number is wrong.
- Every model failure path — no key, rate limit, API error, connection loss,
  refusal, unparseable body, out-of-set label, bad confidence — returns a
  routable outcome. **A dead API degrades this to rules-only, it does not stop
  the run.**

## Honest limitations

Read these before believing any number above.

- **No live Razorpay integration.** We hold no API key and make no calls. The
  settlement side is generated to the *verified* settlements entity contract
  (`research/razorpay.md`) so a real `GET /v1/settlements` substitutes at one
  seam, but nothing here is a working integration and this repo does not claim
  one.
- **Bank statements are `SIMULATED`.** No Razorpay API exposes one — it comes
  from the merchant's bank. Ours are modelled on Indian NEFT/RTGS/IMPS formats.
- **Without `ANTHROPIC_API_KEY`, triage runs a labelled offline keyword
  classifier**, and its accuracy is **circular** — those rules were written
  against these same narrations. The report says so on every offline run, and
  the calibration step refuses to report a threshold from the resulting flat
  curve. Reconciliation figures are unaffected: the matcher never consults the
  classifier.
- **The two cost constants are assumptions**, stated in rupees in
  `evaluation/calibrate.py` for a finance team to replace.

## Dashboard

`make demo` also writes `out/dashboard.html` — a self-contained page with the
match rate, the per-difficulty breakdown, the calibration curve, and the
value-ranked exception queue showing which GL account each row posted to.

Every figure on it is read from `out/report.json` and `out/threshold.json`,
written by the run itself. **Delete those files and the page does not render**
— that is deliberate. A dashboard that can draw itself without a run is a
dashboard that can lie about one.

## Running the live triage tier

The reconciler needs no credential. The **exception-triage** tier does. Copy
`.env.example` to `.env` (git-ignored) and set one key:

```bash
cp .env.example .env      # then put your key in it
make demo
```

`GEMINI_API_KEY` — free tier, no credit card, from
[aistudio.google.com](https://aistudio.google.com). Or `ANTHROPIC_API_KEY` for
the Anthropic tier. Whichever is present is used; with neither, the run
completes and reports that triage was `SIMULATED`.

The batch needs about 24 classifications, comfortably inside Gemini's free tier.

**Swapping providers touches one file.** `recon/gemini_triage.py` and
`recon/triage.py` are interchangeable implementations of one method. The policy
gate, the closed disposition set, the GL account table and the audit trail are
provider-independent — the model only ever proposes a label, so replacing it
cannot widen what it is allowed to do. That the swap is this small is a property
of the design, not a coincidence.

## Evidence in this repo

You do not have to run it to see the result. `docs/sample-run.txt` is the
verbatim terminal output of `make demo`, and `docs/dashboard-light.png` /
`docs/dashboard-dark.png` are the generated dashboard. All three are
regenerated, never edited by hand.

## Where to look

If you have five minutes, read these three, in this order:

| | |
| --- | --- |
| [`docs/decisions.md`](docs/decisions.md) | **D2 is the project.** Why the LLM matcher was built, measured, and deleted. |
| [`docs/failures.md`](docs/failures.md) | Sixteen recorded incidents. Several are fixes to earlier fixes. |
| [`docs/evaluation.md`](docs/evaluation.md) | How every number here is produced, and what the held-out split does *not* prove. |

Everything else:

```
recon/       matcher, extractor, triage, policy gate, audit, pipeline, CLI
evaluation/  scoring against ground truth, cost-calibrated threshold, baseline, bench
tests/       64 adversarial tests
research/    the official brief captured verbatim; verified Razorpay API contract
spec/        product and technical specifications
docs/        architecture, decisions, evaluation, failures, sample run, dashboard
ENGINEERING.md   the standing rules this was built under
```

Committed evidence you can read without running anything:
[`docs/sample-run.txt`](docs/sample-run.txt) (a live Gemini run),
[`docs/dashboard-light.png`](docs/dashboard-light.png) /
[`docs/dashboard-dark.png`](docs/dashboard-dark.png).

`make test` · `make demo` · `make baseline` · `make bench` · `make dashboard`
