"""Typed records shared across the pipeline.

Everything the matcher and policy gate touch is a frozen dataclass so a
malformed row fails at construction rather than three stages later.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from enum import Enum
from typing import Any


class Tier(str, Enum):
    """How a match was arrived at. Ordered most to least certain."""

    T1_UTR_EXACT = "T1_UTR_EXACT"          # UTR found, amount exact
    T2_UTR_VARIANCE = "T2_UTR_VARIANCE"    # UTR found, amount off by fees within tolerance
    T3_AMOUNT_DATE = "T3_AMOUNT_DATE"      # no UTR, but amount+date uniquely identify one settlement
    UNMATCHED = "UNMATCHED"

    # There is deliberately no model-proposed tier. The model does not match --
    # see docs/decisions.md D2.


class Reason(str, Enum):
    """Why a bank row ended up in the exception list. One code per row."""

    NO_CANDIDATE = "NO_CANDIDATE"                  # nothing in the settlement file could match
    AMBIGUOUS = "AMBIGUOUS"                        # >1 candidate, no tiebreaker
    AMOUNT_OUT_OF_TOLERANCE = "AMOUNT_OUT_OF_TOLERANCE"
    ALREADY_CLAIMED = "ALREADY_CLAIMED"            # candidate matched to an earlier bank row
    LLM_LOW_CONFIDENCE = "LLM_LOW_CONFIDENCE"      # below the calibrated threshold
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"            # no key, timeout, or API error
    LLM_MALFORMED = "LLM_MALFORMED"                # response failed schema validation


@dataclass(frozen=True)
class Settlement:
    """One payout row from the gateway settlement report."""

    settlement_id: str     # Razorpay `id`, e.g. setl_7IZKKI4Pnt2kEe
    utr: str               # alphanumeric, e.g. 1597813219e1pq6w
    amount: int            # paise; Razorpay `amount`
    fees: int              # paise; 0 for a normal settlement
    tax: int               # paise; tax on fees, 0 for a normal settlement
    settled_on: date       # derived from `created_at` epoch
    status: str = "processed"

    @property
    def expected_credit(self) -> int:
        """What the bank should credit.

        Razorpay deducts fees per payment, not per settlement, so for a normal
        settlement `fees` and `tax` are 0 and `amount` is already net. We
        subtract only when they are genuinely non-zero.
        """
        return self.amount - self.fees - self.tax


@dataclass(frozen=True)
class BankTxn:
    """One credit line from the bank statement. `narration` is free text."""

    txn_id: str
    value_date: date
    credit_amount: int     # paise
    narration: str


@dataclass
class Decision:
    """The outcome for one bank row, and the whole story of how we got there."""

    txn_id: str
    tier: Tier
    settlement_id: str | None = None
    reason: Reason | None = None
    confidence: float | None = None        # model-reported, only on T4
    variance: int = 0                      # paise, credit minus expected net
    evidence: str = ""                     # human-readable justification
    decided_by: str = "rules"              # "rules" | "model+gate" | "offline+gate"
    # Set only on unresolved rows that went through triage.
    disposition: str | None = None
    gl_account: str | None = None
    auto_posted: bool = False
    gate_rejected_because: str = ""

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["tier"] = self.tier.value
        d["reason"] = self.reason.value if self.reason else ""
        return d


@dataclass
class RunStats:
    """Counters the report is built from. Incremented in one place only."""

    bank_rows: int = 0
    settlements: int = 0
    matched: dict[str, int] = field(default_factory=dict)
    exceptions: dict[str, int] = field(default_factory=dict)
    llm_calls: int = 0
    llm_accepted: int = 0
    llm_rejected: int = 0
    llm_failures: int = 0
    seconds: float = 0.0
