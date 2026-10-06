"""A read-only census of the ledger files under ~/.daimon (#1132 PR 2b).

For each bucket ledger the registry declares: its `jsonl.read` health, the
torn/split/garbage counts, and how many rows still carry a value forget has
tombstoned (the registry's `prose` column, hashed the way forget keys it).
Checkpoint JSON surfaces are counted through privacy's own walker rather than
a second one.

Names, states and counts only. Nothing here returns row content, a forgotten
value or a content key: the result is safe to print, serialize into `status`,
or park in a marker file. Nothing here writes, repairs, or changes how any
reader treats a file; enforcement is a later stage.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import config, jsonl, ledger_repair, normalize, privacy, store, surfaces

MARKER_NAME = store._LEDGER_CENSUS_NAME
MARKER_VERSION = 1

_MIGRATIONS = "migrations.jsonl"
_LOG_LEDGERS = ("checks.jsonl", "recall-delivery.jsonl")


def _counts(result: jsonl.Read) -> dict:
    return {"state": result.health.value, "torn": result.torn,
            "split": result.split, "garbage": result.garbage}


def _tombstoned_rows(rows: list, prose: tuple, keys: set,
                     fragments: bool = False) -> int:
    """Rows with a prose value that folds to a tombstoned key. `fragments`
    (the quarantine sidecar, whose prose is a whole torn line) also looks at
    the JSON strings inside the value, the reach forget's key matcher has."""
    if not keys or not prose:
        return 0
    return sum(
        1 for row in rows
        if any((ledger_repair.holds_forgotten_key(v, keys) if fragments
                else normalize.content_key(v) in keys)
               for v in surfaces.prose_values(prose, row)))


def _checkpoint_residue(slug: str, keys: set) -> tuple[int, int]:
    """(items in this bucket's checkpoint JSON that still hold a forgotten
    value, files that could not be read at all). The walk and the scan are
    the privacy audit's own. An unreadable file has no knowable bucket, so it
    counts against every bucket's census: "could not check" must not fold
    into "clean". With no forgotten key there is nothing to look for and the
    walk is skipped, so both numbers are 0."""
    if not keys:
        return 0, 0
    known, _unknown = privacy._checkpoint_candidates()
    found = unscannable = 0
    for path in known:
        findings, _suppressed, member = privacy._scan_json_surface(
            path, slug, keys, "checkpoint")
        if member is None:
            unscannable += 1
        found += len(findings)
    return found, unscannable


def census_bucket(slug: str, *, checkpoints: bool = True) -> dict:
    """One bucket: {"ledgers": {name: {state, torn, split, garbage,
    tombstoned_present}}, "undeclared": [names], "forgotten_check": "ok" |
    "unavailable", "checkpoints": {"tombstoned_present": n, "unscannable":
    m}}. `checkpoints=False` skips the walk over checkpoint JSON, the one
    part whose cost grows with the whole store.

    The forget tombstones live in events.jsonl, and `forgotten_content_keys`
    answers with an empty set when that file cannot be decoded. So when
    events.jsonl is unreadable or transient the keys cannot be trusted:
    `forgotten_check` is "unavailable" and every `tombstoned_present` is None
    (never 0), the checkpoint walk is skipped and its numbers are None."""
    bucket = config.checkpoint_dir() / slug
    declared = surfaces.bucket_ledger_names()
    reads = {name: jsonl.read(bucket / name) for name in declared}
    trusted = reads["events.jsonl"].health not in (
        jsonl.Health.UNREADABLE, jsonl.Health.TRANSIENT)
    keys = store.forgotten_content_keys(slug) if trusted else set()
    ledgers: dict = {}
    for name, result in reads.items():
        present = _tombstoned_rows(
            result.rows, surfaces.bucket_ledger(name).prose, keys
        ) if trusted else None
        ledgers[name] = {**_counts(result), "tombstoned_present": present}
    sidecar_prose = surfaces.quarantine_prose()
    for path in ledger_repair.sidecars(bucket):
        result = jsonl.read(path)
        present = _tombstoned_rows(result.rows, sidecar_prose, keys,
                                   fragments=True) if trusted else None
        ledgers[path.name] = {**_counts(result), "tombstoned_present": present}
    try:
        undeclared = sorted(p.name for p in bucket.iterdir()
                            if p.name.endswith(".jsonl")
                            and p.name not in declared)
    except OSError:
        undeclared = []
    out: dict = {"ledgers": ledgers, "undeclared": undeclared,
                 "forgotten_check": "ok" if trusted else "unavailable"}
    if checkpoints:
        if trusted:
            found, unscannable = _checkpoint_residue(slug, keys)
            out["checkpoints"] = {"tombstoned_present": found,
                                  "unscannable": unscannable}
        else:
            out["checkpoints"] = {"tombstoned_present": None,
                                  "unscannable": None}
    return out


def census_machine() -> dict:
    """Health only, for the ledger files that sit outside any one bucket:
    the migration receipts, the published team tombstones and the two logs.
    Keys are paths relative to the store's own roots."""
    found: dict[str, Path] = {
        f"checkpoints/{_MIGRATIONS}": config.checkpoint_dir() / _MIGRATIONS}
    for name in _LOG_LEDGERS:
        found[f"logs/{name}"] = config.log_dir() / name
    root = config.team_dir()
    for path in sorted(root.rglob("tombstones.jsonl")):
        found[f"team/{path.relative_to(root).as_posix()}"] = path
    return {key: _counts(jsonl.read(path)) for key, path in found.items()}


def record_marker(slug: str, *, force: bool = False) -> None:
    """Stamp `checkpoints/<slug>/.ledger-census` with this bucket's census,
    once. An existing marker is left alone and costs one stat, unless
    `force` (a repair re-stamps it). The marker
    carries a version, a UTC stamp and the per-ledger state and counts: no
    row content, no checkpoint-surface walk (that part of the census grows
    with the whole store, and a first write should not pay for it). Raises on
    failure; the caller (`store._record_ledger_census`) owns the swallowing."""
    path = config.checkpoint_dir() / slug / MARKER_NAME
    if path.exists() and not force:
        return
    census = census_bucket(slug, checkpoints=False)
    marker = {"version": MARKER_VERSION,
              "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "forgotten_check": census["forgotten_check"],
              "ledgers": census["ledgers"]}
    store._atomic_write(path, json.dumps(marker, indent=2) + "\n")
