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

from . import config, jsonl, normalize, privacy, store, surfaces

MARKER_NAME = store._LEDGER_CENSUS_NAME
MARKER_VERSION = 1

_MIGRATIONS = "migrations.jsonl"
_LOG_LEDGERS = ("checks.jsonl", "recall-delivery.jsonl")


def _counts(result: jsonl.Read) -> dict:
    return {"state": result.health.value, "torn": result.torn,
            "split": result.split, "garbage": result.garbage}


def _tombstoned_rows(rows: list, prose: tuple, keys: set) -> int:
    if not keys or not prose:
        return 0
    return sum(
        1 for row in rows
        if any(normalize.content_key(v) in keys
               for v in surfaces.prose_values(prose, row)))


def _checkpoint_residue(slug: str, keys: set) -> int:
    """Items in this bucket's checkpoint JSON that still hold a forgotten
    value. The walk and the scan are the privacy audit's own."""
    if not keys:
        return 0
    known, _unknown = privacy._checkpoint_candidates()
    found = 0
    for path in known:
        findings, _suppressed, _member = privacy._scan_json_surface(
            path, slug, keys, "checkpoint")
        found += len(findings)
    return found


def census_bucket(slug: str, *, checkpoints: bool = True) -> dict:
    """One bucket: {"ledgers": {name: {state, torn, split, garbage,
    tombstoned_present}}, "undeclared": [names], "checkpoints":
    {"tombstoned_present": n}}. `checkpoints=False` skips the walk over
    checkpoint JSON, the one part whose cost grows with the whole store."""
    bucket = config.checkpoint_dir() / slug
    keys = store.forgotten_content_keys(slug)
    declared = surfaces.bucket_ledger_names()
    ledgers: dict = {}
    for name in declared:
        result = jsonl.read(bucket / name)
        ledgers[name] = {**_counts(result), "tombstoned_present":
                         _tombstoned_rows(result.rows,
                                          surfaces.bucket_ledger(name).prose,
                                          keys)}
    try:
        undeclared = sorted(p.name for p in bucket.iterdir()
                            if p.name.endswith(".jsonl")
                            and p.name not in declared)
    except OSError:
        undeclared = []
    out: dict = {"ledgers": ledgers, "undeclared": undeclared}
    if checkpoints:
        out["checkpoints"] = {
            "tombstoned_present": _checkpoint_residue(slug, keys)}
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


def record_marker(slug: str) -> None:
    """Stamp `checkpoints/<slug>/.ledger-census` with this bucket's census,
    once. An existing marker is left alone and costs one stat. The marker
    carries a version, a UTC stamp and the per-ledger state and counts: no
    row content, no checkpoint-surface walk (that part of the census grows
    with the whole store, and a first write should not pay for it). Raises on
    failure; the caller (`store._record_ledger_census`) owns the swallowing."""
    path = config.checkpoint_dir() / slug / MARKER_NAME
    if path.exists():
        return
    census = census_bucket(slug, checkpoints=False)
    marker = {"version": MARKER_VERSION,
              "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "ledgers": census["ledgers"]}
    store._atomic_write(path, json.dumps(marker, indent=2) + "\n")
