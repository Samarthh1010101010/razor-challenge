"""Synthetic two-source dataset with a known-correct answer key.

The answer key is the point. Match rate alone says nothing about whether the
matches are *right* -- a matcher that pairs everything with anything scores
100%. Generating the data ourselves means every number we report can be checked
against ground truth, including the ones that make us look bad.

Settlement rows follow the verified Razorpay settlements entity (see
research/razorpay.md): `setl_`-prefixed alphanumeric ids, alphanumeric UTRs,
paise amounts, epoch timestamps, and `fees`/`tax` at 0 for a normal settlement.
Bank rows are SIMULATED -- no Razorpay API exposes a merchant's bank statement.

Narration styles are modelled on Indian NEFT/RTGS/IMPS credit lines, which is
where the real difficulty lives: the UTR is present but formatted six different
ways, mangled, or absent entirely.
"""
from __future__ import annotations

import csv
import random
import string
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from recon.models import BankTxn, Settlement

# Fixed seeds. CALIBRATION tunes the policy threshold; HELDOUT is what we report
# on. They must never be the same number -- see docs/evaluation.md.
SEED_CALIBRATION = 20260904
SEED_HELDOUT = 20260905

MERCHANT = "RAZORPAY SOFTWARE PRIVATE LIMITED"
_ID_ALPHABET = string.ascii_letters + string.digits

# How each bank row was built. Lets us report accuracy per difficulty class.
# The matcher never sees this column.
CLEAN_UTR = "clean_utr"
FEE_VARIANCE = "fee_variance"          # non-zero fees/tax: credit != amount
NO_UTR_UNIQUE = "no_utr_unique"
MESSY_NARRATION = "messy_narration"    # UTR recoverable only by reading the text
FOREIGN_CREDIT = "foreign_credit"      # not a settlement at all
AMBIGUOUS_PAIR = "ambiguous_pair"      # two settlements, same amount, same day


def _settlement_id(rng: random.Random) -> str:
    """`setl_` + 14 alphanumeric chars, matching setl_7IZKKI4Pnt2kEe."""
    return "setl_" + "".join(rng.choice(_ID_ALPHABET) for _ in range(14))


def _utr(rng: random.Random) -> str:
    """Alphanumeric UTR in the shape of 1597813219e1pq6w.

    Deliberately not 12 digits. A `\\d{12}` extractor finds nothing here, or
    latches onto an unrelated numeric reference -- which is precisely the
    failure the model tier exists to cover.
    """
    tail = "".join(rng.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return f"{rng.randint(10**9, 10**10 - 1)}{tail}"


def _clean_narration(utr: str, rng: random.Random) -> str:
    return rng.choice([
        f"NEFT-{MERCHANT}-UTR{utr}",
        f"RTGS CR/{MERCHANT[:14]}/UTR{utr}/SETTLEMENT",
        f"NEFT CR-HDFC0000123-RAZORPAY SOFTWA-UTR{utr}",
    ])


def _messy_narration(utr: str, rng: random.Random) -> str:
    """Narrations where a labelled-UTR extractor comes up empty or wrong.

    Each style is one real failure mode: the UTR broken across spaces, a bare
    reference with no UTR label, an unrelated batch number sitting in front of
    the real UTR, an OCR-style character swap in the merchant name, or the UTR
    truncated by the bank's field width.
    """
    spaced = f"{utr[:4]} {utr[4:8]} {utr[8:]}"
    return rng.choice([
        f"NEFT-RAZORPAY SOFT-UTR {spaced}",
        f"IMPS/{utr}/RZPY/PAYOUT",
        f"RAZORPAY SETTLEMENT REF {utr} PART ADJ",
        f"NEFT CR-ICIC0000456-RAZ0RPAY SOFTWA-N{utr}",
        f"BULK PAYOUT BATCH {rng.randint(100000, 999999)} RZPY {utr}",
    ])


def _foreign_narrations(rng: random.Random) -> list[tuple[str, str]]:
    """Credits that are not settlements. Matching one is a false positive.

    Returns (narration, correct_disposition) pairs, **stratified**: two of every
    disposition class rather than ten independent draws. Random draws left whole
    classes absent on some seeds, which would have hidden a triage weakness
    behind an unlucky sample rather than measuring it. Stratifying a test set
    for class coverage is standard; it does not make any classifier look better,
    it only makes every class measurable.
    """
    families = [
        (lambda: f"NEFT-ACME TRADING CO-UTR{_utr(rng)}", "FOREIGN_VENDOR_CREDIT"),
        (lambda: f"IMPS/{_utr(rng)}/REFUND REVERSAL", "FOREIGN_VENDOR_CREDIT"),
        (lambda: f"CASH DEPOSIT BRANCH {rng.randint(100, 999)}", "CASH_OR_BRANCH_DEPOSIT"),
        (lambda: f"NEFT-INT CR-SAVINGS A/C QTR", "BANK_INTEREST_OR_CHARGE"),
        (lambda: f"RTGS CR/GST REFUND AY2026/{_utr(rng)}", "TAX_REFUND"),
    ]
    return [(make(), disp) for make, disp in families for _ in range(2)]


def build(n_settlements: int = 55, seed: int = SEED_HELDOUT):
    """Return (settlements, bank_txns, truth, styles).

    `truth` maps txn_id -> settlement_id. A txn_id absent from `truth` genuinely
    has no correct match: calling it unmatched is right, and claiming a match
    for it is a false positive.
    """
    rng = random.Random(seed)
    base = date(2026, 8, 3)

    settlements: list[Settlement] = []
    for i in range(n_settlements):
        amount = rng.randrange(25_000_00, 9_50_000_00)  # paise
        # Most settlements are "normal": fees and tax are 0 per Razorpay docs.
        # A minority carry non-zero fees, which is what creates the variance class.
        if i % 7 == 5:
            fees = int(amount * 0.0018)
            tax = int(fees * 0.18)
        else:
            fees = tax = 0
        settlements.append(Settlement(
            settlement_id=_settlement_id(rng),
            utr=_utr(rng),
            amount=amount,
            fees=fees,
            tax=tax,
            settled_on=base + timedelta(days=rng.randint(0, 20)),
        ))

    # Force one same-amount, same-day collision so ambiguity is genuinely exercised.
    twin = settlements[7]
    settlements[8] = Settlement(
        settlement_id=settlements[8].settlement_id,
        utr=_utr(rng),
        amount=twin.amount,
        fees=twin.fees,
        tax=twin.tax,
        settled_on=twin.settled_on,
    )

    bank: list[BankTxn] = []
    truth: dict[str, str] = {}
    styles: dict[str, str] = {}
    dispositions: dict[str, str] = {}   # correct triage answer, where one exists
    tid = 0

    def emit(value_date: date, amount: int, narration: str, style: str,
             setl_id: str | None, disposition: str = ""):
        nonlocal tid
        txn = BankTxn(f"bank_{tid:04d}", value_date, amount, narration)
        bank.append(txn)
        styles[txn.txn_id] = style
        if setl_id:
            truth[txn.txn_id] = setl_id
        if disposition:
            dispositions[txn.txn_id] = disposition
        tid += 1

    for idx, s in enumerate(settlements):
        vd = s.settled_on + timedelta(days=1)   # INFERRED T+1, see research/razorpay.md
        credit = s.expected_credit
        if idx in (7, 8):
            # Two identical payouts on one day with no reference in the text.
            # No classifier can separate these; NEEDS_HUMAN is the right answer.
            emit(vd, credit, f"NEFT-{MERCHANT}-SETTLEMENT BULK", AMBIGUOUS_PAIR,
                 s.settlement_id, "NEEDS_HUMAN")
        elif idx % 7 == 3:
            emit(vd, credit, _messy_narration(s.utr, rng), MESSY_NARRATION, s.settlement_id)
        elif idx % 7 == 5:
            emit(vd, credit, _clean_narration(s.utr, rng), FEE_VARIANCE, s.settlement_id)
        elif idx % 7 == 6:
            emit(vd, credit, f"NEFT-{MERCHANT}-SETTLEMENT", NO_UTR_UNIQUE, s.settlement_id)
        else:
            emit(vd, credit, _clean_narration(s.utr, rng), CLEAN_UTR, s.settlement_id)

    for narration, disposition in _foreign_narrations(rng):
        emit(base + timedelta(days=rng.randint(0, 21)),
             rng.randrange(5_000_00, 3_00_000_00), narration, FOREIGN_CREDIT,
             None, disposition)

    rng.shuffle(bank)
    return settlements, bank, truth, styles, dispositions


def write(out: Path, seed: int = SEED_HELDOUT) -> None:
    settlements, bank, truth, styles, dispositions = build(seed=seed)
    out.mkdir(parents=True, exist_ok=True)

    with (out / "settlements.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["settlement_id", "utr", "amount", "fees", "tax", "status", "created_at"])
        for s in settlements:
            epoch = int(datetime.combine(s.settled_on, datetime.min.time(),
                                         tzinfo=timezone.utc).timestamp())
            w.writerow([s.settlement_id, s.utr, s.amount, s.fees, s.tax, s.status, epoch])

    with (out / "bank_statement.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["txn_id", "value_date", "credit_amount", "narration"])
        for b in bank:
            w.writerow([b.txn_id, b.value_date.isoformat(), b.credit_amount, b.narration])

    # Answer key. Read only by the scoring step, never by the matcher.
    with (out / "ground_truth.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["txn_id", "settlement_id", "style", "expected_disposition"])
        for b in bank:
            w.writerow([b.txn_id, truth.get(b.txn_id, ""), styles[b.txn_id],
                        dispositions.get(b.txn_id, "")])


if __name__ == "__main__":
    import sys
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else SEED_HELDOUT
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data")
    write(out, seed)
    print(f"seed={seed} -> {out}/settlements.csv, bank_statement.csv, ground_truth.csv")
