"""Throughput, measured where the measurement means something.

The demo batch reconciles in 1-2 ms, and timing that gives a number that swings
by more than 2x between consecutive runs -- so quoting any single figure from it
is cherry-picking, which is precisely what the brief warns against. This scales
the batch up until the timer has something to bite on and reports a **median**
over repeats, with the spread, so the number is honest about its own precision.
"""
from __future__ import annotations

import statistics
import sys
import time

from recon.generate import build
from recon.match import Index, match_batch


def run(n_settlements: int = 4000, repeats: int = 7) -> dict:
    settlements, bank, _, _, _ = build(n_settlements=n_settlements, seed=7)
    samples = []
    for _ in range(repeats):
        idx = Index(settlements)                   # fresh claims each repeat
        start = time.perf_counter()
        match_batch(bank, idx)
        samples.append(len(bank) / (time.perf_counter() - start))
    return {"rows": len(bank), "settlements": len(settlements), "repeats": repeats,
            "median": statistics.median(samples), "min": min(samples), "max": max(samples)}


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
    r = run(n_settlements=n)
    print(f"reconciled {r['rows']:,} bank rows against {r['settlements']:,} settlements, "
          f"{r['repeats']} repeats")
    print(f"  median  {r['median']:,.0f} rows/sec")
    print(f"  range   {r['min']:,.0f} - {r['max']:,.0f}")
