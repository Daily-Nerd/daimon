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
import os
import re
import shlex
import sys
import time
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
    # #988: there is deliberately NO `kimi` row here yet, and the Kimi adapter
    # ships its briefing and capture hooks without one.
    #
    # What a live probe of Kimi Code 0.42.0 (2026-09-09) DID measure:
    #   - `PreToolUse` fires with `tool_name: "Bash"` and the command at
    #     `tool_input.command`, so `command_path` and `matcher` would copy the
    #     claude-code row unchanged.
    #   - `cwd` is on the payload, resolved on macOS (`/private/tmp`).
    #   - `PreToolUse` exit-0 stdout is DROPPED, not appended to context. Only
    #     `UserPromptSubmit` stdout reaches the model. So the warn channel is
    #     measured ABSENT, and `warn` would have to cap at `record-only` the
    #     way the codex row does, rather than be left open.
    #
    # What it did NOT measure, and what runbook Part 4 has to prove before a
    # row lands here: THE DENY CHANNEL. Neither exit code 2 with a reason on
    # stderr nor the stdout JSON `hookSpecificOutput.permissionDecision: deny`
    # was ever exercised against a real command. Both strings are present in
    # the shipped binary and both are documented, and neither of those is a
    # command observed being blocked.
    #
    # A row added on documentation alone would put `enforce` in the
    # degradation ladder for an enforcement daimon has never watched land,
    # which is the one direction this ladder must not fail in: a check that
    # reports enforcing and silently allows is worse than no check, because it
    # is byte-identical to a clean allow (scar 0072). The windsurf row above
    # is the precedent, and it caps the whole column at `unsupported` for
    # exactly this reason.
}


def disabled() -> bool:
    """The `DAIMON_DISABLE` kill switch, read the way every other daimon hook
    reads it: `1`, `true`, `yes` or `on`, stripped, case-sensitive.

    Copied line for line from `_daimon_hook_lib.disabled()` rather than
    reinterpreted, quirks included. A switch that turns off three hooks and
    leaves the fourth armed is worse than one that is strict everywhere, and
    this is the fourth hook: the one that can block a command, and therefore
    the one an operator most needs a way out of. An over-broad `enforce`
    check denies the very shell action that would retire the ruling.

    PROCESS ENVIRONMENT ONLY. Unlike the path accessors above it does NOT
    fall back to `~/.daimon/env` (scar 0043 does not reach here). Those
    mirror a location a writer and a deleter have to agree on; this is a
    switch someone flips for one session, and a copy left on disk would keep
    every later session disarmed with nothing on screen to say so."""
    return os.environ.get("DAIMON_DISABLE", "").strip() in (
        "1", "true", "yes", "on")


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


# ---- the pipeline (spec sections 3.1, 3.4 and 5) -------------------------

# One deadline for the whole action, in seconds, against a host hook timeout
# of ten. The two seconds left over pay for the manifest read, the resolve,
# and the kill-and-drain after a check that had to be stopped.
#
# `DAIMON_CHECK_TIMEOUT` still caps ONE check; this caps their sum. Without
# it the budget was per check, so two checks that both reached the runner's
# own timeout took past ten seconds, and a hook that reaches the HOST's
# timeout does not block: the action proceeds, nothing is emitted, and
# nothing is logged. That silence is byte-identical to a clean allow, which
# is the one outcome this whole slice exists to make impossible.
ACTION_BUDGET = 8.0

# Where a host names the tool it is about to run. Both hosts that filter by
# tool name spell it this way. A host that spells it differently declares an
# empty `tool_names` and is selected by its `command_path` alone, which is
# what the Windsurf row does.
TOOL_NAME_KEY = "tool_name"


class Decision(NamedTuple):
    """What one action's checks came to.

    `rows` are firing-log rows, built to `FIRING_KEYS` and not yet written:
    `decide` decides, `main` records. Keeping the write out of here is what
    lets the whole matrix be tested without a log on disk, and what keeps a
    failed write from costing the decision."""

    stdout: str
    stderr: str
    exit_code: int
    rows: list


def _dig(payload, path):
    """Walk a path into a payload, or None. A host payload field is a CLAIM
    (scar 0068): any step may be missing or may not be a mapping at all, and
    each of those is an action daimon cannot see rather than a crash."""
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _accepts(profile, payload) -> bool:
    """Whether this payload names a shell action on this host. An empty
    `tool_names` accepts anything, because the host already selected by
    event."""
    names: frozenset = getattr(profile, "tool_names", frozenset())
    if not names:
        return True
    return _dig(payload, (TOOL_NAME_KEY,)) in names


def _row(profile, ts, *, ruling_id="", mode="", outcome="", cause="",
         decision="allow", duration_ms=0) -> dict:
    """One firing-log row, in the declared shape. `log_firing` PROJECTS onto
    FIRING_KEYS, so a row built with a stray field loses it in silence;
    building to the shape here keeps the two ends one declaration."""
    return {
        "ts": ts,
        "ruling_id": str(ruling_id or ""),
        "host": str(getattr(profile, "host", "") or ""),
        "mode": mode,
        "outcome": outcome,
        "cause": cause,
        "decision_emitted": decision,
        "duration_ms": int(duration_ms or 0),
    }


def _stamp(now) -> str:
    if isinstance(now, str) and now:
        return now
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _failure_line(entry, outcome) -> str:
    """`<ruling_id>: <reason>`, with the cause in front when daimon could not
    read what the action sends. The cause is what tells a human whether to
    fix the command or fix the check."""
    ruling_id = str(entry.get("ruling_id") or "")
    reason = outcome.reason or "the check reported a violation and gave no reason"
    if outcome.outcome == "unresolved" and outcome.cause:
        reason = f"{outcome.cause}: {reason}"
    return f"{ruling_id}: {reason}"


def _command_text(raw):
    """The action's command string, or None when there is no command, or the
    Unresolved cause when there is one daimon cannot read.

    Some hosts send the shell tool's command as an argv array. Read as "no
    command", every armed check on such a host goes silently inert while the
    liveness surface reads "armed, never fired", which is the phrase reserved
    for a wired install that nothing has matched yet. It is joined with shell
    quoting rather than with a space, because an argument holding a space
    would otherwise become two arguments in the subject and the check would
    be judging a command nobody ran."""
    if isinstance(raw, str):
        return raw or None
    if isinstance(raw, list):
        if raw and all(isinstance(part, str) for part in raw):
            return shlex.join(raw)
        return ("arg-form-unparsed",
                "the host sent a command daimon could not read as text")
    return None


def _match_state(rt, entry, command) -> str:
    """`hit`, `miss`, or `broken` for one entry's pattern.

    `rt.matches` folds an uncompilable pattern into False, which is right for
    it and wrong here: "a regex that cannot compile" and "a regex that did
    not match" are different facts and only one of them was countable.
    `refutations` compiles the pattern at propose time, so a broken one
    reaches this point only through a hand-edited manifest."""
    pattern = entry.get("match")
    if isinstance(pattern, str) and pattern:
        try:
            re.compile(pattern)
        except (re.error, RecursionError):
            return "broken"
    return "hit" if rt.matches(entry, command) else "miss"


def decide(profile, payload, *, manifest=None, timeout=None,
           now=None) -> Decision:
    """Run this action's armed checks and say what the host should be told.

    Never raises. It fires before every shell action, and on the hosts
    measured so far a hook that crashes is fail-open: the action proceeds and
    nothing anywhere says why. An exception on the way through still returns
    an allow, with the rows already gathered plus one saying the machinery
    was what failed."""
    rows: list = []
    try:
        return _decide(profile, payload, manifest, timeout, now, rows)
    except Exception:  # noqa: BLE001 — a decision is mandatory
        rows.append(_row(profile, _stamp(now), cause="check-crashed",
                         decision="allow"))
        return Decision("", "", 0, rows)


def _decide(profile, payload, manifest, timeout, now, rows) -> Decision:
    if disabled():
        # Before the manifest read, before the payload is even looked at: a
        # disabled hook costs one environment read and leaves no trace. No
        # row either, because nothing ran and nothing declined to run.
        return Decision("", "", 0, rows)
    rt = runtime()
    if rt is None:
        # Nothing to check WITH, and nothing to write a row with either. The
        # caller owns the diagnostic; see `main`.
        return Decision("", "", 0, rows)
    if not isinstance(payload, dict):
        payload = {}
    if not _accepts(profile, payload):
        return Decision("", "", 0, rows)
    command = _command_text(_dig(payload, getattr(profile, "command_path",
                                                  ())))
    stamp = _stamp(now)
    if isinstance(command, tuple):
        # A command that is present and unreadable, which is not the same as
        # absent. It gets a row so the state is countable; the action is
        # allowed because nothing was armed against anything yet.
        cause, _ = command
        rows.append(_row(profile, stamp, cause=cause, outcome="unresolved",
                         decision="allow"))
        return Decision("", "", 0, rows)
    if command is None:
        # Not a row: nothing ran and nothing declined to run. A row for every
        # payload without a command would make the firing log a transcript of
        # the session rather than a record of checks.
        return Decision("", "", 0, rows)

    cwd = payload.get(getattr(profile, "cwd_key", ""))
    if not isinstance(cwd, str) or not cwd:
        # The host is supposed to send it. When it does not, the process the
        # host launched stands in the action's directory anyway, and arming
        # nothing would disarm every check for that action in silence.
        cwd = os.getcwd()

    loaded = rt.load_manifest() if manifest is None else manifest
    reason = getattr(loaded, "reason", "")
    if reason:
        # Spec 3.1: "nothing is armed here" has to be countable, so the stats
        # surface can say "armed, never fired" instead of "clean". An install
        # that armed nothing and a manifest daimon can no longer parse are
        # different facts and keep different causes.
        rows.append(_row(profile, stamp, cause=reason, decision="allow"))
        return Decision("", "", 0, rows)

    try:
        os.path.realpath(cwd)
    except (OSError, ValueError):
        # `armed_for` folds this into an empty list, which would come out as
        # `no-match`. That cause says every armed check was compared and none
        # applied; here nothing was compared at all.
        rows.append(_row(profile, stamp, cause="file-unreadable",
                         outcome="unresolved", decision="allow"))
        return Decision("", "", 0, rows)

    entries = rt.armed_for(cwd, loaded)
    if not entries:
        rows.append(_row(profile, stamp, cause="no-match", decision="allow"))
        return Decision("", "", 0, rows)

    matching = [(entry, _match_state(rt, entry, command))
                for entry in entries]
    matching = [(entry, state) for entry, state in matching
                if state != "miss"]
    if not matching:
        # The prefilter is what makes a hook on every shell action
        # affordable, and spec 3.1 names only the two project-level causes.
        return Decision("", "", 0, rows)

    per_check = (float(timeout) if isinstance(timeout, (int, float))
                 and timeout > 0 else rt.check_timeout())
    deadline = time.monotonic() + ACTION_BUDGET

    # Once, for every matching check. The subject cannot change between two
    # entries of the same action, and reading every file argument again would
    # spend a budget the host will not extend. Skipped entirely when every
    # matching entry carries a pattern that will not compile: there is
    # nothing to run against them, and the resolve is the expensive half.
    subject = (rt.resolve(command, cwd)
               if any(state == "hit" for _, state in matching) else None)
    results = []
    try:
        for entry, state in matching:
            mode = mode_for(profile, entry.get("intent"))
            left = deadline - time.monotonic()
            if state == "broken":
                # The manifest names a pattern daimon cannot compile, so this
                # check cannot be applied at all. `unresolved`, which under
                # `enforce` denies: a rule nobody can evaluate is not a rule
                # that passed.
                outcome = rt.Outcome(
                    "unresolved", "check-crashed",
                    "this ruling's match pattern could not be compiled; run "
                    "`daimon check sync`", -1, 0)
            elif isinstance(subject, rt.Unresolved):
                outcome = rt.Outcome("unresolved", subject.cause,
                                     subject.reason, -1, 0)
            elif left <= 0:
                # The action's budget is already spent, so this check never
                # runs. `unresolved`, not clean: daimon proved nothing about
                # the subject, and letting it through because time ran out is
                # exactly the fail-open the deadline exists to prevent. Under
                # `enforce` it still denies. The row is what keeps a starved
                # check countable instead of invisible.
                outcome = rt.Outcome(
                    "unresolved", "check-timeout",
                    "the action's check budget was spent before this check "
                    "could run", -1, 0)
            else:
                outcome = rt.run(entry, subject, cwd=cwd,
                                 timeout=min(per_check, left))
            # Appended as it is decided, not after the loop. `rt.run` is
            # documented never to raise, and this is the case where the
            # record matters most if it ever does: an earlier check's
            # violation is the only evidence of what happened. The row is
            # stamped `allow` and corrected below for the ones that failed,
            # so a row that escapes through an exception says what the host
            # actually got, which is nothing.
            row = _row(profile, stamp, ruling_id=entry.get("ruling_id"),
                       mode=mode, outcome=outcome.outcome,
                       cause=outcome.cause, decision="allow",
                       duration_ms=outcome.duration_ms)
            rows.append(row)
            results.append((entry, mode, outcome, row))
    finally:
        # The subject holds the command and every file it named. A `finally`
        # is the only placement that survives an exception nobody predicted.
        rt.discard(subject)

    failing = [(entry, mode, outcome, row)
               for entry, mode, outcome, row in results
               if outcome.outcome in ("violation", "unresolved")]
    decision, text = "allow", ""
    # Above every real mode until a failure sets it, so that with nothing
    # failing no row qualifies for the stamp below.
    floor = len(MODES)
    if failing:
        # The strongest FAILING mode decides, not the strongest mode present.
        # An enforce check that passed has nothing to say about a warn check
        # that did not, and reading it the other way blocks actions nobody
        # armed to block.
        deciding = max((mode for _, mode, _, _ in failing), key=MODES.index)
        # Only the failures AT OR ABOVE the deciding mode are spoken aloud. A
        # `record-only` check asked for the log and nothing else, and a
        # neighbour that failed at a stronger mode must not carry its reason
        # out on its behalf. On Codex that is the whole point of the cap:
        # `warn` degrades to `record-only` there so nothing is shown that the
        # host never rendered, and a second check the author did not control
        # would otherwise defeat it.
        floor = MODES.index(deciding)
        text = "\n".join(_failure_line(entry, outcome)
                         for entry, mode, outcome, _ in failing
                         if MODES.index(mode) >= floor)
        if deciding == "enforce":
            decision = "deny"
        elif deciding == "warn":
            decision = "warn"

    emission = encode(profile, decision, text)
    # One action produces one decision, but the log row is per CHECK, so the
    # row records what THIS check contributed. Two kinds of row keep the
    # `allow` they were written with. A check that passed denied nothing, and
    # a clean row stamped `deny` satisfies the "only a deny under enforce
    # proves it was honored" filter while proving nothing of the kind. And a
    # check that failed BELOW the deciding floor had its reason withheld from
    # the host by its own mode, so it was never heard: stamping the decision
    # on it claims a contribution to a block it was not allowed to speak in.
    # The stats surface counts these rows.
    for _entry, mode, _outcome, row in failing:
        if MODES.index(mode) >= floor:
            row["decision_emitted"] = decision
    return Decision(emission.stdout, emission.stderr, emission.exit_code, rows)


# ---- what the thin per-host scripts call ---------------------------------

# Said on a host that renders a message channel, and only there. A host with
# no channel for it would be told nothing either way, and the firing log is
# the honest surface for that fact except that there is no runtime to write
# it with, which is the state being reported.
RUNTIME_MISSING = "daimon: check runtime missing, nothing enforced"


def _read_payload(stream) -> dict:
    """The host's payload, or an empty one. Unparseable stdin, a closed pipe
    and a payload that is not an object are all the same fact here: this
    process cannot see the action, so it has nothing to say about it."""
    try:
        raw = (stream if stream is not None else sys.stdin).read()
    except Exception:  # noqa: BLE001
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def main(host, stdin=None, stdout=None, stderr=None) -> int:
    """Run this host's pre-action check and write the decision.

    The whole per-host script is a call to this with a profile name. Reads
    the payload, decides, records every row, writes the encoded decision
    once at the end, and returns the exit code the encoder chose, which is 0
    for every host that ships a script today.

    Writing stdout LAST and once is deliberate: a partial object on a host
    that parses stdout is worse than no object, and an exception after a
    first write could leave one."""
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    profile = PROFILES.get(host)
    if profile is None:
        return 0
    rt = runtime()
    try:
        if disabled():
            # `decide` checks this too. Here as well because the missing-
            # runtime diagnostic below never reaches it, and a hook the
            # operator turned off must not still be talking to the host.
            return 0
        if rt is None:
            # Nothing to check with and nothing to write a row with. Say so
            # where the host has a channel for it; a warn that degrades below
            # `warn` on this host has nowhere to go, so it goes nowhere.
            if mode_for(profile, "warn") == "warn":
                emission = encode(profile, "warn", RUNTIME_MISSING)
                out.write(emission.stdout)
            return 0
        decision = decide(profile, _read_payload(stdin))
        for row in decision.rows:
            try:
                rt.log_firing(row)
            except Exception:  # noqa: BLE001
                # `log_firing` promises not to raise, and this guard is what
                # keeps that promise from being load-bearing. The row is
                # bookkeeping; the deny is the thing the operator armed, and
                # losing the decision over the record is the wrong trade.
                pass
        out.write(decision.stdout)
        err.write(decision.stderr)
        return decision.exit_code
    except Exception:  # noqa: BLE001 — the action must not die with the hook
        # Nothing written, so the host sees no opinion and proceeds. The row
        # is the only record that anything happened, and it is worth one more
        # attempt that itself cannot raise.
        if rt is not None:
            try:
                rt.log_firing(_row(profile, _stamp(None),
                                   cause="check-crashed", decision="allow"))
            except Exception:  # noqa: BLE001
                pass
        return 0
