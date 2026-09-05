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
    t = GeminiTriage()

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
