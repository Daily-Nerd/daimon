"""#1220: a prompt the configured chain cannot serve is never built.

With a command backend in arg mode (primary or rescue), llm refuses a prompt
over _ARG_MAX_BYTES at exec time, which is after the primary has spent its whole
timeout. The serializer used to size merge groups by count only, so it could
build such a prompt. The invariant these tests pin: every prompt the serializer
sends is servable by every backend in the configured chain, or the call fails
before any backend runs, naming the call and the setting to change.
"""
import json
import subprocess

import pytest

from daimon_briefing import configure, llm, render, serializer


def _good():
    def it(t):
        return {"text": t, "trust": "inferred", "importance": 5}

    return {"working_context": {"active_topic": it("t"), "open_questions": [],
                                "recent_decisions": []},
            "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                                   "contradictions_flagged": []},
            "worker_queue": []}


def _blob_partial(n_bytes):
    g = _good()
    g["working_context"]["open_questions"] = [
        {"text": "y" * n_bytes, "trust": "inferred", "importance": 5}]
    return g


class _Script:
    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def __call__(self, messages, **kw):
        self.calls.append(messages)
        return json.dumps(self.fn(len(self.calls), messages))


def _msgs(n, content=None):
    return [{"role": "user" if i % 2 == 0 else "assistant",
             "content": content or f"line {i} text"} for i in range(n)]


@pytest.fixture
def chunked(monkeypatch):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_CHUNK_LINES", "4")
    monkeypatch.setenv("DAIMON_CHUNK_OVERLAP", "0")
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "100")
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "1")
    return monkeypatch


@pytest.fixture
def rescue_arg(monkeypatch):
    """Primary stdin command, rescue in arg mode."""
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "fakeprimary")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_FALLBACK", "fakerescue -p")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_FALLBACK_INPUT", "arg")
    monkeypatch.setattr(llm, "_missing_binary", lambda b: None)
    return monkeypatch


def _prompt_sizes(chat):
    return [llm.prompt_bytes(c) for c in chat.calls]


# ---- the control: the order that makes a late cap check expensive ----

def test_arg_cap_is_checked_only_after_the_primary_has_run(rescue_arg):
    order = []

    def run(argv, stdin, timeout, env, cwd):
        order.append(argv[0])
        raise subprocess.TimeoutExpired(argv, timeout)
    rescue_arg.setattr(llm, "_run_command", run)
    big = [{"role": "user", "content": "x" * 102_048}]
    with pytest.raises(llm.ChatError) as ei:
        llm.chat(big)
    assert order == ["fakeprimary"]
    assert "prompt too large for arg-mode" in str(ei.value)
    assert "timed out" in str(ei.value)


def test_cap_error_names_the_setting_the_rescue_reads(rescue_arg):
    rescue_arg.setattr(llm, "_run_command",
                       lambda *a, **k: (_ for _ in ()).throw(
                           subprocess.TimeoutExpired("x", 1)))
    big = [{"role": "user", "content": "x" * 102_048}]
    with pytest.raises(llm.ChatError) as ei:
        llm.chat(big)
    assert "DAIMON_LLM_COMMAND_FALLBACK_INPUT" in str(ei.value)
    assert "DAIMON_LLM_COMMAND_INPUT to" not in str(ei.value)


def test_cap_error_names_the_setting_the_primary_reads(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "devin -p")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "arg")
    big = [{"role": "user", "content": "x" * (llm._ARG_MAX_BYTES + 1)}]
    with pytest.raises(llm.ChatError) as ei:
        llm._chat_command(big, deadline=None)
    assert "DAIMON_LLM_COMMAND_INPUT" in str(ei.value)
    assert "DAIMON_LLM_COMMAND_FALLBACK_INPUT" not in str(ei.value)


# ---- llm.prompt_budget_bytes: one number for the configured chain ----

def test_budget_is_none_with_no_arg_mode_anywhere(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "fakeprimary")
    monkeypatch.setattr(llm, "_missing_binary", lambda b: None)
    assert llm.prompt_budget_bytes() is None
    assert llm.prompt_budget_setting() is None


def test_budget_none_for_file_mode_rescue(rescue_arg):
    rescue_arg.setenv("DAIMON_LLM_COMMAND_FALLBACK_INPUT", "file:--prompt-file")
    assert llm.prompt_budget_bytes() is None


def test_budget_is_the_cap_for_an_arg_mode_rescue(rescue_arg):
    assert llm.prompt_budget_bytes() == llm._ARG_MAX_BYTES
    assert llm.prompt_budget_setting() == "DAIMON_LLM_COMMAND_FALLBACK_INPUT"


def test_budget_is_the_cap_for_an_arg_mode_primary(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "fakeprimary")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "arg")
    monkeypatch.setattr(llm, "_missing_binary", lambda b: None)
    assert llm.prompt_budget_bytes() == llm._ARG_MAX_BYTES
    assert llm.prompt_budget_setting() == "DAIMON_LLM_COMMAND_INPUT"


def test_budget_ignores_a_rescue_that_cannot_run(rescue_arg):
    rescue_arg.setattr(llm, "_missing_binary", lambda b: "fakerescue")
    assert llm.prompt_budget_bytes() is None
    rescue_arg.setattr(llm, "_missing_binary", lambda b: None)
    rescue_arg.setenv("DAIMON_LLM_FALLBACK", "0")
    assert llm.prompt_budget_bytes() is None


def test_budget_names_the_legacy_shim_setting_for_a_litellm_primary(monkeypatch):
    # A litellm primary with no explicit rescue falls back to DAIMON_LLM_COMMAND,
    # whose input mode is DAIMON_LLM_COMMAND_INPUT.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "fakerescue -p")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "arg")
    monkeypatch.setattr(llm, "_missing_binary", lambda b: None)
    assert llm.prompt_budget_bytes() == llm._ARG_MAX_BYTES
    assert llm.prompt_budget_setting() == "DAIMON_LLM_COMMAND_INPUT"


def test_budget_binding_setting_is_the_smaller_limit(rescue_arg):
    rescue_arg.setenv("DAIMON_LLM_COMMAND_INPUT", "arg")
    assert llm.prompt_budget_bytes() == llm._ARG_MAX_BYTES
    # ties go to the primary, which runs first
    assert llm.prompt_budget_setting() == "DAIMON_LLM_COMMAND_INPUT"
    limits = {x["role"]: x for x in llm.input_limits()}
    assert limits["rescue"]["setting"] == "DAIMON_LLM_COMMAND_FALLBACK_INPUT"
    assert limits["primary"]["limit"] == limits["rescue"]["limit"] == llm._ARG_MAX_BYTES


def test_prompt_bytes_measures_what_exec_measures():
    msgs = [{"role": "system", "content": "sys é"},
            {"role": "user", "content": "body"}]
    assert llm.prompt_bytes(msgs) == len(llm._flatten_messages(msgs).encode("utf-8"))


# ---- merge grouping: the tree gets deeper instead of failing ----

def test_merge_prompts_never_exceed_the_rescue_cap(chunked, rescue_arg):
    ms = _msgs(24)
    nchunks = len(serializer.chunk_transcript(serializer._render_transcript(ms), 4, 0))
    chat = _Script(lambda n, m: _blob_partial(30_000) if n <= nchunks else _good())
    serializer.serialize_strict("S1", ms, chat=chat)
    assert max(_prompt_sizes(chat)) <= llm._ARG_MAX_BYTES
    assert len(chat.calls) > nchunks + 1        # more than one merge: the tree got deeper


def test_merge_without_a_budget_keeps_the_configured_group_size(chunked):
    ms = _msgs(24)
    nchunks = len(serializer.chunk_transcript(serializer._render_transcript(ms), 4, 0))
    chat = _Script(lambda n, m: _blob_partial(30_000) if n <= nchunks else _good())
    serializer.serialize_strict("S1", ms, chat=chat)
    assert len(chat.calls) == nchunks + 1       # one merge of every partial, unchanged


def test_unfittable_merge_pair_fails_before_any_merge_call(chunked, rescue_arg):
    ms = _msgs(24)
    nchunks = len(serializer.chunk_transcript(serializer._render_transcript(ms), 4, 0))
    chat = _Script(lambda n, m: _blob_partial(60_000) if n <= nchunks else _good())
    with pytest.raises(serializer.PromptTooLargeError) as ei:
        serializer.serialize_strict("S1", ms, chat=chat)
    assert len(chat.calls) == nchunks            # no merge call was made
    msg = str(ei.value)
    assert "merge level 1" in msg
    assert "DAIMON_LLM_COMMAND_FALLBACK_INPUT" in msg
    assert isinstance(ei.value, serializer.LLMCallError)


# ---- chunk, single-pass and perspective prompts ----

def test_oversized_chunk_prompt_fails_before_any_backend_call(chunked, rescue_arg):
    ms = _msgs(8, content="z" * 60_000)         # 4 lines per chunk: ~240 KB
    chat = _Script(lambda n, m: _good())
    with pytest.raises(serializer.PromptTooLargeError) as ei:
        serializer.serialize_strict("S1", ms, chat=chat)
    assert chat.calls == []
    assert "chunk" in str(ei.value)
    assert "DAIMON_LLM_COMMAND_FALLBACK_INPUT" in str(ei.value)


def test_oversized_single_pass_prompt_fails_before_any_backend_call(monkeypatch, rescue_arg):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    ms = _msgs(4, content="z" * 60_000)
    chat = _Script(lambda n, m: _good())
    with pytest.raises(serializer.PromptTooLargeError) as ei:
        serializer.serialize_strict("S1", ms, chat=chat)
    assert chat.calls == []
    assert "transcript" in str(ei.value)


def test_oversized_perspective_pass_fails_before_any_backend_call(chunked, rescue_arg):
    ms = _msgs(8, content="z" * 60_000)
    chat = _Script(lambda n, m: _good())
    with pytest.raises(serializer.PromptTooLargeError) as ei:
        serializer.serialize_strict("S1", ms, chat=chat, escalate=True)
    assert chat.calls == []
    assert "perspective" in str(ei.value)


def test_a_small_prompt_is_not_blocked_by_the_budget(monkeypatch, rescue_arg):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    chat = _Script(lambda n, m: _good())
    serializer.serialize_strict("S1", _msgs(4), chat=chat)
    assert len(chat.calls) == 1


# ---- the doctor view ----

def test_configure_status_carries_the_chain_limits_from_llm(rescue_arg):
    st = configure.status()
    assert st["prompt_budget_bytes"] == llm.prompt_budget_bytes()
    assert st["prompt_limits"] == llm.input_limits()


def test_doctor_warns_when_a_backend_in_the_chain_is_in_arg_mode(rescue_arg, capsys):
    render._plain_configure(configure.status())
    out = capsys.readouterr().out
    assert "arg mode" in out
    assert "fakerescue" in out
    assert "DAIMON_LLM_COMMAND_FALLBACK_INPUT" in out
    assert str(llm._ARG_MAX_BYTES) in out


def test_doctor_is_silent_without_an_arg_mode_backend(monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "fakeprimary")
    monkeypatch.setattr(llm, "_missing_binary", lambda b: None)
    render._plain_configure(configure.status())
    assert "arg mode" not in capsys.readouterr().out
