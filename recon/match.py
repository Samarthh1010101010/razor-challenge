"""Tiered deterministic matcher.

Ordered strongest evidence first. Each tier either resolves a row or hands it
down; nothing falls through silently. Rows that survive every tier are handed to
the model tier as *unresolved*, never as a guess.

Financial safety (CLAUDE.md): a settlement can be claimed exactly once. The
claim is taken at the moment a match is accepted, so two bank rows can never
both be reconciled against the same payout.
"""
from __future__ import annotations

from datetime import timedelta

from recon.extract import references
from recon.models import BankTxn, Decision, Reason, Settlement, Tier

# A bank credit is expected the day after settlement (INFERRED, T+1). We allow a
# window rather than an equality test because the timing is not a documented
# guarantee and weekends shift it.
DATE_WINDOW_DAYS = 3

# Banks credit whole rupees; a settlement is in paise. Anything inside one rupee
# is rounding, not a discrepancy worth a human's attention.
AMOUNT_TOLERANCE_PAISE = 100


class Index:
    """Lookup structures over the settlement file, plus claim tracking."""

    def __init__(self, settlements: list[Settlement]):
        self.by_id = {s.settlement_id: s for s in settlements}
        self.by_utr: dict[str, Settlement] = {s.utr.lower(): s for s in settlements}
        self.all = settlements
        self._claimed: dict[str, str] = {}   # settlement_id -> txn_id that took it

    def is_claimed(self, settlement_id: str) -> bool:
        return settlement_id in self._claimed

    def claim(self, settlement_id: str, txn_id: str) -> None:
        if settlement_id in self._claimed:
            raise RuntimeError(
                f"{settlement_id} already claimed by {self._claimed[settlement_id]}"
            )
        self._claimed[settlement_id] = txn_id

    def candidates_by_amount(self, txn: BankTxn) -> list[Settlement]:
        """Unclaimed settlements whose expected credit and date fit this row."""
        return [
            s for s in self.all
            if not self.is_claimed(s.settlement_id)
            and abs(s.expected_credit - txn.credit_amount) <= AMOUNT_TOLERANCE_PAISE
            and abs((txn.value_date - s.settled_on).days) <= DATE_WINDOW_DAYS
        ]


def match_deterministic(txn: BankTxn, idx: Index) -> Decision:
    """Resolve one bank row using rules alone.

    Returns a Decision that is either matched (T1/T2/T3) or UNMATCHED with a
    reason. UNMATCHED + NO_CANDIDATE / AMBIGUOUS is the signal that the model
    tier may be able to help; the other reasons are terminal.
    """
    labelled, bare = references(txn.narration)

    # T1/T2 -- the bank told us the UTR. Strongest possible evidence.
    if labelled and labelled in idx.by_utr:
        s = idx.by_utr[labelled]
        if idx.is_claimed(s.settlement_id):
            return Decision(txn.txn_id, Tier.UNMATCHED, reason=Reason.ALREADY_CLAIMED,
                            evidence=f"UTR {labelled} already reconciled")
        variance = txn.credit_amount - s.expected_credit
        if abs(variance) <= AMOUNT_TOLERANCE_PAISE:
            idx.claim(s.settlement_id, txn.txn_id)
            return Decision(txn.txn_id, Tier.T1_UTR_EXACT, s.settlement_id,
                            variance=variance,
                            evidence=f"labelled UTR {labelled}, amount exact")
        if s.fees or s.tax:
            # Documented case: non-zero fees mean the credit is below `amount`.
            idx.claim(s.settlement_id, txn.txn_id)
            return Decision(txn.txn_id, Tier.T2_UTR_VARIANCE, s.settlement_id,
                            variance=variance,
                            evidence=(f"labelled UTR {labelled}; credit differs from "
                                      f"amount by {variance} paise, explained by "
                                      f"fees {s.fees} + tax {s.tax}"))
        return Decision(txn.txn_id, Tier.UNMATCHED, reason=Reason.AMOUNT_OUT_OF_TOLERANCE,
                        variance=variance,
                        evidence=f"UTR {labelled} matched but amount off by {variance} paise")

    # A bare reference is only trusted when the amount independently agrees.
    # This is the same two-signal rule the model tier is held to.
    for ref in bare:
        s = idx.by_utr.get(ref)
        if not s or idx.is_claimed(s.settlement_id):
            continue
        if abs(txn.credit_amount - s.expected_credit) <= AMOUNT_TOLERANCE_PAISE:
            idx.claim(s.settlement_id, txn.txn_id)
            return Decision(txn.txn_id, Tier.T1_UTR_EXACT, s.settlement_id,
                            variance=txn.credit_amount - s.expected_credit,
                            evidence=f"unlabelled reference {ref} corroborated by amount")

    # T3 -- no usable reference, but amount and date pick out exactly one payout.
    cands = idx.candidates_by_amount(txn)
    if len(cands) == 1:
        s = cands[0]
        idx.claim(s.settlement_id, txn.txn_id)
        return Decision(txn.txn_id, Tier.T3_AMOUNT_DATE, s.settlement_id,
                        variance=txn.credit_amount - s.expected_credit,
                        evidence=f"unique amount+date match within "
                                 f"{DATE_WINDOW_DAYS}d / {AMOUNT_TOLERANCE_PAISE}p")
    if len(cands) > 1:
        return Decision(txn.txn_id, Tier.UNMATCHED, reason=Reason.AMBIGUOUS,
                        evidence=f"{len(cands)} settlements fit amount+date: "
                                 + ", ".join(c.settlement_id for c in cands))
    return Decision(txn.txn_id, Tier.UNMATCHED, reason=Reason.NO_CANDIDATE,
                    evidence="no unclaimed settlement fits amount+date")
