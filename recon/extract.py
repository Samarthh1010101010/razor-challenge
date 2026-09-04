"""Deterministic reference extraction from bank narration text.

Split into two strengths on purpose, because they have very different precision:

- `labelled_utr` reads a reference that the bank explicitly tagged as a UTR.
  When it fires it is almost always right, so a hit here can be trusted alone.
- `bare_refs` returns every token merely *shaped* like a UTR. It fires on batch
  numbers, IFSC fragments and unrelated references too, so a hit here is only a
  hint and must be corroborated by amount before anything acts on it.

Keeping them separate is what lets the matcher assign different tiers, and lets
the policy gate demand corroboration for the weak signal without also throwing
away the strong one.
"""
from __future__ import annotations

import re

# A Razorpay UTR looks like 1597813219e1pq6w: mostly digits, some lowercase,
# no separators, 12-22 chars. Requiring >=8 digits keeps ordinary words out.
_UTR_SHAPE = re.compile(r"\b(?=[a-z0-9]*\d{8})[a-z0-9]{12,22}\b", re.IGNORECASE)

# "UTR1234abcd", "UTR 1234abcd", "UTR-1234abcd", and the spaced form the bank
# produces when it breaks the reference across its own field width.
_LABELLED = re.compile(
    r"UTR[\s:/-]*((?:[a-z0-9]{3,}[\s-]?){2,8})",
    re.IGNORECASE,
)


def _normalise(token: str) -> str:
    return re.sub(r"[\s-]", "", token).lower()


def labelled_utr(narration: str) -> str | None:
    """Return the reference the bank tagged as a UTR, or None.

    High precision, low recall. Returns None rather than guessing -- a wrong
    reference here would produce a confident wrong match, which is the most
    expensive error this system can make.
    """
    m = _LABELLED.search(narration)
    if not m:
        return None
    cleaned = _normalise(m.group(1))
    # The greedy group can swallow trailing words like "SETTLEMENT" or "PART".
    # Keep only the leading run that still looks like a UTR.
    shape = _UTR_SHAPE.search(cleaned)
    return shape.group(0) if shape else None


def bare_refs(narration: str) -> list[str]:
    """Every token shaped like a UTR, in order of appearance.

    Low precision by design. It fires on any long mostly-numeric token, so an
    account fragment or a long batch id can come back alongside the real
    reference. The matcher must corroborate these against the settlement file;
    it must never act on one unilaterally.

    A `UTR` label fused to the reference (`UTR1234abcd...`) is stripped, since
    the label is not part of the reference and would break an exact comparison.
    """
    out = []
    for tok in _UTR_SHAPE.findall(narration):
        t = _normalise(tok)
        if t.startswith("utr") and _UTR_SHAPE.fullmatch(t[3:]):
            t = t[3:]
        out.append(t)
    return out


def references(narration: str) -> tuple[str | None, list[str]]:
    """Both signals at once: (labelled_or_none, all_bare_candidates)."""
    return labelled_utr(narration), bare_refs(narration)
