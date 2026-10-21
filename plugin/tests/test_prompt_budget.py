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


# ---- llm: two limits, one per role ----

def _limit(x):
    return None if x is None else (x["limit"], x["setting"])


def test_no_limits_with_no_arg_mode_anywhere(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "fakeprimary")
    monkeypatch.setattr(llm, "_missing_binary", lambda b: None)
    assert llm.primary_prompt_limit() is None
    assert llm.rescue_prompt_limit() is None


def test_file_mode_rescue_has_no_limit(rescue_arg):
    rescue_arg.setenv("DAIMON_LLM_COMMAND_FALLBACK_INPUT", "file:--prompt-file")
    assert llm.rescue_prompt_limit() is None


def test_arg_mode_rescue_is_a_rescue_limit_only(rescue_arg):
    assert llm.primary_prompt_limit() is None
    assert _limit(llm.rescue_prompt_limit()) == (
        llm._ARG_MAX_BYTES, "DAIMON_LLM_COMMAND_FALLBACK_INPUT")


def test_arg_mode_primary_is_a_primary_limit_only(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "fakeprimary")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "arg")
    monkeypatch.setattr(llm, "_missing_binary", lambda b: None)
    assert _limit(llm.primary_prompt_limit()) == (
        llm._ARG_MAX_BYTES, "DAIMON_LLM_COMMAND_INPUT")
    assert llm.rescue_prompt_limit() is None


def test_rescue_limit_ignores_a_rescue_that_cannot_run(rescue_arg):
    rescue_arg.setattr(llm, "_missing_binary", lambda b: "fakerescue")
    assert llm.rescue_prompt_limit() is None
    rescue_arg.setattr(llm, "_missing_binary", lambda b: None)
    rescue_arg.setenv("DAIMON_LLM_FALLBACK", "0")
    assert llm.rescue_prompt_limit() is None


def test_legacy_shim_rescue_names_the_command_input_setting(monkeypatch):
    # A litellm primary with no explicit rescue falls back to DAIMON_LLM_COMMAND,
    # whose input mode is DAIMON_LLM_COMMAND_INPUT.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "fakerescue -p")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "arg")
    monkeypatch.setattr(llm, "_missing_binary", lambda b: None)
    assert llm.primary_prompt_limit() is None
    assert _limit(llm.rescue_prompt_limit()) == (
        llm._ARG_MAX_BYTES, "DAIMON_LLM_COMMAND_INPUT")


def test_both_arg_each_role_names_its_own_setting(rescue_arg):
    rescue_arg.setenv("DAIMON_LLM_COMMAND_INPUT", "arg")
    assert llm.primary_prompt_limit()["setting"] == "DAIMON_LLM_COMMAND_INPUT"
    assert llm.rescue_prompt_limit()["setting"] == "DAIMON_LLM_COMMAND_FALLBACK_INPUT"


def test_resolved_triple_stays_a_plain_triple_and_carries_its_setting(rescue_arg):
    fb = llm._resolve_fallback_command()
    cmd, out, inp = fb
    assert (cmd, out, inp) == ("fakerescue -p", "text", "arg")
    assert fb == ("fakerescue -p", "text", "arg")
    assert llm._setting_of(fb) == "DAIMON_LLM_COMMAND_FALLBACK_INPUT"
    assert llm._setting_of(llm._resolve_command()) == "DAIMON_LLM_COMMAND_INPUT"


def test_prompt_bytes_measures_what_exec_measures():
    msgs = [{"role": "system", "content": "sys \u00e9"},
            {"role": "user", "content": "body"}]
    assert llm.prompt_bytes(msgs) == len(llm._flatten_messages(msgs).encode("utf-8"))


def _rescue_spy(monkeypatch):
    """Primary fails (empty rc=1); records every exec so a rescue exec shows."""
    execs = []

    def run(argv, stdin, timeout, env, cwd):
        execs.append(argv[0])
        return 1, "", "boom"
    monkeypatch.setattr(llm, "_run_command", run)
    return execs


# ---- the regression: a rescue must not break a run the primary can serve ----

def test_primary_stdin_big_chunk_proceeds_without_a_rescue(chunked, rescue_arg, caplog):
    ms = _msgs(8, content="z" * 60_000)         # ~240 KB per chunk prompt
    chat = _Script(lambda n, m: _good())
    with caplog.at_level("WARNING"):
        serializer.serialize_strict("S1", ms, chat=chat)
    assert chat.calls                            # the primary served the chunks
    warned = [r.getMessage() for r in caplog.records
              if "runs without a rescue" in r.getMessage()]
    assert any("chunk 1 of" in w and "DAIMON_LLM_COMMAND_FALLBACK_INPUT" in w
               for w in warned)


def test_single_pass_and_perspective_proceed_without_a_rescue(monkeypatch, rescue_arg, caplog):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    chat = _Script(lambda n, m: _good())
    with caplog.at_level("WARNING"):
        serializer.serialize_strict("S1", _msgs(4, content="z" * 60_000), chat=chat)
    assert len(chat.calls) == 1
    assert "transcript: prompt is" in caplog.text


def test_perspective_pass_proceeds_without_a_rescue(chunked, rescue_arg, caplog):
    chat = _Script(lambda n, m: _good())
    with caplog.at_level("WARNING"):
        serializer.serialize_strict("S1", _msgs(8, content="z" * 60_000),
                                    chat=chat, escalate=True)
    assert "perspective" in caplog.text and "runs without a rescue" in caplog.text


def test_no_rescue_exec_for_a_call_the_rescue_would_refuse(rescue_arg):
    execs = _rescue_spy(rescue_arg)
    big = "z" * 120_000

    def chat(messages, **kw):
        return llm._chat_command(messages, kw.get("deadline"))
    rescue_arg.setattr(llm, "rescue_unparseable",
                       lambda *a, **k: pytest.fail("rescue must not run"))
    with pytest.raises(serializer.LLMCallError):
        serializer._call_and_parse(chat, "sys", big, None, "chunk 1 of 1")
    assert execs == ["fakeprimary"]


def test_unparseable_output_skips_a_rescue_that_would_refuse(rescue_arg):
    rescue_arg.setattr(llm, "rescue_unparseable",
                       lambda *a, **k: pytest.fail("rescue must not run"))
    calls = []

    def prose(messages, **kw):
        calls.append(1)
        return "not json"
    with pytest.raises(serializer.OutputParseError):
        serializer._call_and_parse(prose, "sys", "z" * 120_000, None, "chunk 1 of 1")
    assert len(calls) == 2                       # primary + its one retry only


def test_a_prompt_the_rescue_can_serve_still_reaches_the_rescue(rescue_arg):
    seen = []
    rescue_arg.setattr(llm, "rescue_unparseable",
                       lambda msgs, dl: seen.append(1) or json.dumps(_good()))
    out = serializer._call_and_parse(lambda m, **k: "not json", "sys", "small",
                                     None, "chunk 1 of 1")
    assert seen and out["working_context"]


# ---- primary arg: a hard cap on every prompt ----

@pytest.fixture
def primary_arg(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "fakeprimary")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "arg")
    monkeypatch.setattr(llm, "_missing_binary", lambda b: None)
    return monkeypatch


def test_oversized_chunk_prompt_fails_before_any_call_with_a_primary_cap(chunked, primary_arg):
    chat = _Script(lambda n, m: _good())
    with pytest.raises(serializer.PromptTooLargeError) as ei:
        serializer.serialize_strict("S1", _msgs(8, content="z" * 60_000), chat=chat)
    assert chat.calls == []
    assert "chunk" in str(ei.value)
    assert "DAIMON_LLM_COMMAND_INPUT" in str(ei.value)
    assert "FALLBACK" not in str(ei.value)
    assert isinstance(ei.value, serializer.LLMCallError)


def test_oversized_single_pass_and_perspective_fail_up_front(monkeypatch, primary_arg):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    chat = _Script(lambda n, m: _good())
    with pytest.raises(serializer.PromptTooLargeError) as ei:
        serializer.serialize_strict("S1", _msgs(4, content="z" * 60_000), chat=chat)
    assert "transcript" in str(ei.value)
    monkeypatch.setenv("DAIMON_CHUNK_LINES", "4")
    with pytest.raises(serializer.PromptTooLargeError) as ei:
        serializer.serialize_strict("S1", _msgs(8, content="z" * 60_000),
                                    chat=chat, escalate=True)
    assert "perspective" in str(ei.value)
    assert chat.calls == []


def test_a_small_prompt_is_not_blocked_by_either_limit(monkeypatch, rescue_arg):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    chat = _Script(lambda n, m: _good())
    serializer.serialize_strict("S1", _msgs(4), chat=chat)
    assert len(chat.calls) == 1


# ---- merge grouping: the tree gets deeper instead of failing ----

def _six_chunk_session(blob):
    ms = _msgs(24)
    n = len(serializer.chunk_transcript(serializer._render_transcript(ms), 4, 0))
    chat = _Script(lambda i, m: _blob_partial(blob) if i <= n else _good())
    return ms, n, chat


def test_merge_prompts_never_exceed_the_rescue_cap(chunked, rescue_arg):
    ms, n, chat = _six_chunk_session(30_000)
    serializer.serialize_strict("S1", ms, chat=chat)
    assert max(llm.prompt_bytes(c) for c in chat.calls[n:]) <= llm._ARG_MAX_BYTES
    assert len(chat.calls) > n + 1               # more than one merge: deeper tree


def test_merge_without_a_limit_keeps_the_configured_group_size(chunked):
    ms, n, chat = _six_chunk_session(30_000)
    serializer.serialize_strict("S1", ms, chat=chat)
    assert len(chat.calls) == n + 1


def test_merge_with_no_pair_under_the_rescue_cap_runs_under_the_primary_cap(
        chunked, rescue_arg, caplog):
    # Primary is unbounded: pairs of 60 KB partials do not fit the rescue cap
    # but the primary serves them, so the level proceeds and says so.
    ms, n, chat = _six_chunk_session(60_000)
    with caplog.at_level("WARNING"):
        serializer.serialize_strict("S1", ms, chat=chat)
    assert len(chat.calls) > n                   # merges ran
    assert "merge level 1" in caplog.text and "runs without a rescue" in caplog.text


def test_merge_groups_fit_a_primary_cap_alone(chunked, primary_arg):
    ms, n, chat = _six_chunk_session(30_000)
    serializer.serialize_strict("S1", ms, chat=chat)
    assert max(llm.prompt_bytes(c) for c in chat.calls[n:]) <= llm._ARG_MAX_BYTES


def test_no_pair_under_the_primary_cap_fails_before_any_merge_call(chunked, primary_arg):
    ms, n, chat = _six_chunk_session(60_000)
    with pytest.raises(serializer.PromptTooLargeError) as ei:
        serializer.serialize_strict("S1", ms, chat=chat)
    assert len(chat.calls) == n                  # no merge call was made
    assert "merge level 1" in str(ei.value)
    assert "DAIMON_LLM_COMMAND_INPUT" in str(ei.value)


def test_every_merge_level_shrinks_or_raises(chunked, rescue_arg, monkeypatch):
    # Termination: with partials too big for any pair, the level is grouped
    # under the (absent) primary cap, i.e. by count, and the list shrinks.
    partials = [_blob_partial(60_000) for _ in range(5)]
    groups = serializer._merge_groups("S1", partials, 3, "", 1)
    assert any(len(g) > 1 for g in groups)
    assert [p for g in groups for p in g] == partials   # order kept


# ---- the doctor view ----

def test_configure_status_carries_the_limits_from_llm(rescue_arg):
    st = configure.status()
    assert st["prompt_limits"] == llm.input_limits()
    assert st["prompt_hard_limit"] is None
    assert st["prompt_rescue_limit"] == llm.rescue_prompt_limit()["limit"]


def test_doctor_says_what_an_arg_mode_rescue_means(rescue_arg, capsys):
    render._plain_configure(configure.status())
    out = capsys.readouterr().out
    assert "rescue command (fakerescue -p) is in arg mode" in out
    assert "merges are sized to 100000 bytes" in out
    assert "without a rescue" in out
    assert "DAIMON_LLM_COMMAND_FALLBACK_INPUT" in out


def test_doctor_says_what_an_arg_mode_primary_means(primary_arg, capsys):
    render._plain_configure(configure.status())
    out = capsys.readouterr().out
    assert "primary command (fakeprimary) is in arg mode" in out
    assert "fails before any call" in out
    assert "DAIMON_LLM_COMMAND_INPUT" in out


def test_doctor_is_silent_without_an_arg_mode_backend(monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "fakeprimary")
    monkeypatch.setattr(llm, "_missing_binary", lambda b: None)
    render._plain_configure(configure.status())
    assert "arg mode" not in capsys.readouterr().out
