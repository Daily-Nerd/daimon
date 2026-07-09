import io
import json
import logging
import os
import time
import urllib.error
import urllib.request

import pytest

from daimon_briefing import config, llm


@pytest.fixture
def llm_env(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BASE_URL", "http://127.0.0.1:9")  # nothing listens
    monkeypatch.setenv("DAIMON_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("DAIMON_LLM_MODEL", "test-model")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "0")  # disable fallback for existing tests


def _http_error(code, body: bytes):
    return urllib.error.HTTPError(
        url="http://127.0.0.1:9/v1/chat/completions",
        code=code,
        msg="err",
        hdrs=None,
        fp=io.BytesIO(body),
    )


def _ok_response(content):
    body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    return io.BytesIO(body)


def test_chat_temperature_resolves_from_config_when_none(llm_env, monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_TEMPERATURE", "1")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        return _ok_response("ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert captured["body"]["temperature"] == 1.0


def test_chat_temperature_default_is_zero_without_config(llm_env, monkeypatch):
    monkeypatch.delenv("DAIMON_LLM_TEMPERATURE", raising=False)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        return _ok_response("ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    llm.chat([{"role": "user", "content": "hi"}])
    assert captured["body"]["temperature"] == 0.0


def test_chat_explicit_temperature_beats_config(llm_env, monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_TEMPERATURE", "1")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        return _ok_response("ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    llm.chat([{"role": "user", "content": "hi"}], temperature=0.5)
    assert captured["body"]["temperature"] == 0.5


def _ok_response_with_usage(content, usage):
    body = json.dumps({"choices": [{"message": {"content": content}}], "usage": usage}).encode()
    return io.BytesIO(body)


def test_chat_logs_token_usage_when_present(llm_env, monkeypatch, caplog):
    # The API response carries a usage block; chat() must surface the cost as a
    # log line (the serializer otherwise discards it). Non-breaking: still returns
    # the content string.
    def fake_urlopen(req, timeout=None):
        return _ok_response_with_usage(
            "ok", {"total_tokens": 42, "prompt_tokens": 30, "completion_tokens": 12}
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with caplog.at_level(logging.INFO, logger="daimon_briefing.llm"):
        assert llm.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert any("total_tokens=42" in r.getMessage() for r in caplog.records), \
        "chat() should log token usage at INFO when the response includes a usage block"


def test_chat_without_usage_block_does_not_crash(llm_env, monkeypatch):
    # Older/strict upstreams may omit usage entirely — chat() must stay graceful.
    def fake_urlopen(req, timeout=None):
        return _ok_response("ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "ok"


def test_chat_error_suppresses_http_response_body(llm_env, monkeypatch):
    secret_body = b'{"error": "bad key sk-SECRET-LEAKED-VALUE"}'

    def fake_urlopen(req, timeout=None):
        raise _http_error(401, secret_body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(llm.ChatError) as exc:
        llm.chat([{"role": "user", "content": "hi"}])
    msg = str(exc.value)
    assert "sk-SECRET-LEAKED-VALUE" not in msg
    assert "401" in msg


def test_chat_deadline_exhausted_before_first_call(llm_env, monkeypatch):
    def fail_if_called(req, timeout=None):
        raise AssertionError("urlopen must not be called when deadline is exhausted")

    monkeypatch.setattr(urllib.request, "urlopen", fail_if_called)
    with pytest.raises(llm.ChatError) as exc:
        llm.chat(
            [{"role": "user", "content": "hi"}],
            deadline=time.monotonic() - 1,
        )
    assert "deadline" in str(exc.value).lower()


def test_chat_deadline_stops_retries_without_full_backoff(llm_env, monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    start = time.monotonic()
    with pytest.raises(llm.ChatError):
        llm.chat(
            [{"role": "user", "content": "hi"}],
            retries=3,
            deadline=time.monotonic() + 0.2,
        )
    # Without deadline awareness the backoff alone would sleep 3s+.
    assert time.monotonic() - start < 2.0


def test_chat_attempt_timeout_capped_by_deadline(llm_env, monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        raise _http_error(400, b"bad request")  # 4xx -> fail fast after one attempt

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(llm.ChatError):
        llm.chat(
            [{"role": "user", "content": "hi"}],
            timeout=300,
            deadline=time.monotonic() + 5,
        )
    assert seen["timeout"] <= 5


def test_chat_5xx_retries_log_warnings_without_body(llm_env, monkeypatch, caplog):
    # Transport retries must be visible (silent 502 loops made a doomed 40-min
    # run indistinguishable from a healthy one), but the response body must
    # NEVER reach the log — error payloads can echo request contents/secrets.
    import logging

    secret_body = b'{"error": "sk-SECRET-IN-BODY"}'
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _http_error(502, secret_body)
        return _ok_response("ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(time, "sleep", lambda s: None)  # skip the real backoff
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.llm"):
        assert llm.chat([{"role": "user", "content": "hi"}], retries=3) == "ok"

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2  # one per failed attempt, none for the success
    assert all("502" in m for m in warnings)
    assert all("sk-SECRET-IN-BODY" not in m for m in warnings)


def test_chat_no_cache_flag_sends_cache_bypass(llm_env, monkeypatch):
    # LiteLLM's exact-match response cache replays cached responses for
    # identical bodies — a cached empty response permanently pinned a chunk
    # (H1). DAIMON_LLM_NO_CACHE=1 must request a per-call bypass.
    monkeypatch.setenv("DAIMON_LLM_NO_CACHE", "1")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        return _ok_response("ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert captured["body"]["cache"] == {"no-cache": True}


def test_chat_default_body_has_no_cache_key(llm_env, monkeypatch):
    # Opt-in only: strict upstreams may reject unknown fields, so the default
    # body must not carry the cache key at all.
    monkeypatch.delenv("DAIMON_LLM_NO_CACHE", raising=False)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        return _ok_response("ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert "cache" not in captured["body"]


def test_run_command_pipes_stdin_and_captures(tmp_path):
    rc, out, err = llm._run_command(
        ["cat"], stdin_text="hello-stdin", timeout=10,
        env=dict(os.environ), cwd=str(tmp_path))
    assert rc == 0
    assert out == "hello-stdin"


def test_run_command_missing_binary_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        llm._run_command(["definitely-not-a-real-binary-xyz"], stdin_text="x",
                         timeout=10, env=dict(os.environ), cwd=str(tmp_path))


def test_flatten_messages():
    out = llm._flatten_messages([{"role": "system", "content": "rules"},
                                 {"role": "user", "content": "transcript"}])
    assert out == "SYSTEM:\nrules\n\nUSER:\ntranscript"


def test_extract_output_text_and_json():
    assert llm._extract_output("  hi \n", "text") == "hi"
    assert llm._extract_output('{"result":"ok","x":1}', "json:result") == "ok"


def test_resolve_command_prefers_explicit(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "mycli --flag")
    monkeypatch.delenv("DAIMON_LLM_COMMAND_OUTPUT", raising=False)
    monkeypatch.delenv("DAIMON_LLM_COMMAND_INPUT", raising=False)
    assert llm._resolve_command() == ("mycli --flag", "text", "stdin")


def test_resolve_command_claude_preset(monkeypatch):
    monkeypatch.delenv("DAIMON_LLM_COMMAND", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda b: "/usr/bin/claude")
    cmd, out, inp = llm._resolve_command()
    assert cmd.startswith("claude -p") and out == "json:result"
    assert inp == "stdin"  # #58: the claude-cli preset never changes off stdin


def test_resolve_command_none(monkeypatch):
    monkeypatch.delenv("DAIMON_LLM_COMMAND", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda b: None)
    assert llm._resolve_command() is None


def test_resolve_command_carries_explicit_input_spec(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "devin -p")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "file:--prompt-file")
    assert llm._resolve_command() == ("devin -p", "text", "file:--prompt-file")


def test_chat_command_runs_and_sets_disable_env(monkeypatch):
    seen = {}
    def fake_run(argv, stdin_text, timeout, env, cwd):
        seen["argv"], seen["stdin"], seen["env"], seen["cwd"] = argv, stdin_text, env, cwd
        return 0, '{"result":"CKPT"}', ""
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "claude -p --output-format json")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_OUTPUT", "json:result")
    monkeypatch.setattr(llm, "_run_command", fake_run)
    out = llm._chat_command([{"role": "user", "content": "hi"}], deadline=None)
    assert out == "CKPT"
    assert seen["argv"] == ["claude", "-p", "--output-format", "json"]
    assert seen["stdin"] == "USER:\nhi"
    assert seen["env"]["DAIMON_DISABLE"] == "1"
    assert os.path.isdir(seen["cwd"]) is False  # temp dir cleaned up after


# ---- #58: DAIMON_LLM_COMMAND_INPUT — stdin (default) | arg | file:<flag> ----


def test_chat_command_arg_mode_appends_prompt_as_final_argv_element(monkeypatch):
    # The prompt must land as ONE raw argv element — never string-interpolated
    # into the command template, so it can never reach a shell (matches
    # _run_command's never-touches-shell contract).
    seen = {}
    def fake_run(argv, stdin_text, timeout, env, cwd):
        seen["argv"], seen["stdin"] = argv, stdin_text
        return 0, "ok", ""
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "devin -p")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "arg")
    monkeypatch.setattr(llm, "_run_command", fake_run)
    out = llm._chat_command([{"role": "user", "content": "hi"}], deadline=None)
    assert out == "ok"
    assert seen["argv"] == ["devin", "-p", "USER:\nhi"]
    assert seen["stdin"] in ("", None)  # nothing piped in arg mode


def test_chat_command_file_mode_writes_0600_tempfile_inside_call_cwd(monkeypatch):
    seen = {}
    def fake_run(argv, stdin_text, timeout, env, cwd):
        seen["argv"], seen["stdin"], seen["cwd"] = argv, stdin_text, cwd
        # The file must exist and be readable WHILE the command runs.
        flag, path = argv[-2], argv[-1]
        seen["flag"] = flag
        seen["file_contents"] = open(path, encoding="utf-8").read()
        seen["file_mode"] = os.stat(path).st_mode & 0o777
        seen["file_in_cwd"] = os.path.dirname(path) == cwd
        return 0, "ok", ""
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "devin -p")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "file:--prompt-file")
    monkeypatch.setattr(llm, "_run_command", fake_run)
    out = llm._chat_command([{"role": "user", "content": "hi"}], deadline=None)
    assert out == "ok"
    assert seen["flag"] == "--prompt-file"
    assert seen["file_contents"] == "USER:\nhi"
    assert seen["file_mode"] == 0o600
    assert seen["file_in_cwd"] is True
    assert seen["argv"][:2] == ["devin", "-p"]


def test_apply_input_spec_file_flag_stripping_to_empty_degrades_to_stdin(tmp_path):
    # Defensive boundary: config.llm_command_input() normalizes
    # "file:<whitespace>" to "stdin" before _apply_input_spec ever sees it,
    # but a spec reaching this function directly (tests, future callers)
    # with a flag that strips to empty must degrade to stdin behavior —
    # argv untouched, prompt piped — not append an empty flag to argv.
    argv, stdin_text = llm._apply_input_spec(
        ["mycli", "-p"], "PROMPT", "file:   ", str(tmp_path))
    assert argv == ["mycli", "-p"]
    assert stdin_text == "PROMPT"


def test_chat_command_file_mode_strips_whitespace_around_flag(monkeypatch):
    # "file:  --prompt-file  " must not smuggle the padding into argv as
    # "  --prompt-file" — not an injection risk, but a silent misconfiguration
    # most CLIs won't match. The flag is stripped after extraction.
    seen = {}
    def fake_run(argv, stdin_text, timeout, env, cwd):
        seen["argv"] = argv
        return 0, "ok", ""
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "devin -p")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "file:  --prompt-file  ")
    monkeypatch.setattr(llm, "_run_command", fake_run)
    out = llm._chat_command([{"role": "user", "content": "hi"}], deadline=None)
    assert out == "ok"
    assert seen["argv"][-2] == "--prompt-file"  # clean flag, no padding


def test_chat_command_file_mode_cleaned_up_after_run(monkeypatch):
    seen = {}
    def fake_run(argv, stdin_text, timeout, env, cwd):
        seen["cwd"] = cwd
        return 0, "ok", ""
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "devin -p")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "file:--prompt-file")
    monkeypatch.setattr(llm, "_run_command", fake_run)
    llm._chat_command([{"role": "user", "content": "hi"}], deadline=None)
    assert os.path.isdir(seen["cwd"]) is False  # cwd (incl. tempfile) removed


def test_chat_command_file_mode_cleaned_up_after_timeout(monkeypatch):
    import subprocess as sp
    seen = {}
    def fake_run(argv, stdin_text, timeout, env, cwd):
        seen["cwd"] = cwd
        raise sp.TimeoutExpired(cmd=argv, timeout=timeout)
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "devin -p")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "file:--prompt-file")
    monkeypatch.setattr(llm, "_run_command", fake_run)
    with pytest.raises(llm.ChatError):
        llm._chat_command([{"role": "user", "content": "hi"}], deadline=None)
    assert os.path.isdir(seen["cwd"]) is False  # cleaned up even on timeout


def test_chat_command_stdin_default_unchanged(monkeypatch):
    seen = {}
    def fake_run(argv, stdin_text, timeout, env, cwd):
        seen["argv"], seen["stdin"] = argv, stdin_text
        return 0, "ok", ""
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "mycli -p")
    monkeypatch.delenv("DAIMON_LLM_COMMAND_INPUT", raising=False)
    monkeypatch.setattr(llm, "_run_command", fake_run)
    llm._chat_command([{"role": "user", "content": "hi"}], deadline=None)
    assert seen["argv"] == ["mycli", "-p"]  # nothing appended
    assert seen["stdin"] == "USER:\nhi"


def test_chat_command_arg_mode_over_arg_max_raises_chat_error_not_oserror(monkeypatch):
    # A raw OSError E2BIG from the kernel exec() call is opaque; arg-mode must
    # fail loud with a ChatError naming the limit before ever calling exec.
    def fail_if_called(*a, **k):
        raise AssertionError("_run_command must not be called over the ARG_MAX ceiling")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "devin -p")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "arg")
    monkeypatch.setattr(llm, "_run_command", fail_if_called)
    huge = [{"role": "user", "content": "x" * (llm._ARG_MAX_BYTES + 1)}]
    with pytest.raises(llm.ChatError) as exc:
        llm._chat_command(huge, deadline=None)
    msg = str(exc.value)
    assert str(llm._ARG_MAX_BYTES) in msg
    assert "file:" in msg or "stdin" in msg  # names the escape hatch


def test_chat_command_unknown_input_mode_falls_open_to_stdin(monkeypatch):
    # config.llm_command_input() already fails a bogus value open to "stdin"
    # (with a logged warning) — _chat_command must never see the raw bogus
    # string reach argv-building.
    seen = {}
    def fake_run(argv, stdin_text, timeout, env, cwd):
        seen["argv"], seen["stdin"] = argv, stdin_text
        return 0, "ok", ""
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "mycli -p")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "bogus-mode")
    monkeypatch.setattr(llm, "_run_command", fake_run)
    llm._chat_command([{"role": "user", "content": "hi"}], deadline=None)
    assert seen["argv"] == ["mycli", "-p"]
    assert seen["stdin"] == "USER:\nhi"


def test_chat_command_no_command_raises(monkeypatch):
    monkeypatch.delenv("DAIMON_LLM_COMMAND", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda b: None)
    with pytest.raises(llm.ChatError):
        llm._chat_command([{"role": "user", "content": "hi"}], deadline=None)


def test_chat_command_nonzero_exit_raises(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "mycli")
    monkeypatch.setattr(llm, "_run_command", lambda *a, **k: (1, "", "boom"))
    with pytest.raises(llm.ChatError) as e:
        llm._chat_command([{"role": "user", "content": "hi"}], deadline=None)
    assert "boom" not in str(e.value)   # stderr body never leaked


def test_chat_routes_to_command_backend(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setattr(llm, "_chat_command", lambda m, deadline: "FROM_CMD")
    called = {"litellm": False}
    monkeypatch.setattr(llm, "_chat_litellm", lambda *a, **k: called.__setitem__("litellm", True))
    assert llm.chat([{"role": "user", "content": "x"}]) == "FROM_CMD"
    assert called["litellm"] is False


def test_chat_litellm_falls_back_on_error(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    def boom(*a, **k):
        raise llm.ChatError("gateway down")
    monkeypatch.setattr(llm, "_chat_litellm", boom)
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("mycli", "text"))
    monkeypatch.setattr(llm, "_chat_command", lambda m, deadline: "FALLBACK")
    assert llm.chat([{"role": "user", "content": "x"}]) == "FALLBACK"


def test_chat_no_fallback_when_disabled(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "0")
    monkeypatch.setattr(llm, "_chat_litellm", lambda *a, **k: (_ for _ in ()).throw(llm.ChatError("down")))
    with pytest.raises(llm.ChatError):
        llm.chat([{"role": "user", "content": "x"}])


def test_chat_no_fallback_when_no_command(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")          # fallback enabled
    monkeypatch.setattr(llm, "_chat_litellm",
                        lambda *a, **k: (_ for _ in ()).throw(llm.ChatError("down")))
    monkeypatch.setattr(llm, "_resolve_command", lambda: None)   # but nothing resolves
    with pytest.raises(llm.ChatError):
        llm.chat([{"role": "user", "content": "x"}])


def test_chat_auto_uses_litellm_when_key_present(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "auto")
    monkeypatch.setattr(config, "llm_api_key", lambda: "sk-key")
    monkeypatch.setattr(llm, "_chat_litellm", lambda *a, **k: "FROM_LITELLM")
    monkeypatch.setattr(llm, "_chat_command", lambda m, deadline: "FROM_CMD")
    assert llm.chat([{"role": "user", "content": "x"}]) == "FROM_LITELLM"


def test_chat_auto_uses_command_when_no_key_and_cli(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "auto")
    monkeypatch.setattr(config, "llm_api_key", lambda: None)
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("mycli", "text"))
    monkeypatch.setattr(llm, "_chat_command", lambda m, deadline: "FROM_CMD")
    monkeypatch.setattr(llm, "_chat_litellm",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call litellm")))
    assert llm.chat([{"role": "user", "content": "x"}]) == "FROM_CMD"


def test_chat_auto_falls_to_litellm_when_nothing(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "auto")
    monkeypatch.setattr(config, "llm_api_key", lambda: None)
    monkeypatch.setattr(llm, "_resolve_command", lambda: None)
    monkeypatch.setattr(llm, "_chat_litellm",
                        lambda *a, **k: (_ for _ in ()).throw(llm.ChatError("No LLM API key")))
    with pytest.raises(llm.ChatError):
        llm.chat([{"role": "user", "content": "x"}])


# ---- #28 S6: fallback must be observable, not just logged to a dead-drop ----


def test_chat_fallback_sets_flag(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    def boom(*a, **k):
        raise llm.ChatError("gateway down")
    monkeypatch.setattr(llm, "_chat_litellm", boom)
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("mycli", "text"))
    monkeypatch.setattr(llm, "_chat_command", lambda m, deadline: "FALLBACK")
    llm.reset_fallback()
    assert llm.fallback_used() is False
    llm.chat([{"role": "user", "content": "x"}])
    assert llm.fallback_used() is True


def test_chat_direct_success_leaves_flag_clear(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setattr(llm, "_chat_litellm", lambda *a, **k: "OK")
    llm.reset_fallback()
    llm.chat([{"role": "user", "content": "x"}])
    assert llm.fallback_used() is False


# ---- #56: command-backend stderr lands locally; never guessed at again ----


def test_command_backend_failure_writes_stderr_log(monkeypatch, tmp_path):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "failing-cli")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(llm, "_run_command",
                        lambda *a, **k: (101, "", "panic: no prompt provided"))
    with pytest.raises(llm.ChatError) as exc:
        llm.chat([{"role": "user", "content": "hola"}])
    assert "backend-stderr.log" in str(exc.value)
    assert "suppressed" not in str(exc.value)
    log = tmp_path / "logs" / "backend-stderr.log"
    assert "panic: no prompt provided" in log.read_text()
    assert "exit 101" in log.read_text()


def test_command_backend_stderr_log_redacts_secret(monkeypatch, tmp_path):
    # #141: CLI backends can echo prompt fragments (transcript text) into
    # stderr on failure — the local stderr log is a disk artifact and must be
    # scrubbed like every other write site.
    secret = "AKIAIOSFODNN7EXAMPLE"
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "failing-cli")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(llm, "_run_command",
                        lambda *a, **k: (1, "", f"prompt was: key {secret}"))
    with pytest.raises(llm.ChatError):
        llm.chat([{"role": "user", "content": "hola"}])
    text = (tmp_path / "logs" / "backend-stderr.log").read_text()
    assert secret not in text
    assert "[redacted:aws-key]" in text


def test_command_backend_stderr_log_truncates_per_run(monkeypatch, tmp_path):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "failing-cli")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(llm, "_run_command", lambda *a, **k: (1, "", "first"))
    with pytest.raises(llm.ChatError):
        llm.chat([{"role": "user", "content": "x"}])
    monkeypatch.setattr(llm, "_run_command", lambda *a, **k: (1, "", "second"))
    with pytest.raises(llm.ChatError):
        llm.chat([{"role": "user", "content": "x"}])
    text = (tmp_path / "logs" / "backend-stderr.log").read_text()
    assert "second" in text and "first" not in text
