"""Choose the acceptance threshold from measured cost, not from a round number.

Everyone writes `if confidence > 0.8`. That number is a guess, and it is the
wrong shape of guess: the two errors it trades between do not cost the same.

- **False accept** -- an exception auto-posted to the wrong GL account. Nobody
  looks again until month-end review, and unwinding it costs an analyst about an
  hour once you include finding it.
- **False reject** -- an exception routed to a human that the classifier had
  right. Costs the three minutes it takes to read and confirm.

So the optimum is the threshold minimising expected cost over a batch, and it is
derivable from data instead of guessed. We sweep it on the **calibration** seed
and report on the **held-out** seed, because tuning on the data you report is
leakage and it is the first thing a reviewer checks.

The two cost constants below are the model's only assumptions. They are stated
here, in rupees, so a finance team can substitute their own and re-run.
"""
from __future__ import annotations

from dataclasses import dataclass

COST_FALSE_ACCEPT = 1200.0   # INR: investigate + correct a wrongly posted entry
COST_FALSE_REJECT = 30.0     # INR: 3 analyst-minutes at a loaded rate


@dataclass
class Point:
    threshold: float
    false_accepts: int
    false_rejects: int
    expected_cost: float


def sweep(proposals: list[tuple[float, bool]], lo: float = 0.30, hi: float = 0.99,
          step: float = 0.01) -> list[Point]:
    """`proposals` is (confidence, is_correct) for every classified row.

    At a given threshold: a correct proposal below it is a false reject; an
    incorrect proposal at or above it is a false accept.
    """
    out: list[Point] = []
    t = lo
    while t <= hi + 1e-9:
        fa = sum(1 for c, ok in proposals if c >= t and not ok)
        fr = sum(1 for c, ok in proposals if c < t and ok)
        out.append(Point(round(t, 2), fa, fr,
                         fa * COST_FALSE_ACCEPT + fr * COST_FALSE_REJECT))
        t += step
    return out


def is_degenerate(proposals: list[tuple[float, bool]]) -> bool:
    """True when the calibration inputs carry no signal about where to cut.

    The sweep only locates a boundary if both error types are represented. With
    no incorrect proposals there is no false-accept term anywhere in the curve,
    so expected cost is minimised by accepting everything and the "chosen"
    threshold is just below the lowest confidence observed -- an artefact of the
    sample, not a calibrated value. The mirror case (nothing correct) is equally
    uninformative.

    This is the expected state in offline mode: the SIMULATED classifier's rules
    were written against this generator's narrations, so it does not err on them.
    Reporting a threshold from that would be presenting an artefact as a result.
    """
    correct = sum(1 for _, ok in proposals if ok)
    return correct == 0 or correct == len(proposals)


def best(points: list[Point]) -> Point:
    """Lowest expected cost; ties broken toward the stricter threshold.

    Preferring the higher threshold on a tie is deliberate -- when two settings
    cost the same in expectation, the one that hands more work to a human is the
    safer place to be wrong.
    """
    return min(points, key=lambda p: (p.expected_cost, -p.threshold))
