"""Packaged Kimi Code hook installer — `daimon hooks install kimi` (#988).

Kimi Code is the first host whose hooks config is TOML rather than JSON, and
that difference is not cosmetic. `~/.kimi-code/config.toml` is written by the
host's own login flow and holds provider credentials and model tables; a
`[[hooks]]` entry in it accepts EXACTLY four fields (`event`, `matcher`,
`command`, `timeout`), and a fifth fails the WHOLE config load rather than
that one entry. So a `statusMessage` copied across from the Codex manager
would not degrade a hook, it would unhook the host and lock the person out of
their own config until they found it.

Two rules follow, and both are load-bearing:

1. **Never re-serialize.** There is no TOML writer in the stdlib, and the
   kernel floor is 3.10 so there is no `tomllib` READER either. Rather than
   take a dependency (the kernel is stdlib forever) or reimplement TOML, this
   module treats the file as TEXT and edits BLOCKS of it. Everything it did
   not write comes back byte-identical: comments, key order, spacing. The
   `_hook_blocks` scanner below is deliberately small, and it is a locator,
   not a parser: it finds where daimon's own entries begin and end and reads
   the handful of scalar fields those entries carry. It does not attempt
   arrays, inline tables, or multi-line strings, because daimon never writes
   them and never needs to read one to find its own blocks.
2. **Exactly four fields, always.**

Ownership keys on the daimon script filename inside `command`, the same rule
the Codex installer uses, so a hand-edited entry pointing at one of our
scripts is still recognised as ours and refreshed rather than duplicated.

Host facts this encodes, all measured on a live Kimi Code 0.42.0 session on
2026-09-09 (see the probe runbook):

- Only `UserPromptSubmit` stdout reaches the model, as a user message wrapped
  in `<hook_result hook_event="UserPromptSubmit">`. `SessionStart` stdout is
  dropped, so registering it would spawn an interpreter per session to write
  into a closed pipe.
- `SessionEnd` fires on interactive exit and NEVER on `kimi -p` print mode
  (measured twice: a print session ends resumable, so it never closes). `Stop`
  fires per turn in both, which is why the throttled Stop capture is not
  optional here the way it is a pure backstop on Codex.
- No `PreToolUse` entry: the deny channel is unmeasured, so no check profile
  ships (see `checks_host.PROFILES`).
- Hooks load at session start only. A person who installs mid-session has to
  start a new one, and the install output says so.
"""

import os
import re
import shutil
import time
from pathlib import Path
from typing import NamedTuple

LIB = "_daimon_hook_lib.py"

# The shared stdlib-only module the scripts load by same-dir lookup. No
# redact.py: like the Codex hooks, these spawn `daimon serialize` and the CLI
# does the redacting, so a redaction module here would be dead weight. No
# checks_*.py either, because no pre-action check ships for this host yet.
MODULES = (LIB,)


class HookSpec(NamedTuple):
    """One registration. The field set is the host's whole vocabulary for a
    `[[hooks]]` entry, so this type IS the four-field rule: there is nowhere to
    put a fifth field even by accident."""

    script: str
    event: str
    matcher: str
    timeout: int


# `matcher` is a regex the host applies to the event's subject; `.*` on all
# three because none of them is tool-scoped.
HOOKS: tuple[HookSpec, ...] = (
    # The only channel into the model. Carries the briefing on a session's
    # first prompt and the per-prompt recall injection after that.
    HookSpec("daimon-kimi-user-prompt-submit.py", "UserPromptSubmit", ".*", 10),
    HookSpec("daimon-kimi-session-end.py", "SessionEnd", ".*", 10),
    HookSpec("daimon-kimi-stop.py", "Stop", ".*", 10),
)

FILES = tuple(spec.script for spec in HOOKS) + MODULES

# Written above each block so a person reading their own config knows what put
# it there and what will take it away. Removal does NOT key on this line (a
# hand-edited config may have lost it); it keys on the command. It is carried
# along on removal only when it sits directly above a block we own.
MARKER = ("# daimon (issue #988): written by `daimon hooks install kimi`. "
          "Remove with `daimon hooks remove kimi`.")

_ARRAY_HEADER_RE = re.compile(r"^\s*\[\[\s*hooks\s*\]\]\s*(?:#.*)?$")
_ANY_HEADER_RE = re.compile(r"^\s*\[")
# One scalar assignment. Values daimon writes are a quoted string or a bare
# integer, which is the whole vocabulary a `[[hooks]]` entry has.
_KV_RE = re.compile(r"""^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*
                        (?:"((?:[^"\\]|\\.)*)"|'([^']*)'|([^\s#]+))
                        \s*(?:\#.*)?$""", re.VERBOSE)


class ConfigError(Exception):
    """The config could not be read well enough to edit it safely.

    Raised rather than repaired. This file holds the person's provider
    credentials, and a writer that guesses at a shape it does not understand
    is one append away from losing them. A refusal leaves them with a working
    host and a message that names the file.
    """


class Block(NamedTuple):
    """One `[[hooks]]` entry located in the file.

    `start`/`end` are line indices into `splitlines(keepends=True)`, half-open,
    covering the header line through the last line that belongs to the entry.
    `fields` holds its scalar assignments as strings.
    """

    start: int
    end: int
    fields: dict


def _env(env):
    return os.environ if env is None else env


def config_home(home, env=None) -> Path:
    """Kimi's config directory: `KIMI_CODE_HOME` when set, else `~/.kimi-code`.

    The host documents that variable for its own user scope (skills, mcp.json),
    so honouring it is what the host itself would do. It is also the override
    that keeps every test in this repo away from a real `~/.kimi-code`.
    """
    override = str(_env(env).get("KIMI_CODE_HOME") or "").strip()
    return Path(override).expanduser() if override else Path(home) / ".kimi-code"


def hooks_dir(home, env=None) -> Path:
    return config_home(home, env) / "hooks"


def config_path(home, env=None) -> Path:
    return config_home(home, env) / "config.toml"


def _hook_blocks(text: str):
    """Every `[[hooks]]` entry in `text`, in file order.

    A block runs from its `[[hooks]]` header to the line before the next table
    header of any kind, or to end of file. Blank lines and comments inside it
    belong to it; a trailing run of blank lines does not, so removing a block
    cannot eat the separation between its neighbours.
    """
    lines = text.splitlines(keepends=True)
    blocks = []
    index = 0
    while index < len(lines):
        if not _ARRAY_HEADER_RE.match(lines[index]):
            index += 1
            continue
        start = index
        fields: dict = {}
        last_content = index
        cursor = index + 1
        while cursor < len(lines) and not _ANY_HEADER_RE.match(lines[cursor]):
            stripped = lines[cursor].strip()
            if stripped and not stripped.startswith("#"):
                match = _KV_RE.match(lines[cursor])
                if match is None:
                    raise ConfigError(
                        f"line {cursor + 1}: cannot read this line inside a "
                        f"[[hooks]] entry: {stripped!r}")
                key = match.group(1)
                value = next(g for g in match.groups()[1:] if g is not None)
                fields[key] = value
                last_content = cursor
            elif stripped.startswith("#"):
                last_content = cursor
            cursor += 1
        blocks.append(Block(start=start, end=last_content + 1, fields=fields))
        index = cursor
    return blocks


def _is_ours(block: Block) -> bool:
    """True when a block's command runs one of daimon's Kimi hook scripts.

    Keyed on the exact script filenames rather than the substring "daimon", so
    an unrelated tool with daimon in its path is never removed by us, and a
    daimon script registered by hand at some other path is still recognised as
    ours and refreshed rather than duplicated.
    """
    command = str(block.fields.get("command") or "")
    return any(spec.script in command for spec in HOOKS)


def _render(spec, hooks_target: Path) -> str:
    """One `[[hooks]]` entry as text. Exactly four fields, in the host's own
    documented order. `timeout` is written bare: it is an integer, and quoting
    it would make the host reject the entry's type."""
    command = f"python3 {hooks_target / spec.script}"
    return (f"{MARKER}\n"
            "[[hooks]]\n"
            f'event = "{spec.event}"\n'
            f'matcher = "{spec.matcher}"\n'
            f'command = "{command}"\n'
            f"timeout = {spec.timeout}\n")


def _strip_ours(lines, blocks):
    """`lines` minus every block we own, plus the marker line directly above
    one and the blank line we inserted before it.

    Deletions run back to front so earlier indices stay valid. Taking the
    marker and the separating blank line back is what makes install/remove a
    true round trip instead of a cycle that grows the file each time.
    """
    out = list(lines)
    for block in sorted(blocks, key=lambda b: b.start, reverse=True):
        start, end = block.start, block.end
        if start > 0 and out[start - 1].strip() == MARKER:
            start -= 1
        if start > 0 and out[start - 1].strip() == "":
            start -= 1
        del out[start:end]
    return out


def _save(path: Path, text: str) -> str | None:
    """Write `text`, backing up any existing file first. Returns the backup
    name, or None when there was nothing to back up (a fresh install)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    note = None
    if path.exists():
        backup = path.with_name(f"config.toml.daimon-backup-{int(time.time())}")
        shutil.copy2(path, backup)
        note = backup.name
    path.write_text(text, encoding="utf-8")
    return note


def install(pkg, home, env=None):
    """Install/refresh the Kimi Code hook integration; return output lines.

    `pkg` is a traversable for `daimon_briefing._hooks`; `home` is the home
    directory, passed in so tests install into a temp HOME. Idempotent:
    re-running refreshes the scripts to match the installed CLI, and rewrites
    a stale registration in place rather than appending a second one.
    """
    target = hooks_dir(home, env)
    target.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        dest = target / name
        dest.write_bytes((pkg / name).read_bytes())
        if name not in MODULES:  # imported by same-dir lookup, never executed
            dest.chmod(dest.stat().st_mode | 0o100)  # u+x

    path = config_path(home, env)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    blocks = _hook_blocks(original)  # raises ConfigError before anything is written
    ours = [b for b in blocks if _is_ours(b)]

    lines = [f"installed {len(FILES)} file(s) to {target}"]
    kept = _strip_ours(original.splitlines(keepends=True), ours)
    body = "".join(kept)
    if body and not body.endswith("\n"):
        body += "\n"
    additions = "".join(f"\n{_render(spec, target)}" for spec in HOOKS)
    updated = body + additions

    if updated == original:
        for spec in HOOKS:
            lines.append(f"  {spec.event}: already registered ({spec.script})")
        lines.append(f"{path} already up to date")
    else:
        verb = "re-registered" if ours else "registered"
        for spec in HOOKS:
            lines.append(f"  {spec.event}: {verb} {spec.script}")
        backup = _save(path, updated)
        lines.append(f"updated {path}" + (f" (backup: {backup})" if backup
                                          else " (new file)"))

    lines += [
        "",
        "Kimi Code loads hooks at session start. Start a NEW session for these "
        "to take effect; a running one will not pick them up.",
        "",
        "Re-run `daimon hooks install kimi` after every "
        "`uv tool upgrade daimon-briefing`.",
    ]
    return lines


def remove(home, env=None):
    """Remove daimon's `[[hooks]]` entries; return output lines.

    Leaves the installed scripts in place. They are inert once unregistered,
    and deleting them would break any OTHER registration a person wrote by
    hand pointing at the same files.
    """
    path = config_path(home, env)
    if not path.exists():
        return [f"{path} does not exist - nothing to remove"]
    original = path.read_text(encoding="utf-8")
    ours = [b for b in _hook_blocks(original) if _is_ours(b)]
    if not ours:
        return [f"{path}: no daimon entries found"]
    updated = "".join(_strip_ours(original.splitlines(keepends=True), ours))
    backup = _save(path, updated)
    return [f"removed {len(ours)} daimon entr(y/ies) from {path}"
            + (f" (backup: {backup})" if backup else "")]


def registration_status(home, env=None) -> str:
    """REGISTERED / PARTIAL / UNREGISTERED for the config.toml entries.

    An unreadable config reads UNREGISTERED rather than raising: this runs
    inside `daimon hooks status`, whose job is to report, and a status verb
    that crashes on the state it exists to describe is worse than one that
    reports the state it can prove.
    """
    path = config_path(home, env)
    if not path.exists():
        return "UNREGISTERED"
    try:
        blocks = _hook_blocks(path.read_text(encoding="utf-8"))
    except (OSError, ConfigError):
        return "UNREGISTERED"
    found = sum(1 for spec in HOOKS
                if any(spec.script in str(b.fields.get("command") or "")
                       for b in blocks))
    if found == 0:
        return "UNREGISTERED"
    return "REGISTERED" if found == len(HOOKS) else "PARTIAL"
