# Product specification

## Problem

A merchant on Razorpay has two records of the same money and no link between
them.

1. **The settlement report** — Razorpay's view. `setl_...` ids, alphanumeric
   UTRs, paise amounts.
2. **The bank statement** — the bank's view. NEFT/RTGS/IMPS credit lines whose
   narration is free text written by whichever bank sent it.

Nothing joins them automatically. Razorpay ships a settlement-recon report, but
it reconciles *payments to settlements* — its own two views. It never sees the
merchant's bank statement, so the last hop, the one that proves the money
actually arrived, is done by a person opening two files.

## Who has it

The finance analyst who does this every morning, and the controller who cannot
close the books until it is done. At any real transaction volume this is a daily
recurring cost that scales linearly with revenue.

## Why it matters financially

Unreconciled credits sit in suspense. Cash position is wrong until they clear,
month-end close slips, and a settlement that never arrived looks identical to
one nobody has matched yet — so genuine missing money hides inside routine
backlog.

## What the system does

Closes the loop over a batch: matches what it can deterministically, classifies
what it cannot, and hands back a ranked queue of the remainder with a reason
attached to every row.

## Where AI provides value — and where it does not

**Not in matching.** Measured: rules reach 81.5% at 100% precision, and the rows
they leave are ones no reader can resolve, because the identifying information
is absent from the text. A model there adds false positives. See
`docs/decisions.md` D2.

**In the exception queue.** Deciding that `RTGS CR/GST REFUND AY2026/...` is a
tax refund while `NEFT-ACME TRADING CO-...` is a vendor reversal is semantic
work. As rules it is a hardcoded counterparty list that is stale on day one.

## Measurable result

Match rate, precision, recall, false-match count, per-difficulty breakdown,
throughput, triage accuracy, and a value-ranked exception list — all scored
against a generated answer key on a held-out seed.

## Why this beats a rules engine or a chatbot

Against a **pure rules engine**: rules cannot triage the exception queue, which
is where the human time actually goes.

Against an **LLM wrapper**: it would match worse, and it would match *wrongly*
with confidence. We can show the measurement.

## Non-goals

Moving money. Posting to a real ledger. Live Razorpay calls. Forecasting.
Multi-currency. Each is a deliberate omission, not an unfinished feature.
