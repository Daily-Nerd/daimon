# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Minimal OpenAI-compatible chat client for hitting a self-hosted LiteLLM gateway.
Stdlib only (urllib) — no SDK, no secrets in code. Config via env:

    LITELLM_BASE_URL        default http://localhost:4000   (port-forward target)
    LITELLM_API_KEY         your LiteLLM key (master or virtual) — REQUIRED
    LITELLM_MODEL           a model name configured in LiteLLM — REQUIRED for chat()
    LITELLM_CONTEXT_WINDOW  optional int — the active model's context window. When
                            set, chat() runs a pre-flight size check and rejects an
                            oversized prompt before any network call (no stall).

Reach the in-cluster gateway first:
    kubectl port-forward -n <namespace> svc/<litellm-svc> 4000:4000

Discover model names:
    LITELLM_API_KEY=sk-... uv run lib/llm.py            # lists /v1/models
"""

import json
import os
import time
import urllib.error
import urllib.request


class ChatError(RuntimeError):
    """A chat call failed (after retries). Callers can catch this to isolate one item."""


class ContextOverflowError(ChatError):
    """The prompt is estimated to exceed the model's context window. Raised
    pre-flight (before any network call) so an undersized model fails instantly
    instead of stalling until timeout — the ornith failure mode."""


def _estimate_tokens(messages) -> int:
    """Rough token count from total character length (~4 chars/token). Stdlib
    only — no tokenizer dependency. Used solely for the opt-in pre-flight gate;
    callers should pass a context_window with their own safety margin."""
    chars = sum(len(str(m.get("content", ""))) for m in messages)
    return chars // 4 + 1


def _cfg(base_url=None, api_key=None):
    base = (base_url or os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")).rstrip("/")
    key = api_key or os.environ.get("LITELLM_API_KEY")
    if not key:
        raise SystemExit("Set LITELLM_API_KEY (your LiteLLM key). Do NOT hardcode it.")
    return base, key


def _extract_content(data) -> str:
    """Final assistant text from a chat response. Prefer `content`; fall back to
    `reasoning_content` for reasoning models whose gateway leaves `content` empty
    (e.g. deepseek-r1 / ornith served with a reasoning parser)."""
    msg = data["choices"][0]["message"]
    content = msg.get("content") or ""
    if not content.strip():
        content = msg.get("reasoning_content") or ""
    return content


def chat(messages, model=None, base_url=None, api_key=None, temperature=0.0,
         timeout=300, retries=3, context_window=None):
    """POST /v1/chat/completions. Returns (content, usage_dict, model_used).

    Retries genuinely transient failures (connection error, 5xx) with backoff.
    Fails fast — no retry — on errors that won't self-heal: 4xx, and read
    timeouts (a stalled model burns another full `timeout` on retry for the same
    result). Pass `context_window` (or set LITELLM_CONTEXT_WINDOW) to enable a
    pre-flight size check that rejects an oversized prompt before any network
    call. Raises ChatError on giving up.
    """
    base, key = _cfg(base_url, api_key)
    mdl = model or os.environ.get("LITELLM_MODEL")
    if not mdl:
        raise SystemExit("Set LITELLM_MODEL (or pass model=). Run `uv run lib/llm.py` to list options.")

    if context_window is None:
        env_cw = os.environ.get("LITELLM_CONTEXT_WINDOW", "").strip()
        if env_cw:
            try:
                context_window = int(env_cw)
            except ValueError:
                context_window = None  # malformed env → no gate, don't crash a run

    if context_window is not None:
        est = _estimate_tokens(messages)
        if est > context_window:
            raise ContextOverflowError(
                f"Prompt ~{est} tokens exceeds {mdl} context window {context_window}. "
                f"Skipping call — it would stall, not answer. Use a larger-context model."
            )

    body = json.dumps({"model": mdl, "messages": messages, "temperature": temperature}).encode()

    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read())
            return _extract_content(data), data.get("usage", {}), mdl
        except urllib.error.HTTPError as e:
            if 500 <= e.code < 600 and attempt < retries - 1:
                last = f"HTTP {e.code}"
            else:
                raise ChatError(f"LiteLLM HTTP {e.code}: {e.read().decode()[:600]}")
        except TimeoutError:
            raise ChatError(
                f"LiteLLM timed out after {timeout}s at {base} (model {mdl} stalled?). "
                f"No retry — a stall won't self-heal."
            )
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, TimeoutError):  # URLError-wrapped read timeout = same stall
                raise ChatError(
                    f"LiteLLM timed out after {timeout}s at {base} (model {mdl} stalled?). "
                    f"No retry — a stall won't self-heal."
                )
            last = reason
            if attempt == retries - 1:
                raise ChatError(f"LiteLLM unreachable after {retries} tries at {base}: {last}")
        time.sleep(3 * (attempt + 1))  # 3s, 6s backoff
    raise ChatError(f"LiteLLM failed after {retries} tries: {last}")


def list_models(base_url=None, api_key=None, timeout=30):
    base, key = _cfg(base_url, api_key)
    req = urllib.request.Request(base + "/v1/models", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"LiteLLM HTTP {e.code}: {e.read().decode()[:600]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Cannot reach LiteLLM at {base}. Is the port-forward up? ({e.reason})")
    return [m.get("id") for m in data.get("data", [])]


def extract_json(text):
    """Pull a JSON object/array out of a model response, tolerating ```json fences."""
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
    # fall back to bracket spans; try the earliest opening bracket first
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


if __name__ == "__main__":
    print("Models available via LiteLLM:")
    for m in list_models():
        print(f"  - {m}")
