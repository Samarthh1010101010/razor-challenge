"""CSV readers. The single seam where a live Razorpay API would be substituted.

`load_settlements` returns the same Settlement objects a
`GET /v1/settlements` response would map to (see research/razorpay.md), so
swapping the source does not touch the matcher.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from pathlib import Path

from recon.models import BankTxn, Settlement


def load_settlements(path: Path) -> list[Settlement]:
    out = []
    with path.open() as f:
        for r in csv.DictReader(f):
            out.append(Settlement(
                settlement_id=r["settlement_id"],
                utr=r["utr"],
                amount=int(r["amount"]),
                fees=int(r["fees"]),
                tax=int(r["tax"]),
                settled_on=datetime.fromtimestamp(int(r["created_at"]), timezone.utc).date(),
                status=r["status"],
            ))
    return out


def load_bank(path: Path) -> list[BankTxn]:
    with path.open() as f:
        return [BankTxn(r["txn_id"], date.fromisoformat(r["value_date"]),
                        int(r["credit_amount"]), r["narration"])
                for r in csv.DictReader(f)]


def load_truth(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Answer key. Imported only by scoring code, never by the matcher."""
    truth, styles = {}, {}
    with path.open() as f:
        for r in csv.DictReader(f):
            if r["settlement_id"]:
                truth[r["txn_id"]] = r["settlement_id"]
            styles[r["txn_id"]] = r["style"]
    return truth, styles
