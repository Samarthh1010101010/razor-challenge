"""Adversarial tests. Each one is a way the system could quietly be wrong."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from evaluation.calibrate import best, is_degenerate, sweep
from evaluation.score import score
from recon import policy
from recon.extract import bare_refs, labelled_utr, references
from recon.match import (AMOUNT_TOLERANCE_PAISE, Index, match_batch,
                         match_deterministic)
from recon.models import BankTxn, Reason, RunStats, Settlement, Tier
from recon.offline_triage import OfflineTriage
from recon.pipeline import run
from recon.triage import DISPOSITIONS, Proposal, TriageFailure


def S(sid="setl_aaaaaaaaaaaaaa", utr="1234567890abcd", amount=100_000, fees=0, tax=0,
      day=10) -> Settlement:
    return Settlement(sid, utr, amount, fees, tax, date(2026, 8, day))


def B(tid="bank_0001", amount=100_000, narration="NEFT-X-UTR1234567890abcd",
      day=11) -> BankTxn:
    return BankTxn(tid, date(2026, 8, day), amount, narration)


# --- extraction -----------------------------------------------------------

def test_labelled_utr_strips_fused_label():
    assert labelled_utr("NEFT-ACME-UTR1234567890abcd") == "1234567890abcd"
    assert bare_refs("NEFT-ACME-UTR1234567890abcd") == ["1234567890abcd"]


def test_labelled_utr_survives_bank_field_wrapping():
    assert labelled_utr("NEFT-X-UTR 1234 5678 90abcd") == "1234567890abcd"


def test_labelled_utr_returns_none_rather_than_guessing():
    assert labelled_utr("NEFT-RAZORPAY SOFTWARE-SETTLEMENT") is None
    assert references("CASH DEPOSIT BRANCH 0142") == (None, [])


def test_extractor_ignores_short_numeric_tokens():
    # A branch or batch number must not be mistaken for a reference.
    assert bare_refs("BULK PAYOUT BATCH 482913 RZPY") == []


# --- matching -------------------------------------------------------------

def test_exact_utr_and_amount_matches():
    idx = Index([S()])
    d = match_deterministic(B(), idx)
    assert d.tier is Tier.T1_UTR_EXACT and d.settlement_id == "setl_aaaaaaaaaaaaaa"


def test_settlement_cannot_be_claimed_twice():
    """The single most important financial invariant in the system."""
    idx = Index([S()])
    first = match_deterministic(B("bank_0001"), idx)
    second = match_deterministic(B("bank_0002"), idx)
    assert first.settlement_id == "setl_aaaaaaaaaaaaaa"
    assert second.settlement_id is None
    assert second.reason is Reason.ALREADY_CLAIMED


def test_claim_raises_rather_than_overwriting():
    idx = Index([S()])
    idx.claim("setl_aaaaaaaaaaaaaa", "bank_0001")
    with pytest.raises(RuntimeError):
        idx.claim("setl_aaaaaaaaaaaaaa", "bank_0002")


def test_ambiguous_amount_is_escalated_not_guessed():
    a = S("setl_aaaaaaaaaaaaaa", "1111111111aaaa")
    b = S("setl_bbbbbbbbbbbbbb", "2222222222bbbb")
    idx = Index([a, b])
    d = match_deterministic(B(narration="NEFT-X-SETTLEMENT BULK"), idx)
    assert d.settlement_id is None and d.reason is Reason.AMBIGUOUS


def test_utr_match_with_unexplained_amount_gap_is_rejected():
    """A right reference does not license a wrong amount."""
    idx = Index([S(amount=100_000)])
    d = match_deterministic(B(amount=90_000), idx)
    assert d.settlement_id is None
    assert d.reason is Reason.AMOUNT_OUT_OF_TOLERANCE


def test_fee_variance_is_matched_only_when_fees_explain_it():
    s = S(amount=100_000, fees=1_000, tax=180)
    idx = Index([s])
    d = match_deterministic(B(amount=s.expected_credit), idx)
    assert d.tier is Tier.T1_UTR_EXACT   # exact against expected_credit


def test_bare_reference_needs_amount_corroboration():
    """A reference with no UTR label must not be acted on alone."""
    idx = Index([S(amount=100_000)])
    d = match_deterministic(B(amount=555_555, narration="IMPS/1234567890abcd/RZPY"), idx)
    assert d.settlement_id is None


def test_empty_inputs_do_not_crash():
    assert match_deterministic(B(), Index([])).reason is Reason.NO_CANDIDATE
    res = run([], [], OfflineTriage(), 0.7, Path("out/test_empty.jsonl"))
    assert res.decisions == []


# --- the policy gate ------------------------------------------------------

def _prop(disposition="FOREIGN_VENDOR_CREDIT", confidence=0.95) -> Proposal:
    return Proposal(disposition, confidence, "ACME", "because", source="model")


def test_gate_rejects_disposition_outside_the_closed_set():
    idx = Index([S()])
    r = policy.apply(_prop("TRANSFER_TO_FOUNDER_ACCOUNT"), B(), idx, 0.7)
    assert not r.accepted and r.disposition == "NEEDS_HUMAN"
    assert r.rejected_because == "UNKNOWN_DISPOSITION"


def test_gate_rejects_below_calibrated_threshold():
    idx = Index([S()])
    r = policy.apply(_prop(confidence=0.50), B(), idx, 0.70)
    assert not r.accepted and r.rejected_because == "LOW_CONFIDENCE"


def test_gate_rejects_not_ours_when_narration_names_our_settlement():
    """Two-signal agreement: the model's claim contradicts the evidence."""
    idx = Index([S()])
    txn = B(narration="NEFT-ACME TRADING-UTR1234567890abcd")   # our UTR
    r = policy.apply(_prop("FOREIGN_VENDOR_CREDIT"), txn, idx, 0.7)
    assert not r.accepted and r.rejected_because == "CONTRADICTED_BY_REFERENCE"


def test_gate_rejects_awaiting_report_when_a_candidate_exists():
    idx = Index([S()])
    txn = B(narration="NEFT-RAZORPAY-SETTLEMENT")   # amount+date still fit
    r = policy.apply(_prop("AWAITING_SETTLEMENT_REPORT"), txn, idx, 0.7)
    assert not r.accepted and r.rejected_because == "CONTRADICTED_BY_CANDIDATE"


def test_needs_human_is_accepted_but_never_auto_posted():
    idx = Index([S()])
    r = policy.apply(_prop("NEEDS_HUMAN", 0.2), B(), idx, 0.7)
    assert r.accepted and not r.auto_posted


def test_every_rejection_routes_to_suspense():
    idx = Index([S()])
    for p in (_prop("NOPE"), _prop(confidence=0.1)):
        r = policy.apply(p, B(), idx, 0.7)
        assert r.gl_account == policy.GL_ACCOUNTS["NEEDS_HUMAN"]
        assert not r.auto_posted


def test_model_can_never_name_a_gl_account():
    """Accounts come from the code-owned table, keyed by an enum member."""
    assert set(policy.GL_ACCOUNTS) == set(DISPOSITIONS)


# --- degraded triage ------------------------------------------------------

class _Broken:
    """Stands in for every way the model tier can fail."""
    available = True

    def __init__(self, outcome):
        self._outcome = outcome

    def classify(self, txn, settlement_exists):
        return self._outcome


@pytest.mark.parametrize("outcome", [
    TriageFailure("LLM_UNAVAILABLE", "api down"),
    TriageFailure("LLM_MALFORMED", "not json"),
])
def test_triage_failure_degrades_to_rules_without_crashing(outcome):
    s, b = [S()], [B("bank_0001"), B("bank_0002", amount=7, narration="CASH DEPOSIT")]
    res = run(s, b, _Broken(outcome), 0.7, Path("out/test_broken.jsonl"))
    assert res.stats.llm_failures >= 1
    # The deterministic match is unaffected by the model being dead.
    assert res.decisions[0].settlement_id == "setl_aaaaaaaaaaaaaa"
    assert all(d.disposition in (None, "NEEDS_HUMAN") for d in res.decisions)


def test_hallucinated_high_confidence_is_still_gated():
    """A confident wrong answer must be caught by structure, not trust.

    The narration carries an unlabelled reference to a real settlement, but the
    amount does not corroborate it, so the matcher leaves the row unresolved and
    it reaches triage. The model then confidently calls it a third-party credit.
    The gate re-reads the narration and refuses.
    """
    s = [S()]
    b = [B("bank_0001", amount=999_999, narration="IMPS/1234567890abcd/RZPY")]
    res = run(s, b, _Broken(_prop("FOREIGN_VENDOR_CREDIT", 0.99)), 0.7,
              Path("out/test_hallu.jsonl"))
    d = res.decisions[0]
    assert d.gate_rejected_because == "CONTRADICTED_BY_REFERENCE"
    assert not d.auto_posted


def test_terminal_match_failures_never_reach_triage():
    """A row the matcher rejected on amount is settled. The model does not
    get a second opinion on it, because a reference that matched with a wrong
    amount is a discrepancy for a human, not a classification problem."""
    s = [S(amount=100_000)]
    b = [B("bank_0001", amount=999_999)]      # labelled UTR, amount way off
    res = run(s, b, _Broken(_prop("FOREIGN_VENDOR_CREDIT", 0.99)), 0.7,
              Path("out/test_terminal.jsonl"))
    assert res.stats.llm_calls == 0
    assert res.decisions[0].reason is Reason.AMOUNT_OUT_OF_TOLERANCE
    assert res.decisions[0].disposition is None


# --- calibration ----------------------------------------------------------

def test_flat_calibration_is_detected_not_reported():
    assert is_degenerate([(0.9, True), (0.8, True)])
    assert is_degenerate([(0.9, False), (0.8, False)])
    assert not is_degenerate([(0.9, True), (0.4, False)])


def test_sweep_prefers_the_stricter_threshold_on_a_tie():
    pts = sweep([(0.9, True), (0.2, False)])
    assert best(pts).false_accepts == 0


def test_cost_asymmetry_actually_moves_the_threshold():
    """A false accept costs 40x a false reject, so the cut sits above the bad one."""
    pts = sweep([(0.95, True), (0.60, False), (0.55, True)])
    assert best(pts).threshold > 0.60


# --- determinism ----------------------------------------------------------

def test_pipeline_is_reproducible():
    from recon.generate import build
    a = build(seed=99)
    b = build(seed=99)
    assert [x.txn_id for x in a[1]] == [x.txn_id for x in b[1]]
    assert a[2] == b[2]


def test_scoring_counts_a_wrong_match_as_a_false_positive():
    d = match_deterministic(B(), Index([S()]))
    sc = score([d], {"bank_0001": "setl_SOMETHINGELSE"}, {"bank_0001": "x"}, {}, RunStats())
    assert sc.false_positive == 1 and sc.precision == 0.0


# --- regressions from the systematic review ---------------------------------

def test_strong_evidence_beats_weak_regardless_of_statement_order():
    """A labelled UTR must win over an amount-only match, whichever line comes first.

    Regression: the matcher used to run one greedy pass in statement order, so a
    row with no reference could claim, on amount alone, a settlement that a later
    row's labelled UTR proved was its own. That produced a false positive caused
    by nothing but the order of lines in a file.
    """
    s = S("setl_TARGET", "9999999999abcd", 100_000)
    weak = BankTxn("bank_weak", date(2026, 8, 11), 100_000, "NEFT-RAZORPAY-SETTLEMENT")
    strong = BankTxn("bank_strong", date(2026, 8, 11), 100_000, "NEFT-X-UTR9999999999abcd")

    for order in ([weak, strong], [strong, weak]):
        out = {d.txn_id: d for d in match_batch(order, Index([s]))}
        assert out["bank_strong"].settlement_id == "setl_TARGET"
        assert out["bank_strong"].tier is Tier.T1_UTR_EXACT
        assert out["bank_weak"].settlement_id is None


def test_batch_result_is_independent_of_statement_order():
    """Shuffling the statement must not change a single pairing."""
    import random
    from recon.generate import build
    settlements, bank, _, _, _ = build(seed=4242)

    def pairs(rows):
        return {d.txn_id: d.settlement_id for d in match_batch(rows, Index(settlements))}

    baseline = pairs(bank)
    for seed in range(12):
        shuffled = list(bank)
        random.Random(seed).shuffle(shuffled)
        assert pairs(shuffled) == baseline


def test_a_flagged_amount_discrepancy_is_never_rematched_on_weaker_evidence():
    """A labelled UTR with a bad amount is a discrepancy, not a re-match candidate.

    If a later pass could pick it up on amount alone it would convert a flagged
    problem into a silent wrong answer.
    """
    s = S("setl_A", "1111111111aaaa", 100_000)
    other = S("setl_B", "2222222222bbbb", 555_000, day=10)
    txn = BankTxn("bank_1", date(2026, 8, 11), 555_000, "NEFT-X-UTR1111111111aaaa")
    out = match_batch([txn], Index([s, other]))[0]
    assert out.settlement_id is None
    assert out.reason is Reason.AMOUNT_OUT_OF_TOLERANCE


def test_mode_is_declared_not_inferred_from_a_class_name():
    """The report's SIMULATED disclosure depends on this string being right."""
    from recon.triage import ModelTriage
    assert OfflineTriage.mode == "offline"
    assert ModelTriage.mode == "model"
    # An unknown tier must fail safe to the disclosed mode, never to "model".
    res = run([], [], _Broken(TriageFailure("LLM_UNAVAILABLE", "x")), 0.7,
              Path("out/test_mode.jsonl"))
    assert res.mode == "offline"


def test_precision_holds_across_many_unseen_seeds():
    """The headline claim is 100% precision. One seed does not establish that."""
    from recon.generate import build
    total_fp = 0
    for seed in range(500, 530):
        settlements, bank, truth, _, _ = build(seed=seed)
        for d in match_batch(bank, Index(settlements)):
            if d.settlement_id and truth.get(d.txn_id) != d.settlement_id:
                total_fp += 1
    assert total_fp == 0


def test_bucketed_candidate_lookup_equals_an_exhaustive_scan():
    """The amount index is an optimisation; it must change speed, not answers."""
    from recon.generate import build
    from recon.match import AMOUNT_TOLERANCE_PAISE, DATE_WINDOW_DAYS

    def exhaustive(idx, txn):
        return sorted(s.settlement_id for s in idx.all
                      if not idx.is_claimed(s.settlement_id)
                      and abs(s.expected_credit - txn.credit_amount) <= AMOUNT_TOLERANCE_PAISE
                      and abs((txn.value_date - s.settled_on).days) <= DATE_WINDOW_DAYS)

    for seed in (11, 22, 33):
        settlements, bank, _, _, _ = build(seed=seed)
        idx = Index(settlements)
        for txn in bank:
            assert sorted(s.settlement_id for s in idx.candidates_by_amount(txn)) \
                == exhaustive(idx, txn)


def test_candidate_lookup_spans_the_whole_tolerance_window():
    """A candidate at the far edge of the tolerance must not fall between buckets."""
    base = 500_00                      # exactly on a rupee boundary
    settlements = [S("setl_lo", "aaaa111111aaaa", base - AMOUNT_TOLERANCE_PAISE),
                   S("setl_hi", "bbbb222222bbbb", base + AMOUNT_TOLERANCE_PAISE)]
    idx = Index(settlements)
    found = {s.settlement_id for s in idx.candidates_by_amount(
        BankTxn("bank_1", date(2026, 8, 11), base, "x"))}
    assert found == {"setl_lo", "setl_hi"}
