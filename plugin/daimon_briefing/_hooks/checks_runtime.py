"""The #943 check runtime — stdlib only, loadable by a standalone hook.

A host's pre-action hook runs in whatever interpreter the host launched, with
no venv and no `daimon_briefing` on `sys.path`. So the part of the check
machinery that runs INSIDE a hook lives here, and this file ships
byte-identical into `_hooks/` and `hook/` the way `redact.py` does.

Two rules follow from that, both scars:

  * stdlib ONLY (scar 0049). `from . import config` looks completely ordinary
    from inside the package, passes every local test, and breaks every host
    hook. The path accessors below are therefore hand-copied mirrors of
    `config.checks_dir` / `config.log_dir`, and a probe table in
    `test_checks_runtime.py` asserts behavioral equality against the config
    functions rather than against literals.
  * the canonical file is THIS one and the copies are derivatives (scar 0049
    again, which reverses scar 0044's direction for the adapter scripts).
    Edit here, then run `scripts/sync_hooks.py`.

Nothing here raises. The hook that calls it fires before every shell action
on the host, so an exception is an action that proceeds with no record of
why. Every function returns a value that says what happened, including when
what happened is that daimon could not tell.
"""

import json
import os
import re
from pathlib import Path
from typing import NamedTuple

MANIFEST_NAME = "manifest.json"
FIRING_LOG_NAME = "checks.jsonl"

# Scar 0022 applied to the SUBJECT rather than the pattern. `check.match` is
# caller-supplied and capped at 200 bytes, which bounds its length and says
# nothing about its backtracking. Bounding the string it runs against is what
# keeps a pathological pair from outliving the host's hook timeout, and that
# timeout is fail-open: a hook that reaches it does not block, the tool
# proceeds. This is the one known bound and it is documented as such.
MATCH_INPUT_BYTES = 4096


class Manifest(NamedTuple):
    """What `load_manifest` found. `reason` is empty when the manifest was
    read; otherwise it names the state for the firing log, because "no
    manifest" and "a manifest daimon can no longer parse" are different
    facts and folding them would report an unwritten install as a broken
    one."""
    entries: list
    reason: str


# ---- config mirrors (scar 0043: copy the QUIRKS, not the intent) ----------


def _env_file_path() -> Path:
    """Mirror of daimon_briefing.config._env_file_path."""
    raw = os.environ.get("DAIMON_ENV_FILE")
    return Path(raw).expanduser() if raw else Path.home() / ".daimon" / "env"


def _env_file_values() -> dict:
    """Mirror of daimon_briefing.config._file_values — KEY=VALUE lines, with
    the `export ` prefix, surrounding quotes, blank lines and `#` comments
    tolerated. Copied rather than imported because hooks run standalone;
    config.py is the CANONICAL form and this copy must follow it line-form
    for line-form."""
    try:
        text = _env_file_path().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key:
            values[key] = val
    return values


def _config_get(name: str) -> str:
    """Mirror of daimon_briefing.config._get: process env WINS, env file is
    the fallback. Present-but-empty in the process env is a value and
    shadows the file, exactly as on the package side."""
    val = os.environ.get(name)
    if val is not None:
        return val
    return _env_file_values().get(name) or ""


def checks_dir() -> Path:
    """Where the manifest and the materialized bodies live, resolved the way
    the WRITER resolves it.

    `checks.sync` writes through config.checks_dir(); this reads. A path
    resolved any other way here has the hook reading an empty directory
    while the CLI reports N checks armed, and neither side can see the
    split — the same writer/reader gap scar 0043 records one directory over.
    So this reproduces config.checks_dir() exactly, including the parts that
    look like bugs: no strip (so "   " is a relative directory named three
    spaces, and the reader must agree), and the env-file fallback, which is
    the channel a GUI-launched host actually uses."""
    raw = _config_get("DAIMON_CHECKS_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".daimon" / "checks"


def log_dir() -> Path:
    """Mirror of config.log_dir(), same reasoning as checks_dir(): the
    firing log sits under it and `daimon forget` purges through the config
    accessor."""
    raw = _config_get("DAIMON_LOG_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".daimon" / "logs"


# ---- manifest -------------------------------------------------------------


def load_manifest(path=None) -> Manifest:
    """Read the armed-check manifest. Never raises, never partially fails.

    An absent manifest is the ordinary state of an install that has armed
    nothing, so it reads as empty with `no-manifest` rather than as an
    error. A manifest that exists and cannot be parsed is a different fact
    and gets its own reason: both allow the action, and only one of them
    means daimon wrote something it can no longer read."""
    target = Path(path) if path is not None else checks_dir() / MANIFEST_NAME
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Manifest([], "no-manifest")
    except (OSError, UnicodeDecodeError):
        return Manifest([], "manifest-unreadable")
    try:
        data = json.loads(raw)
    except ValueError:
        return Manifest([], "manifest-unreadable")
    if not isinstance(data, list):
        return Manifest([], "manifest-unreadable")
    # A row that is not an object is dropped rather than fatal: one bad row
    # must not disarm every other project's checks.
    return Manifest([row for row in data if isinstance(row, dict)], "")


def armed_for(cwd, manifest) -> list:
    """The manifest entries armed for this working directory.

    `project_dir` on an entry is already the resolved physical root (the
    writer runs it through config.resolve_project_dir, which walks to the
    git toplevel). Here the cwd is realpath'd to meet it, so a project
    reached through a symlink still arms, and the prefix test carries an
    explicit separator so /srv/appliance is not governed by a ruling made
    for /srv/app."""
    entries = getattr(manifest, "entries", manifest)
    if not isinstance(entries, list) or not isinstance(cwd, str) or not cwd:
        return []
    try:
        here = os.path.realpath(cwd)
    except (OSError, ValueError):
        return []
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        root = entry.get("project_dir")
        if not isinstance(root, str) or not root:
            continue
        bounded = root if root.endswith(os.sep) else root + os.sep
        if here == root or here.startswith(bounded):
            out.append(entry)
    return out


def matches(entry, command) -> bool:
    """The cheap prefilter: does this entry's regex hit the command string?

    Unanchored and case-sensitive, per the spec, and read over at most
    MATCH_INPUT_BYTES bytes of the command. An unusable pattern matches
    nothing instead of raising: a hand-edited manifest can carry a regex the
    writer would have refused, and that must not become a traceback on every
    shell action."""
    if not isinstance(entry, dict) or not isinstance(command, str):
        return False
    pattern = entry.get("match")
    if not isinstance(pattern, str) or not pattern:
        return False
    subject = command.encode("utf-8", "replace")[:MATCH_INPUT_BYTES].decode(
        "utf-8", "ignore")
    try:
        return re.search(pattern, subject) is not None
    except (re.error, RecursionError):
        return False
