"""Packaged Codex hook installer — the released `daimon hooks install codex`
path (#262).

Codex is unlike the other packaged hosts: instead of a single entry script that
the user registers by hand, it runs several scripts under several events
(SessionStart briefing injection, SessionEnd and Stop capture, PreToolUse check
enforcement) and discovers them from ``~/.codex/hooks.json``. So this installer
BOTH copies the scripts into ``~/.codex/hooks/`` AND writes the registration
itself, merging idempotently and preserving any unrelated entries already in
``hooks.json``.

This adapts the standalone ``hook/codex-hooks.py`` lifecycle manager (which only
runs from a repo clone) into the package. The registration shapes below are kept
in sync with that manager's ``HOOKS`` — the standalone script cannot import this
package (it runs in whatever interpreter Codex invokes, outside the uv-tool
venv), so the shape necessarily lives in both. Idempotency keys on the script
name inside the command string, so a machine that ran the standalone manager and
then the packaged installer never ends up double-registered.
"""

import json
import re
import shutil
import time
from pathlib import Path

LIB = "_daimon_hook_lib.py"

# ---- #1036 parity: the read-only MCP server registration ------------------
#
# Codex's MCP config lives in a SEPARATE file, ~/.codex/config.toml, under
# [mcp_servers.<name>] — confirmed live (`codex mcp add --help`; a real
# config.toml on this machine already carries [mcp_servers.obsidian] and
# [mcp_servers.computer-use] in exactly this shape). That file holds provider
# credentials and per-project trust state, so the same rule kimi_hooks.py
# states applies here: never re-serialize it. This module treats it as text
# and edits ONE block, [mcp_servers.daimon]; everything else comes back
# byte-identical.
#
# The wrapper it points at is the SAME hook/daimon-mcp-serve.py the Claude
# Code plugin manifest runs (one resolver, not two): it calls
# _daimon_hook_lib.resolve_cli() at invocation time rather than baking a path
# into config.toml that would go stale the moment the CLI moves.
MCP_SCRIPT = "daimon-mcp-serve.py"
MCP_MARKER = ("# daimon (issue #1036): written by `daimon hooks install "
              "codex`. Remove with `daimon hooks remove codex`.")
_MCP_HEADER_RE = re.compile(r"^\s*\[mcp_servers\.daimon\]\s*(?:#.*)?$")
_ANY_HEADER_RE = re.compile(r"^\s*\[")

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
        "script": "daimon-codex-session-end.py",
        "event": "SessionEnd",
        "entry": {
            "hooks": [{
                "type": "command",
                "command": "python3 ~/.codex/hooks/daimon-codex-session-end.py",
                # Codex clamps a timeout above 3 with a user-visible warning,
                # and omitting it silently yields 1, which a cold interpreter
                # start on a loaded machine can exceed. Set it explicitly.
                "timeout": 3,
                "statusMessage": "Checkpointing session...",
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
    {
        # #943: the pre-action check. `matcher` keeps it to shell actions,
        # which Codex names both ways depending on the build. No
        # statusMessage: the other three fire once a session, this one fires
        # before every command. Timeout 10 against a runner budget of 5, so
        # the runner decides before Codex gives up and lets the action
        # through.
        "script": "daimon-codex-pre-action.py",
        "event": "PreToolUse",
        "entry": {
            "matcher": "Bash|shell",
            "hooks": [{
                "type": "command",
                "command": "python3 ~/.codex/hooks/daimon-codex-pre-action.py",
                "timeout": 10,
            }],
        },
    },
    {
        # #1031: action-keyed recall, a SECOND PreToolUse registration rather
        # than a branch inside the pre-action hook. That hook is the only one
        # that can deny, and a recall fault inside it would drop the deny in a
        # way byte-identical to a clean allow. Registered AFTER it so the
        # deny path runs first. Timeout 5 against the shim's own 1.5s
        # subprocess budget. The host is an argument, so one file serves
        # every host.
        "script": "daimon-action-recall.py",
        "event": "PreToolUse",
        "entry": {
            "matcher": "Bash|shell",
            "hooks": [{
                "type": "command",
                "command": "python3 ~/.codex/hooks/daimon-action-recall.py codex",
                "timeout": 5,
            }],
        },
    },
)

# Everything installed into ~/.codex/hooks/: the scripts plus the shared
# stdlib-only modules they import by same-dir lookup. No redact.py — the
# Codex hooks spawn `daimon serialize` (the CLI redacts) and never scrub at
# their own write sites, so the redaction module they'd load is dead weight.
#
# #943 added the last two. The pre-action hook loads checks_host.py from its
# own directory and that loads checks_runtime.py the same way, so a script
# installed without them is a hook that finds nothing and allows in silence.
# They are imported and never executed, which is why the +x below skips them.
MODULES = (LIB, "checks_runtime.py", "checks_host.py")
FILES = tuple(spec["script"] for spec in HOOKS) + MODULES


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


def _newline(text: str) -> str:
    """The file's own line ending, so an appended block matches the lines
    already there rather than mixing endings into one file."""
    return "\r\n" if "\r\n" in text else "\n"


def _read_toml(path) -> str:
    """Bytes, decoded, no newline translation — `read_text` folds CRLF to LF
    and the round trip would then write LF back over a CRLF file."""
    return path.read_bytes().decode("utf-8")


def _mcp_block(lines):
    """(start, end) half-open line-index span of the daimon [mcp_servers.
    daimon] table, including its marker and the blank line above it when
    present, or None. A locator, never a parser: everything outside this
    span is opaque text this module does not read."""
    for i, line in enumerate(lines):
        if _MCP_HEADER_RE.match(line):
            end = i + 1
            while end < len(lines) and not _ANY_HEADER_RE.match(lines[end]):
                end += 1
            start = i
            if start > 0 and lines[start - 1].strip() == MCP_MARKER:
                start -= 1
            if start > 0 and lines[start - 1].strip() == "":
                start -= 1
            return start, end
    return None


def _render_mcp(script_path, nl: str) -> str:
    return (f"{MCP_MARKER}{nl}"
            f"[mcp_servers.daimon]{nl}"
            f'command = "python3"{nl}'
            f'args = ["{script_path}"]{nl}')


def _save_toml(path, text: str) -> str | None:
    """Write `text`, backing up any existing file first. Returns the backup
    name, or None when there was nothing to back up (a fresh file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    note = None
    if path.exists():
        backup = path.with_name(f"config.toml.daimon-backup-{int(time.time())}")
        shutil.copy2(path, backup)
        note = backup.name
    path.write_bytes(text.encode("utf-8"))
    return note


def config_toml_path(home) -> Path:
    return Path(home) / ".codex" / "config.toml"


def install_mcp(pkg, home) -> list[str]:
    """Install/refresh the read-only MCP server registration for Codex.

    Copies the resolver wrapper (the same script the Claude Code plugin
    manifest runs) into ~/.codex/hooks/, then writes or refreshes exactly one
    table in ~/.codex/config.toml. Never touches anything else in that file.
    """
    hooks_target = Path(home) / ".codex" / "hooks"
    hooks_target.mkdir(parents=True, exist_ok=True)
    dest = hooks_target / MCP_SCRIPT
    dest.write_bytes((pkg / MCP_SCRIPT).read_bytes())
    dest.chmod(dest.stat().st_mode | 0o100)  # u+x — Codex execs it directly

    path = config_toml_path(home)
    original = _read_toml(path) if path.exists() else ""
    nl = _newline(original)
    lines = original.splitlines(keepends=True)
    block = _mcp_block(lines)
    rendered = _render_mcp(dest, nl)
    if block is None:
        body = "".join(lines)
        if body and not body.endswith(("\n", "\r\n")):
            body += nl
        updated = body + (nl if body else "") + rendered
    else:
        start, end = block
        updated = "".join(lines[:start]) + rendered + "".join(lines[end:])

    out = [f"installed {MCP_SCRIPT} to {hooks_target}"]
    if updated == original:
        out.append("  mcp_servers.daimon: already registered")
        out.append(f"{path} already up to date")
    else:
        out.append(f"  mcp_servers.daimon: {'refreshed' if block else 'registered'}")
        backup = _save_toml(path, updated)
        out.append(f"updated {path}"
                   + (f" (backup: {backup})" if backup else " (new file)"))
    return out


def mcp_registered(home) -> bool:
    """True when config.toml already carries the [mcp_servers.daimon] table.
    An unreadable file reads as not-registered rather than raising: this
    feeds `daimon hooks status`, whose job is to report a state, not crash
    on it."""
    path = config_toml_path(home)
    if not path.exists():
        return False
    try:
        lines = _read_toml(path).splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return False
    return _mcp_block(lines) is not None


def remove_mcp(home) -> list[str]:
    """Remove daimon's [mcp_servers.daimon] table; return output lines.

    Leaves the installed wrapper script in place, same reasoning as
    kimi_hooks.remove: it is inert once unregistered, and deleting it would
    break a hand-written registration pointing at the same file.
    """
    path = config_toml_path(home)
    if not path.exists():
        return [f"{path} does not exist - nothing to remove"]
    original = _read_toml(path)
    lines = original.splitlines(keepends=True)
    block = _mcp_block(lines)
    if block is None:
        return [f"{path}: no daimon mcp_servers entry found"]
    start, end = block
    updated = "".join(lines[:start]) + "".join(lines[end:])
    backup = _save_toml(path, updated)
    return [f"removed mcp_servers.daimon from {path}"
            + (f" (backup: {backup})" if backup else "")]


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
        if name not in MODULES:  # imported, never executed
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

    # #1036 parity: same install verb also gets the MCP server, riding this
    # command rather than a separate one — one `daimon hooks install codex`
    # leaves the operator with both the hooks and the read-only tool surface.
    lines.append("")
    lines += install_mcp(pkg, home)

    lines += [
        "",
        "Open /hooks in Codex to review and trust the hook definitions — "
        "Codex skips untrusted hooks until you do.",
        "",
        "Re-run `daimon hooks install codex` after every "
        "`uv tool upgrade daimon-briefing`.",
    ]
    return lines
