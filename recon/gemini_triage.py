"""Exception triage via the Gemini API. Same contract as the Anthropic tier.

Uses the REST endpoint over `urllib` rather than a vendor SDK. The core of this
project is standard library only, and one JSON POST does not justify a
dependency -- `ENGINEERING.md` asks every major dependency to be justifiable.

Everything that makes the model tier safe is unchanged and lives outside this
file: the disposition set is closed and re-validated here, the policy gate in
`policy.py` still holds final authority, and the GL account is still a
code-owned table lookup. Swapping the provider swaps *who proposes a label* and
nothing else. That the swap is this small is the point of the design.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from recon.models import BankTxn
from recon.triage import DISPOSITIONS, Proposal, TriageFailure, _SYSTEM, _prompt

# Free-tier Flash model. Override with GEMINI_MODEL if your key exposes another.
DEFAULT_MODEL = "gemini-2.5-flash"
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"

# Gemini's free tier allows 15 requests/minute. A batch fires ~24 calls, so
# firing them back to back throttled two thirds of them: the first live run
# returned 8 LLM_UNAVAILABLE out of 12 and the report read like a bad model
# rather than a hit rate limit. Pace to stay inside the window, and retry once
# on a 429 in case the limit is tighter than advertised.
DEFAULT_RPM = int(os.environ.get("GEMINI_RPM", "15"))
_RETRY_ON_429 = 1

# Gemini takes an OpenAPI-subset schema: uppercase type names, no
# additionalProperties. Same closed enum as the Anthropic tier.
_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "disposition": {"type": "STRING", "enum": list(DISPOSITIONS)},
        "confidence": {"type": "NUMBER"},
        "counterparty": {"type": "STRING"},
        "rationale": {"type": "STRING"},
    },
    "required": ["disposition", "confidence", "counterparty", "rationale"],
}


class GeminiTriage:
    """Live classification via Gemini. Degrades exactly as the Anthropic tier does.

    Every failure path returns a `TriageFailure` rather than raising, because a
    dead API must degrade this system to rules-only, not stop a reconciliation
    run that has already succeeded.
    """

    mode = "model"

    def __init__(self, timeout: float = 30.0, model: str | None = None,
                 rpm: int | None = None):
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        self.model_id = f"gemini:{self.model}"
        self._timeout = timeout
        self._min_interval = 60.0 / max(rpm or DEFAULT_RPM, 1)
        self._last_call = 0.0
        self._key = os.environ.get("GEMINI_API_KEY", "")
        self.available = bool(self._key)
        self.reason_unavailable = "" if self.available else "GEMINI_API_KEY not set"

    # -- transport ---------------------------------------------------------

    def _wait_for_slot(self) -> None:
        """Sleep just enough to keep inside the requests-per-minute budget."""
        gap = time.monotonic() - self._last_call
        if self._last_call and gap < self._min_interval:
            time.sleep(self._min_interval - gap)
        self._last_call = time.monotonic()

    def _post(self, body: dict, _attempt: int = 0) -> tuple[dict | None, TriageFailure | None]:
        self._wait_for_slot()
        req = urllib.request.Request(
            f"{_ENDPOINT}/{self.model}:generateContent",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "x-goog-api-key": self._key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                return json.loads(r.read().decode()), None
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:300]
            if e.code == 404:
                # A wrong model id is the likeliest setup mistake, and the raw
                # 404 does not say which ids the key can actually reach.
                return None, TriageFailure(
                    "LLM_UNAVAILABLE",
                    f"model {self.model!r} not found for this key. "
                    f"Available: {', '.join(self._list_models()) or 'could not list'}")
            if e.code == 429:
                if _attempt < _RETRY_ON_429:
                    # Honour Retry-After when the server sends one.
                    delay = float(e.headers.get("Retry-After") or self._min_interval * 2)
                    time.sleep(min(delay, 60.0))
                    return self._post(body, _attempt + 1)
                return None, TriageFailure("LLM_UNAVAILABLE",
                                           "rate limited by Gemini after a retry")
            return None, TriageFailure("LLM_UNAVAILABLE", f"http {e.code}: {detail}")
        except urllib.error.URLError as e:
            return None, TriageFailure("LLM_UNAVAILABLE", f"connection: {e.reason}")
        except (TimeoutError, OSError) as e:
            return None, TriageFailure("LLM_UNAVAILABLE", f"transport: {e}")

    def _list_models(self) -> list[str]:
        """Best-effort, only to make a 404 actionable. Never raises."""
        try:
            req = urllib.request.Request(_ENDPOINT,
                                         headers={"x-goog-api-key": self._key})
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                data = json.loads(r.read().decode())
            return [m["name"].removeprefix("models/") for m in data.get("models", [])
                    if "generateContent" in m.get("supportedGenerationMethods", [])][:8]
        except Exception:
            return []

    # -- the tier ----------------------------------------------------------

    def classify(self, txn: BankTxn, settlement_exists: bool) -> Proposal | TriageFailure:
        if not self.available:
            return TriageFailure("LLM_UNAVAILABLE", self.reason_unavailable)

        payload, failure = self._post({
            "system_instruction": {"parts": [{"text": _SYSTEM}]},
            "contents": [{"role": "user",
                          "parts": [{"text": _prompt(txn, settlement_exists)}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _SCHEMA,
                # Classification, not composition. Determinism matters more than
                # variety, and a reconciliation run should be reproducible.
                "temperature": 0,
            },
        })
        if failure:
            return failure

        try:
            cand = payload["candidates"][0]
            if cand.get("finishReason") not in (None, "STOP"):
                return TriageFailure("LLM_UNAVAILABLE",
                                     f"stopped early: {cand.get('finishReason')}")
            data = json.loads(cand["content"]["parts"][0]["text"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            return TriageFailure("LLM_MALFORMED", f"unparseable response: {e}")

        # The schema is enforced server-side; re-check anyway. The gate must
        # never receive a label outside the closed set.
        if data.get("disposition") not in DISPOSITIONS:
            return TriageFailure(
                "LLM_MALFORMED",
                f"disposition not in allowed set: {data.get('disposition')!r}")
        try:
            conf = float(data["confidence"])
        except (KeyError, TypeError, ValueError):
            return TriageFailure("LLM_MALFORMED", "confidence missing or non-numeric")
        if not 0.0 <= conf <= 1.0:
            return TriageFailure("LLM_MALFORMED", f"confidence out of range: {conf}")

        return Proposal(data["disposition"], conf,
                        str(data.get("counterparty", ""))[:120],
                        str(data.get("rationale", ""))[:400], source="model")
