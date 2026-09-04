"""Score a run against ground truth. Every number in the report comes from here.

Two things are measured separately because they are different jobs with
different failure costs:

- **Reconciliation** -- did we pair the right bank credit with the right
  settlement? A wrong pair corrupts the books, so precision is the headline and
  the false-positive count is reported even when it is zero.
- **Triage** -- did we label an unresolved credit correctly? A wrong label
  mis-routes work a human was going to do anyway, so it is scored, but it is
  not in the same risk class and the report does not blur them together.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from recon.models import Decision, RunStats


@dataclass
class Score:
    rows: int = 0
    matched: int = 0
    correct: int = 0
    false_positive: int = 0          # matched something, matched the WRONG thing
    missed: int = 0                  # a correct match existed, we did not find it
    correctly_unmatched: int = 0     # no correct match existed, we claimed none
    match_rate: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    by_style: dict[str, tuple[int, int]] = field(default_factory=dict)  # style -> (correct, total)

    triage_scored: int = 0
    triage_correct: int = 0
    triage_accuracy: float = 0.0
    triage_confusion: dict[str, Counter] = field(default_factory=dict)

    auto_posted: int = 0
    routed_to_human: int = 0
    rows_per_second: float = 0.0


def score(decisions: list[Decision], truth: dict[str, str], styles: dict[str, str],
          expected_disposition: dict[str, str], stats: RunStats) -> Score:
    s = Score(rows=len(decisions))
    per_style: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    confusion: dict[str, Counter] = defaultdict(Counter)

    for d in decisions:
        want = truth.get(d.txn_id)          # None == correctly has no match
        style = styles.get(d.txn_id, "unknown")
        per_style[style][1] += 1

        if d.settlement_id:
            s.matched += 1
            if d.settlement_id == want:
                s.correct += 1
                per_style[style][0] += 1
            else:
                s.false_positive += 1
        else:
            if want is None:
                s.correctly_unmatched += 1
                per_style[style][0] += 1
            else:
                s.missed += 1

        if d.auto_posted:
            s.auto_posted += 1
        elif not d.settlement_id:
            s.routed_to_human += 1

        # Triage is scored only where a correct label is known.
        wanted_label = expected_disposition.get(d.txn_id)
        if wanted_label and d.disposition:
            s.triage_scored += 1
            confusion[wanted_label][d.disposition] += 1
            if d.disposition == wanted_label:
                s.triage_correct += 1

    s.match_rate = s.matched / s.rows if s.rows else 0.0
    s.precision = s.correct / s.matched if s.matched else 0.0
    recoverable = sum(1 for t in decisions if truth.get(t.txn_id))
    s.recall = s.correct / recoverable if recoverable else 0.0
    s.triage_accuracy = s.triage_correct / s.triage_scored if s.triage_scored else 0.0
    s.by_style = {k: (v[0], v[1]) for k, v in sorted(per_style.items())}
    s.triage_confusion = dict(confusion)
    s.rows_per_second = s.rows / stats.seconds if stats.seconds else 0.0
    return s


def exceptions_by_value(decisions: list[Decision], bank_by_id: dict) -> list[tuple]:
    """Unresolved rows, largest rupee value first.

    An analyst working a queue top-down should meet the expensive uncertainty
    before the trivial one. Ordering the queue is free and it is the difference
    between a list and a work plan.
    """
    rows = [(bank_by_id[d.txn_id].credit_amount, d) for d in decisions if not d.settlement_id]
    return sorted(rows, key=lambda r: -r[0])
