"""Minimal OpenAI-compatible chat client. Stdlib only (urllib). Config via env
(see config.py): DAIMON_LLM_* falling back to LITELLM_*. Reuses the Track-A
pattern from research/experiments/lib/llm.py — clean copy inside the package."""

import json
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from . import config

log = logging.getLogger(__name__)


def _run_command(argv, stdin_text, timeout, env, cwd):
    """Run a CLI, piping stdin_text to its stdin. Returns (rc, stdout, stderr).
    The ONLY subprocess boundary — tests monkeypatch this. Raises
    FileNotFoundError (missing binary) / subprocess.TimeoutExpired."""
    proc = subprocess.run(
        argv, input=stdin_text, capture_output=True, text=True,
        timeout=timeout, env=env, cwd=cwd,
    )
    return proc.returncode, proc.stdout, proc.stderr


class ChatError(RuntimeError):
    """A chat call failed after retries. Callers catch this to give up gracefully."""


def _chat_litellm(messages, model=None, temperature=None, timeout=None, retries=3, deadline=None):
    """POST /v1/chat/completions. Returns the assistant message content (str).

    Retries transient failures (timeout, connection, 5xx) with backoff; 4xx fails
    fast. Raises ChatError on giving up. Signature is callable-compatible with the
    fake injected in tests: _chat_litellm(messages, **kwargs) -> str.

    `temperature=None` (the default) resolves config.llm_temperature()
    (DAIMON_LLM_TEMPERATURE, default 0.0). An explicit argument always wins.

    `deadline` (time.monotonic() seconds) is a TOTAL budget across all attempts:
    each attempt's socket timeout is capped to the remaining time, and retrying
    stops once the deadline would be exceeded.

    Error messages NEVER include the HTTP response body — error payloads can echo
    request contents/secrets, and hooks log these messages.
    """
    base = config.llm_base_url()
    key = config.llm_api_key()
    if not key:
        raise ChatError("No LLM API key (set DAIMON_LLM_API_KEY or LITELLM_API_KEY).")
    mdl = model or config.llm_model()
    if not mdl:
        raise ChatError("No LLM model (set DAIMON_LLM_MODEL or LITELLM_MODEL).")
    if timeout is None:
        timeout = config.timeout_seconds()
    if temperature is None:
        temperature = config.llm_temperature()

    # temperature is always sent explicitly — some upstreams reject requests
    # that omit it or send a value other than the one they pin.
    payload = {"model": mdl, "messages": messages, "temperature": temperature}
    if config.llm_no_cache():
        # LiteLLM per-request cache bypass. Opt-in only: strict upstreams may
        # reject unknown fields, so the default body must stay unchanged.
        payload["cache"] = {"no-cache": True}
    body = json.dumps(payload).encode()
    last = None
    for attempt in range(retries):
        attempt_timeout = timeout
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ChatError(f"LLM deadline exhausted after {attempt} tries: {last}")
            attempt_timeout = min(timeout, remaining)
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=attempt_timeout) as r:
                data = json.loads(r.read())
            # Surface token cost — the serializer discards the rest of the
            # response, so this log line is the only record of per-call spend.
            usage = data.get("usage") or {}
            if usage:
                log.info("LLM usage model=%s total_tokens=%s prompt=%s completion=%s",
                         mdl, usage.get("total_tokens"),
                         usage.get("prompt_tokens"), usage.get("completion_tokens"))
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            if 500 <= e.code < 600 and attempt < retries - 1:
                last = f"HTTP {e.code}"
            else:
                raise ChatError(f"LLM HTTP {e.code} (response body suppressed)")
        except (TimeoutError, urllib.error.URLError) as e:
            last = getattr(e, "reason", e)
            if attempt == retries - 1:
                raise ChatError(f"LLM unreachable/timeout after {retries} tries at {base}: {last}")
        backoff = 3 * (attempt + 1)
        if deadline is not None and time.monotonic() + backoff >= deadline:
            raise ChatError(f"LLM deadline exhausted after {attempt + 1} tries: {last}")
        # `last` is "HTTP <code>" or the transport reason — never the response
        # body (it can echo request contents/secrets; see docstring).
        log.warning("LLM %s (attempt %d/%d), backing off %ds",
                    last, attempt + 1, retries, backoff)
        time.sleep(backoff)
    raise ChatError(f"LLM failed after {retries} tries: {last}")


def chat(messages, model=None, temperature=None, timeout=None, retries=3, deadline=None):
    """Dispatch to the configured backend. litellm (default) falls back to a
    command backend on ChatError when fallback is enabled and one resolves."""
    backend = config.llm_backend()
    if backend == "auto":
        if config.llm_api_key():
            backend = "litellm"
        elif _resolve_command() is not None:
            backend = "command"
        else:
            backend = "litellm"   # let _chat_litellm raise the helpful no-key error
    if backend in ("command", "claude-cli"):
        return _chat_command(messages, deadline)
    try:
        return _chat_litellm(messages, model=model, temperature=temperature,
                             timeout=timeout, retries=retries, deadline=deadline)
    except ChatError:
        if config.llm_fallback() and _resolve_command() is not None:
            log.warning("llm.fallback backend=command (litellm failed)")
            return _chat_command(messages, deadline)
        raise


def extract_json(text):
    """Pull a JSON object/array out of a model response, tolerating ```json fences.

    Raises json.JSONDecodeError when nothing parseable is found.
    """
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    candidates = []
    for op, cl in (("[", "]"), ("{", "}")):
        i, j = t.find(op), t.rfind(cl)
        if i != -1 and j != -1 and j > i:
            candidates.append((i, t[i:j + 1]))
    for _, span in sorted(candidates):
        try:
            return json.loads(span)
        except json.JSONDecodeError:
            continue
    raise json.JSONDecodeError("no JSON object/array found in response", t, 0)


_CLAUDE_PRESET = ("claude -p --model haiku --output-format json", "json:result")


def _flatten_messages(messages):
    """Format messages for CLI input: role in caps, one newline between role and content,
    two newlines between messages."""
    return "\n\n".join(f"{m['role'].upper()}:\n{m['content']}" for m in messages)


def _resolve_command():
    """Resolve the command backend (command_str, output_spec) or None.

    Order: explicit DAIMON_LLM_COMMAND, else claude-cli preset if claude is
    on PATH, else None (no fallback possible)."""
    cmd = config.llm_command()
    if cmd:
        return cmd, (config.llm_command_output() or "text")
    if shutil.which("claude"):
        return _CLAUDE_PRESET
    return None


def _extract_output(stdout, output_spec):
    """Extract the LLM response from command output.

    output_spec format:
    - "text": return stripped stdout
    - "json:<key>": parse stdout as JSON and extract [key]
    """
    if output_spec.startswith("json:"):
        key = output_spec[len("json:"):]
        obj = json.loads(stdout)
        return obj[key]
    return stdout.strip()


def _chat_command(messages, deadline):
    """Serialize via a headless LLM CLI. Prompt via stdin; runs isolated
    (DAIMON_DISABLE=1, temp cwd). Raises ChatError on any failure — never
    echoes prompt/stdout/stderr (they can carry secrets)."""
    resolved = _resolve_command()
    if not resolved:
        raise ChatError("No command backend (set DAIMON_LLM_COMMAND or install claude).")
    command, output_spec = resolved
    argv = shlex.split(command)
    stdin_text = _flatten_messages(messages)
    timeout = config.timeout_seconds()
    if deadline is not None:
        timeout = min(timeout, max(0.0, deadline - time.monotonic()))
        if timeout <= 0:
            raise ChatError("LLM deadline exhausted before command backend")
    env = {**os.environ, "DAIMON_DISABLE": "1"}
    cwd = tempfile.mkdtemp(prefix="daimon-cli-")
    try:
        try:
            rc, out, err = _run_command(argv, stdin_text, timeout, env, cwd)
        except FileNotFoundError:
            raise ChatError(f"command backend binary not found: {argv[0]}")
        except subprocess.TimeoutExpired:
            raise ChatError("command backend timed out")
        if rc != 0:
            raise ChatError(f"command backend exited {rc} (stderr suppressed)")
        try:
            text = _extract_output(out, output_spec)
        except (json.JSONDecodeError, KeyError, TypeError):
            raise ChatError("command backend output unparseable (body suppressed)")
        if not text or not text.strip():
            raise ChatError("command backend returned empty output")
        log.info("LLM command backend ok argv0=%s", argv[0])
        return text
    finally:
        shutil.rmtree(cwd, ignore_errors=True)
