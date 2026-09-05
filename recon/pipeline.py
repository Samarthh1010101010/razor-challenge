"""End-to-end run: match, then triage what did not match, then gate, then audit.

Order matters and is deliberate. Deterministic matching runs to exhaustion
first, so the model only ever sees rows the rules have already given up on. It
cannot pre-empt a rule, and it cannot see a row that matched.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from recon import policy
from recon.audit import AuditLog
from recon.match import Index, match_batch
from recon.models import BankTxn, Decision, Reason, RunStats, Settlement, Tier
from recon.triage import Proposal, TriageFailure

# Reasons that mean "rules gave up, but the row may still be classifiable".
_TRIAGEABLE = {Reason.NO_CANDIDATE, Reason.AMBIGUOUS}


@dataclass
class RunResult:
    decisions: list[Decision]
    stats: RunStats
    mode: str
    run_id: str
    threshold: float


def run(settlements: list[Settlement], bank: list[BankTxn], triage,
        threshold: float, audit_path: Path | None = None) -> RunResult:
    """Reconcile a batch. `triage` is any object with `.classify(txn, bool)`."""
    # Each triage implementation declares its own mode. Deriving it from the
    # class name was fragile, and this string drives the report's honesty
    # disclosure -- if it is ever wrong, the report claims model results for a
    # run that used the stand-in.
    mode = getattr(triage, "mode", "offline") if getattr(triage, "available", False) \
        else "offline"
    run_id = uuid.uuid4().hex[:12]
    log = AuditLog(audit_path or Path("out/audit.jsonl"), run_id, mode)

    idx = Index(settlements)
    stats = RunStats(bank_rows=len(bank), settlements=len(settlements))
    decisions: list[Decision] = []
    started = time.perf_counter()

    # Match the whole statement first, in evidence-strength order. Triage only
    # ever sees rows the matcher has finished with.
    match_started = time.perf_counter()
    matched = match_batch(bank, idx)
    stats.match_seconds = time.perf_counter() - match_started

    # Pair positionally, for the same reason match_batch does: a repeated
    # txn_id must not make two credits share one decision.
    for txn, d in zip(bank, matched):

        if d.settlement_id is None and d.reason in _TRIAGEABLE:
            # `candidates_by_amount` is evaluated before the gate so the model
            # and the gate see the same world.
            has_candidate = bool(idx.candidates_by_amount(txn))
            stats.llm_calls += 1
            outcome = triage.classify(txn, has_candidate)

            if isinstance(outcome, TriageFailure):
                stats.llm_failures += 1
                # Keep the whole message, trimmed. Splitting on ":" cut a 404
                # off right before the list of models the key can actually use.
                summary = (outcome.detail or outcome.reason)[:160]
                stats.llm_failure_detail[summary] = \
                    stats.llm_failure_detail.get(summary, 0) + 1
                d.reason = Reason[outcome.reason]
                d.evidence = f"{d.evidence}; triage unavailable: {outcome.detail}"
                d.disposition = "NEEDS_HUMAN"
                d.gl_account = policy.GL_ACCOUNTS["NEEDS_HUMAN"]
            else:
                assert isinstance(outcome, Proposal)
                gate = policy.apply(outcome, txn, idx, threshold)
                d.confidence = outcome.confidence
                d.disposition = gate.disposition
                d.gl_account = gate.gl_account
                d.auto_posted = gate.auto_posted
                d.decided_by = f"{outcome.source}+gate"
                if gate.accepted:
                    stats.llm_accepted += 1
                else:
                    stats.llm_rejected += 1
                    d.gate_rejected_because = gate.rejected_because
                    if outcome.confidence < threshold:
                        d.reason = Reason.LLM_LOW_CONFIDENCE
                d.evidence = f"{d.evidence}; {gate.rationale}"

        decisions.append(d)
        log.record(d, {"narration": txn.narration, "credit_amount": txn.credit_amount,
                       "value_date": txn.value_date.isoformat()})

        key = d.tier.value if d.settlement_id else (d.reason.value if d.reason else "UNKNOWN")
        bucket = stats.matched if d.settlement_id else stats.exceptions
        bucket[key] = bucket.get(key, 0) + 1

    stats.seconds = time.perf_counter() - started
    stats.cache_hits = getattr(triage, "hits", 0)
    if hasattr(triage, "flush"):
        triage.flush()
    log.flush()
    return RunResult(decisions, stats, mode, run_id, threshold)
