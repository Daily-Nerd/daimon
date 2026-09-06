"""The #943 host adapter core — one pipeline, one row per host.

A host that runs a pre-action hook is a PROFILE ROW here, not a second
pipeline. The row says what the host calls its event, which tool names it
uses for a shell action, where the command string sits in its payload, how it
wants a decision encoded, and which modes it can actually deliver. Everything
downstream of the row is shared: the same manifest read, the same runner, the
same firing log. Adding a host is adding a row and a six-line script.

Same two rules as `checks_runtime` and for the same reason (scar 0049):

  * stdlib ONLY. This file is loaded by a script running in whatever
    interpreter the host launched, with no venv and no `daimon_briefing` on
    `sys.path`. A package import here breaks every host at once and passes
    every local test.
  * the canonical file is THIS one; `hook/` and `_hooks/` hold derivatives.
    Edit here, then run `scripts/sync_hooks.py`.

`checks_runtime` is loaded from THIS file's own directory by file location,
never by name: by name it would depend on `sys.path` state and could bind an
unrelated top-level module.

Nothing here raises. It fires before every shell action on the host, and on
the hosts measured so far a hook that crashes is fail-open: the action
proceeds and nothing anywhere says why.
"""

import importlib.util
import json
from pathlib import Path
from typing import NamedTuple

# ---- the runtime, by file location (the `_load_redact` shape) -------------


def _load_runtime():
    """Load `checks_runtime.py` from beside this file, or None.

    None is the stale-install shape: this module shipped, its sibling did
    not. The caller turns that into an allow that says so, because a hook
    with no runtime has nothing to check and must not pretend otherwise."""
    path = Path(__file__).resolve().parent / "checks_runtime.py"
    if not path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            "_daimon_checks_runtime", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:  # noqa: BLE001 — a broken sibling must not crash a hook
        return None


_RUNTIME = _load_runtime()


def runtime():
    """The loaded runtime, or None when the sibling is missing or broken."""
    return _RUNTIME


# ---- modes (spec section 5) ----------------------------------------------

# Weakest to strongest. Every cap and every aggregation reads this order, so
# "weaker" and "stronger" are spelled once and cannot disagree.
MODES = ("unsupported", "record-only", "warn", "enforce")

# What an author may ask for, strongest first — the vocabulary
# `refutations.CHECK_INTENTS` validates on the way in.
INTENTS = ("enforce", "warn", "record-only")

# The decision encoders a profile may name.
ENCODERS = ("json-permission", "exit2-stderr")

# What the hook actually tells the host, and therefore what reaches the
# firing log's `decision_emitted`. A record-only mode still emits `allow`:
# nothing was written to the host, and the `mode` column is where "this only
# went to the log" is already said. Claiming a fourth word here would report
# a channel the host was never given.
DECISIONS = ("allow", "warn", "deny")


class Profile(NamedTuple):
    """One host, as a row.

    `host` is the label written to the firing log, so it is what a reader
    attributes liveness to; two rows sharing one would make the per-host
    column unmeasurable.

    `tool_names` is the set of tool names that mean "a shell action"; empty
    means any name is accepted. `command_path` is the path into the payload
    where the command string sits, and it differs per host in NAME as well as
    in nesting. `cwd_key` is the top-level key holding the action's working
    directory.

    `mode_caps` maps each intent to what this host can actually deliver, from
    spec section 5. It is the whole per-host degradation story: a host that
    documents no warn channel caps warn at record-only rather than emitting
    something the operator never sees.

    `matcher` and `install_timeout` are what the registration writes."""

    host: str
    event: str
    tool_names: frozenset
    command_path: tuple
    cwd_key: str
    encoder: str
    mode_caps: dict
    matcher: str
    install_timeout: int


PROFILES = {
    "claude-code": Profile(
        host="claude-code",
        event="PreToolUse",
        tool_names=frozenset({"Bash"}),
        command_path=("tool_input", "command"),
        cwd_key="cwd",
        encoder="json-permission",
        # Both the deny and the warn channel are documented; the deny is
        # measured. Nothing is capped.
        mode_caps={"enforce": "enforce", "warn": "warn",
                   "record-only": "record-only"},
        matcher="Bash",
        install_timeout=10,
    ),
    "codex": Profile(
        host="codex",
        event="PreToolUse",
        # Codex names the shell tool both ways depending on the build, and
        # the matcher it registers is the alternation of the two.
        tool_names=frozenset({"Bash", "shell"}),
        command_path=("tool_input", "command"),
        cwd_key="cwd",
        encoder="json-permission",
        # Codex documents the same JSON deny and NO warn channel. A warn with
        # nowhere to go is not a warn, so it degrades to record-only: the run
        # is still logged and nothing is silently claimed to have been shown.
        mode_caps={"enforce": "enforce", "warn": "record-only",
                   "record-only": "record-only"},
        matcher="Bash|shell",
        install_timeout=10,
    ),
    "windsurf": Profile(
        host="windsurf",
        event="pre_run_command",
        tool_names=frozenset(),
        # Not tool_input.command. Windsurf carries the shell action under a
        # different key at a different name, and a copied path would read
        # None on every action and allow in silence.
        command_path=("tool_info", "command_line"),
        cwd_key="cwd",
        encoder="exit2-stderr",
        # Documented, unmeasured. Until a live probe the whole column reads
        # unsupported, so an enforce intent shows unsupported here rather
        # than claiming an enforcement daimon has never seen delivered.
        mode_caps={"enforce": "unsupported", "warn": "unsupported",
                   "record-only": "unsupported"},
        matcher="",
        install_timeout=10,
    ),
}


def mode_for(profile, intent) -> str:
    """The mode this host actually delivers for that intent.

    The weaker of the two, read off the profile's own cap table. An intent
    this build never heard of reads as the weakest real intent rather than
    the strongest: a manifest is a file on disk, and a word nobody
    recognises must not become an enforcement."""
    caps = getattr(profile, "mode_caps", None)
    if not isinstance(caps, dict):
        return "unsupported"
    asked = intent if intent in INTENTS else "record-only"
    mode = caps.get(asked, "unsupported")
    return mode if mode in MODES else "unsupported"


# ---- encoders (spec section 4) -------------------------------------------


class Emission(NamedTuple):
    """What the hook writes and what it exits with.

    Three channels because the hosts do not agree on one. Claude Code and
    Codex read a JSON object on stdout and treat a non-zero exit as a hook
    error; Windsurf documents exit 2 with the reason on stderr. Carrying all
    three keeps the Windsurf row a row rather than a special case, and the
    tests pin stderr empty and the exit code 0 on every path of the two
    hosts that actually ship a script."""

    stdout: str
    stderr: str
    exit_code: int


_SILENT = Emission("", "", 0)


def _encode_json_permission(profile, decision, text) -> Emission:
    """The measured path on Claude Code and Codex: one JSON object, exit 0.

    The key names are the host's contract, so they are pinned byte for byte
    by a literal comparison in the tests. A rename here is a deny that
    silently becomes an allow, and a parsed-dict assertion would not see it.

    A silent allow writes NOTHING rather than an object saying `allow`: the
    hosts treat absent output as no opinion, and an object claiming a
    decision is a decision daimon did not make."""
    event = str(getattr(profile, "event", "") or "")
    if decision == "deny":
        return Emission(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": "deny",
                "permissionDecisionReason": text,
            },
        }, sort_keys=True), "", 0)
    if decision == "warn" and text:
        return Emission(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": "allow",
            },
            "systemMessage": text,
        }, sort_keys=True), "", 0)
    return _SILENT


def _encode_exit2_stderr(profile, decision, text) -> Emission:
    """Windsurf's documented channel, unmeasured. Reachable only once a
    Windsurf profile has a mode above `unsupported`, which is after a live
    probe; until then this exists so the row is complete and its shape is
    pinned by a test rather than discovered on someone's machine."""
    if decision not in ("deny", "warn") or not text:
        return _SILENT
    return Emission("", text + "\n", 2 if decision == "deny" else 0)


_ENCODERS = {
    "json-permission": _encode_json_permission,
    "exit2-stderr": _encode_exit2_stderr,
}


def encode(profile, decision, text) -> Emission:
    """Render one decision the way this host reads it.

    An unknown decision word or an unknown encoder name is a silent allow,
    never a deny: a value this build does not recognise must not become the
    strongest thing the host can be asked to do."""
    if decision not in DECISIONS:
        return _SILENT
    fn = _ENCODERS.get(str(getattr(profile, "encoder", "")))
    if fn is None:
        return _SILENT
    return fn(profile, decision, str(text or ""))
