"""Rules-only baseline, measured on whichever split you point it at.

This exists because the baseline number in the docs was previously copied from a
run against a dataset that no longer exists (the pre-stratification 61-row set),
while being described as measured on the held-out split. That is a mislabelled
metric, and mislabelled is as damaging to trust as invented. Regenerating it
here means the number in the docs is always the number this command prints.
"""
from __future__ import annotations

import sys
from pathlib import Path

from recon.match import Index, match_batch
from recon.sources import load_bank, load_settlements, load_truth


def measure(data_dir: Path) -> dict:
    settlements = load_settlements(data_dir / "settlements.csv")
    bank = load_bank(data_dir / "bank_statement.csv")
    truth, _, _ = load_truth(data_dir / "ground_truth.csv")

    decisions = match_batch(bank, Index(settlements))
    matched = [d for d in decisions if d.settlement_id]
    correct = [d for d in matched if truth.get(d.txn_id) == d.settlement_id]
    recoverable = sum(1 for d in decisions if truth.get(d.txn_id))

    return {
        "rows": len(bank),
        "settlements": len(settlements),
        "matched": len(matched),
        "correct": len(correct),
        "false_positives": len(matched) - len(correct),
        "match_rate": len(matched) / len(bank) if bank else 0.0,
        "precision": len(correct) / len(matched) if matched else 0.0,
        "recall": len(correct) / recoverable if recoverable else 0.0,
    }


if __name__ == "__main__":
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data")
    r = measure(d)
    print(f"rules-only baseline on {d}  ({r['rows']} rows / {r['settlements']} settlements)")
    print(f"  match rate       {r['match_rate']:.1%}  ({r['matched']}/{r['rows']})")
    print(f"  precision        {r['precision']:.1%}")
    print(f"  recall           {r['recall']:.1%}")
    print(f"  false positives  {r['false_positives']}")
