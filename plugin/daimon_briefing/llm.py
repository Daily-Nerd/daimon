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

from . import config, redact

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


class EmptyOutputError(ChatError):
    """The command backend returned rc=0 with empty (or whitespace-only) stdout.

    A ChatError subclass so existing `except ChatError` callers (llm.chat's
    litellm->command fallback) are unaffected, but distinguishable so the
    serializer's parse-retry loop can treat it like an empty HTTP 200 body
    from a gateway — both are "the backend said nothing", not a transport
    failure (#225)."""


# Read granularity for _read_within_deadline (#298): urlopen's `timeout=`
# bounds a single blocking socket read, not the call as a whole — a response
# that keeps delivering bytes never trips it, so a single r.read() can run
# past `deadline` while every individual read stays under attempt_timeout.
# read1() returns after at most one such read, so checking `deadline` between
# calls bounds total elapsed to roughly deadline + one attempt_timeout instead
# of leaving it unbounded.
_READ_CHUNK_BYTES = 65536


def _read_within_deadline(r, deadline, attempt, last):
    """Read `r` (an HTTPResponse) to EOF via read1(), checking `deadline`
    between reads so a slow-but-live response can't run past the total
    budget the way one blocking r.read() does (#298)."""
    chunks = []
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            raise ChatError(f"LLM deadline exhausted after {attempt} tries: {last}")
        chunk = r.read1(_READ_CHUNK_BYTES)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _chat_litellm(messages, model=None, temperature=None, timeout=None, retries=3, deadline=None):
    """POST /v1/chat/completions. Returns the assistant message content (str).

    Retries transient failures (timeout, connection, 5xx) with backoff; 4xx fails
    fast. Raises ChatError on giving up. Signature is callable-compatible with the
    fake injected in tests: _chat_litellm(messages, **kwargs) -> str.

    `temperature=None` (the default) resolves config.llm_temperature()
    (DAIMON_LLM_TEMPERATURE, default 0.0). An explicit argument always wins.

    `deadline` (time.monotonic() seconds) is a TOTAL budget across all attempts
    AND within a single in-flight call: each attempt's socket timeout is capped
    to the remaining time, retrying stops once the deadline would be exceeded,
    and the response body is read in a loop that re-checks the deadline
    between reads — a single call that keeps delivering bytes cannot outrun
    the budget the way one blocking read() could (#298).

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
                data = json.loads(_read_within_deadline(r, deadline, attempt, last))
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


# #28: the silent-fallback flag. Sticky across chat() calls within one process
# (a serialize is one child process, possibly several chat calls) so the caller
# can stamp "this checkpoint used the weaker fallback backend" on its result
# line. reset_fallback() at the start of a unit of work; fallback_used() after.
_fallback_used = False


def fallback_used() -> bool:
    return _fallback_used


def reset_fallback() -> None:
    global _fallback_used
    _fallback_used = False


def chat(messages, model=None, temperature=None, timeout=None, retries=3, deadline=None):
    """Dispatch to the configured backend. litellm (default) falls back to a
    command backend on ChatError when fallback is enabled and one resolves."""
    global _fallback_used
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
            _fallback_used = True
            return _chat_command(messages, deadline)
        raise


def extract_json(text):
    """Pull a JSON object/array out of a model response, tolerating ```json
    fences ANYWHERE in the text — not only at the start (#311: a model that
    continues the transcript as prose and buries the payload in a mid-response
    fence still gets its JSON recovered).

    Order: whole string, then each fenced block in order (first parseable
    wins), then a raw_decode scan over every `{`/`[` start taking the LONGEST
    parseable span. Longest — not first — because prose can carry tiny inline
    objects, and first-{-to-last-} (the pre-#311 heuristic) dies whenever the
    prose contains template braces ({{ jinja }}), which any transcript
    touching templates or shell will produce.

    Raises json.JSONDecodeError when nothing parseable is found.
    """
    t = text.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # Every fenced block, wherever it sits. split("```") puts fence interiors
    # at odd indices, including an unterminated trailing fence (old behavior
    # for a leading ``` with no closer — kept).
    parts = t.split("```")
    for k in range(1, len(parts), 2):
        block = parts[k]
        if block[:4].lower() == "json":
            block = block[4:]
        try:
            return json.loads(block.strip())
        except json.JSONDecodeError:
            continue
    # Balanced scan: try a strict decode at every plausible start, keep the
    # longest span that parses. raw_decode fails in O(1) on template braces.
    decoder = json.JSONDecoder()
    best = None
    best_len = -1
    for i, ch in enumerate(t):
        if ch not in "{[":
            continue
        try:
            obj, end = decoder.raw_decode(t, i)
        except json.JSONDecodeError:
            continue
        if end - i > best_len:
            best, best_len = obj, end - i
    if best_len >= 0:
        return best
    raise json.JSONDecodeError("no JSON object/array found in response", t, 0)


_CLAUDE_PRESET = ("claude -p --model haiku --output-format json", "json:result")


def _flatten_messages(messages):
    """Format messages for CLI input: role in caps, one newline between role and content,
    two newlines between messages."""
    return "\n\n".join(f"{m['role'].upper()}:\n{m['content']}" for m in messages)


def _resolve_command():
    """Resolve the command backend (command_str, output_spec, input_spec) or
    None.

    Order: explicit DAIMON_LLM_COMMAND, else claude-cli preset if claude is
    on PATH, else None (no fallback possible). The claude-cli preset always
    stays on stdin — `claude -p` reads the prompt from stdin, so the input
    axis (#58) only matters for explicit commands."""
    cmd = config.llm_command()
    if cmd:
        return cmd, (config.llm_command_output() or "text"), config.llm_command_input()
    if shutil.which("claude"):
        return (*_CLAUDE_PRESET, "stdin")
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


# Conservative byte ceiling for arg-mode prompts. Linux caps a single argv
# element well under this (MAX_ARG_STRLEN, 128KiB) and the total argv+environ
# against ARG_MAX (typically a few hundred KiB to a few MiB depending on OS);
# 100_000 bytes leaves headroom under the tightest of those limits across
# platforms so a chunked/merge-sized prompt fails loud with a named ChatError
# — pointing at file:/stdin mode — instead of a raw OSError E2BIG from the
# kernel exec() call.
_ARG_MAX_BYTES = 100_000


def _apply_input_spec(argv, prompt_text, input_spec, cwd):
    """Wire `prompt_text` into argv/stdin per `input_spec`. Returns
    (argv, stdin_text) for `_run_command`.

    - "stdin": argv unchanged, prompt piped via stdin (original behavior).
    - "arg": prompt appended as ONE final raw argv element — never
      string-interpolated into the command template, so it can never reach a
      shell (preserves _run_command's never-touches-shell contract). Guarded
      by _ARG_MAX_BYTES.
    - "file:<flag>": prompt written to a 0600 tempfile inside `cwd` (the
      same per-call tempdir the caller already tempfile.mkdtemp()s and
      shutil.rmtree()s in a finally — covers cleanup on success, failure,
      AND timeout), then "<flag> <path>" appended to argv.

    Any input_spec other than "arg"/"file:..." is treated as "stdin" —
    config.llm_command_input() already fails unrecognized values open to
    "stdin" before this is ever called, so this is a defensive default only.
    """
    if input_spec == "arg":
        size = len(prompt_text.encode("utf-8"))
        if size > _ARG_MAX_BYTES:
            raise ChatError(
                f"prompt too large for arg-mode command input ({size} bytes "
                f"> {_ARG_MAX_BYTES}-byte limit) — switch "
                f"DAIMON_LLM_COMMAND_INPUT to file:<flag> or the stdin default"
            )
        return [*argv, prompt_text], ""
    if input_spec.startswith("file:"):
        # config.llm_command_input() already normalizes the flag, but strip
        # again here so a spec that reaches this boundary directly (tests,
        # future callers) can't smuggle whitespace padding into argv — a
        # silent misconfiguration most CLIs won't match.
        flag = input_spec[len("file:"):].strip()
        if not flag:  # empty-flag spec is unusable — same treatment as stdin
            return argv, prompt_text
        path = os.path.join(cwd, "prompt.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(prompt_text)
        os.chmod(path, 0o600)
        return [*argv, flag, path], ""
    return argv, prompt_text


def _log_backend_stderr(argv0, err, header, out=None) -> str:
    """Write redacted command-backend diagnostics to backend-stderr.log
    (truncate-per-run — the log is "the last failure", not an archive, #56)
    and return a hint embeddable in a ChatError message: the log path on
    success, "stderr suppressed" on any OSError — logging must never mask the
    real failure (fail-open on the logging seam, #225).

    `out` is the backend's stdout, appended under a label AFTER stderr:
    agent-style CLIs (claude among them) report errors on stdout with an
    empty stderr, so a stderr-only log was a bare header exactly when the
    user most needed the cause (#250). Both streams are the user's own disk,
    same trust domain as the transcript — scrubbed, never on any wire."""
    hint = "stderr suppressed"
    try:
        d = config.log_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / "backend-stderr.log"
        # CLI backends can echo prompt fragments (transcript text) into
        # either stream — scrub before it persists (#141).
        err_logged, _ = redact.redact_text(err or "")
        body = f"{header}\n{err_logged}\n"
        if out is not None:
            out_logged, _ = redact.redact_text(out)
            body += f"--- stdout ---\n{out_logged}\n"
        p.write_text(body, encoding="utf-8")
        hint = f"stderr: {p}"
    except OSError:
        pass
    return hint


def _chat_command(messages, deadline):
    """Serialize via a headless LLM CLI. Prompt reaches it per the resolved
    input spec — stdin by default, or arg/file:<flag> for CLIs that don't
    read stdin (DAIMON_LLM_COMMAND_INPUT, #58); runs isolated
    (DAIMON_DISABLE=1, temp cwd). Raises ChatError on any failure — never
    echoes prompt/stdout/stderr (they can carry secrets)."""
    resolved = _resolve_command()
    if not resolved:
        raise ChatError("No command backend (set DAIMON_LLM_COMMAND or install claude).")
    command, output_spec, input_spec = resolved
    argv = shlex.split(command)
    prompt_text = _flatten_messages(messages)
    timeout = config.timeout_seconds()
    if deadline is not None:
        timeout = min(timeout, max(0.0, deadline - time.monotonic()))
        if timeout <= 0:
            raise ChatError("LLM deadline exhausted before command backend")
    env = {**os.environ, "DAIMON_DISABLE": "1"}
    cwd = tempfile.mkdtemp(prefix="daimon-cli-")
    try:
        argv, stdin_text = _apply_input_spec(argv, prompt_text, input_spec, cwd)
        try:
            rc, out, err = _run_command(argv, stdin_text, timeout, env, cwd)
        except FileNotFoundError:
            raise ChatError(f"command backend binary not found: {argv[0]}")
        except subprocess.TimeoutExpired:
            raise ChatError("command backend timed out")
        if rc != 0:
            # stderr stays OFF every wire, but the user's own disk is the same
            # trust domain as the transcript being serialized — discarding it
            # locally turned every backend failure into guesswork (#56, exit
            # 101 in the field with zero diagnostics). Truncate-per-run: the
            # log is "the last failure", not an archive.
            hint = _log_backend_stderr(
                argv[0], err, f"command backend exit {rc} (argv0: {argv[0]})",
                out=out)
            raise ChatError(f"command backend exited {rc} ({hint})")
        try:
            text = _extract_output(out, output_spec)
        except (json.JSONDecodeError, KeyError, TypeError):
            raise ChatError("command backend output unparseable (body suppressed)")
        if not text or not text.strip():
            # rc=0 with nothing to show for it (#225, field incident: 4+ full
            # serialize runs died on this with zero diagnostics — same
            # blind spot #56 fixed for non-zero exits). Same local stderr
            # log, a distinguishable header, and a distinguishable exception
            # type so the serializer can retry it like an empty HTTP 200 body.
            hint = _log_backend_stderr(
                argv[0], err, f"command backend empty output (argv0: {argv[0]})",
                out=out)
            raise EmptyOutputError(f"command backend returned empty output ({hint})")
        log.info("LLM command backend ok argv0=%s", argv[0])
        return text
    finally:
        shutil.rmtree(cwd, ignore_errors=True)
