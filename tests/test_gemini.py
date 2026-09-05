"""Gemini tier, exercised against a mocked transport.

The live path cannot be tested without a credential, so every branch is driven
here through a fake `urlopen`. What matters is that no failure shape escapes as
an exception: a reconciliation run that already succeeded must not be brought
down by a classifier having a bad day.
"""
from __future__ import annotations

import json
import urllib.error
from datetime import date
from io import BytesIO

import pytest

from recon.gemini_triage import GeminiTriage
from recon.models import BankTxn
from recon.triage import Proposal, TriageFailure

TXN = BankTxn("bank_1", date(2026, 8, 11), 250_00, "RTGS CR/GST REFUND AY2026/x")


def _body(**over):
    payload = {"disposition": "TAX_REFUND", "confidence": 0.91,
               "counterparty": "GST", "rationale": "names a GST refund"}
    payload.update(over)
    return {"candidates": [{"finishReason": "STOP",
                            "content": {"parts": [{"text": json.dumps(payload)}]}}]}


def _tier(monkeypatch, response=None, raise_exc=None):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    t = GeminiTriage(rpm=100_000)      # pacing is covered by its own test

    class Resp:
        def __init__(self, d): self._d = json.dumps(d).encode()
        def read(self): return self._d
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        if raise_exc:
            raise raise_exc
        return Resp(response)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return t


def test_happy_path_returns_a_proposal(monkeypatch):
    t = _tier(monkeypatch, _body())
    out = t.classify(TXN, settlement_exists=False)
    assert isinstance(out, Proposal)
    assert out.disposition == "TAX_REFUND" and out.confidence == 0.91
    assert out.source == "model"


def test_missing_key_is_unavailable_not_an_exception(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    t = GeminiTriage()
    assert t.available is False
    assert isinstance(t.classify(TXN, False), TriageFailure)


def test_disposition_outside_the_closed_set_is_rejected(monkeypatch):
    t = _tier(monkeypatch, _body(disposition="TRANSFER_TO_FOUNDER"))
    out = t.classify(TXN, False)
    assert isinstance(out, TriageFailure) and out.reason == "LLM_MALFORMED"


@pytest.mark.parametrize("conf", [1.7, -0.2, "high", None])
def test_bad_confidence_is_rejected(monkeypatch, conf):
    t = _tier(monkeypatch, _body(confidence=conf))
    out = t.classify(TXN, False)
    assert isinstance(out, TriageFailure) and out.reason == "LLM_MALFORMED"


def test_unparseable_body_is_malformed_not_a_crash(monkeypatch):
    bad = {"candidates": [{"finishReason": "STOP",
                           "content": {"parts": [{"text": "not json{"}]}}]}
    out = _tier(monkeypatch, bad).classify(TXN, False)
    assert isinstance(out, TriageFailure) and out.reason == "LLM_MALFORMED"


def test_truncated_generation_is_reported(monkeypatch):
    cut = {"candidates": [{"finishReason": "MAX_TOKENS",
                           "content": {"parts": [{"text": "{}"}]}}]}
    out = _tier(monkeypatch, cut).classify(TXN, False)
    assert isinstance(out, TriageFailure) and "stopped early" in out.detail


def test_rate_limit_degrades_rather_than_raising(monkeypatch):
    err = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, BytesIO(b"{}"))
    out = _tier(monkeypatch, raise_exc=err).classify(TXN, False)
    assert isinstance(out, TriageFailure) and "rate limited" in out.detail


def test_wrong_model_id_says_which_models_exist(monkeypatch):
    """A bare 404 does not tell you what your key can actually reach."""
    err = urllib.error.HTTPError("u", 404, "Not Found", {}, BytesIO(b"{}"))
    t = _tier(monkeypatch, raise_exc=err)
    monkeypatch.setattr(t, "_list_models", lambda: ["gemini-2.5-flash", "gemini-2.5-pro"])
    out = t.classify(TXN, False)
    assert isinstance(out, TriageFailure)
    assert "gemini-2.5-flash" in out.detail


def test_connection_loss_degrades(monkeypatch):
    out = _tier(monkeypatch, raise_exc=urllib.error.URLError("dns")).classify(TXN, False)
    assert isinstance(out, TriageFailure) and out.reason == "LLM_UNAVAILABLE"


def test_model_is_overridable_by_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash")
    assert GeminiTriage().model == "gemini-3.5-flash"


def test_the_pipeline_accepts_this_tier_unchanged(monkeypatch):
    """The whole point of the swap: nothing downstream needed to change."""
    from pathlib import Path
    from recon.match import Settlement
    from recon.pipeline import run
    t = _tier(monkeypatch, _body())
    s = [Settlement("setl_x", "1111111111aaaa", 999_999, 0, 0, date(2026, 8, 10))]
    res = run(s, [TXN], t, 0.7, Path("out/test_gemini.jsonl"))
    assert res.mode == "model"
    assert res.decisions[0].disposition == "TAX_REFUND"
    assert res.decisions[0].decided_by == "model+gate"


def test_dotenv_is_actually_loaded(tmp_path, monkeypatch):
    """.env.example promised this long before any code read it."""
    from recon import config
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    f = tmp_path / ".env"
    f.write_text('# comment\nGEMINI_API_KEY="abc123"\nEMPTY\nGEMINI_MODEL=x\n')
    assert set(config.load(f)) == {"GEMINI_API_KEY", "GEMINI_MODEL"}
    import os
    assert os.environ["GEMINI_API_KEY"] == "abc123"   # quotes stripped


def test_exported_environment_beats_a_stale_dotenv(tmp_path, monkeypatch):
    from recon import config
    monkeypatch.setenv("GEMINI_API_KEY", "real-key")
    f = tmp_path / ".env"
    f.write_text("GEMINI_API_KEY=stale-key\n")
    config.load(f)
    import os
    assert os.environ["GEMINI_API_KEY"] == "real-key"


def test_calls_are_paced_to_the_rate_limit(monkeypatch):
    """Free tiers are the normal case; a batch must stay inside the budget.

    Regression: the first live run fired ~24 calls back to back against a
    15 rpm free tier and 8 of 12 came back throttled, which the report then
    showed as a 25%-accurate model.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    t = GeminiTriage(rpm=15)
    assert t._min_interval == 60.0 / 15

    slept = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))
    t._last_call = 0.0
    t._wait_for_slot()          # first call never waits
    t._wait_for_slot()          # second must
    assert slept and 0 < slept[0] <= t._min_interval


def test_a_429_is_retried_once_before_giving_up(monkeypatch):
    import urllib.error
    from io import BytesIO
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    t = GeminiTriage(rpm=100_000)
    monkeypatch.setattr("time.sleep", lambda s: None)

    calls = {"n": 0}

    def always_429(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError("u", 429, "rate", {}, BytesIO(b"{}"))

    monkeypatch.setattr("urllib.request.urlopen", always_429)
    out = t.classify(TXN, False)
    assert calls["n"] == 2, "should attempt once, retry once, then stop"
    assert isinstance(out, TriageFailure) and "after a retry" in out.detail


def test_cache_prevents_rebuying_an_answer(tmp_path, monkeypatch):
    """A re-run must spend calls only on rows never classified.

    Regression: two live runs re-asked the model the same twelve questions and
    exhausted the day's free-tier quota; the second returned 'rate limited' on
    every row and threw away the first run's real answers.
    """
    from recon.offline_triage import OfflineTriage
    from recon.triage_cache import CachedTriage

    calls = {"n": 0}

    class Counting(OfflineTriage):
        def classify(self, txn, settlement_exists):
            calls["n"] += 1
            return super().classify(txn, settlement_exists)

    path = tmp_path / "cache.json"
    c = CachedTriage(Counting(), path)
    c.classify(TXN, False)
    c.classify(TXN, False)
    assert calls["n"] == 1 and c.hits == 1
    c.flush()

    # A fresh process reuses the answer without calling at all.
    c2 = CachedTriage(Counting(), path)
    assert c2.classify(TXN, False).disposition == "TAX_REFUND"
    assert calls["n"] == 1


def test_failures_are_never_cached(tmp_path, monkeypatch):
    """A rate limit is a fact about today, not about the row."""
    from recon.triage_cache import CachedTriage

    class Dead:
        available, mode, model_id = True, "model", "gemini:test"
        def classify(self, txn, se): return TriageFailure("LLM_UNAVAILABLE", "rate limited")

    path = tmp_path / "c.json"
    c = CachedTriage(Dead(), path)
    c.classify(TXN, False)
    c.flush()
    assert path.read_text().strip() == "{}", "an outage must not become permanent"


def test_cache_key_includes_the_model(tmp_path):
    """A different model is a different classifier; do not attribute its answers."""
    from recon.triage_cache import _key
    a = _key("gemini:gemini-2.5-flash", TXN, False)
    b = _key("gemini:gemini-2.5-pro", TXN, False)
    assert a != b


def test_a_404_switches_to_a_model_the_key_actually_serves(monkeypatch):
    """A setup detail should not cost the whole run.

    Regression: a live run configured gemini-2.5-flash, which appeared in the
    key's own model list and still 404'd on generateContent. All twelve rows
    came back unclassified over a model id.
    """
    import urllib.error
    from io import BytesIO
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    t = GeminiTriage(rpm=100_000, model="gemini-2.5-flash")
    monkeypatch.setattr(t, "_list_models",
                        lambda: ["gemini-2.5-pro", "gemini-flash-latest"])

    seen = []

    class Resp:
        def __init__(self, d): self._d = json.dumps(d).encode()
        def read(self): return self._d
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def urlopen(req, timeout=None):
        seen.append(req.full_url)
        if "gemini-2.5-flash:" in req.full_url:
            raise urllib.error.HTTPError("u", 404, "Not Found", {}, BytesIO(b"{}"))
        return Resp(_body())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    out = t.classify(TXN, False)
    assert isinstance(out, Proposal), "should have retried on a working model"
    assert t.model == "gemini-flash-latest"
    assert t.model_id == "gemini:gemini-flash-latest", "the report must name what answered"
    assert len(seen) == 2


def test_it_gives_up_rather_than_looping_when_nothing_works(monkeypatch):
    import urllib.error
    from io import BytesIO
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    t = GeminiTriage(rpm=100_000, model="nope")
    monkeypatch.setattr(t, "_list_models", lambda: [])

    calls = {"n": 0}

    def always_404(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, BytesIO(b"{}"))

    monkeypatch.setattr("urllib.request.urlopen", always_404)
    out = t.classify(TXN, False)
    assert isinstance(out, TriageFailure)
    assert calls["n"] < 6, "must not loop through models forever"
