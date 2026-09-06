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
