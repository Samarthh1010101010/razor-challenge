"""Exception triage. The only place a model is used in this system.

It never sees a matched row and never proposes a match. Its job is the queue a
human would otherwise work by hand: read an unresolved bank credit and say what
kind of thing it is, from a closed set of dispositions.

Two safety properties are structural, not prompted:

1. The model chooses a **label from a fixed enum**, enforced by the API's own
   schema validation and re-checked here. It never names a GL account, an
   amount, or a settlement id -- the account is derived from the label by
   deterministic lookup in `policy.py`.
2. Its proposal is only advisory. `policy.py` decides, using evidence the model
   never saw.

Worst case, a mis-labelled exception lands in a queue a human was already going
to read. It cannot cause a false match.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from recon.models import BankTxn

MODEL = "claude-opus-5"

# Closed set. Anything outside it is rejected before the gate ever runs.
DISPOSITIONS = (
    "FOREIGN_VENDOR_CREDIT",       # counterparty is not us: vendor refund, reversal
    "TAX_REFUND",                  # GST / income-tax refund
    "BANK_INTEREST_OR_CHARGE",     # interest credit, fee reversal
    "CASH_OR_BRANCH_DEPOSIT",      # deposited over the counter
    "AWAITING_SETTLEMENT_REPORT",  # looks like our settlement, report not in yet
    "NEEDS_HUMAN",                 # genuinely undeterminable from the text
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "disposition": {"type": "string", "enum": list(DISPOSITIONS)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "counterparty": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["disposition", "confidence", "counterparty", "rationale"],
    "additionalProperties": False,
}

_SYSTEM = """You classify unreconciled credits on an Indian merchant's bank statement.

The merchant is a Razorpay customer. Their payment gateway settlements have
already been matched by a deterministic reconciler. Every row you see is one it
could NOT match, and your only job is to say what kind of credit it is.

Rules:
- Choose exactly one disposition from the allowed set.
- Judge only from the narration text and the stated facts. Do not speculate.
- AWAITING_SETTLEMENT_REPORT is for credits that clearly name the merchant or
  the gateway but have no settlement on file yet. If the counterparty is plainly
  someone else, it is not this.
- Set confidence to your genuine belief. A narration like "CASH DEPOSIT BRANCH
  0142" carries little information; say so with a low number rather than
  guessing high.
- NEEDS_HUMAN is the correct and expected answer when the text does not
  determine the answer. Choosing it is not a failure."""


@dataclass(frozen=True)
class Proposal:
    """What the model returned. Advisory until the policy gate accepts it."""

    disposition: str
    confidence: float
    counterparty: str
    rationale: str
    source: str            # "model" | "offline"


@dataclass(frozen=True)
class TriageFailure:
    """Why no proposal was produced. Always a routable outcome, never an exception."""

    reason: str            # LLM_UNAVAILABLE | LLM_MALFORMED
    detail: str


def _prompt(txn: BankTxn, settlement_exists: bool) -> str:
    return (
        f"Bank credit that could not be reconciled.\n\n"
        f"Value date: {txn.value_date}\n"
        f"Amount: INR {txn.credit_amount / 100:,.2f}\n"
        f"Narration: {txn.narration}\n\n"
        f"An unclaimed settlement matching this amount and date "
        f"{'DOES' if settlement_exists else 'does NOT'} exist on file.\n\n"
        f"Classify this credit."
    )


class ModelTriage:
    """Live classification via the Messages API.

    Every failure path returns a TriageFailure rather than raising, because a
    dead API must degrade this system to rules-only, not stop the reconciliation
    run. `available` is False when no credential is configured.
    """

    def __init__(self, timeout: float = 20.0, max_retries: int = 1):
        self._client = None
        self.available = False
        self.reason_unavailable = ""
        if not os.environ.get("ANTHROPIC_API_KEY"):
            self.reason_unavailable = "ANTHROPIC_API_KEY not set"
            return
        try:
            import anthropic
        except ImportError:
            self.reason_unavailable = "anthropic package not installed"
            return
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(timeout=timeout, max_retries=max_retries)
        self.available = True

    def classify(self, txn: BankTxn, settlement_exists: bool) -> Proposal | TriageFailure:
        if not self.available:
            return TriageFailure("LLM_UNAVAILABLE", self.reason_unavailable)
        try:
            resp = self._client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=_SYSTEM,
                # Classification does not repay deep reasoning; low effort keeps
                # cost and latency down without measurably changing the label.
                output_config={"effort": "low", "format": {"type": "json_schema",
                                                           "schema": _SCHEMA}},
                messages=[{"role": "user", "content": _prompt(txn, settlement_exists)}],
            )
        except self._anthropic.RateLimitError as e:
            return TriageFailure("LLM_UNAVAILABLE", f"rate limited: {e}")
        except self._anthropic.APIStatusError as e:
            return TriageFailure("LLM_UNAVAILABLE", f"api status {e.status_code}")
        except self._anthropic.APIConnectionError as e:
            return TriageFailure("LLM_UNAVAILABLE", f"connection: {e}")

        if resp.stop_reason == "refusal":
            return TriageFailure("LLM_UNAVAILABLE", "refused")
        try:
            text = next(b.text for b in resp.content if b.type == "text")
            data = json.loads(text)
        except (StopIteration, json.JSONDecodeError) as e:
            return TriageFailure("LLM_MALFORMED", f"unparseable response: {e}")

        # The schema is enforced server-side, but we re-check rather than trust
        # it. A label outside the closed set must never reach the gate.
        if data.get("disposition") not in DISPOSITIONS:
            return TriageFailure("LLM_MALFORMED",
                                 f"disposition not in allowed set: {data.get('disposition')!r}")
        try:
            conf = float(data["confidence"])
        except (KeyError, TypeError, ValueError):
            return TriageFailure("LLM_MALFORMED", "confidence missing or non-numeric")
        if not 0.0 <= conf <= 1.0:
            return TriageFailure("LLM_MALFORMED", f"confidence out of range: {conf}")

        return Proposal(data["disposition"], conf, str(data.get("counterparty", ""))[:120],
                        str(data.get("rationale", ""))[:400], source="model")
