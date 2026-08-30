"""Recall index (#112): derived sqlite3+FTS5 lexical search over checkpoints.

NEVER source of truth. The db at config.recall_db() is a disposable cache built
by scanning the same files everything else reads:

    local flat store   <checkpoint_dir>/<session_id>.json   (pointers excluded)
    team dir           <team_dir>/*/authors/<author>/*.json (all remotes, #111)
                       <team_dir>/*/projects/**/authors/<author>/*.json (#200)

Any doubt about the db — missing, corrupt, foreign schema, stale fingerprint —
resolves to a full rebuild. Rebuild is a linear scan of at most a few hundred
small JSON files; correctness over cleverness (no incremental upserts).

Schema: `items` carries one row per cognitive item (text/trust/kind/author/
project_slug/session_id/created), plus two Graphiti-inspired interval slots:
`superseded_by` (populated — see below) and `invalidated_by` (#835: populated
from DERIVED contradiction evidence only — the per-bucket verification
ledger's worldcheck receipt-contradiction rows fold in at rebuild as
"<check>:<reason>@<ts>", latest by ts, author-scoped to this install. The
value is EVIDENCE, not a verdict: "was contradicted by <evidence> at <ts>"
— no confirmation rows or cure path exist yet, so it never means "is
currently false". Replacement and contradiction are independent axes:
neither write ever touches the other. Authority precedent: model-flagged
contradictions never mint the link — derived world evidence writes it, or
nothing does. #837 made the read surfaces honor it: search sorts a
contradicted row below a merely-replaced one, suggest demotes it harder than
supersession does, and both CLI renderers mark it. None of them filters —
the evidence is machine-local and cure-less, so burial stays visible and
reversible rather than silent). A contentless FTS5 table indexes text +
quote for MATCH; rows join back to `items` by rowid.

Supersession v3 (#234) is ITEM-LEVEL evidence only: `superseded_by` is set by
typed `supersedes` links (#14) — id-bound directly, free-text via never-guess
unique salient-term resolution — and by events.jsonl resolutions. Whole-
checkpoint recency (the v1 flag, measured at coin-flip precision) now only
populates `frontier`, a silent rank input: newest-checkpoint items tiebreak
above older ones, no label. Flagged items rank down but are never hidden
(an old decision is still evidence).

Project attribution: team copies carry a stamped `project_slug` (#111), and
write_checkpoint stamps local flat files the same way — pointer rotation
expires, a stamp doesn't. Legacy pre-stamp files fall back to the per-project
bucket pointers (<dir>/<slug>/latest|prev-N.json -> session_id); a stampless
session no live pointer references indexes with project_slug NULL and only
surfaces under --all-projects — an unknown project must never leak into
another project's scoped recall (same philosophy as store's pointer routing).
"""

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from . import (config, normalize, policy, redact, schema, scoring, store,
               teamproject)

log = logging.getLogger("daimon.recall")


def _note_error(where: str, exc: BaseException) -> None:
    """Breadcrumb for a swallowed index error (#28). Recall is fail-open by
    design — a broken index degrades to [] — but silently, a broken recall is
    indistinguishable from \"no prior work\". One line to recall-error.log
    (read back by `daimon status`) plus a log.warning. Best-effort: the
    breadcrumb itself must never break the swallow."""
    log.warning("recall.%s swallowed %s: %s", where, type(exc).__name__, exc)
    try:
        d = config.log_dir()
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with (d / "recall-error.log").open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {where}: {type(exc).__name__}: {exc}\n")
    except OSError:
        pass

# v2 (#125): items grew importance + first_seen for suggest()'s ranking.
# v3 (#234): items grew item_id + frontier, and superseded_by changed MEANING —
# it now carries only item-level evidence (typed supersedes links, events.jsonl
# resolutions), never whole-checkpoint recency; recency lives in `frontier` as
# a silent rank input. The version bump makes _ensure_fresh discard old dbs.
# v4 (#317): items + items_fts grew a scene column (episodic context traces);
# an old db queried with the new column list would OperationalError, so the
# bump forces the rebuild instead.
# v5 (#452): items grew pinned — the cli age gate exempts pinned standing
# rules, so suggest()'s SELECT names the column and a v4 db would
# OperationalError on it; the bump funnels that into the rebuild too.
# v6 (#836): items grew the (item_id, project_slug) identity index the
# ledger folds bind through — purely a performance object, but the bump
# rolls every existing db onto it deterministically instead of waiting for
# an unrelated fingerprint change.
_SCHEMA_VERSION = "6"

_FTS5_MISSING_MSG = (
    "sqlite3 has no FTS5 module — `daimon recall` needs an FTS5-enabled "
    "SQLite (every python.org / uv-managed CPython since 3.6 ships one; "
    "rebuild your Python against a full SQLite to fix this)"
)

# (checkpoint section, key, indexed kind). Every trust-tagged cognitive list in
# the serializer schema, including contradictions_flagged (item shape varies) —
# derived from the shared item-field table (#146).
_KIND_SOURCES = schema.KIND_SOURCES


class RecallError(RuntimeError):
    """Recall cannot run at all (e.g. sqlite3 built without FTS5)."""


def _load(path: Path) -> dict | None:
    try:
        cp = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None  # torn/foreign file — skip, never crash a rebuild
    return cp if isinstance(cp, dict) else None


def _items(cp: dict):
    """Yield (kind, text, trust, quote, scene, importance, first_seen, item_id,
    pinned, supersede_targets) for every cognitive item in a checkpoint.
    Tolerant of shape drift: bare strings become text-only items; anything
    without usable text is skipped (an index row with no text matches
    nothing). importance/first_seen/item_id are None on pre-D-011 items;
    scene is "" on items without one (#317). pinned is 0/1 — the #369
    force-pinned standing-rule flag, carried so the #452 age gate can read it
    without re-opening checkpoints (any truthy value counts; absent = 0).
    supersede_targets is the item's `supersedes` link target strings (#234) —
    usually empty."""
    for section, key, kind in _KIND_SOURCES:
        block = cp.get(section)
        if not isinstance(block, dict):
            continue
        raw = block.get(key)
        if key == "active_topic":
            raw = [raw]
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, str):
                item = {"text": item}
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            imp = item.get("importance")
            if not (isinstance(imp, int) and not isinstance(imp, bool)):
                imp = None
            fs = item.get("first_seen")
            if not isinstance(fs, str):
                fs = None
            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id.strip():
                item_id = None
            targets = []
            links = item.get("links")
            if isinstance(links, list):
                for link in links:
                    if (isinstance(link, dict)
                            and link.get("type") == "supersedes"
                            and isinstance(link.get("target"), str)
                            and link["target"].strip()):
                        targets.append(link["target"].strip())
            yield (kind, text, str(item.get("trust") or ""),
                   str(item.get("quote") or ""), str(item.get("scene") or ""),
                   imp, fs, item_id, 1 if item.get("pinned") else 0, targets)


def _bucket_slugs(d: Path) -> dict[str, str]:
    """session_id -> project slug, derived from the per-project bucket pointers
    (<dir>/<slug>/latest.json|prev-N.json). Fallback attribution for legacy
    flat files written before write_checkpoint stamped project_slug."""
    out: dict[str, str] = {}
    try:
        entries = list(d.iterdir())
    except OSError:
        return out
    for child in entries:
        try:
            pointers = [p for p in child.iterdir()
                        if p.is_file() and store._POINTER_RE.match(p.name)]
        except (OSError, NotADirectoryError):
            continue
        for p in pointers:
            cp = _load(p)
            sid = cp.get("session_id") if cp else None
            if sid:
                out[str(sid)] = child.name
    return out


def _scan_sources():
    """Yield (session_id, author, project_slug, created_epoch, checkpoint) for
    every checkpoint recall can see. Team dir first — its copies carry the
    authoritative project_slug stamp — then the local flat store, skipping any
    (author, session) the team scan already produced (DAIMON_TEAM dual-writes
    the same checkpoint to both places; one row set, not two).

    Team files honor the #113 retention window (store.team_retention_cutoff —
    the same floor read_team uses, so recall and `brief --team` agree on what
    has aged out, #120). An aged-out team file is skipped WITHOUT entering
    `seen`, so a dual-written copy of your OWN session still indexes from the
    local scan below — retention is a team-view concept, never a cap on your
    own searchable history.

    Inbound gate (#423): foreign content (a synced remote's files, other
    authors) passes policy.admit_foreign before a row can exist — the index
    is the durable local copy, so scope, local re-redaction, the local forget
    tombstones and the foreign verbatim->inferred clamp all apply HERE, not
    at query time. The index is machine-global (no project dir in hand), so
    remote membership is judged by what the sidecar's own toml vouches for
    (teamproject.granted_paths) against the checkpoint's stamped logical
    path; flat-era foreign blobs carry no toml-checkable identity and fail
    CLOSED. A gated-out file is skipped WITHOUT entering `seen` — same
    posture as retention. The 'local' mirror (this machine's own writes) and
    this author's own synced-back copies stay ungated."""
    seen: set[tuple[str, str]] = set()
    root = config.team_dir()
    cutoff = store.team_retention_cutoff()
    self_author = store.project_slug(config.author())
    # #600 slice B: teammates' published tombstones gate the index too — an
    # inbound row is suppressed by ANY tombstone this machine can see, local
    # or foreign (over-suppression is this path's documented posture).
    forgotten = (store.all_forgotten_content_keys()
                 | store.foreign_forgotten_content_keys())
    try:
        remotes = list(root.iterdir())
    except OSError:
        remotes = []
    # Mirror of _team_write_slugs / read_team (#423): the env grant is
    # explicit machine intent with a single synced clone, unroutable noise
    # with several.
    clones = [r for r in remotes if r.is_dir()
              and r.name != store._TEAM_LOCAL_REMOTE
              and (r / ".git").exists()]
    honor_env = len(clones) <= 1
    for remote in remotes:
        foreign = remote.name != store._TEAM_LOCAL_REMOTE
        granted = (teamproject.granted_paths(remote, honor_env=honor_env)
                   if foreign else None)
        # Both layout eras (#200): legacy flat authors/* plus nested
        # projects/**/authors/* — same walker read_team's fan-in rests on.
        author_dirs = store._team_author_dirs(remote)
        for adir in author_dirs:
            try:
                files = [p for p in adir.iterdir()
                         if p.is_file() and p.suffix == ".json"]
            except OSError:
                continue
            for p in files:
                recency = store._file_recency(p)
                if cutoff is not None and recency < cutoff:
                    continue  # aged out of the team view (#113/#120)
                cp = _load(p)
                if cp is None:
                    continue
                sid = str(cp.get("session_id") or p.stem)
                author = str(cp.get("author") or adir.name)
                if foreign and store.project_slug(author) != self_author:
                    stamp = cp.get("team_project")
                    segs = (teamproject.logical_segments(stamp)
                            if isinstance(stamp, str) else ())
                    cp = policy.admit_foreign(
                        cp, member=bool(segs) and segs in granted,
                        forgotten_keys=forgotten,
                        redact_fn=redact.redact_text)
                    if cp is None:
                        continue  # not admitted; never enters `seen`
                key = (author, sid)
                if key in seen:
                    continue
                seen.add(key)
                yield sid, author, cp.get("project_slug"), recency, cp

    d = config.checkpoint_dir()
    slug_by_sid = _bucket_slugs(d)
    try:
        files = store._session_files(d)
    except OSError:
        files = []
    for p in files:
        cp = _load(p)
        if cp is None:
            continue
        sid = str(cp.get("session_id") or p.stem)
        # Phase 1 stamps `author` on every write; legacy checkpoints fall back
        # to the current identity — local files are this machine's own history.
        # Note: that fallback is UNSTABLE for pre-#111 files — change
        # DAIMON_AUTHOR/git identity and the same legacy session reindexes
        # under the new (author, sid) key. Accepted: rebuilds are total, so no
        # duplicates persist within one index.
        author = str(cp.get("author") or "") or config.author()
        key = (author, sid)
        if key in seen:
            continue
        seen.add(key)
        # Attribution: the embedded stamp outlives pointer rotation; the bucket
        # pointers are the fallback for legacy pre-stamp files only. No fuzzy
        # backfill for stampless rotated-out sessions — they stay NULL-slug
        # (all-projects-only) rather than risk a wrong project.
        slug = cp.get("project_slug") or slug_by_sid.get(sid)
        yield sid, author, slug, store._file_recency(p), cp


def _fingerprint() -> str:
    """Staleness key over every source file: a hash of the sorted
    (path, mtime_ns, size) set. Count+newest-mtime alone misses a same-second
    delete+add (count and max unchanged, world different) — the name set makes
    that visible. Computed BEFORE a scan so a race errs toward one extra
    rebuild, never toward serving stale rows."""
    paths: list[Path] = []
    d = config.checkpoint_dir()
    try:
        for e in d.iterdir():
            if e.is_file() and e.suffix == ".json":
                paths.append(e)  # pointers included: rotation moves attribution
            elif e.is_dir():
                # events.jsonl is index CONTENT (_apply_event_resolutions folds
                # it into superseded_by), so it must be fingerprint INPUT too —
                # else a resolve/reopen serves stale rows until an unrelated
                # checkpoint write happens to invalidate the db (#245).
                # verification.jsonl joined the same club in #835
                # (_apply_verification_invalidations folds it into
                # invalidated_by): new contradiction evidence must rebuild,
                # never serve stale NULLs. The name set is store's contract
                # (INDEX_CONTENT_LEDGERS), so a third fold-able ledger
                # cannot dodge this walk unnoticed.
                paths.extend(p for p in e.iterdir()
                             if p.is_file()
                             and (p.suffix == ".json"
                                  or p.name in store.INDEX_CONTENT_LEDGERS))
    except OSError:
        pass
    try:
        paths.extend(config.team_dir().rglob("*.json"))
        # #600 slice B: a teammate's tombstone ledger is index CONTENT for
        # the same reason events.jsonl is — _scan_sources suppresses rows
        # against it — so it must be fingerprint INPUT too. Without this a
        # pulled tombstone leaves the fingerprint unchanged, no rebuild
        # runs, and the index keeps serving the value read_team already
        # withholds. Naming the file .jsonl to dodge the *.json walks is
        # exactly what made this easy to miss.
        paths.extend(config.team_dir().rglob(store._TOMBSTONE_NAME))
    except OSError:
        pass
    entries = []
    for p in paths:
        try:
            st = p.stat()
        except OSError:
            continue
        entries.append(f"{p}\0{st.st_mtime_ns}\0{st.st_size}")
    entries.sort()
    # Retention (#120) changes index CONTENT without touching any file: a team
    # file ages past the cutoff, or the knob changes. Fold the knob + current
    # day into the key so the index refreshes on knob changes and at day
    # granularity as files age out (retention is day-grained anyway).
    days = config.team_retention_days()
    entries.append(f"retention\0{days}\0{int(time.time() // 86400) if days else 0}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _init_schema(conn: sqlite3.Connection) -> None:
    try:
        conn.executescript(
            """
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE items(
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,
                quote TEXT,
                scene TEXT,
                trust TEXT,
                kind TEXT,
                author TEXT,
                project_slug TEXT,
                session_id TEXT,
                created REAL,
                superseded_by TEXT,
                invalidated_by TEXT,
                importance INTEGER,
                first_seen TEXT,
                item_id TEXT,
                pinned INTEGER NOT NULL DEFAULT 0,
                frontier INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX idx_items_identity ON items(item_id, project_slug);
            CREATE VIRTUAL TABLE items_fts USING fts5(text, quote, scene, content='');
            """
        )
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).lower():
            raise RecallError(_FTS5_MISSING_MSG) from exc
        raise


# Same floor as carry._MIN_SHARED (kept in sync by test): a text link target
# must share >= this many salient terms with exactly ONE distinct prior text.
_MIN_LINK_SHARED = 3

# The #14 item-id shape — twin of carry._ID_SHAPE (import would be circular:
# carry imports recall).
_ID_SHAPE = re.compile(r"[a-z]-[0-9a-f]{6,}(-\d+)?")


def _apply_typed_supersession(conn: sqlite3.Connection, links: list) -> None:
    """Mark link targets superseded (#234). Two target shapes:

    id-shape — direct item_id match (bind_links already resolved it).
    free text — field reality: carry-time binding rarely lands, so the
      rebuild resolves text targets itself with bind_links' never-guess
      semantics: same (author, project, kind), strictly older sessions,
      >= _MIN_LINK_SHARED shared salient terms, and exactly ONE distinct
      matching text — an item carried across N checkpoints is N rows of one
      logical item, so uniqueness is by text, and every row of the matched
      text is marked. Zero or several distinct matches -> leave unmarked
      (a wrong supersession fabricates staleness; a missed one just stays
      quiet, same bias as carry.bind_links)."""
    for (author, slug, kind, owner_sid, owner_recency,
         owner_item_id, owner_text, target) in links:
        if _ID_SHAPE.fullmatch(target):
            conn.execute(
                "UPDATE items SET superseded_by = ?"
                " WHERE item_id = ? AND author = ? AND project_slug IS ?"
                " AND session_id != ?",
                (owner_sid, target, author, slug, owner_sid),
            )
            continue
        want = set(salient_terms(target))
        if not want:
            continue
        rows = conn.execute(
            "SELECT id, text, item_id FROM items"
            " WHERE author = ? AND project_slug IS ? AND kind = ?"
            " AND session_id != ? AND created < ?",
            (author, slug, kind, owner_sid, owner_recency),
        ).fetchall()
        by_text: dict[str, list] = {}
        for rowid, text, item_id in rows:
            # Self/twin guard (mirrors bind_links): carried copies of the
            # superseding item itself must never match its own target.
            if (owner_item_id and item_id == owner_item_id) \
                    or text == owner_text:
                continue
            if len(want & set(salient_terms(text))) >= _MIN_LINK_SHARED:
                by_text.setdefault(text, []).append(rowid)
        if len(by_text) != 1:
            continue  # unbound or ambiguous — never guess
        (rowids,) = by_text.values()
        conn.executemany(
            "UPDATE items SET superseded_by = ? WHERE id = ?",
            [(owner_sid, rid) for rid in rowids],
        )


def _apply_event_resolutions(conn: sqlite3.Connection) -> None:
    """Fold each project bucket's events.jsonl into superseded_by (#234).

    The liveness rule is store.is_resolved over the store.resolutions fold —
    the SAME rule and fold brief/withhold/carry use, not a re-implementation
    (#255: the hand-rolled exact-match copy here silently disagreed with the
    briefing on every free-form status and on reopen-prefix revivals). That
    inherits the whole contract: free-form statuses resolve, reopen* revives
    (case-insensitive), supersede-candidate:* never marks (a machine guess
    must never suppress, #111), and same-second ties break on content (#143;
    reopen wins a tie → unmarked, never-guess). The stored value is the
    superseding item id when the status names one, else "resolved"."""
    try:
        buckets = [d for d in config.checkpoint_dir().iterdir() if d.is_dir()]
    except OSError:
        return
    for bucket in buckets:
        # The bucket dir NAME is the slug, and slug munging is idempotent
        # (guarded by test_project_slug_is_idempotent_on_slugs), so it routes
        # store.resolutions exactly like a project dir.
        forgotten_ids: set[str] = set()
        for ref, evt in store.resolutions(project_dir=bucket.name).items():
            if not store.is_resolved(evt):
                continue
            status = str(evt.get("status") or "")
            # #321: forgotten is removal, not resolution — the content must
            # leave the index entirely, historical checkpoint copies included.
            # Prefix-match like every other status reader; only the LATEST
            # event counts (a later reopen un-hides history by design).
            if status.lower().startswith("forgotten"):
                forgotten_ids.add(ref)
                continue
            value = "resolved"
            if status.lower().startswith("superseded-by:"):
                value = status.split(":", 1)[1].strip() or "resolved"
            conn.execute(
                "UPDATE items SET superseded_by = ?"
                " WHERE item_id = ? AND project_slug IS ?",
                (value, ref, bucket.name),
            )
        if not forgotten_ids:
            continue
        # #427: the scrub is VALUE-keyed, not only id-keyed. One value can
        # live under sibling ids (same sentence in two sections, widened hash
        # within one — store._stamp_item_ids), and forget rewrites only the
        # LATEST session file: an older per-session checkpoint still holds the
        # value under an id the ledger never tombstoned, and rebuild indexes
        # it. The tombstone status embeds the canonical content key
        # (`forgotten:<content_key>`, same ledger the write gate reads), so
        # any row whose text canonicalizes into the forgotten set goes too.
        # The id-keyed delete stays: it covers rows whose stored text was
        # redacted into a different key at capture. Canonicalization runs only
        # when a tombstone exists for the bucket (rows are scanned here, not
        # per-event, so the common no-forget rebuild pays nothing).
        forgotten_keys = store.forgotten_content_keys(project_dir=bucket.name)
        rows = conn.execute(
            "SELECT id, text, quote, scene, item_id FROM items"
            " WHERE project_slug IS ?", (bucket.name,)).fetchall()
        for rowid, text, quote, scene, item_id in rows:
            # #599: the value can also sit in a row's quote/scene column
            # (indexed verbatim). Whole-row delete is the fail-safe: the
            # rebuilt row from the scrubbed checkpoint re-inserts the
            # survivor without the field; a row only reachable here (its
            # surface was unwritable) over-suppresses rather than serves
            # forgotten plaintext.
            if item_id not in forgotten_ids and not (
                    forgotten_keys
                    and (normalize.content_key(text or "") in forgotten_keys
                         or (quote and normalize.content_key(quote)
                             in forgotten_keys)
                         or (scene and normalize.content_key(scene)
                             in forgotten_keys))):
                continue
            # contentless fts5: deletion is the special 'delete'
            # INSERT and must repeat the original column values
            conn.execute(
                "INSERT INTO items_fts(items_fts, rowid, text, quote, scene)"
                " VALUES('delete', ?, ?, ?, ?)",
                (rowid, text, quote, scene))
            conn.execute("DELETE FROM items WHERE id = ?", (rowid,))


# A dead snapshot is unambiguous after this long: a live rebuild holds its
# tmp for seconds, and store._TMP_REAP_SECONDS set the same one-hour
# precedent for checkpoint staging twins.
_SNAPSHOT_REAP_SECONDS = 3600


def reap_dead_snapshots(now: float | None = None, apply: bool = True) -> list:
    """Delete index snapshots left by crashed rebuilds (#601).

    rebuild() stages into `recall.db.<pid>.tmp` and only unlinks ITS OWN
    pid's leftover — a crash under any other pid strands the snapshot (plus
    sqlite's `-journal` sidecar) forever: store._reap_stale_tmps never visits
    this directory and its filter is `.endswith(".tmp")`, which the sidecars
    fail. The strands are full plaintext copies, and the unopenable sidecars
    pinned real installs at `daimon audit privacy` exit 3 (cannot-prove).

    The filter IS the registry declaration (surfaces.match → the entry
    whose strategy is `reap`, shape `recall.db.{pid}.tmp*` with {pid}
    digit-anchored) — never a second hand-written predicate, which would
    reintroduce the parallel-list defect in the one path that DELETES
    files (adversarial-review finding). `recall.db.tmp`,
    `recall.db.bak.tmp`, `recall.db.tmp.gz` beside a DAIMON_RECALL_DB
    override are a user's own files and stay undeclared; the live db's
    sqlite sidecars are dash-named (`recall.db-journal`) so they cannot
    match the dotted glob at all. Age-gated like the checkpoint reaper —
    anything older than an hour is dead by construction (this runs
    unattended from heal at session start on some hosts, so containment
    is the load-bearing property). `apply=False` only lists (heal
    --dry-run). Best-effort per file; returns the paths reaped (or
    would-reap)."""
    from . import surfaces
    db = config.recall_db()
    if now is None:
        now = time.time()
    reaped: list = []
    try:
        candidates = sorted(db.parent.glob(db.name + ".*"))
    except OSError:
        return reaped
    for p in candidates:
        entry = surfaces.match(p.name)
        if entry is None or entry.delete != "reap":
            continue
        try:
            if not p.is_file():
                continue
            if now - p.stat().st_mtime < _SNAPSHOT_REAP_SECONDS:
                continue
            if apply:
                p.unlink()
        except OSError:
            continue
        reaped.append(p)
    return reaped


# #835: the ledger checks that may write invalidated_by — worldcheck's
# receipt-validity contradiction evidence (worldcheck._LEDGER_CHECK; pinned
# equal by test rather than imported, so recall's import graph stays free of
# worldcheck's probe machinery). Capture-time rejection rows ("quote",
# "outcome", #376) describe the CAPTURE, not later disproof, and never
# write; model-flagged contradictions have no path here at all — authority
# precedent: derived world evidence writes the slot, or nothing does.
_INVALIDATION_CHECKS = ("receipt",)
# #839: the cure half. Derived evidence clears derived evidence, which needs
# no widening of #836's authority model: the same probe that contradicted the
# claim is the one now saying it holds. A HUMAN ruling channel is the other
# half and stays deliberately unbuilt, because that WOULD widen the model.
_CONFIRMATION_CHECKS = ("receipt-ok",)


def _apply_verification_invalidations(conn: sqlite3.Connection) -> None:
    """Fold each project bucket's verification ledger into invalidated_by
    (#835): the LATEST worldcheck receipt-contradiction row per item marks
    that item's rows with "<check>:<reason>@<ts>" — a scalar evidence
    reference, the same convention superseded_by keeps (a bare TEXT value,
    never a JSON blob).

    SEMANTICS (#836 review): the field records the latest contradiction
    EVIDENCE — "was contradicted by <evidence> at <ts>" — never a
    present-tense verdict. worldcheck appends no confirmation rows and no
    cure path exists yet (human resolve / confirmation rows are the named
    follow-up), so a populated value means "a probe once contradicted
    this", not "this is currently false".

    Latest by TS, NEVER line order — store.resolutions' documented contract,
    mirrored: the ledger interleaves concurrent writers and clock-skewed
    appends (the cli re-appends rows every briefing), so rows validate,
    dedup to one per item_ref by parsed-ts maximum (an unstamped row never
    displaces a stamped one; equal stamps fall to canonical-JSON order,
    deterministic under any line order), then ONE UPDATE per item through
    the (item_id, project_slug) identity index.

    Binding, each choice deliberate (#836 review):
    - the SAME bucket directory is read and bound (bucket=): bucket names
      are not slug-idempotent (dots munge to '-'), so re-slugging the name
      could read a sibling bucket's ledger;
    - author-scoped to this install's author: receipt evidence is machine-
      local (vitni verified THIS install's origin checkpoint bytes), so it
      must never brand a teammate's mirrored copy of the same item id —
      the same direct-id author binding _apply_typed_supersession uses;
    - superseded_by is an independent axis, never read or written here.

    Known limitations, accepted and documented: rows under a NULL slug
    (unattributed sessions have no bucket), rows a project left behind
    under a moved/renamed slug (the ledger lives with the old bucket), and
    rows captured under a different local author name (an install whose
    author changed) all stay unmarked. Malformed rows are skipped — a
    wrong invalidation fabricates distrust, a missed one stays quiet."""
    author = config.author()
    try:
        buckets = [d for d in config.checkpoint_dir().iterdir() if d.is_dir()]
    except OSError:
        return
    for bucket in buckets:
        # ONE resolution of latest-by-ts, shared with the cure gate that
        # decides whether a confirmation is worth writing (#839). Two copies
        # would drift, and the write side deciding "currently contradicted"
        # differently from the read side is the recorder-and-verifier
        # mismatch this codebase keeps paying for.
        for ref, row in store.latest_receipt_verdicts(bucket=bucket).items():
            # A confirmation CLEARS rather than stamping: the slot holds the
            # latest contradiction evidence, and once the latest evidence is
            # a confirmation there is no contradiction evidence to hold.
            value = (f"{row['check']}:{row['reason']}@{row['ts']}"
                     if row["verdict"] == "contradicted" else None)
            conn.execute(
                "UPDATE items SET invalidated_by = ?"
                " WHERE item_id = ? AND project_slug IS ? AND author IS ?",
                (value, ref, bucket.name, author))


def describe_invalidation(value) -> str | None:
    """Render one stored `invalidated_by` value as a marker phrase, or None.

    The ONE parse of the encoding the fold above writes, deliberately living
    beside that write (#837): a renderer that re-split the value on its own
    would be free to drift into describing a different view than the one the
    verifier recorded. Every read surface calls this.

    Wording is bound by the field's semantics, not by convenience: the slot
    holds the latest contradiction EVIDENCE, so the phrase names the evidence
    and its timestamp and stops there. It never says "false" and never says
    "invalid" — no confirmation row and no cure path exist yet, so a
    present-tense verdict would claim more than the record can carry. Tense
    belongs to the caller's sentence, not to this fragment.

    Tolerant by construction: a value this module could not have written
    (hand-edited db, a future encoding) still renders as evidence rather than
    raising on a user's read path — a marker is never worth a traceback."""
    if not isinstance(value, str) or not value.strip():
        return None
    evidence, sep, ts = value.rpartition("@")
    if not (sep and evidence and ts):
        return f"contradicted by {value}"
    return f"contradicted by {evidence} at {ts}"


def rebuild() -> int:
    """Drop + rebuild the whole index by scanning local + team checkpoints.
    Atomic: builds into a sibling temp file, then os.replace — a concurrent
    reader never opens a half-built db. Returns the number of items indexed."""
    path = config.recall_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = _fingerprint()  # before the scan: race-safe direction
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(str(tmp))
    try:
        _init_schema(conn)
        count = 0
        # (author, project_slug) -> (stamped, recency, session_id) of the newest
        # checkpoint. `stamped` leads the tuple (#240): a stampless legacy file's
        # recency is its mtime — when the file was last TOUCHED (migration,
        # copy, GC), not when the session happened — so letting it compete with
        # real `created` stamps inverts the frontier and flags the true latest
        # as superseded by an older session. A stamped checkpoint always
        # outranks a stampless one; mtime ordering applies among stampless
        # peers only. session_id is the same-second tie-break (#31 item 7):
        # scan order is readdir order, which is unspecified — without a stable
        # secondary key the superseded flags flip across rebuilds.
        newest: dict[tuple, tuple[int, float, str]] = {}
        # (author, slug, kind, owner_sid, owner_recency, owner_item_id,
        #  owner_text, target) per supersedes link (#234).
        links: list[tuple] = []
        for sid, author, slug, recency, cp in _scan_sources():
            # Unattributed sessions never supersede each other (#31 item 6):
            # NULL slugs are UNRELATED projects sharing a non-identity, not
            # one project's history — they stay out of the newest map entirely.
            if slug is not None:
                key = (author, slug)
                stamped = int(store._created_epoch(cp.get("created")) is not None)
                if key not in newest or (stamped, recency, sid) > newest[key]:
                    newest[key] = (stamped, recency, sid)
            for (kind, text, trust, quote, scene, importance, first_seen,
                 item_id, pinned, targets) in _items(cp):
                cur = conn.execute(
                    "INSERT INTO items"
                    " (text, quote, scene, trust, kind, author, project_slug,"
                    "  session_id, created, importance, first_seen, item_id,"
                    "  pinned)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (text, quote, scene, trust, kind, author, slug, sid, recency,
                     importance, first_seen, item_id, pinned),
                )
                conn.execute(
                    "INSERT INTO items_fts(rowid, text, quote, scene)"
                    " VALUES (?, ?, ?, ?)",
                    (cur.lastrowid, text, quote, scene),
                )
                count += 1
                if slug is not None:
                    for target in targets:
                        links.append((author, slug, kind, sid, recency,
                                      item_id, text, target))
        # Whole-checkpoint recency (#234 v3): a silent rank input, NEVER a
        # label. Measured precision of the old recency-derived flag was
        # indistinguishable from a coin flip; only item-level evidence below
        # may set superseded_by. #240's stamped-over-stampless ordering is
        # preserved in the newest map above.
        for (author, slug), (_stamped, _recency, sid) in newest.items():
            conn.execute(
                "UPDATE items SET frontier = 1"
                " WHERE author = ? AND project_slug IS ? AND session_id = ?",
                (author, slug, sid),
            )
        _apply_typed_supersession(conn, links)
        _apply_event_resolutions(conn)
        _apply_verification_invalidations(conn)
        conn.execute("INSERT INTO meta VALUES ('schema_version', ?)",
                     (_SCHEMA_VERSION,))
        conn.execute("INSERT INTO meta VALUES ('fingerprint', ?)", (fingerprint,))
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, path)
    return count


def _ensure_fresh() -> None:
    """Rebuild whenever the db is missing, unreadable, foreign, or stale.
    Derived index: EVERY failure mode funnels into rebuild, silently."""
    path = config.recall_db()
    if not path.exists():
        rebuild()
        return
    try:
        conn = sqlite3.connect(str(path))
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta"))
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        rebuild()
        return
    if (meta.get("schema_version") != _SCHEMA_VERSION
            or meta.get("fingerprint") != _fingerprint()):
        rebuild()


def warm() -> None:
    """Eagerly freshen the index at write time (#246). Staleness is CREATED
    where files change (serialize, team sync, checkpoint re-writes) but the
    lazy _ensure_fresh pays for it on the READ side — and the first reader
    after a serialize is recall-inject on the user's next prompt, putting a
    full rebuild (~800ms on a real corpus) on the per-prompt critical path.
    Call sites are all off that path, so the rebuild happens where nobody is
    waiting; the read side then finds a matching fingerprint and no-ops.

    Idempotent (~ms when already fresh) and fail-open: a broken or FTS5-less
    rebuild must never fail the write that triggered it — swallowed with the
    standard breadcrumb, and the lazy read-side path stays as the safety
    net."""
    try:
        _ensure_fresh()
    except Exception as exc:  # noqa: BLE001 — see docstring: never fail a write
        _note_error("warm", exc)


def index_attribution() -> dict | None:
    """Attribution counts from the EXISTING index, read-only (#233): never
    rebuilds — status must not pay the rebuild cost, and a missing index is
    not an error. Returns {"items": N, "unattributed": M} (M = project_slug
    NULL rows: legacy stampless sessions, reachable only under
    --all-projects), or None when the db is absent/corrupt/foreign."""
    path = config.recall_db()
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute(
                "SELECT count(*), count(*) - count(project_slug) FROM items"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    # fetchone on an aggregate SELECT always yields exactly one row.
    return {"items": int(row[0]), "unattributed": int(row[1])}


def _match_expr(query: str, join: str = " ") -> str | None:
    """User text -> a safe FTS5 MATCH expression: every whitespace token becomes
    a quoted phrase (internal quotes doubled), joined by implicit AND (or the
    given operator, e.g. " OR " for the #25 fallback). Bare quotes/AND/OR/NEAR/
    parens/'*' thus match as words instead of erroring as syntax. None when
    nothing searchable remains."""
    parts = []
    for token in query.split():
        parts.append('"' + token.replace('"', '""') + '"')
    return join.join(parts) or None


def _dedupe_rows(rows: list[dict], want_n: int) -> list[dict]:
    """#288: one result per distinct item. Key (kind, author, text) — the
    same words from two authors is attribution, not duplication. Among
    duplicates the NEWEST row (max created) supplies the result: its
    supersession/frontier stamps are the current state. Position is the
    group's best-ranked appearance, so relevance order survives. The
    replace branch matters when the newest copy sorts BELOW an older one
    (e.g. the newest is superseded and the superseded-last ordering demotes
    it) — content still comes from the newest, position from the best."""
    by_key: dict[tuple, int] = {}
    out: list[dict] = []
    for row in rows:
        key = (row.get("kind"), row.get("author"),
               " ".join(str(row.get("text") or "").split()))
        slot = by_key.get(key)
        if slot is not None:
            if (row.get("created") or 0) > (out[slot].get("created") or 0):
                out[slot] = row
            continue
        by_key[key] = len(out)
        out.append(row)
    return out[:want_n]


def search(query: str, project_dir=None, all_projects: bool = False,
           limit: int = 20, slug: str | None = None) -> list[dict]:
    """FTS5 MATCH over the (auto-refreshed) index. Live items first, then by
    bm25 rank, newest checkpoint first within equal rank. Scope: project_dir's
    slug unless all_projects (or the project is unknown — no filter then,
    matching read_team's semantics). An explicit `slug` IS the scope (#243):
    it addresses a bucket by its stored identity — the slug is lossy, so this
    is the only route to buckets whose source path is gone (other machine,
    deleted dir) — and overrides both project_dir and all_projects (callers
    guard the flag conflict at the CLI). Never raises on hostile query text;
    raises RecallError only when FTS5 itself is unavailable.

    AND is primary; when a multi-term query matches nothing, the same quoted
    tokens retry joined by OR (#25) — bm25 ranks items covering more terms
    first, so a richer cue degrades to partial matches instead of zeroing out
    (encoding specificity: more cue must never mean less recall)."""
    expr = _match_expr(query)
    if expr is None:
        return []
    try:
        _ensure_fresh()
    except (OSError, sqlite3.Error) as exc:
        _note_error("search.refresh", exc)  # then try the query on what exists
    want = slug if slug else (None if all_projects
                              else store.project_slug(project_dir))

    sql = (
        "SELECT i.text, i.quote, i.trust, i.kind, i.author, i.project_slug,"
        " i.session_id, i.created, i.superseded_by, i.invalidated_by,"
        " i.importance, i.first_seen, i.item_id, i.frontier,"
        " bm25(items_fts) AS rank"
        " FROM items_fts JOIN items i ON i.id = items_fts.rowid"
        " WHERE items_fts MATCH ?"
    )
    if want is not None:
        sql += " AND i.project_slug = ?"
    # frontier is a TIEBREAK after relevance (#234): equally-relevant rows
    # from the newest checkpoint edge out older ones — silently, no label.
    # Contradiction leads the demotion keys (#837): "at least as strongly as
    # superseded" is the issue's floor, and evidence that a probe contradicted
    # a claim is the stronger fact of the two, so a contradicted row sorts
    # below a merely-replaced one. Both stay ABOVE nothing — demoted, never
    # filtered out: the evidence is machine-local and has no cure path, so
    # burial must remain visible and reversible rather than silent.
    sql += (" ORDER BY (i.invalidated_by IS NOT NULL) ASC,"
            " (i.superseded_by IS NOT NULL) ASC, rank ASC,"
            " i.frontier DESC, i.created DESC LIMIT ?")

    want_n = max(1, int(limit))

    def _run(match_expr: str) -> list[dict]:
        params: list = [match_expr]
        if want is not None:
            params.append(want)
        # #288 overfetch: a carried item occupies one row PER checkpoint that
        # carries it, and dedupe below collapses those — fetch headroom so the
        # deduped list can still fill the caller's limit. 4x covers typical
        # carry depth; pathological fan-out may under-fill, which reads as
        # "fewer results", never as duplicates.
        params.append(want_n * 4)
        conn = sqlite3.connect(str(config.recall_db()))
        try:
            cur = conn.execute(sql, params)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def _query() -> list[dict]:
        rows = _run(expr)
        if not rows:
            or_expr = _match_expr(query, " OR ")
            if or_expr != expr:  # differs only when there are >=2 tokens
                rows = _run(or_expr)
        return _dedupe_rows(rows, want_n)

    try:
        return _query()
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).lower() and "no such module" in str(exc).lower():
            raise RecallError(_FTS5_MISSING_MSG) from exc
        # Residual FTS5 syntax edge (e.g. a token that tokenizes to an empty
        # phrase): a weird query yields no matches, never a traceback.
        return []
    except sqlite3.DatabaseError:
        # Corrupted between _ensure_fresh and the query (or mid-read): the
        # index is derived — rebuild once and retry; give up empty, not loud.
        # OSError here too: disk-full mid-rebuild must not escape search().
        try:
            rebuild()
            return _query()
        except (OSError, sqlite3.DatabaseError) as exc:
            _note_error("search", exc)
            return []


def lookup_item(item_id: str, project_dir=None, slug: str | None = None) -> dict | None:
    """Single-id read against the (auto-refreshed) index (#674).

    `why`'s own walk (store.project_surfaces) only ever sees this project's
    local flat/pointer surfaces — never the team dir, and only a stampless
    legacy file that happens to sit inside its project's bucket directory.
    The index is deliberately more permissive (team ingestion, #158
    _bucket_slugs pointer-derived attribution for legacy files), so a row
    can exist here for an id `why`'s walk structurally cannot reach.

    This is a companion to search(), never a substitute for its scoping:
    same project_slug equality (never team_project/granted-segments), same
    fail-open posture (any index trouble degrades to None, not a raise —
    the caller's own "not found" refusal is the safe default already).
    Read-only. Callers must never treat a hit here as license to widen what
    forget/project_surfaces/the privacy audit consider this project's own
    surfaces — this only ever feeds a DISPLAY fallback.

    Returns the newest matching row (ties broken by `created`), or None."""
    want = slug if slug else store.project_slug(project_dir)
    if want is None:
        return None
    try:
        _ensure_fresh()
    except (OSError, sqlite3.Error, RecallError) as exc:
        _note_error("lookup_item.refresh", exc)
    try:
        conn = sqlite3.connect(str(config.recall_db()))
        try:
            cur = conn.execute(
                "SELECT text, quote, trust, kind, author, project_slug,"
                " session_id, created, superseded_by, item_id, frontier"
                " FROM items WHERE item_id = ? AND project_slug = ?"
                " ORDER BY created DESC LIMIT 1",
                (item_id, want),
            )
            cols = [c[0] for c in cur.description]
            row = cur.fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        _note_error("lookup_item", exc)
        return None
    return dict(zip(cols, row)) if row is not None else None


# ---- #125: proactive suggestion — "you worked on this before" ----

# Words that carry no retrieval signal in a work prompt: English function words
# plus the request-noise vocabulary of talking to an agent. Salience = what's
# LEFT after these; a prompt reduced to nothing stays silent.
_STOPWORDS = frozenset("""
a about after again all also and any are because been before being but can
cant come could did didnt does doesnt doing dont down each few for from had
has have having her here him his how into its itself just let lets like make
more most much must new not now off once only other our out over own same
she should side some still such than that the their them then there these
they this those through too under until very was way well were what when
where which while who why will with would you your yours
please help want need fix add use using used code file files run running
work working thing things stuff issue problem question trying still
algo antes aqui asi aun bien cada casi como con cual cuando del desde donde
ella ellos entre era ese esa eso esta estas este esto estos hace hacer hacia
hasta hay las les los mas menos mientras misma mismo mucho muy nada nos
nosotros otra otro para pero poco por porque pues que quien ser sin sobre
son soy sus tal tambien tanto tener tiene toda todo todos una uno unos
usted vamos
favor ayuda ayudame necesito quiero puedes puedo podes dale arregla arreglar
agrega agregar usa usar usando corre correr corriendo funciona funcionar
codigo archivo archivos cosa cosas problema problemas pregunta preguntas
tratando todavia entonces ahora gracias quizas intenta intentar
""".split())
# Spanish entries are stored diacritic-folded (tambien, not también) because
# salient_terms folds tokens before the stopword check — one entry covers both
# spellings. Both language bands mirror each other: function words plus the
# imperative/filler band (favor/ayuda/necesito = please/help/need); scar #18
# rule — do not drop beyond the frequency band the English list established.

_TERM_CAP = 24          # bounded query cost; 12 dropped real cue terms on long
                        # prompts (#31 item 5, encoding-specificity inversion)
_MIN_TERMS = 2          # a one-word prompt is never a retrieval request
_MIN_OVERLAP = 2        # matched SESSION must share >=2 distinct salient terms
                        # across its items: one shared word is coincidence, not
                        # prior work (noise budget). Session-level, not per-item
                        # — a multi-topic prompt splits its terms across items
                        # (first field miss, 2026-07-02)

# recall index `kind` -> scoring TYPE_RULES key (#78 composition), from the
# shared schema (#146). `contradiction` has no dedicated rules and is absent —
# the .get() below keeps its default fallback.
_KIND_TO_TYPE = schema.KIND_TO_TYPE


def _fold(tok: str) -> str:
    """Strip combining marks so terms align with what FTS5 stored: the index
    uses unicode61 with its remove_diacritics default, so it holds "sesion"
    for "sesión" — folded prompt terms match, raw accents never would."""
    return "".join(
        c for c in unicodedata.normalize("NFD", tok) if not unicodedata.combining(c))


_TOKEN_RE = re.compile(r"\w[\w\-]*")
# Identifier separators + camelCase: `auth_token`, `session-start`, `parseJSON`.
_SUBTOKEN_SPLIT_RE = re.compile(r"[_\-./:]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
# Cheap pre-check: does this token have any boundary worth splitting on?
_SPLITTABLE_RE = re.compile(r"[_\-./:]|[a-z0-9][A-Z]")
# Longest first so `-ies` is tried before `-s`.
_INFLECTIONS = ("ings", "edly", "ing", "ies", "ers", "est", "ed", "es", "er",
                "ly", "s", "d")
_STEM_MIN = 3   # never stem down to a stub shorter than a salient term


def _match_units(text: str) -> set:
    """Every whole word-unit a salient term may legitimately match (#490).

    Retrieval is FTS5 `MATCH` under unicode61 — strict token equality — while
    the gates that judge it (`_MIN_OVERLAP` here, cli's `_STALE_MIN_HITS` via
    `term_hits`) used substring containment. Substring hits are a strict
    superset of token hits, so every threshold stated in "distinct salient
    terms" was evaluated on an inflated statistic, one-sided and always
    permissive: `port` was credited against `transport`, `one` against
    `honest`, `cli` against `client`.

    Raw token equality is the wrong correction. `salient_terms` tokenizes on
    `\\w[\\w-]*`, so compound identifiers are SINGLE tokens and substring
    matching was the only reason a query for `token` reached `auth_token` — in
    a code corpus that is the vocabulary, not noise. Measured on the real
    corpus, only ~14% of substring-only credits were genuine mid-word false
    positives; the rest were compounds (~72%) and inflections (~15%).

    So: split each token on identifier separators and camelCase, add cheap
    inflection stems, and credit a term only when it equals a whole unit. A
    term is never credited for matching the middle of a word.
    """
    units = set()
    for m in _TOKEN_RE.finditer(text):
        raw = m.group(0)
        # Fast path: this runs over every candidate row on the per-prompt
        # critical path, and _fold's NFD normalize dominates. Almost every
        # token is plain ASCII with no identifier boundary, so check for both
        # before paying for either.
        low = raw.lower() if raw.isascii() else _fold(raw).lower()
        units.add(low)
        if not _SPLITTABLE_RE.search(raw):
            continue
        for part in _SUBTOKEN_SPLIT_RE.split(_CAMEL_RE.sub("-", raw)):
            if part:
                units.add(part.lower() if part.isascii()
                          else _fold(part).lower())
    return units


def _term_variants(term: str) -> set:
    """Inflected forms of ONE salient term.

    Morphology is folded on the QUERY side, not the haystack side, and that is
    a performance decision with teeth: `suggest` compares <=24 terms against up
    to 256 candidate rows, so expanding the terms once per prompt costs ~24
    small sets while stemming every haystack token costs thousands. Measured on
    real rows, haystack-side stemming ran ~7x slower than the substring
    matching it replaced; this direction is ~1.4x.

    Over-generous in one direction only: a form that is not a real word can be
    generated (`statuss`), which at worst credits a term a stemmer would also
    credit. It never removes a form.
    """
    forms = {term}
    for suf in _INFLECTIONS:
        forms.add(term + suf)
        if term.endswith(suf) and len(term) - len(suf) >= _STEM_MIN:
            base = term[:-len(suf)]
            forms.add(base)
            if suf == "ies":
                forms.add(base + "y")
            elif suf in ("es", "ed", "er", "est", "ing"):
                forms.add(base + "e")
    if term.endswith("y") and len(term) > _STEM_MIN:
        forms.add(term[:-1] + "ies")
    if not term.endswith("e"):
        forms.add(term + "es")
    else:
        forms.add(term + "s")
    return forms


def credited_terms(terms, text: str) -> set:
    """Which of `terms` the text legitimately answers, on word boundaries."""
    units = _match_units(text)
    return {t for t in terms if _term_variants(t) & units}


def salient_terms(prompt: str) -> list[str]:
    """Prompt -> deduped lowercase retrieval terms, prompt order preserved.
    Tokens are word runs (unicode: "sesión" stays one token, never "sesi"+"n";
    code identifiers survive: auth_token stays whole), diacritic-folded to
    match the FTS5 index; <3 chars and stopwords drop. Fewer than _MIN_TERMS
    remaining -> [] (callers stay silent)."""
    out: list[str] = []
    seen = set()
    for m in re.finditer(r"\w[\w\-]*", prompt):
        tok = _fold(m.group(0)).lower()
        if len(tok) < 3 or tok in _STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= _TERM_CAP:
            break
    return out if len(out) >= _MIN_TERMS else []


# #450: literal openings of the host-emitted blocks that reach the prompt hook
# as if they were user input — background-task notifications, teammate/agent
# messages, slash-command output. Measured on the maintainer's transcripts,
# 37.9% of injections landed on these, at the same rate as on real prompts:
# nothing consumes those suggestions, so they are pure token cost. Literal and
# case-sensitive on purpose — the hosts emit exactly one casing, and loosening
# the match only buys false skips.
_MACHINE_MARKERS = (
    "[SYSTEM NOTIFICATION",
    "<task-notification>",
    "<teammate-message",
    "<agent-message",
    "<local-command-stdout>",
)

_MACHINE_SCAN_CHARS = 400   # opening region only — see is_machine_prompt


def is_machine_prompt(prompt: str) -> bool:
    """True when the prompt is structurally a host-emitted block rather than a
    person asking for work (#450). Deliberately conservative: a missed skip is
    the status quo, a wrong skip costs one suggestion.

    Boundary — a marker counts only when it OPENS A LINE inside the first
    _MACHINE_SCAN_CHARS characters (after leading whitespace):

      - Line-start, because a machine block's marker is the block's opening;
        a human quoting one does it mid-sentence ("why does the hook fire on
        <task-notification> blocks?"). A plain substring scan would silence
        recall on exactly the prompts that discuss recall. Indented markers
        (a pasted code sample) are left ambiguous and still get suggestions.
      - Windowed, because a genuine prompt may paste a whole block far below
        its own question; only the opening region can carry the block that IS
        the prompt. Observed shape: the notification wrapper opens with
        `[SYSTEM NOTIFICATION` at offset 0 and carries `<task-notification>`
        ~490 chars in, past this window — the marker list is redundant for
        that reason, so the window never has to be widened to catch it.

    Truncation at the window can only split a marker, i.e. only ever miss a
    skip, which is the safe direction.
    """
    head = prompt.lstrip()[:_MACHINE_SCAN_CHARS]
    return any(line.startswith(_MACHINE_MARKERS) for line in head.split("\n"))


# Interval-slot demotions for the auto-inject path. Multiplicative and
# INDEPENDENT, because the two facts are (#836): "replaced by later work" and
# "a probe contradicted this" can hold together, separately, or not at all, so
# an item carrying both is demoted by both. Contradiction is the heavier of
# the two — a replaced decision was still the right call at the time, whereas
# contradiction evidence says a check disagreed with the claim itself.
#
# Neither is a filter. #112's rule holds for both: an overturned item is still
# evidence, and this evidence is machine-local with no cure path yet, so it
# ranks down and renders flagged rather than disappearing.
_SUPERSEDED_WEIGHT = 0.7
_INVALIDATED_WEIGHT = 0.4


def _suggest_weight(row, item_type: str, now: float) -> float:
    """One row's suggest() rank weight: #78 effective_weight, then the
    interval-slot demotions. Extracted so the penalties are testable at the
    weight level rather than only through a rigged end-to-end fixture."""
    weight = scoring.effective_weight(
        {"importance": row.get("importance"),
         "first_seen": row.get("first_seen"),
         # trust is part of the record's authority (#408): drop it here and
         # the trust ceiling never applies to recall ranking, letting an
         # inferred item ride relevance x recency to a verbatim item's band.
         "trust": row.get("trust")},
        item_type, now)
    if row.get("superseded_by"):
        weight *= _SUPERSEDED_WEIGHT
    if row.get("invalidated_by"):
        weight *= _INVALIDATED_WEIGHT
    return weight


def suggest(prompt: str, project_dir=None, current_session=None,
            exclude_sessions=(), limit: int = 2, now=None) -> list[dict]:
    """Proactive matches for a user prompt, or [] — silence is the default and
    every gate errs toward it (#125 noise budget):

      - unknown project -> [] (a suggestion from the wrong project is noise)
      - fewer than 2 salient terms -> []
      - never the current session, never `exclude_sessions` (what the
        SessionStart briefing already covered)
      - a matched session must share >=2 DISTINCT salient terms with the
        prompt, counted across all of its items — a multi-topic prompt splits
        its terms across items, so a per-item count silences exactly the
        sessions it exists to surface (first field miss, 2026-07-02); one
        shared word, however many items repeat it, is still coincidence
      - at most `limit` results, one per session, ranked by
        FTS5 relevance x #78 effective_weight

    Superseded items are INCLUDED, ranked down and flagged — since v3 the
    flag means item-level evidence (a typed supersedes link or a logged
    resolution, #234), so it is rare and load-bearing; it still never hides
    a result (an overturned decision is still evidence, #112).

    Items carrying contradiction evidence (#837) are included on the same
    terms and demoted harder, the two penalties stacking because the axes
    are independent. This is the path that mattered most: everything here
    lands in the user's next prompt unasked, so an unconsumed invalidated_by
    meant re-asserting a claim this install's own ledger contradicted, at
    full weight, on the one surface nobody chose to read.

    Each returned row also carries `term_hits` (its own distinct-term match
    count) and `pinned` (the #369 standing-rule flag) — inputs to the cli
    age gate (#452), not rank inputs here.
    """
    slug = store.project_slug(project_dir)
    if slug is None:
        return []
    terms = salient_terms(prompt)
    if not terms:
        return []
    if now is None:
        now = time.time()
    excluded = set(exclude_sessions)
    if current_session:
        excluded.add(str(current_session))

    # Term variants are computed ONCE per prompt (<=24 terms) and reused across
    # every candidate row — the whole point of folding morphology on the query
    # side rather than the haystack side (#490).
    variants = {t: _term_variants(t) for t in terms}
    expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
    try:
        _ensure_fresh()
    except (OSError, sqlite3.Error) as exc:
        _note_error("suggest.refresh", exc)
    sql = (
        "SELECT i.text, i.quote, i.trust, i.kind, i.author, i.project_slug,"
        " i.session_id, i.created, i.importance, i.first_seen, i.superseded_by,"
        # #837: the column MUST be selected here, not just written — this is
        # the auto-inject path, so a missing select re-asserts a claim this
        # install's own ledger contradicted, on the highest-leverage surface.
        " i.invalidated_by,"
        # pinned rides out for the #452 age gate (standing rules are
        # age-independent); it is NOT a rank input here.
        " i.pinned,"
        " bm25(items_fts) AS rank"
        " FROM items_fts JOIN items i ON i.id = items_fts.rowid"
        # Best-ranked candidates first (#31 item 4): without ORDER BY the LIMIT
        # window is arbitrary — on a busy project (>N matching rows) the
        # strongest rows could be truncated away, silencing prior work.
        " WHERE items_fts MATCH ? AND i.project_slug = ?"
        " ORDER BY rank ASC LIMIT 256"
    )
    try:
        conn = sqlite3.connect(str(config.recall_db()))
        try:
            cur = conn.execute(sql, (expr, slug))
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        _note_error("suggest", exc)
        return []  # suggestion is opportunistic — any db trouble means silence

    # Pass 1: per-session distinct-term coverage. The overlap gate below is
    # session-level — which terms a session's items match TOGETHER — so the
    # coverage sets must be complete before any row can be judged.
    coverage: dict[str, set] = {}
    matched: list[tuple[dict, set]] = []
    for r in rows:
        if r["session_id"] in excluded:
            continue
        # #490: match on WORD BOUNDARIES, not substrings — the candidate set
        # came from a token-based MATCH, so a gate counting substrings reads a
        # statistic the retrieval never granted. _match_units folds the
        # haystack the same way salient_terms folds the query (#27), so
        # accented content FTS5 already matched still counts.
        hit = {t for t, forms in variants.items()
               if forms & _match_units(f"{r['text']} {r['quote'] or ''}")}
        if not hit:
            continue
        coverage.setdefault(r["session_id"], set()).update(hit)
        matched.append((r, hit))

    scored = []
    for r, hit in matched:
        if len(coverage[r["session_id"]]) < _MIN_OVERLAP:
            continue
        # #452: the per-ITEM distinct-term count rides out with the row (the
        # session-level coverage above is the gate; this is the row's own
        # match strength). Purely additive — cli's age gate reads it to demand
        # a stronger match from stale items; nothing here ranks on it beyond
        # the existing len(hit) tiebreak below.
        r["term_hits"] = len(hit)
        relevance = max(0.0, -float(r["rank"]))  # FTS5 bm25(): smaller = better
        weight = _suggest_weight(
            r, _KIND_TO_TYPE.get(r["kind"], "recent_decision"), now)
        scored.append((relevance * weight, len(hit), r))

    scored.sort(key=lambda s: (-s[0], -s[1]))
    out, used_sessions = [], set()
    for _score, _overlap, r in scored:
        if r["session_id"] in used_sessions:
            continue
        used_sessions.add(r["session_id"])
        out.append(r)
        if len(out) >= limit:
            break
    return out
