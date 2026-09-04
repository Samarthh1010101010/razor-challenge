# Decision log

## D1 — Track 04, multi-source reconciliation

Every clause of the Track 04 bar is a checkable deliverable, and the brief
itself asks for synthetic data, so the track is fully satisfiable with no live
Razorpay integration. That removes the biggest demo-day failure mode. Mapping
from each clause to where we satisfy it is in `research/buildathon.md`.

## D2 — The model does not do the matching. Measured, not assumed.

**This is the most important decision in the project, and it was forced by a
measurement that contradicted the original design.**

The plan was the obvious one: rules match what they can, an LLM reads the messy
bank narrations that regexes cannot parse. We built the deterministic tiers
first, to establish the baseline the model tier would have to beat.

The baseline, on 61 bank rows against 55 settlements:

```
matched = 53 / 61   match rate 86.9%   precision 100.0%   false positives 0
unresolved: 6 foreign_credit, 2 ambiguous_pair
```

The eight unresolved rows are not a gap the model can close:

- **6 foreign credits** (a vendor refund, a GST refund, a branch cash deposit)
  are not settlements at all. Leaving them unmatched is the *correct* answer.
  A model that matched them would be manufacturing false positives.
- **2 ambiguous rows** are two settlements with identical amounts on the same
  day, and a narration reading `...-SETTLEMENT BULK` with no reference. The
  information needed to separate them **does not exist in the text**. A model
  asked to choose would be guessing, and a confident guess on a financial
  record is the single most expensive error this system can make.

Every `messy_narration` row — the ones written specifically to defeat regexes —
was resolved deterministically, by extracting an unlabelled reference and
requiring the amount to corroborate it independently.

So the honest finding is: **reconciliation matching is a deterministic problem,
and an LLM makes it worse, not better.** `CLAUDE.md` says to ask "why can this
not be deterministic code?" and to use deterministic logic when the answer is
"it can." Shipping an LLM matcher here to look AI-native would be exactly the
forced usage the brief penalises.

### Where the model does earn its place

The brief frames Track 04 itself: *"verification capacity, not generation speed,
is the bottleneck."* The bottleneck is not the 86.9% the rules already close. It
is the **exception queue** — the rows a human must now open, understand, and
dispose of.

That work *is* semantic. Deciding that `RTGS CR/GST REFUND AY2026/...` is a tax
refund belonging to a different GL account, while `NEFT-ACME TRADING CO-...` is
a vendor payout reversal, requires reading text and knowing what the words mean.
Expressing it in rules means hardcoding a vendor list that is stale the day it
ships.

So the model's job is **exception triage, not matching**:

| Stage | Owner |
| --- | --- |
| Match bank credit to settlement | Deterministic rules. No model. |
| Classify an unresolved credit's disposition | Model proposes |
| Accept or reject that disposition | Deterministic policy gate |
| Post to a GL account | Deterministic allowlist |

The model never touches a reconciliation decision, never sees a matched row, and
cannot cause a false match. Its worst failure mode is a mis-routed exception
that a human was already going to review.

**This makes "AI Judgment" a strength rather than a risk**: we can show the
measurement that says rules beat the model at matching, and point at the place
we used it instead.

## D3 — Two-signal agreement before any automated acceptance

No single signal is ever sufficient. A reference extracted from narration text is
acted on only when the amount independently corroborates it. The same rule binds
the model tier: a proposed disposition is accepted only when the policy gate
agrees from features the model never saw. Structural, not prompt-enforced.

## D4 — Claim-once enforcement

A settlement is claimable exactly once, taken at the moment a match is accepted.
Two bank rows can never reconcile against the same payout. `Index.claim` raises
rather than silently overwriting.

## D5 — Calibration and reporting splits are different seeds

The acceptance threshold is tuned on `SEED_CALIBRATION` and every reported
number comes from `SEED_HELDOUT`. Tuning on the data you report on is leakage,
and it is the first thing a skeptical reviewer checks.
