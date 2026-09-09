#!/usr/bin/env python3
"""Kimi Code UserPromptSubmit hook: the ONLY channel into the model.

Measured on 0.42.0 (2026-09-09): of every event that fires, only this one's
stdout reaches the session, arriving as a user message wrapped in
`<hook_result hook_event="UserPromptSubmit">`. `SessionStart` fires and its
stdout is dropped. So this hook carries both jobs the other hosts split
between two events:

1. The briefing, on the FIRST prompt of a session. Kimi offers no usable
   session-start event, so "first" is kept here as a per-session marker rather
   than inferred from an event. The consequence a person sees is that the
   briefing arrives with their first message instead of before it, and the
   host page says so.
2. The per-prompt recall injection, on every prompt after that, shelling out
   to `daimon recall-inject` exactly as the Claude Code prompt hook does.

Noise contract, copied deliberately from daimon-prompt-recall.py: this fires
on EVERY prompt, so failures are SILENT (exit 0, no output). A diagnostic per
prompt would be spam. Unlike on Claude Code there is no SessionStart hook to
surface install problems once a session, so a broken install here is quiet by
design; `daimon hooks status` is the surface that reports it.

Exit code discipline matters more here than on the capture hooks.
`UserPromptSubmit` is BLOCKABLE on this host: a non-zero exit is how a hook
refuses the person's prompt. Every path returns 0.
"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _daimon_hook_lib as lib
except Exception:  # noqa: BLE001 — missing/corrupt lib: silent no-op (see above)
    lib = None

STATE_DIR = Path.home() / ".daimon" / "kimi"
BRIEF_TIMEOUT = 8   # the host's registered timeout is 10
RECALL_TIMEOUT = 4
DELIVERY_TIMEOUT = 1.5


def _safe_name(session_id: str) -> str:
    return session_id.replace("/", "_").replace("\\", "_").replace("..", "_")


def _claim_first_prompt(session_id: str) -> bool:
    """True exactly once per session: the prompt that gets the briefing.

    Uses `open(..., "x")`, which is atomic on every platform daimon runs on, so
    two prompts racing in the same session cannot both win and inject the
    briefing twice. A marker that cannot be written at all yields False rather
    than True: repeating a briefing on every prompt would be worse than never
    showing it, because it would push the real conversation out of context.
    """
    if not session_id:
        return False
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with (STATE_DIR / f"{_safe_name(session_id)}.briefed").open("x") as f:
            f.write(str(int(time.time())))
        return True
    except FileExistsError:
        return False
    except OSError:
        return False


def _brief(cli, cwd: str) -> str:
    """The rendered briefing, or "" when there is nothing to say.

    Shells out to `daimon brief` so the renderer stays single-source-of-truth,
    matching the Claude Code and Codex hook paths. Plain text, no JSON
    envelope: this host appends what the hook prints and wraps it itself.
    """
    slug = lib.slug(cwd)
    ckpt_dir = lib.checkpoint_dir()
    latest = ckpt_dir / slug / "latest.json" if slug else None
    fallback = False
    if latest is None or not latest.exists():
        fallback = latest is not None
        latest = ckpt_dir / "latest.json"
    if not latest.exists() or cli is None:
        return ""
    try:
        proc = subprocess.run([cli, "brief"], capture_output=True, text=True,
                              timeout=BRIEF_TIMEOUT, env=lib.project_env(cwd))
    except (subprocess.TimeoutExpired, OSError):
        return ""
    text = proc.stdout.strip()
    if proc.returncode != 0 or not text or text.startswith("No checkpoint yet"):
        return ""
    suffix = (" (global fallback - checkpoint may be from another project)"
              if fallback else "")
    return f"DAIMON BRIEFING {lib.age_line(latest)}{suffix}\n{text}"


def _deliver(cli, cwd: str, session: str) -> None:
    """Any undecided ask addressed to this project that this session has not
    been shown (#756). Silent on every failure, like everything on this path."""
    if not session:
        return  # the session id is half the delivery write-once key
    cmd = [cli, "request-inject", "--session", session]
    if cwd:
        cmd += ["--project", cwd]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=DELIVERY_TIMEOUT, env=lib.project_env(cwd))
    except (subprocess.TimeoutExpired, OSError):
        return
    if proc.returncode == 0 and proc.stdout.strip():
        print(proc.stdout.strip())


def _recall(cli, cwd: str, session: str, prompt: str) -> None:
    # Slash commands are host directives, not work statements — never match.
    if not prompt.strip() or prompt.lstrip().startswith("/"):
        return
    cmd = [cli, "recall-inject"]
    if cwd:
        cmd += ["--project", cwd]
    if session:
        cmd += ["--session", session]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              timeout=RECALL_TIMEOUT, env=lib.project_env(cwd))
    except (subprocess.TimeoutExpired, OSError):
        return
    if proc.returncode == 0 and proc.stdout.strip():
        print(proc.stdout.strip())


def main() -> int:
    if lib is None or lib.disabled():
        return 0
    data = lib.payload()
    cwd = str(data.get("cwd") or "").strip()
    session = str(data.get("session_id") or "").strip()
    # Kimi sends `prompt` as a LIST of content parts, where every other host
    # sends a string. Unflattened it would reach recall-inject as a repr.
    prompt = lib.kimi_prompt_text(data.get("prompt"))
    cli = lib.resolve_cli()
    if cli is None:
        return 0

    if _claim_first_prompt(session):
        text = _brief(cwd=cwd, cli=cli)
        if text:
            print(text)
        # The briefing already answers "where were we", so recall on the same
        # prompt would restate it. Delivery still runs: an undecided ask is
        # owed regardless of what the person typed.
        if (lib._config_get("DAIMON_LIVE_DELIVERY") or "").strip() in (
                "1", "true", "yes", "on"):
            _deliver(cli, cwd, session)
        # Opportunistic self-heal of a previously FAILED serialize (#26). This
        # is the closest thing to a session start this host offers, and a
        # failed serialize leaves no checkpoint, which is exactly the case
        # where heal matters. Runs last: it can never affect what was printed.
        lib.spawn_heal(cli, cwd)
        return 0

    if (lib._config_get("DAIMON_LIVE_DELIVERY") or "").strip() in (
            "1", "true", "yes", "on"):
        _deliver(cli, cwd, session)
    _recall(cli, cwd, session, prompt)
    return 0


if __name__ == "__main__":
    # Exit 0 on ANY error: a non-zero exit from this event refuses the
    # person's prompt on this host.
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        sys.exit(0)
