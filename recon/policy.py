"""The gate. Deterministic authority over every model proposal.

`triage.py` proposes a disposition. Nothing here trusts it. The gate re-derives
evidence the model never saw and rejects any proposal that contradicts it, so a
confident wrong answer is caught by structure rather than by hoping the model
was right.

The GL account is **never** proposed by the model. It is looked up from the
accepted disposition in a table that lives in code, so no model output can ever
name an account this table does not contain.
"""
from __future__ import annotations

from dataclasses import dataclass

from recon.extract import references
from recon.match import Index
from recon.models import BankTxn
from recon.triage import DISPOSITIONS, Proposal

# Disposition -> GL account. Closed mapping, code-owned.
GL_ACCOUNTS: dict[str, str] = {
    "FOREIGN_VENDOR_CREDIT":      "2100-AP-VENDOR-REFUND",
    "TAX_REFUND":                 "1450-TAX-RECEIVABLE",
    "BANK_INTEREST_OR_CHARGE":    "4200-OTHER-INCOME",
    "CASH_OR_BRANCH_DEPOSIT":     "1010-CASH-IN-TRANSIT",
    "AWAITING_SETTLEMENT_REPORT": "1210-GATEWAY-RECEIVABLE",
    "NEEDS_HUMAN":                "9999-SUSPENSE",
}
assert set(GL_ACCOUNTS) == set(DISPOSITIONS), "every disposition needs an account"

# Dispositions asserting the credit is not ours. Each carries a claim the gate
# can independently falsify from the settlement file.
_NOT_OURS = {"FOREIGN_VENDOR_CREDIT", "TAX_REFUND",
             "BANK_INTEREST_OR_CHARGE", "CASH_OR_BRANCH_DEPOSIT"}


@dataclass(frozen=True)
class GateResult:
    accepted: bool
    disposition: str          # the disposition actually applied
    gl_account: str
    rationale: str            # why the gate ruled this way, in its own words
    auto_posted: bool         # False means a human still has to look
    rejected_because: str = ""


def _routed_to_human(why: str, note: str) -> GateResult:
    """Every rejection lands in suspense for a human. Never silently dropped."""
    return GateResult(False, "NEEDS_HUMAN", GL_ACCOUNTS["NEEDS_HUMAN"], note,
                      auto_posted=False, rejected_because=why)


def apply(proposal: Proposal, txn: BankTxn, idx: Index, threshold: float) -> GateResult:
    """Decide whether to act on a proposal.

    `threshold` is calibrated on a separate seed (see evaluation/calibrate.py);
    it is passed in rather than hardcoded so the value in use is always the one
    the calibration produced.
    """
    if proposal.disposition not in GL_ACCOUNTS:
        return _routed_to_human("UNKNOWN_DISPOSITION",
                                f"{proposal.disposition!r} is not an allowed disposition")

    # NEEDS_HUMAN is the model declining to guess. That is the correct answer
    # often enough that we accept it as-is -- but it is never auto-posted.
    if proposal.disposition == "NEEDS_HUMAN":
        return GateResult(True, "NEEDS_HUMAN", GL_ACCOUNTS["NEEDS_HUMAN"],
                          "model declined to classify; queued for review",
                          auto_posted=False)

    if proposal.confidence < threshold:
        return _routed_to_human(
            "LOW_CONFIDENCE",
            f"confidence {proposal.confidence:.2f} below calibrated threshold {threshold:.2f}")

    # --- Two-signal agreement. The gate re-derives evidence independently. ---

    labelled, bare = references(txn.narration)
    narration_names_a_settlement = any(
        ref in idx.by_utr for ref in ([labelled] if labelled else []) + bare
    )
    unclaimed_amount_match = bool(idx.candidates_by_amount(txn))

    if proposal.disposition in _NOT_OURS and narration_names_a_settlement:
        # The model says this credit belongs to someone else, but the narration
        # carries a reference that resolves to one of our own settlements.
        return _routed_to_human(
            "CONTRADICTED_BY_REFERENCE",
            "model called this a third-party credit, but its narration carries a "
            "reference matching a settlement on file")

    if proposal.disposition == "AWAITING_SETTLEMENT_REPORT" and unclaimed_amount_match:
        # The model says the settlement has not arrived, but an unclaimed one
        # fits on amount and date -- so the reconciler left it open for another
        # reason (ambiguity), and posting to receivable would paper over that.
        return _routed_to_human(
            "CONTRADICTED_BY_CANDIDATE",
            "model said no settlement has arrived, but an unclaimed settlement "
            "fits this amount and date")

    return GateResult(True, proposal.disposition, GL_ACCOUNTS[proposal.disposition],
                      proposal.rationale, auto_posted=True)
