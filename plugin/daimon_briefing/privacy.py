"""Read-only tombstone residue audit (`daimon audit privacy`).

#583 shipped because forget's deletion contract was written against the
wrong surface set, and a passing test asserted the residue. This module
verifies the contract instead of trusting it: hash every plaintext field
on every surface and intersect with the forget ledger's tombstone set.

Read-only is load-bearing: sqlite opens go through URIs (a plain connect
CREATES a missing db) — mode=ro for the live index, immutable=1 for the dead
orphan snapshots, because mode=ro alone still lets sqlite write -shm/-wal
sidecars for a WAL-mode file. The recall public API is never touched
(_ensure_fresh rebuilds). Findings carry hashes, never the text — audit
output gets re-serialized into checkpoints, so printing the value would
re-capture the thing the user deleted.
"""
import json
import sqlite3
import time
from pathlib import Path

from . import config, normalize, store, teamproject

# Plaintext-bearing item fields. forget currently hashes only `text`
# (cli._cmd_forget, store.scrub_content_key, recall rebuild) — auditing
# quote/scene as well is deliberate: it detects the residue forget cannot
# yet reach, which is this tool's reason to exist.
_FIELDS = ("text", "quote", "scene")

# Free-text fields store.append_event writes to events.jsonl. `note` alone was
# a third of the surface: `resolve`/`reopen` pass the item's WHOLE text as
# `item_text` with no forget gate, and `status` is free-form by design (readers
# prefix-match), so a user's own resolution wording can BE the value. The file
# is append-only and never rewritten, so forget cannot reach any of the three.
_EVENT_FIELDS = ("note", "item_text", "status")

_EVENTS_NAME = "events.jsonl"

# Files that live in the checkpoint store and hold NO item plaintext BY
# CONSTRUCTION. Every exemption is checked against its owning module, because
# an exemption granted on a hunch is exactly how a plaintext surface goes
# unreported. Reporting these made exit 0 unreachable on a real install (74
# `.receipt` sidecars in the flat dir of the machine this was specced on).
_EXEMPT_NAMES = frozenset({
    # store._pointer_lock: an empty flock sidecar, opened "a+" and never written.
    store._LOCK_NAME,
    # store.append_verification: item_ref + a reason CODE, "never the rejected
    # text" — quote verification runs pre-redaction, so it must not log text.
    "verification.jsonl",
    # store.record_forget_hits: {ts, key} — the canonical hash only, "NEVER the
    # text or any prefix of it".
    "forget-hits.jsonl",
    # macOS Finder metadata: directory listing/positions, never file contents.
    ".DS_Store",
})
# receipts._sidecar_path. The blob is {jws, receipt, kid, performer_id} where
# the receipt carries inputs/outputs HASHES, method and nonce — it binds to a
# checkpoint's bytes, it never copies them.
_EXEMPT_SUFFIX = ".receipt"


def _is_plaintext_free(path: Path) -> bool:
    return path.name in _EXEMPT_NAMES or path.suffix == _EXEMPT_SUFFIX


def _hashes(item: dict) -> set[str]:
    out: set[str] = set()
    for field in _FIELDS:
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            out.add(normalize.content_key(value))
    return out


def _checkpoint_candidates() -> tuple[list[Path], list[tuple[Path, str | None]]]:
    """(json surfaces, [(unknown path, owning bucket or None)]).

    Unlike store._plaintext_surfaces, files the walk does NOT recognise are
    returned, never dropped: a `latest.json.bak-*` full-checkpoint copy is
    exactly how plaintext escapes a suffix-filtered walk. Sub-directories
    inside a bucket are reported for the same reason — an undescended dir is
    unread contents, not an absence of them.

    Unknowns are TAGGED with their bucket: a stray file in project B's bucket
    is B's blind spot, and an untagged list made every project on the machine
    unprovable at once. Flat-dir unknowns tag None (= every audit's problem):
    `latest.json` there is the GLOBAL pointer and its neighbours may hold any
    project's session. `.chunk-cache/` is reported separately at store level;
    `events.jsonl` is scanned as the note ledger, not as an item surface."""
    known: list[Path] = []
    unknown: list[tuple[Path, str | None]] = []
    d = config.checkpoint_dir()
    try:
        entries = list(d.iterdir())
    except OSError:
        return [], []
    for entry in entries:
        try:
            if entry.is_file():
                if entry.suffix == ".json":
                    known.append(entry)
                elif not _is_plaintext_free(entry):
                    unknown.append((entry, None))
            elif entry.is_dir() and entry.name != ".chunk-cache":
                for p in entry.iterdir():
                    if not p.is_file():
                        unknown.append((p, entry.name))
                    elif p.suffix == ".json":
                        known.append(p)
                    elif p.name != _EVENTS_NAME and not _is_plaintext_free(p):
                        unknown.append((p, entry.name))
        except OSError:
            # Could not even list it — tag None, the conservative side: an
            # entry whose bucket is unknown belongs to nobody in particular.
            unknown.append((entry, None))
    return known, unknown


def _load_payload(path: Path) -> dict | None:
    """The file's JSON object, or None = could not read it as one.

    UnicodeDecodeError is a ValueError, and that is deliberate in the catch:
    a non-UTF-8 file must land in `unscannable`, never abort the audit."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _payload_findings(payload: dict, path: Path, keys: set[str],
                      surface: str) -> list[dict]:
    findings: list[dict] = []
    for section, key in store._ITEM_LISTS:
        for item in ((payload.get(section) or {}).get(key) or []):
            if not isinstance(item, dict):
                continue
            for h in _hashes(item) & keys:
                findings.append({"path": str(path),
                                 "item_id": item.get("id"),
                                 "content_hash": h,
                                 "surface": surface})
    return findings


def _scan_json_surface(path: Path, slug: str, keys: set[str],
                       surface: str) -> tuple[list[dict], bool | None]:
    """Findings + membership. None membership = unreadable (unscannable).

    Membership mirrors store.project_surfaces: bucket location OR payload
    project_slug — but unreadable files are SURFACED here, not silently
    excluded, because "could not check" must never fold into "clean"."""
    payload = _load_payload(path)
    if payload is None:
        return [], None
    if path.parent.name != slug and payload.get("project_slug") != slug:
        return [], False
    return _payload_findings(payload, path, keys, surface), True


def _team_segments(root: Path, path: Path) -> tuple[str, ...] | None:
    """Logical project segments of a NESTED-era team file, else None.

    store._dual_write_team's nested layout is
        <team_dir>/<remote>/projects/<seg…>/authors/<author>/<sid>.json
    and the legacy flat era is <team_dir>/<remote>/authors/<author>/<sid>.json
    (no segments — that era is stamp-filtered, exactly as read_team does)."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return None
    if len(parts) < 6 or parts[1] != "projects" or parts[-3] != "authors":
        return None
    return parts[2:-3] or None


def _orphan_index_files() -> list[Path]:
    """Crashed rebuilds leave `recall.db.<pid>.tmp` (+`-journal`) beside the
    index — near-complete plaintext snapshots (four live multi-MB examples
    existed on the dev machine the day this was specced). They are the same
    sqlite shape, so they get the same scan, not a hand-wave."""
    db = config.recall_db()
    try:
        return sorted(p for p in db.parent.glob(db.name + ".*") if p.is_file())
    except OSError:
        return []


def _scan_recall_db(db_path: Path, slug: str, keys: set[str],
                    immutable: bool = False) -> tuple[list[dict], list[dict], bool | None]:
    """Scan the derived index WITHOUT the recall API (which rebuilds).

    A row match is only real residue if the stored fingerprint equals a
    freshly computed one — forget deletes recall rows LAZILY at the next
    rebuild, so a stale index legitimately still holds the value. Classing
    that as residue would cry wolf after every single forget.

    `immutable=1` for the orphan snapshots: `mode=ro` still lets sqlite create
    `-shm`/`-wal` sidecars for a WAL-mode file, which would make a READ-ONLY
    auditor write to the store. Orphans are dead files by definition (a
    crashed rebuild's leftovers), so promising sqlite they cannot change is
    both true and the only way to guarantee no sidecar appears. The LIVE index
    keeps `mode=ro` — it is not dead, and a stale snapshot of it would lie."""
    from . import recall  # local import: only _fingerprint (pure read) is used
    try:
        if not db_path.exists():
            return [], [], True  # absent index holds nothing — vacuously clean
    except OSError:
        # exists() RAISES on an unreadable parent (EACCES is not in pathlib's
        # ignored set): cannot even stat it, so it cannot be proven clean.
        return [], [], None
    query = "?immutable=1" if immutable else "?mode=ro"
    try:
        conn = sqlite3.connect(f"file:{db_path}{query}", uri=True)
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta"))
            rows = conn.execute(
                "SELECT item_id, text, quote, scene, project_slug, author"
                " FROM items").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return [], [], None
    current = meta.get("fingerprint") == recall._fingerprint()
    union = store.all_forgotten_content_keys()
    self_author = store.project_slug(config.author())
    residue: list[dict] = []
    informational: list[dict] = []
    for item_id, text, quote, scene, row_slug, author in rows:
        hashes = _hashes({"text": text, "quote": quote, "scene": scene})
        if row_slug == slug:
            hit, surface = hashes & keys, "recall-index-residue"
        elif row_slug is None:
            hit, surface = hashes & union, "unattributed"
        elif author and store.project_slug(str(author)) != self_author:
            # Foreign-author rows answer to the machine-global union — the
            # shipped inbound gate's semantics (recall.py:244), mirrored.
            hit, surface = hashes & union, "recall-index-residue"
        else:
            continue    # another local project's row — its own audit's job
        for h in hit:
            finding = {"path": str(db_path), "item_id": item_id,
                       "content_hash": h, "surface": surface}
            if current:
                residue.append(finding)
            else:
                finding["surface"] = "stale-index-pending-rebuild"
                informational.append(finding)
    return residue, informational, True


def _scan_team_dir(slug: str, keys: set[str], project_dir,
                   unscannable: list[str]) -> list[dict]:
    """Findings in the shared team mirror; appends unreadable files in place.

    Membership by PATH for the nested era, mirroring store.read_team: a
    teammate's copy of THIS project is stamped with the WRITER's own
    path-derived `project_slug` (their checkout lives elsewhere), so a
    payload-only test skips precisely the file this audit exists to catch —
    the motivating case in the spec. The `projects/<seg…>/` subtree IS the
    project identity there, so it is the filter.

    A subtree counts as this project's when the logical path resolves to it
    (teamproject.read_candidates — the same resolver read_team fans across) OR
    when this machine's own mirror in that subtree is stamped with our slug.
    The second signal is what keeps `--all` honest: it audits from a bucket
    slug, not a working dir, so there is no git origin left to probe.

    The payload stamp stays an OR-fallback — it is the whole membership test
    for the legacy flat era (`<remote>/authors/<author>/<sid>.json`), and it
    is how our own nested copies identify themselves."""
    root = config.team_dir()
    try:
        files = sorted(root.rglob("*.json"))
    except OSError:
        return []
    loaded: list[tuple[Path, dict, tuple[str, ...] | None]] = []
    for path in files:
        payload = _load_payload(path)
        if payload is None:
            unscannable.append(str(path))
            continue
        loaded.append((path, payload, _team_segments(root, path)))
    owned = set(teamproject.read_candidates(project_dir))
    owned |= {segs for _p, payload, segs in loaded
              if segs and payload.get("project_slug") == slug}
    findings: list[dict] = []
    for path, payload, segs in loaded:
        if (segs in owned if segs else False) \
                or payload.get("project_slug") == slug:
            findings.extend(_payload_findings(payload, path, keys, "team-copy"))
    return findings


def audit_project(project_dir=None) -> dict:
    slug = store.project_slug(project_dir)
    keys = store.forgotten_content_keys(project_dir=project_dir)
    result = {"slug": slug, "findings": [], "informational": [],
              "unscannable": [], "surfaces_scanned": 0,
              "zero_surfaces": False}
    if not slug:
        result["zero_surfaces"] = True
        result["cache"] = {"entries": 0, "oldest_days": None}
        return result
    known, unknown = _checkpoint_candidates()
    # Only this project's blind spots: an unknown file in ANOTHER bucket is
    # that project's audit's problem (None = flat dir = everyone's).
    result["unscannable"].extend(
        str(p) for p, bucket in unknown if bucket in (None, slug))
    members = 0
    for path in known:
        findings, member = _scan_json_surface(path, slug, keys, "checkpoint")
        if member is None:
            result["unscannable"].append(str(path))
        elif member:
            members += 1
            result["findings"].extend(findings)
    result["surfaces_scanned"] = members
    result["zero_surfaces"] = members == 0
    result["findings"].extend(_scan_team_dir(slug, keys, project_dir,
                                             result["unscannable"]))
    res, info, readable = _scan_recall_db(config.recall_db(), slug, keys)
    if readable is None:
        result["unscannable"].append(str(config.recall_db()))
    else:
        result["findings"].extend(res)
        result["informational"].extend(info)
    for orphan in _orphan_index_files():
        res, info, readable = _scan_recall_db(orphan, slug, keys,
                                              immutable=True)
        if readable is None:
            result["unscannable"].append(str(orphan))
            continue
        for f in res + info:
            f["surface"] = "orphan-tmp"
            result["findings"].append(f)
    # Event ledger: every free-text field hashed WHOLE — catches a field that
    # IS the value verbatim; one merely containing it is undetectable by hash
    # (stated report limitation). `status` on the tombstone row is
    # "forgotten:<hash>": hashing that STRING can never equal the key it
    # names, so it needs no special case. `item_id` is always the row's
    # item_ref, whichever field matched.
    events = config.checkpoint_dir() / slug / _EVENTS_NAME
    try:
        # Read first, ask later: `events.exists()` RAISES on an unreadable
        # parent dir (EACCES is not in pathlib's ignored set), which would
        # crash the auditor on exactly the tree it must report on.
        # UnicodeDecodeError IS a ValueError: a non-UTF-8 ledger is
        # cannot-check, not a traceback out of a read-only auditor.
        lines = events.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []                  # no ledger written yet — nothing to scan
    except (OSError, ValueError):
        lines = []
        result["unscannable"].append(str(events))
    for line in lines:
        try:
            evt = json.loads(line)
        except ValueError:
            continue
        if not isinstance(evt, dict):
            continue
        for field in _EVENT_FIELDS:
            value = evt.get(field)
            if not (isinstance(value, str) and value.strip()):
                continue
            h = normalize.content_key(value)
            if h in keys:
                result["findings"].append({
                    "path": str(events),
                    "item_id": evt.get("item_ref"),
                    "content_hash": h, "surface": "events-note"})
    # Chunk cache: value-level detection impossible (cache keyed by chunk
    # text, values are substrings). Store-level honesty: entry count + real
    # oldest age — the reaper runs only on WRITES, so never assert bounded.
    cache_dir = config.checkpoint_dir() / ".chunk-cache"
    entries, oldest = 0, None
    try:
        for p in cache_dir.iterdir():
            if p.is_file():
                entries += 1
                age = (time.time() - p.stat().st_mtime) / 86400
                oldest = age if oldest is None else max(oldest, age)
    except OSError:
        pass
    result["cache"] = {"entries": entries, "oldest_days": oldest}
    return result


def audit_all() -> list[dict]:
    """One audit per local bucket — each against ITS OWN tombstone set.

    Never the global union for local rows: project B legitimately holding a
    sentence project A forgot is not residue (shipped deletion is per-bucket,
    recall.py:479; the union governs only the inbound foreign gate)."""
    results: list[dict] = []
    try:
        buckets = sorted(e.name for e in config.checkpoint_dir().iterdir()
                         if e.is_dir() and e.name != ".chunk-cache")
    except OSError:
        buckets = []
    for slug in buckets:
        results.append(audit_project(project_dir=slug))
    return results


def exit_code(results: list[dict]) -> int:
    """0 proven clean / 1 residue / 3 cannot-prove. 2 belongs to argparse and
    the house hard-error convention. Cannot-prove NEVER folds to clean —
    that is scripted false confidence, the exact thing #583 shipped."""
    # Empty result set means nothing was audited (no buckets, or checkpoint_dir
    # inaccessible) — cannot distinguish from "clean", so must report cannot-prove.
    if not results:
        return 3
    if any(r["findings"] for r in results):
        return 1
    if any(r["unscannable"] or r["zero_surfaces"] for r in results):
        return 3
    return 0
