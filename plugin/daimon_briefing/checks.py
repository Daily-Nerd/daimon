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

    armed = []
    for record in refutations.listing(states={"active"}, polarity="ruling",
                                      project_dir=project_dir):
        check = record.get("check")
        # `check_lifecycle` is derived at fold time and is the ONE place that
        # knows a candidate's check is not a mode. Reading `state` here
        # instead would be a second answer to the same question.
        if not isinstance(check, dict) or record.get("check_lifecycle") != "armed":
            continue
        sha = str(check.get("sha256") or "")
        ruling_id = str(record.get("refutation_id") or "")
        if not sha or not ruling_id:
            continue
        armed.append(({
            "ruling_id": ruling_id,
            "project_dir": root,
            "match": str(check.get("match") or ""),
            "intent": str(check.get("intent") or "warn"),
            "sha256": sha,
            "armed_at": str(record.get("activated_at") or ""),
        }, str(check.get("body") or "")))

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
