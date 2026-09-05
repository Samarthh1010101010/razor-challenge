"""Persistent cache of classifications, keyed by the input that produced them.

Two reasons, and the second matters more than the first.

**Quota.** A free tier has a daily request budget. Re-running a batch re-asked
the model questions it had already answered, and two runs exhausted the day's
quota -- the second returned `rate limited` on all twelve rows. Cached answers
mean a re-run only spends calls on rows that have never been classified, so
partial progress accumulates instead of being thrown away and re-bought.

**Reproducibility.** A committed cache lets anyone regenerate the exact reported
numbers with no credential at all. The alternative -- "trust our screenshot" --
is what the evaluation section of this repo argues against. Every cached entry
records the model that produced it and when, so a stale or foreign entry is
visible rather than silently reused.

The cache key deliberately includes the model id: a different model is a
different classifier, and its answers must not be attributed to this one.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from recon.models import BankTxn
from recon.triage import Proposal

DEFAULT_PATH = Path("data/triage_cache.json")


def _key(model_id: str, txn: BankTxn, settlement_exists: bool) -> str:
    """Hash of everything the classifier was actually shown."""
    material = f"{model_id}|{txn.narration}|{txn.credit_amount}|{settlement_exists}"
    return hashlib.sha256(material.encode()).hexdigest()[:20]


class CachedTriage:
    """Wraps any triage tier. Delegates on a miss, records on a success.

    Failures are never cached: a rate limit is a fact about today, not about the
    row, and caching it would make a transient outage permanent.
    """

    def __init__(self, inner, path: Path = DEFAULT_PATH):
        self._inner = inner
        self._path = path
        self.available = getattr(inner, "available", False)
        self.mode = getattr(inner, "mode", "offline")
        self.model_id = getattr(inner, "model_id", self.mode)
        self.reason_unavailable = getattr(inner, "reason_unavailable", "")
        self.hits = 0
        self.misses = 0
        self._store: dict[str, dict] = {}
        if path.exists():
            try:
                self._store = json.loads(path.read_text())
            except json.JSONDecodeError:
                self._store = {}       # a corrupt cache is a miss, not a crash

    def classify(self, txn: BankTxn, settlement_exists: bool):
        k = _key(self.model_id, txn, settlement_exists)
        if k in self._store:
            self.hits += 1
            e = self._store[k]
            return Proposal(e["disposition"], e["confidence"], e["counterparty"],
                            e["rationale"], source=e.get("source", "model"))

        self.misses += 1
        out = self._inner.classify(txn, settlement_exists)
        if isinstance(out, Proposal):
            self._store[k] = {**asdict(out), "model_id": self.model_id,
                              "cached_at": datetime.now(timezone.utc).isoformat(),
                              "narration": txn.narration[:80]}
        return out

    def flush(self) -> int:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._store, indent=2, sort_keys=True))
        return len(self._store)
