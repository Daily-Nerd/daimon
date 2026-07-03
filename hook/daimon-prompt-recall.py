"""Claude Code UserPromptSubmit hook: proactive 'you worked on this before' (#125).

Shells out to `daimon recall-inject` (the single source of truth for matching,
noise gates, and cooldown) with the prompt on stdin. Anything printed to stdout
is injected as additional context for the turn.

Noise contract — this differs from the SessionStart brief hook on purpose:
this hook fires on EVERY prompt, so failures are SILENT (exit 0, no output).
A diagnostic line per prompt would be spam; the SessionStart hook already
surfaces install problems once per session. The only output this hook ever
produces is a real suggestion.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _daimon_hook_lib as lib
except Exception:  # noqa: BLE001 — missing/corrupt lib: silent no-op (see above)
    lib = None

TIMEOUT = 4  # seconds; hooks.json budget is 5


def main() -> int:
    if lib is None or lib.disabled():
        return 0
    data = lib.payload()
    prompt = str(data.get("prompt") or "")
    # Slash commands are host directives, not work statements — never match.
    if not prompt.strip() or prompt.lstrip().startswith("/"):
        return 0
    cwd = str(data.get("cwd") or "").strip()
    session = str(data.get("session_id") or "").strip()
    cli = lib.resolve_cli()
    if cli is None:
        return 0
    cmd = [cli, "recall-inject"]
    if cwd:
        cmd += ["--project", cwd]
    if session:
        cmd += ["--session", session]
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=TIMEOUT, env=lib.project_env(cwd),
        )
    except (subprocess.TimeoutExpired, OSError):
        return 0
    if proc.returncode == 0 and proc.stdout.strip():
        print(proc.stdout.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
