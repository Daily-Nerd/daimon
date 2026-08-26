import http.server
import io
import json
import logging
import os
import re
import threading
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


# ---- #298: urlopen's `timeout=` bounds a single blocking socket op, not the
# call as a whole — a response that keeps delivering bytes never trips it, so
# a single call can run past `deadline` while returning "success". These three
# tests use a REAL socket (no mocked urlopen): the bug lives in the socket
# layer, so a fake chat callable can't exercise it.


class _TrickleHandler(http.server.BaseHTTPRequestHandler):
    """Drips a chunked HTTP/1.1 body a few bytes at a time, each gap well
    under the per-call socket timeout the test passes — proving any total
    bound comes from `deadline`, not from urlopen's own `timeout=`."""

    protocol_version = "HTTP/1.1"
    body = b""
    chunk_size = 1
    gap = 0.05

    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for i in range(0, len(self.body), self.chunk_size):
            piece = self.body[i:i + self.chunk_size]
            time.sleep(self.gap)
            self.wfile.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def log_message(self, *a):
        pass


def _start_trickle_server(body: bytes, chunk_size: int = 1, gap: float = 0.05):
    handler = type("_Handler", (_TrickleHandler,), {
        "body": body, "chunk_size": chunk_size, "gap": gap,
    })
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_chat_mid_response_deadline_raises_before_trickle_completes(llm_env, monkeypatch):
    # 30 chunks x 0.05s gap = ~1.5s to fully drain, each gap far under the 5s
    # socket timeout below, so urlopen's own timeout never fires. A 0.15s
    # deadline must still cut this off well short of the full trickle.
    srv = _start_trickle_server(b"x" * 30, chunk_size=1, gap=0.05)
    try:
        monkeypatch.setenv(
            "DAIMON_LLM_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}")
        start = time.monotonic()
        with pytest.raises(llm.ChatError) as exc:
            llm.chat(
                [{"role": "user", "content": "hi"}],
                timeout=5,
                retries=1,
                deadline=time.monotonic() + 0.15,
            )
        elapsed = time.monotonic() - start
        assert "deadline" in str(exc.value).lower()
        assert elapsed < 1.0, (
            f"took {elapsed:.2f}s — deadline did not bound an in-flight response")
    finally:
        srv.shutdown()


def test_chat_response_completing_inside_deadline_still_succeeds(llm_env, monkeypatch):
    # Same trickling transport, but the deadline comfortably outlives it —
    # the chunked read must still reassemble the body correctly and return
    # the parsed content, unchanged from a single-shot read.
    payload = json.dumps(
        {"choices": [{"message": {"content": "trickled-ok"}}]}).encode()
    srv = _start_trickle_server(payload, chunk_size=4, gap=0.02)
    try:
        monkeypatch.setenv(
            "DAIMON_LLM_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}")
        result = llm.chat(
            [{"role": "user", "content": "hi"}],
            timeout=5,
            deadline=time.monotonic() + 5,
        )
        assert result == "trickled-ok"
    finally:
        srv.shutdown()


def test_chat_no_deadline_reads_slow_response_to_completion(llm_env, monkeypatch):
    # deadline=None must keep meaning "no total budget" — a slow-but-finite
    # response is still read fully and returned, never cut short mid-flight.
    payload = json.dumps(
        {"choices": [{"message": {"content": "no-deadline-ok"}}]}).encode()
    srv = _start_trickle_server(payload, chunk_size=4, gap=0.02)
    try:
        monkeypatch.setenv(
            "DAIMON_LLM_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}")
        result = llm.chat([{"role": "user", "content": "hi"}], timeout=5)
        assert result == "no-deadline-ok"
    finally:
        srv.shutdown()


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
    # #546: the preset is the IMPLEMENTATION of the claude-cli backend, so it
    # resolves when the operator named that backend.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "claude-cli")
    monkeypatch.delenv("DAIMON_LLM_COMMAND", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda b: "/usr/bin/claude")
    cmd, out, inp = llm._resolve_command()
    assert cmd.startswith("claude -p") and out == "json:result"
    assert inp == "stdin"  # #58: the claude-cli preset never changes off stdin


# ---- #546: PATH presence is not consent.
#
# _resolve_command() handed the full transcript to whatever `claude` happened
# to be first on PATH, with no opt-in — while the sibling axes (how the prompt
# is delivered, how output is parsed) both REQUIRE explicit configuration. The
# asymmetry was the tell: everything about the command was configurable except
# WHICH BINARY RECEIVES THE TRANSCRIPT.
#
# The split: DAIMON_LLM_BACKEND=claude-cli is a named, deliberate choice and
# keeps its zero-config preset. `auto` and the litellm rescue are not choices
# at all — PATH picked for the operator — and now require naming the command.


def test_resolve_command_auto_backend_does_not_adopt_a_path_claude(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "auto")
    monkeypatch.delenv("DAIMON_LLM_COMMAND", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda b: "/usr/bin/claude")
    assert llm._resolve_command() is None


def test_resolve_command_command_backend_requires_naming_the_command(monkeypatch):
    # Picking `command` without saying which command is under-specified; the
    # wizard asks for it. claude-cli is the zero-config alias for that intent.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.delenv("DAIMON_LLM_COMMAND", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda b: "/usr/bin/claude")
    assert llm._resolve_command() is None


def test_resolve_command_explicit_command_still_wins_on_any_backend(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "auto")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "mycli")
    monkeypatch.delenv("DAIMON_LLM_COMMAND_OUTPUT", raising=False)
    monkeypatch.delenv("DAIMON_LLM_COMMAND_INPUT", raising=False)
    assert llm._resolve_command() == ("mycli", "text", "stdin")


def test_auto_cascade_no_longer_resolves_to_command_via_path_alone(monkeypatch):
    # The (b) population: `auto`, no key, a claude on PATH. Previously this
    # silently became a command backend. Now it reaches litellm's own
    # helpful no-key error instead of a binary nobody named.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "auto")
    monkeypatch.delenv("DAIMON_LLM_COMMAND", raising=False)
    monkeypatch.setattr(config, "llm_api_key", lambda: None)
    monkeypatch.setattr(llm.shutil, "which", lambda b: "/usr/bin/claude")
    assert llm.resolve_backend() == {"backend": "litellm", "source": "auto-none"}


def test_litellm_rescue_no_longer_adopts_a_path_claude(monkeypatch):
    # The (c) population: a gateway primary quietly rescued by whatever claude
    # was on PATH. The rescue must now be named.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.delenv("DAIMON_LLM_COMMAND", raising=False)
    monkeypatch.delenv("DAIMON_LLM_COMMAND_FALLBACK", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda b: "/usr/bin/claude")
    assert llm._resolve_fallback_command() is None


def test_claude_cli_backend_keeps_its_zero_config_rescue_shim(monkeypatch):
    # claude-cli is a named choice, so a litellm primary explicitly rescued by
    # DAIMON_LLM_COMMAND still works — the shim is unaffected by #546.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "legacycli")
    monkeypatch.delenv("DAIMON_LLM_COMMAND_FALLBACK", raising=False)
    assert llm._resolve_fallback_command() == ("legacycli", "text", "stdin")


def test_chat_command_error_names_the_consent_fix(monkeypatch):
    # The (b) break must be loud and actionable, never a silent stop.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.delenv("DAIMON_LLM_COMMAND", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda b: "/usr/bin/claude")
    with pytest.raises(llm.ChatError) as e:
        llm._chat_command([{"role": "user", "content": "x"}], deadline=None)
    msg = str(e.value)
    assert "DAIMON_LLM_COMMAND" in msg
    assert "claude-cli" in msg  # names the zero-config route too


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
    monkeypatch.setattr(llm, "_chat_command", lambda m, deadline, resolved=None: "FROM_CMD")
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
    monkeypatch.setattr(llm, "_missing_binary", lambda c: None)  # #747: binary "exists"
    monkeypatch.setattr(llm, "_chat_command", lambda m, deadline, resolved=None: "FALLBACK")
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
    monkeypatch.setattr(llm, "_chat_command", lambda m, deadline, resolved=None: "FROM_CMD")
    assert llm.chat([{"role": "user", "content": "x"}]) == "FROM_LITELLM"


def test_chat_auto_uses_command_when_no_key_and_cli(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "auto")
    monkeypatch.setattr(config, "llm_api_key", lambda: None)
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("mycli", "text"))
    monkeypatch.setattr(llm, "_chat_command", lambda m, deadline, resolved=None: "FROM_CMD")
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


# ---- #475 slice 1: resolve_backend() — single source of truth for dispatch.
# chat() re-implemented the "auto" cascade inline; rescue-posture code (slice
# 2) needs the same decision. Two copies of one decision drift the moment
# either changes — extract it once, make chat() USE it, and pin the agreement
# with a test that can never let them diverge again.


def test_resolve_backend_explicit_command(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    assert llm.resolve_backend() == {"backend": "command", "source": "explicit"}


def test_resolve_backend_explicit_claude_cli(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "claude-cli")
    assert llm.resolve_backend() == {"backend": "claude-cli", "source": "explicit"}


def test_resolve_backend_explicit_litellm(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    assert llm.resolve_backend() == {"backend": "litellm", "source": "explicit"}


def test_resolve_backend_auto_key_present(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "auto")
    monkeypatch.setattr(config, "llm_api_key", lambda: "sk-key")
    assert llm.resolve_backend() == {"backend": "litellm", "source": "auto-key"}


def test_resolve_backend_auto_no_key_command_resolves(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "auto")
    monkeypatch.setattr(config, "llm_api_key", lambda: None)
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("mycli", "text", "stdin"))
    assert llm.resolve_backend() == {"backend": "command", "source": "auto-command"}


def test_resolve_backend_auto_no_key_no_command(monkeypatch):
    # The "auto-none" branch: chat() still picks litellm here (so
    # _chat_litellm raises its helpful no-key error), but `source` marks it
    # distinctly from a real litellm install — do not collapse it.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "auto")
    monkeypatch.setattr(config, "llm_api_key", lambda: None)
    monkeypatch.setattr(llm, "_resolve_command", lambda: None)
    assert llm.resolve_backend() == {"backend": "litellm", "source": "auto-none"}


def _dispatch_family(backend: str) -> str:
    """chat() branches on `backend in ("command", "claude-cli")`, sending
    both onto the SAME function (_chat_command) — the drift test below can
    only observe which underlying function actually ran, not the distinct
    label, so it buckets resolve_backend()'s output the same way chat() does
    before comparing."""
    return "command" if backend in ("command", "claude-cli") else "litellm"


@pytest.mark.parametrize(
    "backend_env, api_key, command_resolves",
    [
        ("command", None, None),
        ("claude-cli", None, None),
        ("litellm", "sk-test", None),
        ("auto", "sk-key", None),
        ("auto", None, ("mycli", "text", "stdin")),
        ("auto", None, None),
    ],
    ids=[
        "explicit-command", "explicit-claude-cli", "explicit-litellm",
        "auto-key", "auto-command", "auto-none",
    ],
)
def test_resolve_backend_agrees_with_chat_dispatch(
    monkeypatch, backend_env, api_key, command_resolves
):
    """The agreement test (#475 design doc, test 8): chat()'s real dispatch
    must never disagree with resolve_backend(). Stub both backend
    implementations to record which one chat() actually invokes, then assert
    it matches resolve_backend() — this is what makes the two-copies-drift
    class structurally untestable-to-break."""
    monkeypatch.setenv("DAIMON_LLM_BACKEND", backend_env)
    monkeypatch.setattr(config, "llm_api_key", lambda: api_key)
    monkeypatch.setattr(llm, "_resolve_command", lambda: command_resolves)
    invoked = {"backend": None}

    def fake_litellm(*a, **k):
        invoked["backend"] = "litellm"
        return "FROM_LITELLM"

    def fake_command(m, deadline, resolved=None):
        invoked["backend"] = "command"
        return "FROM_CMD"

    monkeypatch.setattr(llm, "_chat_litellm", fake_litellm)
    monkeypatch.setattr(llm, "_chat_command", fake_command)

    llm.chat([{"role": "user", "content": "x"}])

    assert invoked["backend"] == _dispatch_family(llm.resolve_backend()["backend"])


# ---- #475 slice 2: rescue_posture() — whether a rescue path exists for the
# CURRENT configuration, derived from resolve_backend() (never re-derived).


def test_rescue_posture_no_backend(monkeypatch):
    # resolve_backend()'s auto-none branch: nothing resolves at all. Must read
    # as "no-backend", never "gap" — a machine with no LLM configured at all
    # should not nag about a missing fallback.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "auto")
    monkeypatch.setattr(config, "llm_api_key", lambda: None)
    monkeypatch.setattr(llm, "_resolve_command", lambda: None)
    assert llm.rescue_posture() == "no-backend"


def test_rescue_posture_no_backend_for_explicit_litellm_without_a_key(monkeypatch):
    # The SAME reality as the auto-none case, reached by a different route.
    # Keying "no-backend" on resolve_backend()'s source alone reported this
    # config as "gap" — telling an operator to install a fallback when what
    # they actually lack is an API key, and flipping rescue_gap's answer on a
    # real configuration. The condition is "no credentials AND no command",
    # not "how did we get here".
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setattr(config, "llm_api_key", lambda: None)
    monkeypatch.setattr(llm, "_resolve_command", lambda: None)
    assert llm.rescue_posture() == "no-backend"


def test_rescue_posture_keyless_litellm_with_a_command_is_covered(monkeypatch):
    # No key, but a command resolves and fallback is on: every call fails over
    # to the command and succeeds. That is a rescue path, not an absent
    # backend — the no-backend test must require BOTH halves to be missing.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setattr(config, "llm_api_key", lambda: None)
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("mycli", "text", "stdin"))
    monkeypatch.setattr(llm.shutil, "which", lambda b: f"/usr/bin/{b}")  # #747
    assert llm.rescue_posture() == "covered"


def test_rescue_posture_none_for_explicit_command_backend(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    assert llm.rescue_posture() == "none"


def test_rescue_posture_none_for_explicit_claude_cli_backend(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "claude-cli")
    assert llm.rescue_posture() == "none"


def test_rescue_posture_command_primary_stays_none_even_with_fallback_and_key(monkeypatch):
    # Test 3 of the design's plan: flags cannot conjure a rescue direction
    # that does not exist. A `command` primary has no fallback direction by
    # construction, regardless of DAIMON_LLM_FALLBACK or an available litellm
    # key.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setattr(config, "llm_api_key", lambda: "sk-key")
    assert llm.rescue_posture() == "none"


def test_rescue_posture_disabled(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setattr(config, "llm_api_key", lambda: "sk-key")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "0")
    assert llm.rescue_posture() == "disabled"


def test_rescue_posture_covered(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setattr(config, "llm_api_key", lambda: "sk-key")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("mycli", "text", "stdin"))
    monkeypatch.setattr(llm.shutil, "which", lambda b: f"/usr/bin/{b}")  # #747
    assert llm.rescue_posture() == "covered"


def test_rescue_posture_gap(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setattr(config, "llm_api_key", lambda: "sk-key")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setattr(llm, "_resolve_command", lambda: None)
    assert llm.rescue_posture() == "gap"


def test_rescue_posture_unknown_backend_string_joins_litellm_family(monkeypatch):
    # chat() treats anything not in ("command", "claude-cli") as the litellm
    # branch (config.llm_backend() is free text) — posture must mirror that
    # exactly: no crash, no sixth state, same litellm-family resolution.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "bogus")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    # A key must be present to exercise the FAMILY question. Without one the
    # honest answer is "no-backend" regardless of which family the string
    # joins, and this test would be asking two questions at once.
    monkeypatch.setattr(config, "llm_api_key", lambda: "sk-key")
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("mycli", "text", "stdin"))
    monkeypatch.setattr(llm.shutil, "which", lambda b: f"/usr/bin/{b}")  # #747
    assert llm.rescue_posture() == "covered"
    monkeypatch.setattr(llm, "_resolve_command", lambda: None)
    assert llm.rescue_posture() == "gap"
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "0")
    assert llm.rescue_posture() == "disabled"


# ---- #28 S6: fallback must be observable, not just logged to a dead-drop ----


def test_chat_fallback_sets_flag(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    def boom(*a, **k):
        raise llm.ChatError("gateway down")
    monkeypatch.setattr(llm, "_chat_litellm", boom)
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("mycli", "text"))
    monkeypatch.setattr(llm, "_missing_binary", lambda c: None)  # #747: binary "exists"
    monkeypatch.setattr(llm, "_chat_command", lambda m, deadline, resolved=None: "FALLBACK")
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


def test_command_backend_stderr_log_appends_across_runs(monkeypatch, tmp_path):
    # #474 REVERSES the #56 truncate-per-run rule: on a field install 143
    # lifetime errors left exactly one line, so a diagnostic question stayed
    # open for nine days while every failure erased the evidence for the last.
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
    assert "first" in text and "second" in text
    assert text.index("first") < text.index("second")  # chronological


def test_command_backend_stderr_log_entries_are_utc_stamped(monkeypatch, tmp_path):
    # #474: entries have to be correlatable with serialize.log and checkpoint
    # ages, which are UTC `%Y-%m-%dT%H:%M:%SZ`.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "failing-cli")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(llm, "_run_command", lambda *a, **k: (1, "", "boom"))
    with pytest.raises(llm.ChatError):
        llm.chat([{"role": "user", "content": "x"}])
    text = (tmp_path / "logs" / "backend-stderr.log").read_text()
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", text)


def test_command_backend_stderr_log_is_size_bounded(monkeypatch, tmp_path):
    # An append-only failure log on an install failing 78% of the time is a
    # disk-filler unless it is bounded. Oldest entries go first.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "failing-cli")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "logs"))
    log = tmp_path / "logs" / "backend-stderr.log"
    log.parent.mkdir(parents=True)
    log.write_text("ANCIENT-MARKER\n" + ("filler line\n" * 60000))
    assert log.stat().st_size > llm._STDERR_LOG_MAX_BYTES
    monkeypatch.setattr(llm, "_run_command", lambda *a, **k: (1, "", "newest"))
    with pytest.raises(llm.ChatError):
        llm.chat([{"role": "user", "content": "x"}])
    assert log.stat().st_size <= llm._STDERR_LOG_MAX_BYTES
    text = log.read_text()
    assert "newest" in text          # the run that just failed survives
    assert "ANCIENT-MARKER" not in text  # the oldest entry is what gets dropped


def test_command_backend_stderr_log_read_failure_still_logs(monkeypatch, tmp_path):
    # #225 fail-open, extended to the read seam the append introduced: a prior
    # log that cannot be read back must not cost the current entry.
    from pathlib import Path

    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "failing-cli")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "logs"))
    log = tmp_path / "logs" / "backend-stderr.log"
    log.parent.mkdir(parents=True)
    log.write_text("prior entry\n")
    monkeypatch.setattr(Path, "read_bytes",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("EIO")))
    monkeypatch.setattr(llm, "_run_command", lambda *a, **k: (1, "", "newest"))
    with pytest.raises(llm.ChatError) as exc:
        llm.chat([{"role": "user", "content": "x"}])
    assert "backend-stderr.log" in str(exc.value)
    assert "suppressed" not in str(exc.value)
    assert "newest" in log.read_text()


# ---- #225: rc=0 + empty stdout gets the same local diagnostics as a non-zero
# exit, and is raised as a distinguishable EmptyOutputError so the serializer's
# parse-retry can treat it like an empty HTTP 200 body ----


def test_command_backend_empty_output_writes_stderr_log(monkeypatch, tmp_path):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "quiet-cli")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(llm, "_run_command",
                        lambda *a, **k: (0, "   \n", "warming up model..."))
    with pytest.raises(llm.EmptyOutputError) as exc:
        llm.chat([{"role": "user", "content": "hola"}])
    assert "backend-stderr.log" in str(exc.value)
    assert "suppressed" not in str(exc.value)
    log = tmp_path / "logs" / "backend-stderr.log"
    assert "warming up model..." in log.read_text()
    assert "empty output" in log.read_text()


def test_command_backend_empty_output_log_write_fails_open(monkeypatch, tmp_path):
    # A broken log dir (disk full, permissions, ...) must never mask the real
    # empty-output failure — fail-open on the logging seam.
    from pathlib import Path

    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "quiet-cli")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(llm, "_run_command", lambda *a, **k: (0, "", "some stderr"))
    monkeypatch.setattr(Path, "mkdir",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(llm.EmptyOutputError) as exc:
        llm.chat([{"role": "user", "content": "hola"}])
    assert "stderr suppressed" in str(exc.value)


def test_empty_output_error_is_a_chat_error():
    # Callers that catch the broad ChatError (e.g. llm.chat's own fallback
    # logic) must keep working unmodified.
    assert issubclass(llm.EmptyOutputError, llm.ChatError)


# ---- #250: CLIs that report errors on STDOUT (claude does) must not leave an
# empty breadcrumb — the failure log carries both streams, labeled ----


def test_command_backend_failure_logs_stdout_too(monkeypatch, tmp_path):
    # the live claude shape: "Not logged in · Please run /login" on stdout,
    # stderr empty, rc 1
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "claude-like")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(llm, "_run_command",
                        lambda *a, **k: (1, "Not logged in · Please run /login", ""))
    with pytest.raises(llm.ChatError):
        llm.chat([{"role": "user", "content": "hola"}])
    text = (tmp_path / "logs" / "backend-stderr.log").read_text()
    assert "Not logged in" in text
    assert "stdout" in text  # labeled, so the reader knows which stream spoke


def test_command_backend_empty_output_logs_stdout_when_stderr_silent(monkeypatch, tmp_path):
    # the zero-config claude preset shape: rc 0, json:result output spec, an
    # empty result field — but the raw stdout envelope carries the actual
    # error details. Empty stderr made the old log a bare header (#250).
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "claude-like")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_OUTPUT", "json:result")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "logs"))
    envelope = '{"result": "", "is_error": true, "subtype": "error_during_execution"}'
    monkeypatch.setattr(llm, "_run_command",
                        lambda *a, **k: (0, envelope, ""))
    with pytest.raises(llm.EmptyOutputError):
        llm.chat([{"role": "user", "content": "hola"}])
    text = (tmp_path / "logs" / "backend-stderr.log").read_text()
    assert "error_during_execution" in text


def test_command_backend_stdout_in_log_is_redacted(monkeypatch, tmp_path):
    # same #141 argument as stderr: stdout can echo prompt fragments
    secret = "AKIAIOSFODNN7EXAMPLE"
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "leaky-cli")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(llm, "_run_command",
                        lambda *a, **k: (1, f"echoing key {secret}", ""))
    with pytest.raises(llm.ChatError):
        llm.chat([{"role": "user", "content": "hola"}])
    text = (tmp_path / "logs" / "backend-stderr.log").read_text()
    assert secret not in text
    assert "[redacted:aws-key]" in text


def test_command_backend_stderr_stays_first_when_both_streams_speak(monkeypatch, tmp_path):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "noisy-cli")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(llm, "_run_command",
                        lambda *a, **k: (2, "partial body", "the real panic"))
    with pytest.raises(llm.ChatError):
        llm.chat([{"role": "user", "content": "hola"}])
    text = (tmp_path / "logs" / "backend-stderr.log").read_text()
    assert text.index("the real panic") < text.index("partial body")


# ---- #311: extract_json must reach fenced JSON embedded mid-prose ----


def test_extract_json_bare_object():
    assert llm.extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_leading_fence():
    assert llm.extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_leading_fence_no_tag():
    assert llm.extract_json('```\n[1, 2]\n```') == [1, 2]


def test_extract_json_object_with_trailing_prose():
    # The pre-#311 span heuristic handled this shape; it must keep working.
    assert llm.extract_json('{"a": 1}\n\nDone.') == {"a": 1}


def test_extract_json_leading_prose_then_object():
    assert llm.extract_json('Here it is:\n{"a": 1}') == {"a": 1}


def test_extract_json_nothing_raises():
    with pytest.raises(json.JSONDecodeError):
        llm.extract_json("no json here at all")


def test_extract_json_fence_mid_prose_with_template_braces():
    # #311 field shape: the model continued the transcript as prose (which
    # contained Jinja braces), emitted the checkpoint in a fence mid-response,
    # then closed with more prose. The old first-{/last-} span started at a
    # Jinja brace and could never parse; the fence was unreachable because the
    # fence-strip branch only fired on a LEADING fence.
    resp = (
        "A: Task confirmed. Template uses {{ states('sensor.x') }} here.\n\n"
        "Prose with {% if %} blocks too.\n\n"
        '```json\n{"session_id": "S1", "worker_queue": []}\n```\n\n'
        "**Session complete.** More prose."
    )
    assert llm.extract_json(resp) == {"session_id": "S1", "worker_queue": []}


def test_extract_json_untagged_fence_mid_prose():
    resp = 'intro {{ x }} prose\n```\n{"a": 1}\n```\nbye'
    assert llm.extract_json(resp) == {"a": 1}


def test_extract_json_skips_non_json_fence_takes_next():
    # First fence is YAML, second is the payload — first parseable fence wins.
    resp = (
        "look:\n```yaml\nkey: value\n```\nthen\n"
        '```json\n{"a": 2}\n```\n'
    )
    assert llm.extract_json(resp) == {"a": 2}


def test_extract_json_unfenced_object_after_template_braces():
    # No fence at all: the balanced scan must find the parseable object even
    # though earlier prose contains { that can never start valid JSON.
    resp = 'uses {{ jinja }} and then the payload {"a": 3, "b": [1]} end'
    assert llm.extract_json(resp) == {"a": 3, "b": [1]}


def test_extract_json_prefers_largest_parseable_span_over_prose_fragment():
    # A tiny inline object in prose must not shadow the real payload when
    # nothing is fenced — the scan prefers the longest parseable span.
    resp = ('set {"debug": true} earlier, real output: '
            '{"session_id": "S1", "working_context": {"open_questions": []}}')
    assert llm.extract_json(resp) == {
        "session_id": "S1", "working_context": {"open_questions": []}}


def test_extract_json_unterminated_leading_fence():
    # Old behavior: a leading fence with no closer still parsed. Keep it.
    assert llm.extract_json('```json\n{"a": 4}') == {"a": 4}


# ---- #341: the fallback must get its own budget, not the drained remainder --


def test_chat_fallback_extends_drained_deadline(monkeypatch):
    # A dead/slow gateway drains the shared deadline BEFORE fallback runs —
    # the exact failure fallback exists to rescue. Entry must re-arm the clock
    # to at least fallback_min_seconds, or the rescue is dead on arrival
    # (#341 field data: fallback entered 6+ times, killed by the inherited
    # deadline every time, 0 rescues).
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setenv("DAIMON_FALLBACK_MIN_SECONDS", "300")
    monkeypatch.setattr(llm, "_chat_litellm",
                        lambda *a, **k: (_ for _ in ()).throw(llm.ChatError("down")))
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("mycli", "text", "stdin"))
    monkeypatch.setattr(llm, "_missing_binary", lambda c: None)  # #747: binary "exists"
    seen = {}

    def fake_command(messages, deadline, resolved=None):
        seen["deadline"] = deadline
        return "OK"

    monkeypatch.setattr(llm, "_chat_command", fake_command)
    drained = time.monotonic() - 1
    assert llm.chat([{"role": "user", "content": "x"}], deadline=drained) == "OK"
    assert seen["deadline"] >= time.monotonic() + 250


def test_chat_fallback_keeps_larger_remaining_budget(monkeypatch):
    # max(remaining, floor): a healthy remaining budget is never shrunk.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setenv("DAIMON_FALLBACK_MIN_SECONDS", "300")
    monkeypatch.setattr(llm, "_chat_litellm",
                        lambda *a, **k: (_ for _ in ()).throw(llm.ChatError("down")))
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("mycli", "text", "stdin"))
    monkeypatch.setattr(llm, "_missing_binary", lambda c: None)  # #747: binary "exists"
    seen = {}

    def fake_command(messages, deadline, resolved=None):
        seen["deadline"] = deadline
        return "OK"

    monkeypatch.setattr(llm, "_chat_command", fake_command)
    roomy = time.monotonic() + 10_000
    assert llm.chat([{"role": "user", "content": "x"}], deadline=roomy) == "OK"
    assert seen["deadline"] == roomy


def test_chat_fallback_no_deadline_stays_none(monkeypatch):
    # deadline=None means "no budget" — the floor must not invent one.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setattr(llm, "_chat_litellm",
                        lambda *a, **k: (_ for _ in ()).throw(llm.ChatError("down")))
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("mycli", "text", "stdin"))
    monkeypatch.setattr(llm, "_missing_binary", lambda c: None)  # #747: binary "exists"
    seen = {}

    def fake_command(messages, deadline, resolved=None):
        seen["deadline"] = deadline
        return "OK"

    monkeypatch.setattr(llm, "_chat_command", fake_command)
    assert llm.chat([{"role": "user", "content": "x"}]) == "OK"
    assert seen["deadline"] is None


# ---- #458 / scar 0032: the served model comes from the wire, not the config ----
#
# A gateway alias is routing config; gateways run silent fallback chains, so the
# response body's `model` field is the only per-call truth about which model
# actually served. llm must capture it per call and expose it to the stamping
# caller (module-sticky accessor, mirroring the #28 fallback_used pattern —
# chat()'s `-> str` contract is consumed by every injectable-chat seam and
# must not change shape).


def _ok_response_with_model(content, served_model):
    body = json.dumps({
        "model": served_model,
        "choices": [{"message": {"content": content}}],
    }).encode()
    return io.BytesIO(body)


def test_chat_records_served_model_from_response_body(llm_env, monkeypatch):
    # The live incident (2026-07-30): request named the gateway alias, the
    # response's own `model` field named the local fallback that actually ran.
    llm.reset_served_models()

    def fake_urlopen(req, timeout=None):
        return _ok_response_with_model(
            "ok", "unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert llm.served_models() == ["unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF"]


def test_chat_without_model_field_records_honest_absence(llm_env, monkeypatch):
    # No `model` in the response body -> nothing recorded. Never copy the
    # requested alias into the served slot — that would re-create the exact
    # lie #458 exists to kill.
    llm.reset_served_models()

    def fake_urlopen(req, timeout=None):
        return _ok_response("ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert llm.served_models() == []


def test_served_models_distinct_sorted_and_resettable(llm_env, monkeypatch):
    # Multiple calls in one process (a chunked serialize) accumulate; the
    # accessor reports DISTINCT names, sorted, so the stamp is deterministic.
    llm.reset_served_models()
    served = iter(["z-model", "a-model", "z-model"])

    def fake_urlopen(req, timeout=None):
        return _ok_response_with_model("ok", next(served))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    for _ in range(3):
        assert llm.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert llm.served_models() == ["a-model", "z-model"]
    llm.reset_served_models()
    assert llm.served_models() == []


def test_note_served_folds_a_replayed_producer_into_the_collector():
    # #465: a replayed cached chunk's recorded producer is an observation of
    # "a model whose output is in this checkpoint" — folding it in makes the
    # EXISTING mixed-run detection cover replay-then-live-substitution.
    llm.reset_served_models()
    llm.note_served("provider/real-model")
    assert llm.served_models() == ["provider/real-model"]
    llm.note_served("  z-model  ")            # stripped like the wire path
    llm.note_served("provider/real-model")    # dedupe still happens on read
    assert llm.served_models() == ["provider/real-model", "z-model"]


def test_note_served_ignores_empty_input():
    # Honest absence: nothing to record beats recording a blank (scar 0032).
    llm.reset_served_models()
    for junk in (None, "", "   ", 42, ["a-model"]):
        llm.note_served(junk)
    assert llm.served_models() == []


def test_command_backend_records_no_served_model(monkeypatch):
    # The claude-CLI/command backend exposes no served-model info at all —
    # honest absence, never a guess (scar 0032: measurements attributed
    # without a served-model receipt must say so by carrying nothing).
    llm.reset_served_models()
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "mycli")
    monkeypatch.delenv("DAIMON_LLM_COMMAND_OUTPUT", raising=False)
    monkeypatch.delenv("DAIMON_LLM_COMMAND_INPUT", raising=False)
    monkeypatch.setattr(llm, "_run_command",
                        lambda argv, stdin_text, timeout, env, cwd: (0, "OK", ""))
    assert llm.chat([{"role": "user", "content": "hi"}]) == "OK"
    assert llm.served_models() == []


# ---- #531: streaming. Non-streaming requests emit zero bytes until the whole
# completion is done, which turns DAIMON_TIMEOUT into a hard ceiling on total
# server-side generation time — merge calls emitting 30-40k completion tokens
# cross the default 420s at normal throughput and get killed healthy. With
# streaming, urlopen's socket timeout bounds the INTER-FRAME gap instead, and
# no healthy gap approaches the timeout.


def _sse_response(frames):
    """An OpenAI-style SSE body: one `data:` line per frame, blank-line
    separated, closed by `data: [DONE]`."""
    lines = []
    for f in frames:
        payload = f if isinstance(f, str) else json.dumps(f)
        lines.append(f"data: {payload}")
        lines.append("")
    lines.append("data: [DONE]")
    lines.append("")
    return io.BytesIO("\n".join(lines).encode())


def test_chat_payload_requests_streaming_by_default(llm_env, monkeypatch):
    monkeypatch.delenv("DAIMON_LLM_STREAM", raising=False)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        return _ok_response("ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    llm.chat([{"role": "user", "content": "hi"}])
    assert captured["body"]["stream"] is True
    # Without stream_options the usage block never arrives on a stream, and
    # the usage log line (the only per-call spend record) goes dark.
    assert captured["body"]["stream_options"] == {"include_usage": True}


def test_chat_stream_opt_out_keeps_body_unchanged(llm_env, monkeypatch):
    # Strict upstreams may reject unknown fields (the llm_no_cache precedent):
    # DAIMON_LLM_STREAM=0 must restore the exact pre-#531 body shape.
    monkeypatch.setenv("DAIMON_LLM_STREAM", "0")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        return _ok_response("ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    llm.chat([{"role": "user", "content": "hi"}])
    assert "stream" not in captured["body"]
    assert "stream_options" not in captured["body"]


def test_chat_parses_sse_streamed_response(llm_env, monkeypatch):
    def fake_urlopen(req, timeout=None):
        return _sse_response([
            {"choices": [{"delta": {"role": "assistant"}}], "model": "served-x"},
            {"choices": [{"delta": {"content": "Hel"}}], "model": "served-x"},
            {"choices": [{"delta": {"content": "lo"}}], "model": "served-x"},
        ])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "Hello"


def test_chat_sse_records_served_model(llm_env, monkeypatch):
    llm.reset_served_models()

    def fake_urlopen(req, timeout=None):
        return _sse_response([
            {"choices": [{"delta": {"content": "ok"}}], "model": "served-y"},
            {"choices": [{"delta": {"content": "!"}}], "model": "served-y"},
        ])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    llm.chat([{"role": "user", "content": "hi"}])
    assert llm.served_models() == ["served-y"]


def test_chat_sse_logs_usage_from_final_frame(llm_env, monkeypatch, caplog):
    # stream_options.include_usage delivers usage in a trailing frame that has
    # no choices — the spend log line must survive the streaming switch.
    def fake_urlopen(req, timeout=None):
        return _sse_response([
            {"choices": [{"delta": {"content": "ok"}}], "model": "served-z"},
            {"choices": [], "usage": {"total_tokens": 42, "prompt_tokens": 30,
                                      "completion_tokens": 12}},
        ])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with caplog.at_level(logging.INFO, logger="daimon_briefing.llm"):
        assert llm.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert any("total_tokens=42" in r.getMessage() for r in caplog.records)


def test_chat_sse_skips_comments_and_garbled_frames(llm_env, monkeypatch):
    # Gateways interleave `: ping` comment lines, and a torn frame must skip,
    # not sink the call.
    body = (
        ": ping\n\n"
        'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
        "data: {torn json\n\n"
        'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def fake_urlopen(req, timeout=None):
        return io.BytesIO(body.encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "ab"


def test_chat_plain_json_answer_to_a_stream_request_still_parses(llm_env, monkeypatch):
    # A gateway may ignore `stream: true` and answer with one JSON object —
    # the client must serve it, not die on an SSE parse.
    def fake_urlopen(req, timeout=None):
        return _ok_response_with_usage("plain", {"total_tokens": 7})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "plain"


def test_chat_sse_empty_content_raises_chat_error(llm_env, monkeypatch):
    # A stream that closes having delivered no content is the SSE twin of an
    # empty HTTP 200 body — surface it as ChatError, never return "".
    def fake_urlopen(req, timeout=None):
        return _sse_response([{"choices": [{"delta": {}}], "model": "m"}])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(llm.ChatError):
        llm.chat([{"role": "user", "content": "hi"}])


def test_chat_sse_detection_skips_leading_blank_lines(llm_env, monkeypatch):
    # Keep-alive newlines before the first frame must not defeat detection.
    body = '\n\n\ndata: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n'

    def fake_urlopen(req, timeout=None):
        return io.BytesIO(body.encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "ok"


def test_chat_all_blank_body_is_not_sse_and_surfaces_as_chat_error(llm_env, monkeypatch):
    # A body of pure whitespace is neither a stream nor JSON — it must fail
    # loud as a ChatError-family parse failure, never be mistaken for SSE.
    def fake_urlopen(req, timeout=None):
        return io.BytesIO(b"\n\n  \n")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(Exception) as exc:
        llm.chat([{"role": "user", "content": "hi"}])
    assert not isinstance(exc.value, AssertionError)


def test_chat_sse_non_dict_frame_is_skipped(llm_env, monkeypatch):
    # A frame that parses as JSON but is not an object (e.g. a bare array)
    # has no fields to read — skip it, keep the stream's real content.
    body = (
        'data: [1, 2, 3]\n\n'
        'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
        "data: [DONE]\n"
    )

    def fake_urlopen(req, timeout=None):
        return io.BytesIO(body.encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "ok"


# ---- #533: deadline expiry is daimon's own budget, not a backend failure —
# the fallback log line must say which one happened, or the reader hunts a
# gateway problem that does not exist.


def test_deadline_exhausted_is_a_chat_error_subclass():
    # Existing `except ChatError` callers must keep catching it unchanged.
    assert issubclass(llm.DeadlineExhausted, llm.ChatError)


def test_chat_fallback_log_names_deadline_expiry(monkeypatch, caplog):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")

    def budget_gone(*a, **k):
        raise llm.DeadlineExhausted("LLM deadline exhausted after 1 tries: x")

    monkeypatch.setattr(llm, "_chat_litellm", budget_gone)
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("mycli", "text"))
    monkeypatch.setattr(llm, "_missing_binary", lambda c: None)  # #747: binary "exists"
    monkeypatch.setattr(llm, "_chat_command", lambda m, deadline, resolved=None: "FALLBACK")
    llm.reset_fallback()
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.llm"):
        llm.chat([{"role": "user", "content": "x"}])
    msgs = [r.getMessage() for r in caplog.records if "llm.fallback" in r.getMessage()]
    assert msgs and "deadline" in msgs[0]
    assert "litellm failed" not in msgs[0]


def test_chat_fallback_log_still_names_backend_failure(monkeypatch, caplog):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")

    def boom(*a, **k):
        raise llm.ChatError("gateway down")

    monkeypatch.setattr(llm, "_chat_litellm", boom)
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("mycli", "text"))
    monkeypatch.setattr(llm, "_missing_binary", lambda c: None)  # #747: binary "exists"
    monkeypatch.setattr(llm, "_chat_command", lambda m, deadline, resolved=None: "FALLBACK")
    llm.reset_fallback()
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.llm"):
        llm.chat([{"role": "user", "content": "x"}])
    msgs = [r.getMessage() for r in caplog.records if "llm.fallback" in r.getMessage()]
    assert msgs and "litellm failed" in msgs[0]


def test_deadline_exhaustion_sites_raise_the_subclass(llm_env, monkeypatch):
    # The pre-first-call site: deadline already in the past.
    with pytest.raises(llm.DeadlineExhausted):
        llm._chat_litellm([{"role": "user", "content": "x"}],
                          deadline=time.monotonic() - 1)


def test_command_backend_deadline_exhausted_raises_the_subclass(monkeypatch):
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("mycli", "text", "stdin"))
    with pytest.raises(llm.DeadlineExhausted):
        llm._chat_command([{"role": "user", "content": "x"}],
                          deadline=time.monotonic() - 1)
# ---- #535: the served-model stamp is a gateway alias (scar 0032) — log the
# presence of provider-specific usage fields so a silent model substitution
# is detectable from the log, permanently, going forward.


def test_usage_log_names_present_provider_fields(llm_env, monkeypatch, caplog):
    def fake_urlopen(req, timeout=None):
        return _ok_response_with_usage("ok", {
            "total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "completion_tokens_details": {"reasoning_tokens": 0},
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with caplog.at_level(logging.INFO, logger="daimon_briefing.llm"):
        llm.chat([{"role": "user", "content": "hi"}])
    msgs = [r.getMessage() for r in caplog.records if "LLM usage" in r.getMessage()]
    assert msgs, "usage log line missing"
    line = msgs[0]
    assert "provider_fields=cache_creation,cache_read,reasoning" in line


def test_usage_log_says_none_when_provider_fields_absent(llm_env, monkeypatch, caplog):
    # A typical local OpenAI-compatible server carries none of the fields —
    # the log must say so explicitly (absence is the discriminator's signal,
    # so it cannot be expressed by omission).
    def fake_urlopen(req, timeout=None):
        return _ok_response_with_usage("ok", {
            "total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5,
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with caplog.at_level(logging.INFO, logger="daimon_briefing.llm"):
        llm.chat([{"role": "user", "content": "hi"}])
    msgs = [r.getMessage() for r in caplog.records if "LLM usage" in r.getMessage()]
    assert msgs and "provider_fields=none" in msgs[0]


def test_usage_log_partial_provider_fields(llm_env, monkeypatch, caplog):
    # Only what is actually present gets named — never inferred to a full set.
    def fake_urlopen(req, timeout=None):
        return _ok_response_with_usage("ok", {
            "total_tokens": 10,
            "cache_read_input_tokens": 3,
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with caplog.at_level(logging.INFO, logger="daimon_briefing.llm"):
        llm.chat([{"role": "user", "content": "hi"}])
    msgs = [r.getMessage() for r in caplog.records if "LLM usage" in r.getMessage()]
    assert msgs and "provider_fields=cache_read" in msgs[0]


# ---- #475 slice 3: ONE rescue command, for every backend.
#
# Before this slice DAIMON_LLM_COMMAND meant two different things depending on
# DAIMON_LLM_BACKEND: the primary when the backend was `command`, the rescue
# when it was litellm. A `command` primary therefore had no rescue direction at
# all (chat() returned from _chat_command before the try block existed) and an
# install ran a 78% capture error rate for 14 days with nothing to fall back to.
#
# The rule now: each backend has exactly ONE fallback, and it is always
# DAIMON_LLM_COMMAND_FALLBACK. No chains. A compat shim keeps pre-#475 litellm
# installs rescued by DAIMON_LLM_COMMAND alone.


def test_resolve_fallback_command_reads_its_own_triple(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_COMMAND_FALLBACK", "rescuecli")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_FALLBACK_OUTPUT", "json:answer")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_FALLBACK_INPUT", "arg")
    assert llm._resolve_fallback_command() == ("rescuecli", "json:answer", "arg")


def test_resolve_fallback_command_output_defaults_to_text(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_COMMAND_FALLBACK", "rescuecli")
    monkeypatch.delenv("DAIMON_LLM_COMMAND_FALLBACK_OUTPUT", raising=False)
    monkeypatch.delenv("DAIMON_LLM_COMMAND_FALLBACK_INPUT", raising=False)
    assert llm._resolve_fallback_command() == ("rescuecli", "text", "stdin")


def test_resolve_fallback_command_shim_keeps_pre_475_litellm_installs_rescued(monkeypatch):
    # The whole installed base configured its litellm rescue as
    # DAIMON_LLM_COMMAND. Upgrading must not silently delete that rescue.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.delenv("DAIMON_LLM_COMMAND_FALLBACK", raising=False)
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("legacycli", "text", "stdin"))
    assert llm._resolve_fallback_command() == ("legacycli", "text", "stdin")


def test_resolve_fallback_command_explicit_wins_over_the_shim(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_FALLBACK", "rescuecli")
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("legacycli", "text", "stdin"))
    assert llm._resolve_fallback_command()[0] == "rescuecli"


def test_resolve_fallback_command_never_self_falls_back_on_a_command_primary(monkeypatch):
    # The shim must NOT apply when DAIMON_LLM_COMMAND is the primary — that
    # would retry the identical failing invocation and call it a rescue.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.delenv("DAIMON_LLM_COMMAND_FALLBACK", raising=False)
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("mycli", "text", "stdin"))
    assert llm._resolve_fallback_command() is None


def test_chat_command_primary_falls_back_on_error(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setattr(llm, "_resolve_fallback_command",
                        lambda: ("rescuecli", "text", "stdin"))
    monkeypatch.setattr(llm, "_missing_binary", lambda c: None)  # #747: binary "exists"

    def dispatch(messages, deadline, resolved=None):
        if resolved is None:
            raise llm.ChatError("primary cli exited 1")
        return "FROM_RESCUE"

    monkeypatch.setattr(llm, "_chat_command", dispatch)
    assert llm.chat([{"role": "user", "content": "x"}]) == "FROM_RESCUE"


def test_chat_command_primary_fallback_sets_the_flag(monkeypatch):
    # NOT cosmetic: serializer._save_chunk_cache gates on fallback_used(). A
    # rescue that leaves the flag clear caches the RESCUE cli's output under
    # the primary's key (scar 0015, and it defeats the #465 multi-producer
    # gate).
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setattr(llm, "_resolve_fallback_command",
                        lambda: ("rescuecli", "text", "stdin"))
    monkeypatch.setattr(llm, "_missing_binary", lambda c: None)  # #747: binary "exists"
    monkeypatch.setattr(llm, "_chat_command",
                        lambda m, deadline, resolved=None: (
                            "OK" if resolved else (_ for _ in ()).throw(llm.ChatError("x"))))
    llm.chat([{"role": "user", "content": "x"}])
    assert llm.fallback_used() is True


def test_chat_command_primary_raises_when_nothing_to_fall_back_to(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setattr(llm, "_resolve_fallback_command", lambda: None)
    monkeypatch.setattr(llm, "_chat_command",
                        lambda m, deadline, resolved=None: (
                            _ for _ in ()).throw(llm.ChatError("primary down")))
    with pytest.raises(llm.ChatError):
        llm.chat([{"role": "user", "content": "x"}])
    assert llm.fallback_used() is False


def test_chat_command_primary_respects_fallback_disabled(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "0")
    monkeypatch.setattr(llm, "_resolve_fallback_command",
                        lambda: ("rescuecli", "text", "stdin"))
    monkeypatch.setattr(llm, "_chat_command",
                        lambda m, deadline, resolved=None: (
                            _ for _ in ()).throw(llm.ChatError("primary down")))
    with pytest.raises(llm.ChatError):
        llm.chat([{"role": "user", "content": "x"}])


def test_chat_command_primary_fallback_rearms_a_drained_deadline(monkeypatch):
    # #341 on the new edge: the primary may have burned the whole budget
    # before failing, which would kill the rescue on arrival.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setattr(config, "fallback_min_seconds", lambda: 300)
    monkeypatch.setattr(llm, "_resolve_fallback_command",
                        lambda: ("rescuecli", "text", "stdin"))
    monkeypatch.setattr(llm, "_missing_binary", lambda c: None)  # #747: binary "exists"
    seen = {}

    def dispatch(messages, deadline, resolved=None):
        if resolved is None:
            raise llm.ChatError("primary down")
        seen["deadline"] = deadline
        return "OK"

    monkeypatch.setattr(llm, "_chat_command", dispatch)
    llm.chat([{"role": "user", "content": "x"}], deadline=time.monotonic() - 5)
    assert seen["deadline"] >= time.monotonic() + 290


def test_chat_command_primary_fallback_log_keeps_the_ledger_literal(monkeypatch, caplog):
    # ledger.py counts fallback attempts by matching the literal
    # "llm.fallback backend=command". A new edge that logs anything else
    # counts zero and reproduces the exact "attempted 0" unreadability #475
    # was filed about.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setattr(llm, "_resolve_fallback_command",
                        lambda: ("rescuecli", "text", "stdin"))
    monkeypatch.setattr(llm, "_missing_binary", lambda c: None)  # #747: binary "exists"
    monkeypatch.setattr(llm, "_chat_command",
                        lambda m, deadline, resolved=None: (
                            "OK" if resolved else (_ for _ in ()).throw(llm.ChatError("x"))))
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.llm"):
        llm.chat([{"role": "user", "content": "x"}])
    assert any(r.getMessage().startswith("llm.fallback backend=command")
               for r in caplog.records)


def test_chat_command_uses_the_resolved_fallback_triple(monkeypatch):
    # The rescue must run the FALLBACK's own command/output/input specs, not
    # the primary's — a chain whose hops need different input axes (#58) is
    # exactly why the fallback carries its own triple.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    triple = ("rescuecli --json", "json:answer", "arg")
    monkeypatch.setattr(llm, "_resolve_fallback_command", lambda: triple)
    monkeypatch.setattr(llm, "_missing_binary", lambda c: None)  # #747: binary "exists"
    seen = {}

    def dispatch(messages, deadline, resolved=None):
        if resolved is None:
            raise llm.ChatError("primary down")
        seen["resolved"] = resolved
        return "OK"

    monkeypatch.setattr(llm, "_chat_command", dispatch)
    llm.chat([{"role": "user", "content": "x"}])
    assert seen["resolved"] == triple


def test_chat_command_backend_honours_an_explicit_resolution(monkeypatch):
    # _chat_command must run what it is handed rather than re-resolving.
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "primarycli")
    seen = {}

    def fake_run(argv, stdin_text, timeout, env, cwd):
        seen["argv"] = argv
        return 0, "hello", ""

    monkeypatch.setattr(llm, "_run_command", fake_run)
    out = llm._chat_command([{"role": "user", "content": "hi"}], deadline=None,
                            resolved=("rescuecli", "text", "stdin"))
    assert out == "hello"
    assert seen["argv"][0] == "rescuecli"


def test_rescue_posture_command_backend_is_covered_with_a_fallback(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setattr(llm, "_resolve_fallback_command",
                        lambda: ("rescuecli", "text", "stdin"))
    monkeypatch.setattr(llm.shutil, "which", lambda b: f"/usr/bin/{b}")  # #747
    assert llm.rescue_posture() == "covered"


def test_rescue_posture_command_backend_stays_none_without_a_fallback(monkeypatch):
    # #479's honesty fix must survive: no configured rescue still reads
    # "none", never "covered".
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setattr(llm, "_resolve_fallback_command", lambda: None)
    assert llm.rescue_posture() == "none"


def test_rescue_posture_command_backend_reports_disabled_when_turned_off(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "0")
    monkeypatch.setattr(llm, "_resolve_fallback_command",
                        lambda: ("rescuecli", "text", "stdin"))
    assert llm.rescue_posture() == "disabled"


def test_rescue_posture_reresolves_liveness_every_call(monkeypatch):
    # Posture must be derived from a CURRENT resolution, never cached from
    # config presence: a fallback binary removed in a workstation refresh must
    # stop reading as "covered".
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    live = {"resolves": True}
    monkeypatch.setattr(llm, "_resolve_fallback_command",
                        lambda: ("rescuecli", "text", "stdin") if live["resolves"] else None)
    monkeypatch.setattr(llm.shutil, "which", lambda b: f"/usr/bin/{b}")  # #747
    assert llm.rescue_posture() == "covered"
    live["resolves"] = False
    assert llm.rescue_posture() == "none"


# ---- #663: a well-formed response with unusable content reaches the rescue ---


def test_rescue_unparseable_routes_to_the_fallback(monkeypatch, caplog):
    # The primary returned rc=0 and a successful envelope, so chat() never
    # raised and never consulted the fallback. The caller that discovers the
    # content is unusable needs its own way in.
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setattr(llm, "_resolve_fallback_command",
                        lambda: ("rescuecli", "text", "stdin"))
    monkeypatch.setattr(llm, "_missing_binary", lambda c: None)  # #747: binary "exists"
    seen = {}

    def fake_command(messages, deadline, resolved=None):
        seen["resolved"] = resolved
        return "FALLBACK"

    monkeypatch.setattr(llm, "_chat_command", fake_command)
    llm.reset_fallback()
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.llm"):
        out = llm.rescue_unparseable([{"role": "user", "content": "x"}], None)
    assert out == "FALLBACK"
    assert seen["resolved"] == ("rescuecli", "text", "stdin")
    assert llm.fallback_used() is True
    # The ledger counts rescues by matching this literal (ledger.py).
    msgs = [r.getMessage() for r in caplog.records if "llm.fallback" in r.getMessage()]
    assert msgs and msgs[0].startswith("llm.fallback backend=command")
    assert "unparseable" in msgs[0]


def test_rescue_unparseable_without_a_fallback_raises_no_rescue(monkeypatch):
    # No fallback configured: the caller must be able to tell "nothing tried"
    # apart from "the rescue also failed", so it can raise its own error.
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setattr(llm, "_resolve_fallback_command", lambda: None)
    llm.reset_fallback()
    with pytest.raises(llm.NoRescueAvailable):
        llm.rescue_unparseable([{"role": "user", "content": "x"}], None)
    assert llm.fallback_used() is False


# ---- #747: a fallback whose binary does not exist is not a rescue ----


def test_rescue_posture_command_edge_not_covered_for_missing_fallback_binary(monkeypatch):
    # Field case: DAIMON_LLM_COMMAND_FALLBACK=1 made the rescue binary a
    # program named `1`; posture read "covered" for an exec that can only fail.
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "primarycli -x")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_FALLBACK", "1")
    monkeypatch.setattr(llm.shutil, "which", lambda b: None)
    assert llm.rescue_posture() == "none"


def test_rescue_posture_litellm_edge_not_covered_for_missing_fallback_binary(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setattr(config, "llm_api_key", lambda: "sk-key")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_FALLBACK", "1")
    monkeypatch.setattr(llm.shutil, "which", lambda b: None)
    assert llm.rescue_posture() == "gap"


def test_fallback_missing_binary_names_argv0(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_FALLBACK", "1 --flag value")
    monkeypatch.setattr(llm.shutil, "which", lambda b: None)
    assert llm.fallback_missing_binary() == "1"


def test_fallback_missing_binary_none_when_binary_exists(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_FALLBACK", "rescuecli -p")
    monkeypatch.setattr(llm.shutil, "which", lambda b: f"/usr/bin/{b}")
    assert llm.fallback_missing_binary() is None


def test_fallback_missing_binary_none_when_no_fallback_resolves(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.delenv("DAIMON_LLM_COMMAND_FALLBACK", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda b: None)
    assert llm.fallback_missing_binary() is None


def test_missing_binary_unsplittable_command_reports_itself():
    # An unbalanced quote cannot shlex.split, so it cannot exec either —
    # the whole string IS the missing name (#747).
    assert llm._missing_binary("'unbalanced") == "'unbalanced"


def test_missing_binary_empty_command_reports_itself():
    assert llm._missing_binary("") == ""
    assert llm._missing_binary("   ") == "   "


# ---- #748: a failed rescue exec must not destroy the primary's diagnostic ----


def test_rescue_exec_failure_preserves_primary_diagnostic(monkeypatch, caplog):
    # Field case: the real error was the missing model, but the surfaced error
    # was "command backend binary not found: 1" — the rescue's own failure
    # overwrote the one diagnostic worth acting on. which() is pinned to say
    # the binary exists while exec still refuses it: the advisory pre-check
    # runs under THIS process's PATH, and the exec's view can differ — the
    # chain is exactly for the failures the pre-check cannot see.
    primary = "No LLM model (set DAIMON_LLM_MODEL or LITELLM_MODEL)."
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_FALLBACK", "1")

    def boom(*a, **k):
        raise llm.ChatError(primary)

    def missing(argv, stdin_text, timeout, env, cwd):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(llm.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(llm, "_chat_litellm", boom)
    monkeypatch.setattr(llm, "_run_command", missing)
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.llm"):
        with pytest.raises(llm.ChatError) as exc:
            llm.chat([{"role": "user", "content": "x"}])
    text = str(exc.value)
    assert "command backend binary not found: 1" in text
    assert primary in text
    # The ledger literal must survive the new failure path (ledger.py counts
    # attempts by matching it).
    assert any(r.getMessage().startswith("llm.fallback backend=command")
               for r in caplog.records)


def test_rescue_empty_output_keeps_retry_class_and_primary_text(monkeypatch):
    # EmptyOutputError is the serializer's cache-buster retry class — chaining
    # the primary's text must not demote it to a plain ChatError.
    primary = "gateway down"
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_FALLBACK", "fbcli")
    monkeypatch.setattr(llm, "_chat_litellm",
                        lambda *a, **k: (_ for _ in ()).throw(llm.ChatError(primary)))
    monkeypatch.setattr(llm.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(llm, "_run_command", lambda *a, **k: (0, "", ""))
    with pytest.raises(llm.EmptyOutputError) as exc:
        llm.chat([{"role": "user", "content": "x"}])
    assert primary in str(exc.value)


def test_rescue_not_attempted_when_fallback_binary_is_missing(monkeypatch, caplog):
    # Runtime must agree with posture (#747 reads this config as none/gap):
    # attempting the doomed exec would log the ledger-counted attempt literal,
    # set _fallback_used, and bury the primary's diagnostic. The primary error
    # surfaces verbatim instead — the honest read of the field case.
    primary = "No LLM model (set DAIMON_LLM_MODEL or LITELLM_MODEL)."
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "1")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_FALLBACK", "1")
    monkeypatch.setattr(llm, "_chat_litellm",
                        lambda *a, **k: (_ for _ in ()).throw(llm.ChatError(primary)))
    monkeypatch.setattr(llm.shutil, "which", lambda b: None)
    llm.reset_fallback()
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.llm"):
        with pytest.raises(llm.ChatError) as exc:
            llm.chat([{"role": "user", "content": "x"}])
    assert str(exc.value) == primary
    assert not any(r.getMessage().startswith("llm.fallback backend=command")
                   for r in caplog.records)
    assert llm.fallback_used() is False
