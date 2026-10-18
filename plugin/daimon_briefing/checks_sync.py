"""#943: turning armed rulings into something a standalone hook can run.

The writing half of the check contract. `checks_runtime` reads a manifest and
a set of executable bodies without importing daimon; this module is what
writes them, and it is the only writer of `~/.daimon/checks/`. The firing log
the hooks write back is folded in `checks`.

The manifest is a derived artifact, so it is built from what a reader may
see: the rulings come through the view, and a ruling whose enforcement value
is withheld (forgotten, quarantined or closed) arms nothing (#1208). That is
why this module sits above the view and `checks` does not: the ledger layer
holds the raw rows and must not import the judge of them.

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
from pathlib import Path
from typing import NamedTuple

from . import checks_runtime, config, jsonl, refutations, store, view
from .surfaces import WritePosture, Writer


class SyncReport(NamedTuple):
    """What one sync did. `armed` counts THIS project's entries, never the
    manifest's total: the directory is global and every other project's
    entries pass through untouched."""
    ok: bool
    armed: int
    slug: str
    reason: str = ""
    # Armed rulings left out because an enforcement value of theirs is
    # withheld from a reader (forgotten, quarantined or closed). They arm
    # nothing, and the count is all that is said about them.
    not_as_ratified: int = 0


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


def _wanted(project_dir, root: str) -> tuple[list, int]:
    """Every entry this project's ledger says should be armed, paired with
    its body, and how many armed rulings were left out because they are not
    as ratified. The ONE place the six-key manifest entry is built, so the
    writer and the audit that grades the writer cannot describe different
    shapes.

    The rows are read through the view, once for the whole listing, and a
    ruling whose enforcement value is withheld from a reader contributes
    nothing: an enforcement set is built only from values a reader may see.
    `check.match` and `check.body` are never taken from a raw row."""
    rows = refutations.listing(states={"active"}, polarity="ruling",
                               project_dir=project_dir)
    judged = view.masked(project_dir, "refutations.jsonl", rows)
    out = []
    skipped = 0
    for record in judged:
        check = record.get("check")
        # `check_lifecycle` is derived at fold time and is the ONE place that
        # knows a candidate's check is not a mode. Reading `state` here
        # instead would be a second answer to the same question. Three other
        # readers of that field exist (`cli._ledger._ruling_lines`, the
        # viewer payload, and this module's own sync); none of them changes
        # behavior for this one (scar 0053).
        if not isinstance(check, dict) or record.get("check_lifecycle") != "armed":
            continue
        if record.get("as_ratified") is not True:
            skipped += 1
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
    return out, skipped


def sync(project_dir=None, *, writer: Writer) -> SyncReport:
    """Rebuild this project's armed checks from its ledger. Never raises.

    `writer` (#1132 PR 10b) is who is syncing. The manifest is rebuilt from
    `refutations.jsonl`, and a ledger that cannot be read yields NO armed
    entries, so a rebuild from it would unlink every check this project has
    armed. A person's sync (HUMAN) therefore REFUSES while that ledger is
    unproven, as a failed report with the reason; a forget's own
    re-derivation (CURE) proceeds."""
    try:
        return _sync(project_dir, writer)
    except Exception as exc:  # noqa: BLE001 — a report, never a raise
        return SyncReport(False, 0, "", f"{type(exc).__name__}: {exc}")


def sync_layers(project_dir=None, *, writer: Writer) -> list:
    """Sync every ruling layer above `project_dir` (#1092) through this SAME
    `sync` entry point (#1095), so `hooks install` and `check sync` re-arm an
    inherited check on a machine that has never synced the layer's own
    project directly.

    Returns `[(layer_dir, SyncReport), ...]`, nearest layer first (the order
    `config.layer_scopes` already gives). Each layer is keyed on its OWN
    resolved root, which `sync` never collapses into the caller's root — a
    layer directory qualifies only when it is outside every git working
    tree (`config.layer_scopes`'s own #2 guard), so it can never resolve to
    the child's repo the way a plain subdirectory would. `_sync` already
    keys manifest entries on `project_dir`, so a repeat run — the layer's own
    ratify-triggered sync, or this same loop run again from a grandchild
    below it — cannot double-arm the same id: it lands once per root, never
    once per (root, descendant) pair.

    Never raises: `config.layer_scopes` itself never raises (its own
    contract) and `sync` swallows its own exceptions, surfacing a failure as
    `SyncReport(ok=False, ...)` rather than aborting the loop — so this
    needs no guard of its own, the same trust `_inherited_active` extends to
    it."""
    return [(layer, sync(layer, writer=writer))
            for layer in config.layer_scopes(project_dir)]


def not_as_ratified_line(report: SyncReport, layer_home: str = "") -> str:
    """The one line both verbs print when a sync left armed rulings out, or
    "" when it left none. A count only: it names no ruling, value or reason."""
    if not report.ok or not report.not_as_ratified:
        return ""
    where = f" (layer {layer_home})" if layer_home else ""
    return (f"checks: {report.not_as_ratified} ruling(s) not as ratified, "
            f"not armed; see daimon ruling list{where}")


def _sync(project_dir, writer: Writer) -> SyncReport:
    resolved = config.resolve_project_dir(project_dir)
    slug = store.project_slug(resolved)
    if not slug:
        return SyncReport(False, 0, "", "that project directory names no "
                                        "bucket, so there is no ledger to read")
    judged = jsonl.posture(config.checkpoint_dir() / slug / "refutations.jsonl",
                           "refutations.jsonl", writer)
    if judged.write is WritePosture.REFUSE:
        return SyncReport(False, 0, slug, str(judged.refusal()))
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

    armed, skipped = _wanted(project_dir, root)

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
    return SyncReport(True, len(entries), slug, not_as_ratified=skipped)


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


def audit_layers(project_dir=None) -> list:
    """Grade every ruling layer above `project_dir` (#1092) the same way
    `audit` grades the project's own root (#1095), so `check sync --check`
    reports drift in an inherited check too: a layer's body edited by hand,
    or a layer whose manifest entry never reached this machine at all.

    Returns `[(layer_dir, Audit), ...]`, nearest layer first. Read-only,
    same posture as `audit`; never raises (`config.layer_scopes` never
    raises on its own, and `audit` swallows its own exceptions), so this
    needs no guard of its own."""
    return [(layer, audit(layer)) for layer in config.layer_scopes(project_dir)]


def _audit(project_dir) -> Audit:
    root = str(config.resolve_project_dir(project_dir))
    base = config.checks_dir()
    loaded = checks_runtime.load_manifest(base / checks_runtime.MANIFEST_NAME)
    state = {"": "read", "no-manifest": "absent",
             "manifest-unreadable": "unreadable"}.get(loaded.reason, "unreadable")

    entries, _ = _wanted(project_dir, root)
    wanted = {(e["ruling_id"], e["sha256"]) for e, _ in entries}
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
