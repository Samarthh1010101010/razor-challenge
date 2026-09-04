"""Append-only decision log.

Every bank row that passes through the pipeline writes exactly one record, and
records are never mutated or removed. If a number in the report cannot be traced
back to a line in here, the number is wrong.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from recon.models import Decision


class AuditLog:
    """JSONL sink. Opened once per run, appended to, never rewritten."""

    def __init__(self, path: Path, run_id: str, mode: str):
        self.path = path
        self.run_id = run_id
        self.mode = mode          # which extractor tier was live for this run
        self._entries: list[dict[str, Any]] = []
        path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, decision: Decision, inputs: dict[str, Any]) -> None:
        entry = {
            "run_id": self.run_id,
            "mode": self.mode,
            "at": datetime.now(timezone.utc).isoformat(),
            "txn_id": decision.txn_id,
            "tier": decision.tier.value,
            "settlement_id": decision.settlement_id,
            "reason": decision.reason.value if decision.reason else None,
            "confidence": decision.confidence,
            "variance_paise": decision.variance,
            "decided_by": decision.decided_by,
            # The disposition and the account are the financial action. Leaving
            # them out meant the posting itself was not auditable, while the
            # README claimed every reported number traced back to this log.
            "disposition": decision.disposition,
            "gl_account": decision.gl_account,
            "auto_posted": decision.auto_posted,
            "gate_rejected_because": decision.gate_rejected_because or None,
            "evidence": decision.evidence,
            "inputs": inputs,
        }
        self._entries.append(entry)

    def flush(self) -> int:
        with self.path.open("a") as f:
            for e in self._entries:
                f.write(json.dumps(e) + "\n")
        n = len(self._entries)
        self._entries.clear()
        return n
