"""#943: turning armed rulings into something a standalone hook can run.

The package half of the check contract, in two modules. `checks_runtime`
reads a manifest and a set of executable bodies without importing daimon;
`checks_sync` is what writes them, from the judged ledger, and it is the only
writer of `~/.daimon/checks/`.

This module is the ledger half of the family: it folds `logs/checks.jsonl`,
the firing log of armed checks, and runs a body once for `check try`. It
builds no derived artifact and reads no judged rows, so it imports nothing
above the ledger layer.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import NamedTuple

from . import checks_runtime, config, refutations


# ---- reading the firing log (#943 slice 5) --------------------------------

# The three outcomes, never folded (spec 2.3). A row carrying anything else
# is still a firing — it ran — but it lands in no outcome column.
_OUTCOMES = ("clean", "violation", "unresolved")


def _empty_fold() -> dict:
    return {"fired": 0, "clean": 0, "violation": 0, "unresolved": 0,
            "denied": 0, "last_ts": ""}


def _absorb(fold: dict, row: dict) -> None:
    fold["fired"] += 1
    outcome = str(row.get("outcome") or "")
    if outcome in _OUTCOMES:
        fold[outcome] += 1
    if str(row.get("decision_emitted") or "") == "deny":
        fold["denied"] += 1
    ts = str(row.get("ts") or "")
    # Greatest stamp, never the last line. An append-only log is ordered by
    # append and nothing else, and a reader that takes the tail reports
    # whichever row happened to land last (scar 0009). The format is a fixed
    # %Y-%m-%dT%H:%M:%SZ, so a string compare IS a time compare.
    if ts > fold["last_ts"]:
        fold["last_ts"] = ts


class FiringSummary(NamedTuple):
    """What the firing log says about THIS project, folded.

    `log_state` tells an absent log from an unreadable one from a read one,
    because the whole point of this surface is that silence is a state and
    not a synonym for clean.

    `rulings` is keyed by `(ruling_id, host)`: the per-host split is the only
    honest form, since a check's mode is a property of the host and one
    ruling can be enforcing on one and record-only on another. `hook_seen` is
    keyed by host alone and holds the project-level rows — the ones the hook
    writes with no ruling id at all, which prove it RAN without proving
    anything about a check.

    `totals` sums every host, because the CLI has no notion of which host it
    is on; `ruling checks` is where the split is rendered.

    `path` travels with the summary so a surface naming the log names the
    file this read actually opened, rather than resolving it a second time
    and risking the writer/reader split scar 0043 records.

    `window_since` is the oldest stamp still in the FILE, which is where
    every count here starts. The log is capped (#955), so a count over it is
    a count over a window, and a surface that called it lifetime would be
    reporting numbers the file cannot support. It is the whole file's oldest
    row and not this project's, because rows that belong to nobody here still
    hold the head of the log; the stamp is the only thing a foreign row
    contributes (scar 0055). Empty when the read found no stamped row at all,
    which is also what an absent and an unreadable log report."""

    log_state: str
    rulings: dict
    hook_seen: dict
    totals: dict
    path: str = ""
    window_since: str = ""

    def for_ruling(self, ruling_id: str) -> dict:
        """One ruling across every host, for the `ruling show` liveness line.
        `host` names the host that wrote the most recent row, which is the
        only host a single line can honestly attribute a last firing to."""
        fold = _empty_fold()
        fold["host"] = ""
        for (rid, host), part in self.rulings.items():
            if rid != ruling_id:
                continue
            for key in ("fired", "clean", "violation", "unresolved", "denied"):
                fold[key] += part[key]
            if part["last_ts"] > fold["last_ts"]:
                fold["last_ts"] = part["last_ts"]
                fold["host"] = host
        return fold


def firing_summary(project_dir=None) -> FiringSummary:
    """Fold `~/.daimon/logs/checks.jsonl` for one project. Never raises.

    The log is GLOBAL and this is a rendering path, so every id that is not
    in this project's ledger is discarded here, before anything can print it
    (scar 0055: printing another bucket's record text writes that text into
    this project's checkpoint). The ledger read spans every state, not just
    active: retiring a ruling disarms its check, it does not un-fire what
    already ran.

    Read through `config.log_dir()`, which `checks_runtime.log_dir()` is a
    quirk-faithful mirror of (scar 0043). Resolving the path any other way
    reads an empty directory while the same command reports checks armed.
    """
    try:
        return _firing_summary(project_dir)
    except Exception:  # noqa: BLE001 — a reporting read never takes a caller down
        # The path resolution may be exactly what failed, so the guard must
        # not depend on it: a raise in here defeats the whole promise.
        try:
            where = _log_path()
        except Exception:  # noqa: BLE001
            where = ""
        return FiringSummary("unreadable", {}, {}, _empty_fold(), where)


def _log_path() -> str:
    return str(config.log_dir() / checks_runtime.FIRING_LOG_NAME)


def _firing_summary(project_dir) -> FiringSummary:
    where = _log_path()
    path = Path(where)
    if not path.exists():
        return FiringSummary("absent", {}, {}, _empty_fold(), where)

    try:
        # One read of the ruling ledger, and its health with it: a read that
        # cannot be vouched for (an OS error, a transient failure, an
        # undecodable byte) must not count as "this project has no rulings",
        # which would fold every firing away as foreign (#1132 PR 10a).
        got = refutations.read_events(project_dir)
        if got.unscannable:
            return FiringSummary("unreadable", {}, {}, _empty_fold(), where)
        mine = {str(record.get("refutation_id") or "")
                for record in refutations.fold(got.rows).values()
                if record.get("polarity") == "ruling"}
    except Exception:  # noqa: BLE001
        mine = set()
    # #1095: a layer's active check arms here too (`sync_layers`, and
    # `armed_for` matches this project by path prefix), so a firing it logs
    # has to fold into THIS project's counts or `status`/`stats` read a
    # gate that fires on every action as one that has never fired.
    # `inherited_active` never raises on its own (fail-open to `[]`), so
    # this needs no guard of its own.
    mine |= {str(record.get("refutation_id") or "")
            for record in refutations.inherited_active(project_dir)
            if isinstance(record.get("check"), dict)}

    rulings: dict = {}
    hook_seen: dict = {}
    totals = _empty_fold()
    # Mutable because the fold is one streaming pass and the oldest stamp is
    # not known until it ends.
    window = {"since": ""}
    # STREAMED, one line at a time, single pass. `daimon status` folds this
    # on every run and the log has no cap yet, so reading it whole made the
    # most-used verb hold the entire file: 150 MB of resident memory on a
    # 34 MB log. Nothing here needs two passes or random access.
    # Not on jsonl.read: it loads the whole file, and this fold streams.
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                _fold_line(line, mine, rulings, hook_seen, totals, window)
    except (OSError, UnicodeDecodeError):
        return FiringSummary("unreadable", {}, {}, _empty_fold(), where)
    return FiringSummary("read", rulings, hook_seen, totals, where,
                         window["since"])


def _fold_line(line, mine: set, rulings: dict, hook_seen: dict,
               totals: dict, window: dict) -> None:
    """One row into the fold. Malformed lines never sink the read."""
    try:
        row = json.loads(line)
    except (ValueError, TypeError):
        return
    if isinstance(row, dict):
        ruling_id = str(row.get("ruling_id") or "")
        host = str(row.get("host") or "")
        stamp = str(row.get("ts") or "")
        # Every row the read can see, including the ones this project never
        # gets to count: the window is where the FILE starts. An unstamped
        # row is skipped rather than taken as the oldest, since "" sorts
        # before every real stamp and would report a window starting nowhere.
        if stamp and (not window["since"] or stamp < window["since"]):
            window["since"] = stamp
        if not ruling_id:
            # Scar 0042: the empty id is a VALUE — the hook writes it for
            # no-manifest, manifest-unreadable and no-match. It proves the
            # hook ran and says nothing about any check, so it is counted
            # per host and never attributed to a ruling.
            # MACHINE-wide, and labelled so. The row shape carries no cwd
            # and no project (spec 3.4), so this fold cannot be scoped and a
            # caller must not present it as one project's liveness.
            seen = hook_seen.setdefault(
                host, {"rows": 0, "scope": "machine", "last_ts": ""})
            seen["rows"] += 1
            if stamp > seen["last_ts"]:
                seen["last_ts"] = stamp
        elif ruling_id in mine:
            _absorb(rulings.setdefault((ruling_id, host), _empty_fold()), row)
            _absorb(totals, row)


def try_run(ruling_id: str, command: str, *, channel: str, cwd=None,
            project_dir=None, proposed: bool = False):
    """Run one ruling's check against a command, without arming anything.

    Human-only, and enforced HERE rather than only at the CLI: this is the
    one verb in the family that EXECUTES a body, and `ui` and `signed` reach
    it directly. A dry run of a candidate is the whole point of the verb —
    docs tell an author to try a body before a human arms it — so a body no
    human has confirmed does run, which is exactly why the caller has to be
    a human.

    Writes nothing. Not the firing log (a rehearsal counted as a firing
    makes the liveness surface report a check that never guarded an action),
    not the manifest, and not a body under ~/.daimon/checks, where the hook
    could not tell it from an armed one."""
    if refutations.CHANNEL_AUTHORITY.get(channel) != "human":
        raise refutations.RefutationError(
            "a dry run executes the check body, so it requires a human "
            f"channel; this call arrived through {channel!r}")
    # #1095: resolved across the merged view, own bucket first, so a dry
    # run against a check inherited from a layer above this project does
    # not read "unknown ruling" the way a bare `refutations.get` would.
    record, _inherited_from = refutations.resolve_ruling(
        ruling_id, project_dir=project_dir)
    if record is None:
        raise refutations.RefutationError(f"unknown ruling: {ruling_id}")
    if record.get("polarity") != "ruling":
        raise refutations.RefutationError(
            f"{ruling_id} is a refutation; only a ruling carries a check")
    if proposed:
        check = (record.get("revision_proposed") or {}).get("check")
        if not isinstance(check, dict):
            raise refutations.RefutationError(
                f"{ruling_id} has no proposed check; drop --proposed to run "
                "the one it carries")
    else:
        check = record.get("check")
        if not isinstance(check, dict):
            raise refutations.RefutationError(
                f"{ruling_id} carries no check")
    body = str(check.get("body") or "")

    workdir = tempfile.mkdtemp(prefix="daimon-check-try-")
    try:
        path = Path(workdir) / "check.sh"
        path.write_text(body, encoding="utf-8")
        os.chmod(path, 0o500)
        subject = checks_runtime.resolve(command, cwd or os.getcwd())
        if isinstance(subject, checks_runtime.Unresolved):
            return checks_runtime.Outcome(
                "unresolved", subject.cause, subject.reason, -1, 0)
        try:
            # The stored sha256 goes along, so the dry run exercises the same
            # re-hash the armed path does instead of a shortcut around it.
            return checks_runtime.run(
                {"ruling_id": ruling_id,
                 "sha256": str(check.get("sha256") or ""),
                 "body_path": str(path)},
                subject, cwd=cwd or os.getcwd(),
                timeout=config.check_timeout())
        finally:
            checks_runtime.discard(subject)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
