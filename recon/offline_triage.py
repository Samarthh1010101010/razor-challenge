"""SIMULATED offline classifier. Not a model, and never presented as one.

Exists so the pipeline, its tests, and the evaluation run end to end on a
machine with no API credential. Every decision it produces is tagged
`source="offline"` and every report states when a run used it, so no number
sourced from this file can be mistaken for a model result.

It is a keyword classifier. It is deliberately not clever: its purpose is to
keep the pipeline exercisable, not to stand in for the model's judgement.
"""
from __future__ import annotations

import re

from recon.models import BankTxn
from recon.triage import Proposal

# Ordered. First hit wins.
_RULES: list[tuple[str, str, float]] = [
    (r"\bGST\b|\bTAX\b|\bREFUND AY\b|\bIT REFUND\b", "TAX_REFUND", 0.80),
    (r"CASH DEPOSIT|BRANCH \d+|CDM", "CASH_OR_BRANCH_DEPOSIT", 0.85),
    (r"\bINT(EREST)?\.? CR\b|\bCHARGES? REV\b", "BANK_INTEREST_OR_CHARGE", 0.75),
    (r"VENDOR|REVERSAL|RETURN|TRADING CO", "FOREIGN_VENDOR_CREDIT", 0.70),
    (r"RAZORPAY|RZPY|SETTLEMENT", "AWAITING_SETTLEMENT_REPORT", 0.60),
]


class OfflineTriage:
    """Drop-in replacement for ModelTriage with the same call signature."""

    available = True
    reason_unavailable = ""

    def classify(self, txn: BankTxn, settlement_exists: bool) -> Proposal:
        for pattern, disposition, confidence in _RULES:
            if re.search(pattern, txn.narration, re.IGNORECASE):
                return Proposal(disposition, confidence,
                                counterparty=txn.narration[:60],
                                rationale=f"offline keyword rule matched /{pattern}/",
                                source="offline")
        return Proposal("NEEDS_HUMAN", 1.0, counterparty=txn.narration[:60],
                        rationale="no offline rule matched", source="offline")
