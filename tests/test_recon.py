"""Adversarial tests. Each one is a way the system could quietly be wrong."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from evaluation.calibrate import best, is_degenerate, sweep
from evaluation.score import score
from recon import policy
from recon.extract import bare_refs, labelled_utr, references
from recon.match import Index, match_deterministic
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
