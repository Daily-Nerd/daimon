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
from datetime import datetime, timezone

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


class DeadlineExhausted(ChatError):
    """The shared serialize budget ran out — daimon's own clock, not a backend
    failure (#533). A ChatError subclass so every `except ChatError` caller is
    unaffected; distinguishable so chat()'s fallback branch can log budget
    expiry as budget expiry instead of blaming a healthy gateway."""


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
            raise DeadlineExhausted(f"LLM deadline exhausted after {attempt} tries: {last}")
        chunk = r.read1(_READ_CHUNK_BYTES)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _looks_like_sse(raw: bytes) -> bool:
    """True when a response body is an SSE stream rather than one JSON object.

    Sniffs the first non-blank line for the two things a stream can open with:
    a `data:` field or a `:` comment (gateways send `: ping` keep-alives).
    Sniffing the body instead of trusting a Content-Type header keeps the
    fake-response test seam (io.BytesIO, no headers) and misbehaving gateways
    on the same honest path."""
    for line in raw.split(b"\n"):
        s = line.strip()
        if not s:
            continue
        return s.startswith(b"data:") or s.startswith(b":")
    return False


def _parse_sse(raw: bytes):
    """Fold an OpenAI-style SSE body into (content, served_model, usage).

    `content` is the concatenated `choices[0].delta.content` across frames,
    or None when no frame carried any (distinct from "" — callers must treat
    a content-free stream as an error, not an answer). `served_model` is the
    first frame's `model` field; `usage` the last frame that carried one
    (stream_options.include_usage delivers it in a trailing frame with no
    choices). Torn or non-JSON frames are skipped — one bad frame must not
    sink a call whose remaining frames carry the completion."""
    parts = []
    served = None
    usage = {}
    saw_content = False
    for line in raw.decode("utf-8", errors="replace").split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue  # blank separators and `:` comment lines
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            break
        try:
            frame = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(frame, dict):
            continue
        if served is None:
            m = frame.get("model")
            if isinstance(m, str) and m.strip():
                served = m
        if frame.get("usage"):
            usage = frame["usage"]
        choices = frame.get("choices") or []
        if choices:
            delta = choices[0].get("delta") or {}
            piece = delta.get("content")
            if isinstance(piece, str):
                parts.append(piece)
                saw_content = True
    return ("".join(parts) if saw_content else None), served, usage


def _provider_field_flags(usage: dict) -> str:
    """#535: name which provider-specific usage fields the response carried.

    The served-model stamp is a gateway alias and proves nothing (scar 0032);
    Anthropic-served responses carry usage fields typical local
    OpenAI-compatible servers do not. Logging their PRESENCE (never their
    values — presence is the discriminator) makes a silent model substitution
    catchable from the log with one grep: an alias claiming an Anthropic
    model whose calls all say `provider_fields=none` was not served by one.
    Absence must be stated explicitly ("none") — the signal cannot be
    expressed by omission."""
    present = []
    if "cache_creation_input_tokens" in usage:
        present.append("cache_creation")
    if "cache_read_input_tokens" in usage:
        present.append("cache_read")
    if "reasoning_tokens" in (usage.get("completion_tokens_details") or {}):
        present.append("reasoning")
    return ",".join(present) if present else "none"


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
    if config.llm_stream():
        # #531: without streaming the server emits zero bytes until the whole
        # completion is done, so urlopen's socket timeout becomes a hard
        # ceiling on total generation time — merge-sized completions cross it
        # at normal throughput and get killed healthy. Streamed, the socket
        # timeout bounds the inter-frame gap (measured max 0.31s on a live
        # gateway) and guards what it was always meant to: a dead connection.
        # stream_options keeps the usage block arriving (in a trailing
        # frame), so the per-call spend log line below stays lit.
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
    body = json.dumps(payload).encode()
    last = None
    for attempt in range(retries):
        attempt_timeout = timeout
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DeadlineExhausted(f"LLM deadline exhausted after {attempt} tries: {last}")
            attempt_timeout = min(timeout, remaining)
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=attempt_timeout) as r:
                raw = _read_within_deadline(r, deadline, attempt, last)
            if _looks_like_sse(raw):
                content, served, usage = _parse_sse(raw)
                if content is None:
                    # A stream that closed having delivered no content is the
                    # SSE twin of an empty HTTP 200 body — never return "".
                    raise ChatError("LLM stream closed with no content (body suppressed)")
            else:
                # Plain JSON — either streaming is off, or the gateway ignored
                # `stream: true` and answered with one object. Serve it.
                data = json.loads(raw)
                content = data["choices"][0]["message"]["content"]
                served = data.get("model")
                usage = data.get("usage") or {}
            # #458 / scar 0032: record the model the response SAYS served this
            # call — the requested `mdl` is a gateway alias and proves nothing.
            # list.append is atomic under the GIL, so concurrent chunk threads
            # record safely; served_models() dedupes/sorts on read. Absent or
            # non-string field -> record nothing (honest absence, no guessing).
            if isinstance(served, str) and served.strip():
                _served_models.append(served.strip())
            # Surface token cost — the serializer discards the rest of the
            # response, so this log line is the only record of per-call spend.
            if usage:
                log.info("LLM usage model=%s served=%s total_tokens=%s prompt=%s completion=%s provider_fields=%s",
                         mdl, served, usage.get("total_tokens"),
                         usage.get("prompt_tokens"), usage.get("completion_tokens"),
                         _provider_field_flags(usage))
            return content
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
            raise DeadlineExhausted(f"LLM deadline exhausted after {attempt + 1} tries: {last}")
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


# #458 / scar 0032: the requested model name is ROUTING CONFIG, not provenance.
# Behind an OpenAI-compatible gateway it is an alias, and gateways run silent
# fallback chains — the call succeeds, a different model serves it, no error
# is raised (2026-07-30 live: the configured alias was served by a local
# Qwen3-30B; the response's own `model` field said so). That field is the only
# per-call truth, so _chat_litellm records it below. Module-sticky accessors
# deliberately mirror the #28 fallback pattern above: chat()'s `-> str`
# contract is consumed by every injectable-chat seam (serializer, cli, bench,
# test fakes) and must not change shape. reset_served_models() at the start of
# a unit of work; served_models() when stamping. The command backend exposes
# NO served-model info and records nothing — honest absence, never the
# requested name copied into the served slot.
_served_models: list = []


def served_models() -> list:
    """Distinct served-model names observed since the last reset, sorted —
    deterministic stamp order regardless of chunk-thread completion order."""
    return sorted(set(_served_models))


def reset_served_models() -> None:
    # Clear in place: concurrent chunk threads hold the same list object.
    del _served_models[:]


def note_served(name) -> None:
    """Record a served-model observation that did not come off this process's
    wire — today only #465's chunk-cache replay.

    A replayed cached chunk's recorded producer is a genuine observation of
    "a model whose output is in this checkpoint": the content joins the
    serialize exactly like a live call's would. Folding it into the same
    collector makes the EXISTING mixed-run detection (the serializer's
    substitution WARNING + `serialize:model-substituted` counter +
    `llm_model_served` stamp) cover replay-then-live-substitution with no new
    stamp logic. Same append-under-GIL contract as _chat_litellm's record; same
    honest-absence rule — a non-string or blank name records NOTHING rather
    than guessing (scar 0032)."""
    if isinstance(name, str) and name.strip():
        _served_models.append(name.strip())


def resolve_backend() -> dict:
    """The single source of truth for which backend serves a chat() call.

    {"backend": "litellm"|"command"|"claude-cli",
     "source": "explicit"|"auto-key"|"auto-command"|"auto-none"}

    chat() calls this instead of re-deriving the "auto" cascade inline —
    daimon#475's rescue-posture code calls it too, and any other caller must,
    rather than re-implementing the decision a second time (two copies of
    one decision drift the moment either changes).

    Mirrors chat()'s dispatch exactly:
    - config.llm_backend() returns an explicit value ("litellm", "command",
      "claude-cli") -> that backend, source "explicit".
    - "auto" + config.llm_api_key() truthy -> "litellm", source "auto-key".
    - "auto" + no key + _resolve_command() resolves -> "command", source
      "auto-command".
    - "auto" + no key + nothing resolves -> "litellm", source "auto-none".
      This is the branch where litellm is picked ONLY so _chat_litellm
      raises its helpful no-key error — `source` is what lets callers tell
      this apart from a real litellm install, and is the whole reason
      `source` exists. Do not collapse it.
    """
    backend = config.llm_backend()
    if backend != "auto":
        return {"backend": backend, "source": "explicit"}
    if config.llm_api_key():
        return {"backend": "litellm", "source": "auto-key"}
    if _resolve_command() is not None:
        return {"backend": "command", "source": "auto-command"}
    return {"backend": "litellm", "source": "auto-none"}


def rescue_posture() -> str:
    """Whether a rescue path exists for the CURRENTLY CONFIGURED primary
    (daimon#475 part 2) — derived from resolve_backend(), never re-derived.

    Field evidence: a `command`-backend install ran a 78% capture error rate
    for 14 days while `daimon stats` showed `attempted 0, succeeded 0` —
    indistinguishable from "a rescue existed and was never needed". Posture
    is the missing fact: `chat()` returns from `_chat_command` at the top of
    its dispatch, before the litellm/fallback try block even exists, so a
    `command` primary has NO rescue direction by construction — no flag can
    conjure one.

    Five states, checked in this order (order matters):
    - "none": the resolved backend is "command" or "claude-cli" — both
      dispatch through _chat_command, which has no fallback branch at all.
      Checked FIRST because a command backend needs no credentials, so the
      no-backend test below must not claim it. An unknown backend string
      (config.llm_backend() is free text) is NOT "command" or "claude-cli",
      so it falls through to the litellm-family checks below — mirroring
      chat()'s own dispatch, which treats any other string as the litellm
      branch. Same rule, same edge case, same answer: no crash, no sixth
      state.
    - "no-backend": litellm family with neither credentials nor a resolvable
      command — nothing can serve a call, so there is no rescue question to
      answer and this must NOT nag about a missing fallback.

      This deliberately does NOT key on resolve_backend()'s "auto-none"
      source, which covers only the `auto` route to that state. Explicit
      `DAIMON_LLM_BACKEND=litellm` with no key reaches the identical reality
      by a different route, and keying on the source alone reported it as
      "gap" — advising the operator to install a fallback when what they
      actually lack is an API key. Verified against every backend/key/
      fallback/command combination: the source-only test changed
      rescue_gap's answer on real configurations, this one does not.
    - "disabled": litellm family, but config.llm_fallback() is off — the
      operator turned rescue off deliberately.
    - "covered": litellm family, fallback on, and a command backend
      resolves — a rescue path exists.
    - "gap": litellm family, fallback on, nothing resolves — the #341
      warning case.
    """
    resolved = resolve_backend()
    if resolved["backend"] in ("command", "claude-cli"):
        return "none"
    if not config.llm_api_key() and _resolve_command() is None:
        return "no-backend"
    if not config.llm_fallback():
        return "disabled"
    if _resolve_command() is not None:
        return "covered"
    return "gap"


def chat(messages, model=None, temperature=None, timeout=None, retries=3, deadline=None):
    """Dispatch to the configured backend. litellm (default) falls back to a
    command backend on ChatError when fallback is enabled and one resolves."""
    global _fallback_used
    backend = resolve_backend()["backend"]
    if backend in ("command", "claude-cli"):
        return _chat_command(messages, deadline)
    try:
        return _chat_litellm(messages, model=model, temperature=temperature,
                             timeout=timeout, retries=retries, deadline=deadline)
    except ChatError as e:
        if config.llm_fallback() and _resolve_command() is not None:
            # #533: budget expiry is daimon's own clock, not a gateway error —
            # the label decides which of two very different things gets debugged.
            reason = ("serialize deadline expired"
                      if isinstance(e, DeadlineExhausted) else "litellm failed")
            log.warning("llm.fallback backend=command (%s)", reason)
            _fallback_used = True
            if deadline is not None:
                # #341: the primary just drained the shared budget retrying
                # the failing gateway — handing the fallback the remainder
                # kills it on arrival in exactly the outage it exists to
                # rescue. Re-arm to at least the configured floor; a healthy
                # remaining budget is never shrunk.
                deadline = max(deadline,
                               time.monotonic() + config.fallback_min_seconds())
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


# #474 reverses #56's truncate-per-run rule on field evidence: 143 lifetime
# errors left exactly ONE line, and a question about them stayed open nine days
# while every new failure erased the evidence for the last. The log is now a
# BOUNDED APPEND — a byte cap trimmed oldest-first, chosen over rotation
# because rotation is more machinery (numbering, N-file cleanup, a second path
# every reader has to know about) for the same "cannot grow without limit"
# guarantee. 512 KiB is thousands of entries and rounding error next to one
# transcript.
_STDERR_LOG_MAX_BYTES = 512 * 1024


def _log_backend_stderr(argv0, err, header, out=None) -> str:
    """Append redacted command-backend diagnostics to backend-stderr.log and
    return a hint embeddable in a ChatError message: the log path on success,
    "stderr suppressed" on any OSError — logging must never mask the real
    failure (fail-open on the logging seam, #225).

    The log is an ARCHIVE bounded to the last _STDERR_LOG_MAX_BYTES, each entry
    UTC-stamped so it correlates with serialize.log and checkpoint ages. It was
    truncate-per-run — "the last failure", not an archive — until the field
    showed a repeating failure destroying its own history exactly when the
    repetition IS the diagnosis (#56, reversed on evidence by #474).

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
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = f"--- {stamp} ---\n{header}\n{err_logged}\n"
        if out is not None:
            out_logged, _ = redact.redact_text(out)
            body += f"--- stdout ---\n{out_logged}\n"
        try:
            prior = p.read_bytes()
        except OSError:
            prior = b""  # absent (first failure) or unreadable: still log this one
        raw = prior + body.encode("utf-8")
        if len(raw) > _STDERR_LOG_MAX_BYTES:
            raw = raw[-_STDERR_LOG_MAX_BYTES:]
            # The tail slice can land mid-character; dropping through the first
            # newline discards those bytes with the half entry they belong to.
            nl = raw.find(b"\n")
            raw = raw[nl + 1:] if nl != -1 else b""
        p.write_bytes(raw)
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
            raise DeadlineExhausted("LLM deadline exhausted before command backend")
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
            # 101 in the field with zero diagnostics). Bounded append: a
            # repeating failure must not erase its own history (#474).
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
