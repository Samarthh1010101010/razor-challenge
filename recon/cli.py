"""Command line entry point. `python -m recon.cli demo` runs the whole thing."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from evaluation.calibrate import (COST_FALSE_ACCEPT, COST_FALSE_REJECT, best,
                                  is_degenerate, sweep)
from evaluation.score import Score, exceptions_by_value, score
from recon import dashboard, generate
from recon.offline_triage import OfflineTriage
from recon.pipeline import run
from recon.sources import load_bank, load_settlements, load_truth
from recon.triage import ModelTriage

DATA = Path("data")
CALIB = DATA / "calibration"
OUT = Path("out")


def _triage(force_offline: bool):
    """Live model when a credential exists, else the labelled offline stand-in."""
    if not force_offline:
        m = ModelTriage()
        if m.available:
            return m, "model"
    return OfflineTriage(), "offline"


def _load(d: Path):
    return (load_settlements(d / "settlements.csv"), load_bank(d / "bank_statement.csv"),
            *load_truth(d / "ground_truth.csv"))


def do_generate(_):
    generate.write(DATA, generate.SEED_HELDOUT)
    generate.write(CALIB, generate.SEED_CALIBRATION)
    print(f"held-out  -> {DATA}  (seed {generate.SEED_HELDOUT})")
    print(f"calibration -> {CALIB} (seed {generate.SEED_CALIBRATION})")


def do_calibrate(args):
    """Sweep the threshold on the calibration split only. Never on held-out."""
    s, b, truth, styles, want = _load(CALIB)
    triage, mode = _triage(args.offline)
    # Sweep needs proposals, so run at threshold 0 to accept everything, then
    # score each proposal's correctness against the answer key.
    res = run(s, b, triage, 0.0, OUT / "calibration_audit.jsonl")
    pairs = [(d.confidence, d.disposition == want.get(d.txn_id))
             for d in res.decisions
             if d.confidence is not None and want.get(d.txn_id)]
    if not pairs:
        print("no classified rows on the calibration split; keeping default 0.70")
        chosen = 0.70
        points = []
    else:
        points = sweep(pairs)
        if is_degenerate(pairs):
            chosen = 0.70
            print(f"calibration split: {len(pairs)} classified rows, mode={mode}")
            print("  curve is FLAT -- no threshold in range produces any error, so")
            print("  it carries no signal and nothing was calibrated. Falling back")
            print("  to a stated default of 0.70. This is expected in offline mode:")
            print("  the SIMULATED classifier's rules were written against this")
            print("  generator's narrations. Run with ANTHROPIC_API_KEY for a real curve.")
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / "threshold.json").write_text(json.dumps(
                {"threshold": chosen, "mode": mode, "calibrated": False,
                 "reason": "degenerate curve", "curve": [vars(p) for p in points]}, indent=2))
            return chosen
        p = best(points)
        chosen = p.threshold
        print(f"calibration split: {len(pairs)} classified rows, mode={mode}")
        print(f"cost model: false accept INR {COST_FALSE_ACCEPT:,.0f} / "
              f"false reject INR {COST_FALSE_REJECT:,.0f}")
        print(f"chosen threshold {chosen:.2f}  "
              f"(expected cost INR {p.expected_cost:,.0f}, "
              f"{p.false_accepts} false accepts, {p.false_rejects} false rejects)")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "threshold.json").write_text(json.dumps(
        {"threshold": chosen, "mode": mode,
         "cost_false_accept": COST_FALSE_ACCEPT, "cost_false_reject": COST_FALSE_REJECT,
         "curve": [vars(p) for p in points]}, indent=2))
    return chosen


def _threshold() -> float:
    f = OUT / "threshold.json"
    if f.exists():
        return json.loads(f.read_text())["threshold"]
    return 0.70


def _report(sc: Score, res, decisions, bank_by_id, mode):
    print()
    print("=" * 68)
    print(f"  RECONCILIATION REPORT   run {res.run_id}   triage mode: {mode}")
    if mode == "offline":
        print("  NOTE: SIMULATED offline classifier (no ANTHROPIC_API_KEY set).")
        print("        Triage figures below are NOT model results, and they are")
        print("        CIRCULAR: the offline keyword rules were written against")
        print("        these same narrations, so its accuracy is not evidence of")
        print("        anything. Reconciliation figures above are unaffected --")
        print("        the matcher never consults the classifier.")
    print("=" * 68)
    print(f"  batch           {sc.rows} bank credits vs {res.stats.settlements} settlements")
    print(f"  throughput      {sc.rows_per_second:,.0f} rows/sec "
          f"({res.stats.seconds * 1000:.0f} ms total)")
    print()
    print("  RECONCILIATION")
    print(f"    match rate    {sc.match_rate:.1%}  ({sc.matched}/{sc.rows})")
    print(f"    precision     {sc.precision:.1%}  ({sc.correct} correct of {sc.matched} claimed)")
    print(f"    recall        {sc.recall:.1%}")
    print(f"    FALSE MATCHES {sc.false_positive}      <- the number that matters")
    print(f"    missed        {sc.missed}")
    print(f"    correctly left unmatched  {sc.correctly_unmatched}")
    print()
    print("    by difficulty class:")
    for style, (ok, total) in sc.by_style.items():
        print(f"      {style:<20} {ok}/{total}")
    print()
    label = "TRIAGE" if mode == "model" else "TRIAGE  [SIMULATED - see note above]"
    print(f"  {label} (threshold {res.threshold:.2f}, calibrated on a separate seed)")
    if sc.triage_scored:
        print(f"    accuracy      {sc.triage_accuracy:.1%}  "
              f"({sc.triage_correct}/{sc.triage_scored})")
    else:
        print("    accuracy      n/a (no labelled rows reached triage)")
    print(f"    auto-posted   {sc.auto_posted}")
    print(f"    to a human    {sc.routed_to_human}")
    print(f"    gate rejected {res.stats.llm_rejected} of {res.stats.llm_calls} proposals")
    print(f"    triage failures {res.stats.llm_failures}")
    print()
    print("  EXCEPTION QUEUE (largest value first)")
    for amount, d in exceptions_by_value(decisions, bank_by_id)[:8]:
        reason = d.gate_rejected_because or (d.reason.value if d.reason else "")
        print(f"    INR {amount/100:>12,.2f}  {d.disposition or '-':<26} {reason}")
    print("=" * 68)


def do_run(args):
    s, b, truth, styles, want = _load(DATA)
    triage, mode = _triage(args.offline)
    res = run(s, b, triage, _threshold(), OUT / "audit.jsonl")
    sc = score(res.decisions, truth, styles, want, res.stats)
    bank_by_id = {t.txn_id: t for t in b}
    _report(sc, res, res.decisions, bank_by_id, mode)
    OUT.mkdir(parents=True, exist_ok=True)
    queue = [{"txn_id": d.txn_id, "amount": amt,
              "disposition": d.disposition or "",
              "reason": d.gate_rejected_because or (d.reason.value if d.reason else ""),
              "auto_posted": d.auto_posted,
              "gl_account": d.gl_account or "",
              "narration": bank_by_id[d.txn_id].narration}
             for amt, d in exceptions_by_value(res.decisions, bank_by_id)]
    (OUT / "report.json").write_text(json.dumps(
        {"run_id": res.run_id, "mode": mode, "threshold": res.threshold,
         "settlements": res.stats.settlements,
         "score": {k: v for k, v in vars(sc).items() if k != "triage_confusion"},
         "triage_confusion": {k: dict(v) for k, v in sc.triage_confusion.items()},
         "exception_queue": queue},
        indent=2, default=str))
    dashboard.write(OUT)
    print(f"dashboard:   {OUT/'dashboard.html'}")
    print(f"\naudit trail: {OUT/'audit.jsonl'}   machine-readable: {OUT/'report.json'}")
    return sc


def do_demo(args):
    do_generate(args)
    print()
    do_calibrate(args)
    do_run(args)


def main():
    ap = argparse.ArgumentParser(prog="recon")
    ap.add_argument("--offline", action="store_true",
                    help="force the SIMULATED classifier even if a key is set")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("generate", do_generate), ("calibrate", do_calibrate),
                     ("run", do_run), ("demo", do_demo)):
        sub.add_parser(name).set_defaults(fn=fn)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
