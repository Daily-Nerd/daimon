"""Onboarding helper: detect the resolved LLM backend and fill config gaps by
writing ~/.daimon/env. Detection is NOT reimplemented — it reuses the real
resolver in llm.py so the doctor view can never disagree with what llm.chat()
would actually run (the single-source-of-truth requirement from #48).

Stdlib only, offline: no live LLM call is made here.
"""

import os
import shutil
import tempfile
from pathlib import Path

from . import config, llm


def resolved_backend() -> str:
    """The backend llm.chat() would actually use. Mirrors its `auto` branch
    exactly — if these diverge, the doctor lies."""
    setting = config.llm_backend()
    if setting != "auto":
        return setting
    if config.llm_api_key():
        return "litellm"
    if llm._resolve_command() is not None:
        return "command"
    return "litellm"   # let _chat_litellm raise the helpful no-key error


def status() -> dict:
    """Detection snapshot for the doctor view. No LLM call."""
    rb = resolved_backend()
    cmd = llm._resolve_command()  # (command_str, output_spec, input_spec) | None
    if rb in ("command", "claude-cli"):
        ready = cmd is not None
    else:  # litellm needs BOTH key and model (matches the serialize pre-flight)
        ready = bool(config.llm_api_key() and config.llm_model())
    return {
        "resolved_backend": rb,
        "ready": ready,
        "claude_on_path": shutil.which("claude") is not None,
        "has_api_key": config.llm_api_key() is not None,
        "has_model": config.llm_model() is not None,
        "command": cmd[0] if cmd else None,
        "input": cmd[2] if cmd else None,  # #58: DAIMON_LLM_COMMAND_INPUT
        "command_source": (
            "explicit" if config.llm_command()
            else ("claude-cli" if cmd else None)
        ),
        "env_file": str(config._env_file_path()),
        "env_file_exists": config._env_file_path().exists(),
    }


def write_env(updates: dict) -> Path:
    """Merge `updates` into ~/.daimon/env (DAIMON_ENV_FILE) and rewrite it as
    sorted KEY=VALUE lines, preserving unrelated pre-existing keys.

    The file is machine-managed: comments/order are NOT preserved (normalized).
    Written atomically (temp + fsync + os.replace) and chmod 600 — it holds API keys.
    Empty merge result -> no file is created (the claude zero-config case writes
    nothing). Returns the target path either way.
    """
    path = config._env_file_path()
    merged = {**config._file_values(), **updates}
    if not merged:
        return path  # nothing to persist -> never create an empty file
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{k}={merged[k]}\n" for k in sorted(merged))
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())  # durable before replace: a power cut must
            # never leave a truncated env file (it holds the backend config)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)
    return path
