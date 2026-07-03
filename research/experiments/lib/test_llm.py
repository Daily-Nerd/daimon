"""Unit tests for lib/llm.py content extraction + fail-fast/pre-flight (no network)."""

import json
import urllib.error

import pytest

import llm
from llm import _extract_content


def _resp(message: dict) -> dict:
    return {"choices": [{"message": message}]}


class _FakeResp:
    """Minimal context-manager stand-in for an urlopen() response."""

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


_OK_PAYLOAD = {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 5}}


@pytest.fixture
def _creds(monkeypatch):
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test")
    monkeypatch.delenv("LITELLM_CONTEXT_WINDOW", raising=False)  # no ambient gate
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)  # no real backoff waits


def test_extract_content_prefers_content():
    assert _extract_content(_resp({"content": "the answer"})) == "the answer"


def test_extract_content_falls_back_to_reasoning_when_content_empty():
    r = _resp({"content": "", "reasoning_content": "reasoned answer"})
    assert _extract_content(r) == "reasoned answer"


def test_extract_content_falls_back_when_content_whitespace():
    r = _resp({"content": "   ", "reasoning_content": "X"})
    assert _extract_content(r) == "X"


def test_extract_content_prefers_nonempty_content_over_reasoning():
    r = _resp({"content": "real", "reasoning_content": "trace"})
    assert _extract_content(r) == "real"


def test_extract_content_missing_keys_returns_empty():
    assert _extract_content(_resp({})) == ""


# --- token estimation -------------------------------------------------------

def test_estimate_tokens_scales_with_chars():
    small = llm._estimate_tokens([{"role": "user", "content": "a" * 40}])
    big = llm._estimate_tokens([{"role": "user", "content": "a" * 4000}])
    assert big > small
    assert 8 <= small <= 12  # ~40 chars / 4 ≈ 10 tokens


def test_estimate_tokens_sums_all_messages():
    one = llm._estimate_tokens([{"content": "a" * 400}])
    two = llm._estimate_tokens([{"content": "a" * 400}, {"content": "a" * 400}])
    assert two > one


# --- pre-flight context-window check ----------------------------------------

def test_chat_preflight_overflow_skips_network(_creds, monkeypatch):
    calls = []
    monkeypatch.setattr(llm.urllib.request, "urlopen",
                        lambda req, timeout=None: calls.append(1) or _FakeResp(_OK_PAYLOAD))
    with pytest.raises(llm.ContextOverflowError):
        llm.chat([{"role": "user", "content": "x" * 8000}], model="m", context_window=10)
    assert calls == []  # raised before any network call


def test_chat_preflight_allows_when_within_window(_creds, monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(_OK_PAYLOAD))
    content, usage, mdl = llm.chat([{"role": "user", "content": "hi"}],
                                   model="m", context_window=100_000)
    assert content == "ok"
    assert mdl == "m"


def test_chat_no_preflight_when_window_none(_creds, monkeypatch):
    # context_window=None (default) → no estimate, call proceeds regardless of size.
    monkeypatch.setattr(llm.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(_OK_PAYLOAD))
    content, _usage, _mdl = llm.chat([{"role": "user", "content": "x" * 8000}], model="m")
    assert content == "ok"


def test_context_overflow_is_chat_error():
    assert issubclass(llm.ContextOverflowError, llm.ChatError)


def test_chat_preflight_reads_env_context_window(_creds, monkeypatch):
    # No context_window arg → falls back to LITELLM_CONTEXT_WINDOW so every
    # caller (verify_live, scale benchmark, …) gets the gate from one env var.
    calls = []
    monkeypatch.setattr(llm.urllib.request, "urlopen",
                        lambda req, timeout=None: calls.append(1) or _FakeResp(_OK_PAYLOAD))
    monkeypatch.setenv("LITELLM_CONTEXT_WINDOW", "10")
    with pytest.raises(llm.ContextOverflowError):
        llm.chat([{"role": "user", "content": "x" * 8000}], model="m")
    assert calls == []


def test_chat_explicit_window_overrides_env(_creds, monkeypatch):
    # An explicit arg wins over the env default.
    monkeypatch.setattr(llm.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(_OK_PAYLOAD))
    monkeypatch.setenv("LITELLM_CONTEXT_WINDOW", "10")
    content, _u, _m = llm.chat([{"role": "user", "content": "x" * 8000}],
                               model="m", context_window=100_000)
    assert content == "ok"


def test_chat_malformed_env_window_ignored(_creds, monkeypatch):
    # A garbage env value must not crash a run — gate is simply off.
    monkeypatch.setattr(llm.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(_OK_PAYLOAD))
    monkeypatch.setenv("LITELLM_CONTEXT_WINDOW", "not-a-number")
    content, _u, _m = llm.chat([{"role": "user", "content": "x" * 8000}], model="m")
    assert content == "ok"


# --- fail-fast guard: a stall must not be retried 3× ------------------------

def test_chat_timeout_fails_fast_no_retry(_creds, monkeypatch):
    calls = []

    def _stall(req, timeout=None):
        calls.append(1)
        raise TimeoutError("model stalled")

    monkeypatch.setattr(llm.urllib.request, "urlopen", _stall)
    with pytest.raises(llm.ChatError):
        llm.chat([{"role": "user", "content": "hi"}], model="m", retries=3, timeout=1)
    assert len(calls) == 1  # stall is non-retryable


def test_chat_urlerror_wrapping_timeout_fails_fast(_creds, monkeypatch):
    calls = []

    def _stall(req, timeout=None):
        calls.append(1)
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(llm.urllib.request, "urlopen", _stall)
    with pytest.raises(llm.ChatError):
        llm.chat([{"role": "user", "content": "hi"}], model="m", retries=3, timeout=1)
    assert len(calls) == 1


def test_chat_urlerror_still_retries(_creds, monkeypatch):
    # Connection errors are genuinely transient (port-forward not up yet) → keep retrying.
    calls = []

    def _refused(req, timeout=None):
        calls.append(1)
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(llm.urllib.request, "urlopen", _refused)
    with pytest.raises(llm.ChatError):
        llm.chat([{"role": "user", "content": "hi"}], model="m", retries=3)
    assert len(calls) == 3  # full retry budget used
