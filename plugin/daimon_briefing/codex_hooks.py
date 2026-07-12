"""Packaged Codex hook installer — the released `daimon hooks install codex`
path (#262).

Codex is unlike the other packaged hosts: instead of a single entry script that
the user registers by hand, it runs two distinct scripts under two events
(SessionStart briefing injection, Stop opportunistic capture) and discovers them
from ``~/.codex/hooks.json``. So this installer BOTH copies the scripts into
``~/.codex/hooks/`` AND writes the registration itself, merging idempotently and
preserving any unrelated entries already in ``hooks.json``.

This adapts the standalone ``hook/codex-hooks.py`` lifecycle manager (which only
runs from a repo clone) into the package. The registration shapes below are kept
in sync with that manager's ``HOOKS`` — the standalone script cannot import this
package (it runs in whatever interpreter Codex invokes, outside the uv-tool
venv), so the shape necessarily lives in both. Idempotency keys on the script
name inside the command string, so a machine that ran the standalone manager and
then the packaged installer never ends up double-registered.
"""

import json
import shutil
import time

LIB = "_daimon_hook_lib.py"

# event -> (script filename, hooks.json registration entry). Byte-for-byte the
# same shapes as hook/codex-hooks.py::HOOKS.
HOOKS = (
    {
        "script": "daimon-codex-session-start.py",
        "event": "SessionStart",
        "entry": {
            "matcher": "startup|resume",
            "hooks": [{
                "type": "command",
                "command": "python3 ~/.codex/hooks/daimon-codex-session-start.py",
                "timeout": 10,
                "statusMessage": "Reading daimon briefing...",
            }],
        },
    },
    {
        "script": "daimon-codex-stop.py",
        "event": "Stop",
        "entry": {
            "hooks": [{
                "type": "command",
                "command": "python3 ~/.codex/hooks/daimon-codex-stop.py",
                "timeout": 10,
                "statusMessage": "Writing daimon checkpoint...",
            }],
        },
    },
)

# Everything installed into ~/.codex/hooks/: the two scripts plus the shared
# stdlib-only helper module they import by same-dir lookup. No redact.py — the
# Codex hooks spawn `daimon serialize` (the CLI redacts) and never scrub at
# their own write sites, so the redaction module they'd load is dead weight.
FILES = tuple(spec["script"] for spec in HOOKS) + (LIB,)


def _is_ours(group, script):
    """True when a hooks.json group already registers our `script`. Keys on the
    command substring so it matches regardless of surrounding entry shape."""
    return any(script in h.get("command", "")
               for h in group.get("hooks", []) if isinstance(h, dict))


def _load(hooks_json):
    """Parse ~/.codex/hooks.json, degrading a missing/corrupt file to {} so a
    fresh or hand-broken install still merges cleanly instead of crashing."""
    if not hooks_json.exists():
        return {}
    try:
        data = json.loads(hooks_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(hooks_json, settings):
    """Write hooks.json, backing up any existing file first so a merge can be
    undone by hand if Codex ever rejects the result."""
    hooks_json.parent.mkdir(parents=True, exist_ok=True)
    if hooks_json.exists():
        backup = hooks_json.with_name(f"hooks.json.daimon-backup-{int(time.time())}")
        shutil.copy2(hooks_json, backup)
    hooks_json.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def install(pkg, home):
    """Install/refresh the Codex hook integration and return the output lines.

    ``pkg`` is a traversable for ``daimon_briefing._hooks`` (importlib.resources
    files() or a plain Path); ``home`` is the home directory, passed in so tests
    can install into a temp HOME. Idempotent: re-running refreshes the scripts to
    match the installed CLI and never duplicates a registration.
    """
    codex_dir = home / ".codex"
    hooks_dir = codex_dir / "hooks"
    hooks_json = codex_dir / "hooks.json"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    for name in FILES:
        dest = hooks_dir / name
        dest.write_bytes((pkg / name).read_bytes())
        if name != LIB:  # the lib is imported, not executed
            dest.chmod(dest.stat().st_mode | 0o100)  # u+x — Codex runs the scripts

    settings = _load(hooks_json)
    hooks_cfg = settings.setdefault("hooks", {})
    lines = [f"installed {len(FILES)} file(s) to {hooks_dir}"]
    changed = False
    for spec in HOOKS:
        groups = hooks_cfg.setdefault(spec["event"], [])
        if any(_is_ours(g, spec["script"]) for g in groups):
            lines.append(f"  {spec['event']}: already registered ({spec['script']})")
        else:
            groups.append(spec["entry"])
            changed = True
            lines.append(f"  {spec['event']}: registered {spec['script']}")
    if changed:
        _save(hooks_json, settings)
        lines.append(f"updated {hooks_json}")
    else:
        lines.append(f"{hooks_json} already up to date")

    lines += [
        "",
        "Open /hooks in Codex to review and trust the hook definitions — "
        "Codex skips untrusted hooks until you do.",
        "",
        "Re-run `daimon hooks install codex` after every "
        "`uv tool upgrade daimon-briefing`.",
    ]
    return lines
