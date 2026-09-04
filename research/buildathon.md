# Razorpay AI Buildathon — official brief (captured verbatim)

`VERIFIED` — fetched from <https://razorpay.com/buildathon/> on 2026-09-04.
Text below is quoted from the page. Anything outside a quote is our commentary.

## Programme

> Students only. 6 or 12 month AI Builder Internship. In-person, Bangalore, from September.

> No resume screening. No long application. Four steps: pick a track, build
> something real, show your work (a public repo, a 5 minute pitch video, the
> architecture), and if it has signal we call you in.

> ₹75,000 (monthly stipend) · 6 or 12 (months, your choice) · In-person
> (Bangalore, from September). Shortlisted builders go straight to a panel.
> No aptitude test. No group discussion.

## Submission

Application form: <https://forms.gle/d9r2gvxp8cmoZhon9> — `VERIFIED` open as of
2026-09-04 22:38 IST. Fields, read from the form payload:

1. Track Selection (Track 1–4, or Open Track)
2. GitHub Repository URL
3. 5-min Pitch Video Link
4. A confirmation checkbox:

> I confirm that this is my official final project submission. I understand that
> no further changes or edits can be made after submitting.

**Submission is final.** Do not submit until the repo and video are done.

**Deadline:** `INFERRED` — 5 September 2026. The official page states no date;
this comes from secondary coverage and a widely shared announcement post. Treat
end of 5 Sept IST as the working deadline and verify on the form before relying
on it.

## Tracks, with each track's stated bar

### 01 — AI Growth & Agentic Commerce

> Grow the merchant's revenue, and make them sellable to AI buyers.
> Build an agent that grows revenue for a merchant on Razorpay test-mode APIs,
> or that makes a merchant transactable by an AI buyer end to end.
> Example directions: Conversational in-app checkout, Agent-readable catalog,
> Upsell & cross-sell agent, Campaign orchestrator.

> **The bar:** Every money action explainable, bounded and gated. Show the audit
> trail and one failure handled gracefully.

### 02 — AI Risk Manager

> Stop the merchant losing money to fraud, returns and chargebacks.
> Build a working detector, verifier or auto-responder for one class of loss,
> with measured precision and recall on a held-out test set.
> Example directions: Chargeback evidence responder, Return-risk scorer,
> Fraud-spike detector, Abuse-ring sentinel.

> **The bar:** Honest metrics including false-positive cost. Strictly
> defense-only: anything offense-capable is disqualified.

### 03 — AI Revenue Recovery

> Find revenue that's slipping away and win it back.
> Build an agent that detects revenue at risk, determines the right
> intervention, and executes a bounded recovery workflow: from payment failures
> and checkout abandonment to overdue receivables.
> Example directions: Payment degradation → root cause → recovery action,
> Checkout drop-off recovery, Failed-subscription recovery, B2B receivables
> chaser, Mandate retry sequencer, Hinglish voice recovery, Promise-to-pay tracker.

> **The bar:** Don't just identify the problem. Show measured money recovered
> across a batch, with compliant escalation, stopping rules, and an audit trail.

### 04 — AI Finance Controller  ← **our track**

> Run the books and the cash position.
> Build an agent that closes one finance-ops loop across a 50+ record batch of
> synthetic data, reporting its match rate and the exceptions it could not resolve.
> Why now: The 2026 builder consensus: verification capacity, not generation
> speed, is the bottleneck. Reconciliation, settlement and forecasting are still
> done by hand.
> Example directions: Multi-source reconciliation, Settlement Q&A agent,
> Forward cash forecaster, Tax-line matcher.

> **The bar:** Throughput plus measured accuracy plus an honest exception list.
> One cherry-picked match proves nothing.

### 05 — Open Track

> Build what you believe should exist. Pick a real problem, use AI meaningfully,
> and show us something that works. Any domain, workflow, or user is fair game.

> **The bar:** Open doesn't mean easier. Show a real problem, a working product,
> meaningful use of AI, and evidence that it creates value. The same bar for
> execution, reliability, and depth applies here.

## Track choice: 04, Multi-source reconciliation

Every clause of the Track 04 bar is a concrete, checkable deliverable, and each
maps to something we build:

| Brief requires | Where we satisfy it |
| --- | --- |
| "one finance-ops loop" | bank credit ↔ gateway settlement reconciliation, closed end to end |
| "50+ record batch" | 61 bank rows against 55 settlements, seeded and reproducible |
| "synthetic data" | `recon/generate.py`, fixed seed, ground truth emitted alongside |
| "reporting its match rate" | `evaluation/` — match rate by tier |
| "the exceptions it could not resolve" | typed reason codes per unresolved row, ranked by value |
| "throughput" | rows/sec measured on the real run |
| "measured accuracy" | scored against ground truth, not self-reported confidence |
| "One cherry-picked match proves nothing" | full batch reported; false positives disclosed |

It is also the only track whose bar is fully satisfiable without a live Razorpay
integration — the brief itself asks for synthetic data. That removes the largest
demo-day failure mode.

## Criteria attributed to Razorpay but NOT on the official page

Secondary coverage widely reports four criteria — Problem Taste, Build Quality,
AI Judgment, Failure Recovery. `INFERRED`, and not corroborated by the official
page. They are consistent with the per-track bars, so we build to satisfy both,
but no claim in this repo should cite them as official.
