"""#943: turning armed rulings into something a standalone hook can run.

The package half of the check contract. `checks_runtime` reads a manifest and
a set of executable bodies without importing daimon; this module is what
writes them, from the ledger, and it is the only writer of
`~/.daimon/checks/`.

Sync is called by every ledger writer that can change whether a check is
armed, and by `daimon check sync`. It is idempotent by construction: a repeat
run touches neither bytes nor mtimes, because it runs after every ratify in
the tree and churning the disk each time would be its own defect.

It never raises. It runs AFTER a ledger write that already landed, so turning
a bookkeeping failure into a failed ratify would be the wrong trade; the
caller reports the failure and keeps its own exit code.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import NamedTuple

from . import checks_runtime, config, refutations, store


class SyncReport(NamedTuple):
    """What one sync did. `armed` counts THIS project's entries, never the
    manifest's total: the directory is global and every other project's
    entries pass through untouched."""
    ok: bool
    armed: int
    slug: str
    reason: str = ""


def _write_body(path: Path, body: str) -> None:
    """Materialize one check body, atomically, at 0o500.

    Skipped entirely when the bytes on disk already match, which is what
    makes a repeat sync free. Mode 0500 is read-and-execute for the owner:
    the runner re-hashes before every exec anyway, so the mode is a statement
    about intent rather than the thing keeping the body honest."""
    blob = body.encode("utf-8")
    try:
        if path.read_bytes() == blob:
            return
    except OSError:
        pass
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_bytes(blob)
    os.chmod(tmp, 0o500)
    os.replace(tmp, path)  # atomic on POSIX


def _wanted(project_dir, root: str) -> list:
    """Every entry this project's ledger says should be armed, paired with
    its body. The ONE place the six-key manifest entry is built, so the
    writer and the audit that grades the writer cannot describe different
    shapes."""
    out = []
    for record in refutations.listing(states={"active"}, polarity="ruling",
                                      project_dir=project_dir):
        check = record.get("check")
        # `check_lifecycle` is derived at fold time and is the ONE place that
        # knows a candidate's check is not a mode. Reading `state` here
        # instead would be a second answer to the same question. Three other
        # readers of that field exist (`cli._ledger._ruling_lines`, the
        # viewer payload, and this module's own sync); none of them changes
        # behavior for this one (scar 0053).
        if not isinstance(check, dict) or record.get("check_lifecycle") != "armed":
            continue
        sha = str(check.get("sha256") or "")
        ruling_id = str(record.get("refutation_id") or "")
        if not sha or not ruling_id:
            continue
        out.append(({
            "ruling_id": ruling_id,
            "project_dir": root,
            "match": str(check.get("match") or ""),
            "intent": str(check.get("intent") or "warn"),
            "sha256": sha,
            "armed_at": str(record.get("activated_at") or ""),
        }, str(check.get("body") or "")))
    return out


def sync(project_dir=None) -> SyncReport:
    """Rebuild this project's armed checks from its ledger. Never raises."""
    try:
        return _sync(project_dir)
    except Exception as exc:  # noqa: BLE001 — a report, never a raise
        return SyncReport(False, 0, "", f"{type(exc).__name__}: {exc}")


def _sync(project_dir) -> SyncReport:
    resolved = config.resolve_project_dir(project_dir)
    slug = store.project_slug(resolved)
    if not slug:
        return SyncReport(False, 0, "", "that project directory names no "
                                        "bucket, so there is no ledger to read")
    root = str(resolved)
    base = config.checks_dir()
    manifest_path = base / checks_runtime.MANIFEST_NAME

    loaded = checks_runtime.load_manifest(manifest_path)
    if loaded.reason == "manifest-unreadable":
        # Rebuilding from one project's view would drop every other
        # project's entries, which is a wider action than the failure
        # justifies. Refuse and say so.
        return SyncReport(False, 0, slug,
                          "the existing manifest could not be read; rebuilding "
                          "from this project alone would disarm the others")

    armed = _wanted(project_dir, root)

    entries = [entry for entry, _ in armed]
    others = [e for e in loaded.entries if e.get("project_dir") != root]
    merged = others + entries

    # A ruling id hashes subject and scope, so two projects can name the same
    # body file. Only names THIS project claimed before, and that nothing in
    # the merged manifest still wants, are removed.
    wanted = {checks_runtime.body_name(e) for e in merged}
    stale = {checks_runtime.body_name(e) for e in loaded.entries
             if e.get("project_dir") == root} - wanted

    base.mkdir(parents=True, exist_ok=True)
    for entry, body in armed:
        _write_body(base / checks_runtime.body_name(entry), body)

    blob = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
    try:
        current = manifest_path.read_text(encoding="utf-8")
    except OSError:
        current = ""
    if current != blob:
        store._atomic_write(manifest_path, blob)

    # After the manifest, never before: a body removed while the manifest
    # still names it is a body-hash-mismatch on the next action, and the
    # order that produces that is the order nobody runs in a test.
    for name in sorted(stale):
        try:
            (base / name).unlink()
        except OSError:
            pass
    return SyncReport(True, len(entries), slug)


# ---- auditing the manifest against the ledger (#943 slice 5) --------------


class Audit(NamedTuple):
    """Whether what the hooks read still matches what the ledger wants.

    Every list holds RULING IDS and nothing else: no match patterns, no
    bodies, no project directories. This result is printed, and printing is
    a write (scar 0055).

    `state` is the manifest's own read state, kept apart the way
    `load_manifest` keeps it: an install that armed nothing (`absent`) and a
    manifest daimon can no longer parse (`unreadable`) are different facts,
    and folding them would report a fresh machine as a broken one.

    A ruling whose pinned hash moved appears in BOTH `missing` and `stale`:
    missing at the hash the ledger wants, stale at the hash the manifest
    still names. Reporting one half hides which side moved."""

    state: str
    wanted: list
    have: list
    missing: list
    stale: list
    body_missing: list
    body_mismatch: list
    drift: bool


def audit(project_dir=None) -> Audit:
    """Read-only. Never raises, never repairs.

    `sync` has no dry run and could not be borrowed for this: calling it to
    detect drift would repair the drift as a side effect, and an audit that
    changes what it audits reports nothing.

    It also catches a state the manifest cannot express — a body whose bytes
    no longer hash to the pinned value. The runner catches that at exec time
    as `body-hash-mismatch`, which is one blocked action too late to be a
    report."""
    try:
        return _audit(project_dir)
    except Exception:  # noqa: BLE001 — a report, never a raise
        return Audit("unreadable", [], [], [], [], [], [], False)


def _audit(project_dir) -> Audit:
    root = str(config.resolve_project_dir(project_dir))
    base = config.checks_dir()
    loaded = checks_runtime.load_manifest(base / checks_runtime.MANIFEST_NAME)
    state = {"": "read", "no-manifest": "absent",
             "manifest-unreadable": "unreadable"}.get(loaded.reason, "unreadable")

    wanted = {(e["ruling_id"], e["sha256"]) for e, _ in _wanted(project_dir, root)}
    # Project EQUALITY, never `armed_for`'s prefix match: that primitive
    # answers "which entries govern this cwd" and would pull in a parent
    # project's rows when run from a nested directory.
    ours = [e for e in loaded.entries if e.get("project_dir") == root
            and str(e.get("ruling_id") or "")]
    have = {(str(e.get("ruling_id")), str(e.get("sha256") or ""))
            for e in ours}

    body_missing, body_mismatch = set(), set()
    for entry in ours:
        path = checks_runtime.body_path(entry, base)
        try:
            blob = path.read_bytes()
        except OSError:
            body_missing.add(str(entry.get("ruling_id")))
            continue
        if hashlib.sha256(blob).hexdigest() != str(entry.get("sha256") or ""):
            body_mismatch.add(str(entry.get("ruling_id")))

    missing = sorted({rid for rid, _ in wanted - have})
    stale = sorted({rid for rid, _ in have - wanted})
    drift = bool(missing or stale or body_missing or body_mismatch
                 or state == "unreadable")
    return Audit(state, sorted({rid for rid, _ in wanted}),
                 sorted({rid for rid, _ in have}), missing, stale,
                 sorted(body_missing), sorted(body_mismatch), drift)


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
    and risking the writer/reader split scar 0043 records."""

    log_state: str
    rulings: dict
    hook_seen: dict
    totals: dict
    path: str = ""

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
        return FiringSummary("unreadable", {}, {}, _empty_fold(), _log_path())


def _log_path() -> str:
    return str(config.log_dir() / checks_runtime.FIRING_LOG_NAME)


def _firing_summary(project_dir) -> FiringSummary:
    where = _log_path()
    path = Path(where)
    if not path.exists():
        return FiringSummary("absent", {}, {}, _empty_fold(), where)

    try:
        mine = {str(record.get("refutation_id") or "")
                for record in refutations.listing(polarity="ruling",
                                                  project_dir=project_dir)}
    except Exception:  # noqa: BLE001
        mine = set()

    rulings: dict = {}
    hook_seen: dict = {}
    totals = _empty_fold()
    # STREAMED, one line at a time, single pass. `daimon status` folds this
    # on every run and the log has no cap yet, so reading it whole made the
    # most-used verb hold the entire file: 150 MB of resident memory on a
    # 34 MB log. Nothing here needs two passes or random access.
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                _fold_line(line, mine, rulings, hook_seen, totals)
    except (OSError, UnicodeDecodeError):
        return FiringSummary("unreadable", {}, {}, _empty_fold(), where)
    return FiringSummary("read", rulings, hook_seen, totals, where)


def _fold_line(line, mine: set, rulings: dict, hook_seen: dict,
               totals: dict) -> None:
    """One row into the fold. Malformed lines never sink the read."""
    try:
        row = json.loads(line)
    except (ValueError, TypeError):
        return
    if isinstance(row, dict):
        ruling_id = str(row.get("ruling_id") or "")
        host = str(row.get("host") or "")
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
            ts = str(row.get("ts") or "")
            if ts > seen["last_ts"]:
                seen["last_ts"] = ts
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
    record = refutations.get(ruling_id, project_dir=project_dir)
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
