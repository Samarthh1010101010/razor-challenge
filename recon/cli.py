"""Command line entry point. `python -m recon.cli demo` runs the whole thing."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from evaluation.calibrate import (COST_FALSE_ACCEPT, COST_FALSE_REJECT, best,
                                  is_degenerate, sweep)
from evaluation.score import Score, exceptions_by_value, score
from recon import config, dashboard, generate
from recon.gemini_triage import GeminiTriage
from recon.offline_triage import OfflineTriage
from recon.pipeline import run
from recon.sources import load_bank, load_settlements, load_truth
from recon.triage import ModelTriage
from recon.triage_cache import CachedTriage

DATA = Path("data")
CALIB = DATA / "calibration"
OUT = Path("out")


def _triage(force_offline: bool):
    """First provider with a credential wins; otherwise the labelled stand-in.

    Order is arbitrary but fixed, so a machine with both keys set always picks
    the same one and runs stay reproducible.
    """
    if not force_offline:
        for tier in (GeminiTriage(), ModelTriage()):
            if tier.available:
                # Cache in front: a re-run must not re-buy answers it already has.
                cached = CachedTriage(tier)
                return cached, cached.model_id
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
            print(f"  curve is FLAT over {len(pairs)} classified row(s) -- no threshold")
            print("  in range produces any error, so it carries no signal and nothing")
            print("  was calibrated. Falling back to a stated default of 0.70.")
            if mode == "offline":
                print("  Expected offline: the SIMULATED classifier's rules were written")
                print("  against this generator's narrations, so it never errs on them.")
            else:
                print("  Too few rows reached a classification to locate a boundary.")
                print("  Re-run to let the cache accumulate more answers.")
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


def _report(sc: Score, res, decisions, bank_by_id, mode, label=""):
    print()
    print("=" * 68)
    print(f"  RECONCILIATION REPORT   run {res.run_id}   triage: {label or mode}")
    if mode == "offline":
        print("  NOTE: SIMULATED offline classifier (no GEMINI_API_KEY or")
        print("        ANTHROPIC_API_KEY set).")
        print("        Triage figures below are NOT model results, and they are")
        print("        CIRCULAR: the offline keyword rules were written against")
        print("        these same narrations, so its accuracy is not evidence of")
        print("        anything. Reconciliation figures above are unaffected --")
        print("        the matcher never consults the classifier.")
    print("=" * 68)
    print(f"  batch           {sc.rows} bank credits vs {res.stats.settlements} settlements")
    # Two different numbers. Blending them let a paced API turn a matcher that
    # does ~80k rows/sec into a reported "3 rows/sec".
    match_rate_s = (sc.rows / res.stats.match_seconds) if res.stats.match_seconds else 0.0
    print(f"  matching        {match_rate_s:,.0f} rows/sec "
          f"({res.stats.match_seconds * 1000:.0f} ms)")
    if res.stats.llm_calls:
        print(f"  triage          {res.stats.llm_calls} calls in "
              f"{res.stats.seconds - res.stats.match_seconds:.0f}s "
              f"(network-bound, paced to the provider's rate limit)")
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
    if sc.triage_classified:
        print(f"    accuracy      {sc.triage_accuracy:.1%}  "
              f"({sc.triage_correct}/{sc.triage_classified} answered)")
    else:
        print("    accuracy      n/a (no row reached a classification)")
    if sc.triage_unanswered:
        print(f"    unanswered    {sc.triage_unanswered}  "
              f"<- API failures, not wrong answers")
    print(f"    auto-posted   {sc.auto_posted}")
    print(f"    to a human    {sc.routed_to_human}")
    print(f"    gate rejected {res.stats.llm_rejected} of {res.stats.llm_calls} proposals")
    print(f"    triage failures {res.stats.llm_failures}")
    for why, n in sorted(res.stats.llm_failure_detail.items(), key=lambda x: -x[1]):
        print(f"      {n:>3}x  {why}")
    if res.stats.cache_hits:
        print(f"    cache hits    {res.stats.cache_hits} "
              f"(answers reused, no call made)")
    print()
    print("  EXCEPTION QUEUE (largest value first)")
    for amount, d in exceptions_by_value(decisions, bank_by_id)[:8]:
        reason = d.gate_rejected_because or (d.reason.value if d.reason else "")
        print(f"    INR {amount/100:>12,.2f}  {d.disposition or '-':<26} {reason}")
    print("=" * 68)


def do_run(args):
    s, b, truth, styles, want = _load(DATA)
    triage, label = _triage(args.offline)
    res = run(s, b, triage, _threshold(), OUT / "audit.jsonl")
    sc = score(res.decisions, truth, styles, want, res.stats)
    bank_by_id = {t.txn_id: t for t in b}
    mode = res.mode
    _report(sc, res, res.decisions, bank_by_id, mode, label)
    OUT.mkdir(parents=True, exist_ok=True)
    queue = [{"txn_id": d.txn_id, "amount": amt,
              "disposition": d.disposition or "",
              "reason": d.gate_rejected_because or (d.reason.value if d.reason else ""),
              "auto_posted": d.auto_posted,
              "gl_account": d.gl_account or "",
              "narration": bank_by_id[d.txn_id].narration}
             for amt, d in exceptions_by_value(res.decisions, bank_by_id)]
    (OUT / "report.json").write_text(json.dumps(
        {"run_id": res.run_id, "mode": mode, "triage_label": label,
         "threshold": res.threshold,
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
    # Before anything reads a credential.
    loaded = config.load()
    if loaded:
        print(f"loaded from .env: {', '.join(loaded)}")

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
