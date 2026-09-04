"""Deterministic matcher, run as evidence-strength passes over the whole batch.

Claims are exclusive: whoever asks for a settlement first gets it. That makes
the *order in which rows are considered* a correctness concern, not a
performance detail.

A single greedy pass in statement order gets this wrong. A row with no reference
at all, matched on amount and date alone, can claim a settlement that a later
row's **labelled UTR** proves is its own. The later row then reports
ALREADY_CLAIMED and the earlier match is a false positive produced by nothing
but the order of lines in a file.

So `match_batch` runs passes: every labelled-UTR claim is taken before any
unlabelled reference is considered, and both before anything is matched on
amount alone. Strong evidence always wins, and the output does not depend on
statement order at all.

Financial safety (ENGINEERING.md): a settlement is claimable exactly once. The claim
is taken at the moment a match is accepted.
"""
from __future__ import annotations

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

        # Settlements bucketed by expected credit in whole rupees. Without this,
        # finding amount candidates scans every settlement for every row, which
        # is O(rows x settlements): fine for a 65-row demo, but throughput
        # halved with each doubling of the settlement file, so a real month-end
        # batch degraded badly. The tolerance is +/-100 paise, so a row's
        # candidates can only live in its own rupee bucket or the two adjacent
        # ones -- three dict lookups instead of a full scan.
        self._by_rupee: dict[int, list[Settlement]] = {}
        for s in settlements:
            self._by_rupee.setdefault(s.expected_credit // 100, []).append(s)

    def is_claimed(self, settlement_id: str) -> bool:
        return settlement_id in self._claimed

    def claim(self, settlement_id: str, txn_id: str) -> None:
        if settlement_id in self._claimed:
            raise RuntimeError(
                f"{settlement_id} already claimed by {self._claimed[settlement_id]}"
            )
        self._claimed[settlement_id] = txn_id

    def candidates_by_amount(self, txn: BankTxn) -> list[Settlement]:
        """Unclaimed settlements whose expected credit and date fit this row.

        Bucket lookup, not a scan -- see `_by_rupee`. The predicates below are
        identical to the exhaustive version; only the candidate set they are
        applied to is narrowed, and it is narrowed to a provable superset.
        """
        key = txn.credit_amount // 100
        return [
            s
            for bucket in (key - 1, key, key + 1)
            for s in self._by_rupee.get(bucket, ())
            if not self.is_claimed(s.settlement_id)
            and abs(s.expected_credit - txn.credit_amount) <= AMOUNT_TOLERANCE_PAISE
            and abs((txn.value_date - s.settled_on).days) <= DATE_WINDOW_DAYS
        ]


def _try_labelled(txn: BankTxn, idx: Index) -> Decision | None:
    """Pass 1 -- the bank explicitly tagged a UTR. The strongest evidence there is.

    `None` defers the row to a later pass. A returned Decision is final for this
    row, *including* a rejection: a labelled UTR whose amount does not reconcile
    is a discrepancy for a human, and letting a weaker pass re-match it on looser
    evidence would turn a flagged problem into a silent wrong answer.
    """
    labelled, _ = references(txn.narration)
    if not labelled or labelled not in idx.by_utr:
        return None

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
                        evidence=(f"labelled UTR {labelled}; credit differs from amount "
                                  f"by {variance} paise, explained by fees {s.fees} "
                                  f"+ tax {s.tax}"))

    return Decision(txn.txn_id, Tier.UNMATCHED, reason=Reason.AMOUNT_OUT_OF_TOLERANCE,
                    variance=variance,
                    evidence=f"UTR {labelled} matched but amount off by {variance} paise")


def _try_bare(txn: BankTxn, idx: Index) -> Decision | None:
    """Pass 2 -- an unlabelled reference, trusted only if the amount agrees.

    Two-signal agreement: the reference alone is not enough, because the same
    shape matches account fragments and batch ids. Defers on no match so the
    amount-only pass can still try.
    """
    _, bare = references(txn.narration)
    for ref in bare:
        s = idx.by_utr.get(ref)
        if not s or idx.is_claimed(s.settlement_id):
            continue
        if abs(txn.credit_amount - s.expected_credit) <= AMOUNT_TOLERANCE_PAISE:
            idx.claim(s.settlement_id, txn.txn_id)
            return Decision(txn.txn_id, Tier.T1_UTR_EXACT, s.settlement_id,
                            variance=txn.credit_amount - s.expected_credit,
                            evidence=f"unlabelled reference {ref} corroborated by amount")
    return None


def _try_amount_date(txn: BankTxn, idx: Index) -> Decision | None:
    """Pass 3 -- no usable reference. Match only if amount and date are unique.

    By the time this runs, every reference-backed claim is already taken, so the
    candidate set here is genuinely what is left over.
    """
    cands = idx.candidates_by_amount(txn)
    if len(cands) != 1:
        return None
    s = cands[0]
    idx.claim(s.settlement_id, txn.txn_id)
    return Decision(txn.txn_id, Tier.T3_AMOUNT_DATE, s.settlement_id,
                    variance=txn.credit_amount - s.expected_credit,
                    evidence=f"unique amount+date match within {DATE_WINDOW_DAYS}d / "
                             f"{AMOUNT_TOLERANCE_PAISE}p")


def _explain_unmatched(txn: BankTxn, idx: Index) -> Decision:
    """Why a row survived every pass. Evaluated after all claims are settled."""
    cands = idx.candidates_by_amount(txn)
    if len(cands) > 1:
        return Decision(txn.txn_id, Tier.UNMATCHED, reason=Reason.AMBIGUOUS,
                        evidence=f"{len(cands)} settlements fit amount+date: "
                                 + ", ".join(c.settlement_id for c in cands))
    return Decision(txn.txn_id, Tier.UNMATCHED, reason=Reason.NO_CANDIDATE,
                    evidence="no unclaimed settlement fits amount+date")


def match_batch(bank: list[BankTxn], idx: Index) -> list[Decision]:
    """Reconcile a whole statement. **This is the correct entry point.**

    Returns decisions in the caller's original row order, but resolves them in
    evidence-strength order, so the result is independent of how the bank
    happened to sort the file.
    """
    # Keyed by position, never by txn_id. A bank export can repeat a reference
    # id, and keying on it silently collapsed two distinct credits into one
    # decision -- the counts still balanced, so nothing flagged it.
    pending = list(enumerate(bank))
    resolved: dict[int, Decision] = {}

    for attempt in (_try_labelled, _try_bare, _try_amount_date):
        deferred: list[tuple[int, BankTxn]] = []
        for i, txn in pending:
            d = attempt(txn, idx)
            if d is None:
                deferred.append((i, txn))
            else:
                resolved[i] = d
        pending = deferred

    for i, txn in pending:
        resolved[i] = _explain_unmatched(txn, idx)

    return [resolved[i] for i in range(len(bank))]


def match_deterministic(txn: BankTxn, idx: Index) -> Decision:
    """Resolve a single row against the current index state.

    Used for unit tests and one-off inspection. Reconciling a statement with
    this in a loop reintroduces the order dependence `match_batch` exists to
    remove -- use `match_batch` for real work.
    """
    for attempt in (_try_labelled, _try_bare, _try_amount_date):
        d = attempt(txn, idx)
        if d is not None:
            return d
    return _explain_unmatched(txn, idx)
