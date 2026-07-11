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
`superseded_by` (populated — see below) and `invalidated_by` (schema slot ONLY,
no logic yet; future contradiction intervals). A contentless FTS5 table indexes
text + quote for MATCH; rows join back to `items` by rowid.

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

from . import config, schema, scoring, store

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
_SCHEMA_VERSION = "3"

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
    """Yield (kind, text, trust, quote, importance, first_seen, item_id,
    supersede_targets) for every cognitive item in a checkpoint. Tolerant of
    shape drift: bare strings become text-only items; anything without usable
    text is skipped (an index row with no text matches nothing). importance/
    first_seen/item_id are None on pre-D-011 items. supersede_targets is the
    item's `supersedes` link target strings (#234) — usually empty."""
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
                   str(item.get("quote") or ""), imp, fs, item_id, targets)


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
    own searchable history."""
    seen: set[tuple[str, str]] = set()
    root = config.team_dir()
    cutoff = store.team_retention_cutoff()
    try:
        remotes = list(root.iterdir())
    except OSError:
        remotes = []
    for remote in remotes:
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
                paths.extend(p for p in e.iterdir()
                             if p.is_file() and (p.suffix == ".json"
                                                 or p.name == "events.jsonl"))
    except OSError:
        pass
    try:
        paths.extend(config.team_dir().rglob("*.json"))
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
                frontier INTEGER NOT NULL DEFAULT 0
            );
            CREATE VIRTUAL TABLE items_fts USING fts5(text, quote, content='');
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
    Conservative: an item_ref is marked only when its newest resolving event
    ("resolved" or "superseded-by:<id>") is STRICTLY newer than any reopen —
    ties stay unmarked (never-guess). "supersede-candidate:*" never marks:
    candidates are the unconfirmed tier by design (#111). The stored value is
    the superseding item id when the status names one, else "resolved"."""
    try:
        buckets = [d for d in config.checkpoint_dir().iterdir() if d.is_dir()]
    except OSError:
        return
    for bucket in buckets:
        path = bucket / "events.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        marks: dict[str, tuple[str, str]] = {}  # ref -> (resolve_ts, value)
        reopens: dict[str, str] = {}            # ref -> newest reopen ts
        for line in lines:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(e, dict) or e.get("kind") != "resolution":
                continue
            ref = str(e.get("item_ref") or "")
            ts = str(e.get("ts") or "")
            status = str(e.get("status") or "")
            if not ref or not ts:
                continue
            if status == "reopened":
                if ts > reopens.get(ref, ""):
                    reopens[ref] = ts
            elif status == "resolved" or status.startswith("superseded-by:"):
                value = status.split(":", 1)[1] if ":" in status else "resolved"
                if ts > marks.get(ref, ("", ""))[0]:
                    marks[ref] = (ts, value)
        for ref, (ts, value) in marks.items():
            if ts <= reopens.get(ref, ""):
                continue  # reopened at/after the resolve — stays live
            conn.execute(
                "UPDATE items SET superseded_by = ?"
                " WHERE item_id = ? AND project_slug IS ?",
                (value, ref, bucket.name),
            )


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
            for (kind, text, trust, quote, importance, first_seen,
                 item_id, targets) in _items(cp):
                cur = conn.execute(
                    "INSERT INTO items"
                    " (text, quote, trust, kind, author, project_slug,"
                    "  session_id, created, importance, first_seen, item_id)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (text, quote, trust, kind, author, slug, sid, recency,
                     importance, first_seen, item_id),
                )
                conn.execute(
                    "INSERT INTO items_fts(rowid, text, quote) VALUES (?, ?, ?)",
                    (cur.lastrowid, text, quote),
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
        " i.importance, i.first_seen, i.frontier,"
        " bm25(items_fts) AS rank"
        " FROM items_fts JOIN items i ON i.id = items_fts.rowid"
        " WHERE items_fts MATCH ?"
    )
    if want is not None:
        sql += " AND i.project_slug = ?"
    # frontier is a TIEBREAK after relevance (#234): equally-relevant rows
    # from the newest checkpoint edge out older ones — silently, no label.
    sql += (" ORDER BY (i.superseded_by IS NOT NULL) ASC, rank ASC,"
            " i.frontier DESC, i.created DESC LIMIT ?")

    def _run(match_expr: str) -> list[dict]:
        params: list = [match_expr]
        if want is not None:
            params.append(want)
        params.append(max(1, int(limit)))
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
        return rows

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

    expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
    try:
        _ensure_fresh()
    except (OSError, sqlite3.Error) as exc:
        _note_error("suggest.refresh", exc)
    sql = (
        "SELECT i.text, i.quote, i.trust, i.kind, i.author, i.project_slug,"
        " i.session_id, i.created, i.importance, i.first_seen, i.superseded_by,"
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
        # Fold the haystack like the terms (#27): salient_terms folds
        # "sesión"->"sesion", so an unfolded haystack silences accented
        # content that FTS5 (remove_diacritics) already matched.
        haystack = _fold(f"{r['text']} {r['quote'] or ''}".lower())
        hit = {t for t in terms if t in haystack}
        if not hit:
            continue
        coverage.setdefault(r["session_id"], set()).update(hit)
        matched.append((r, hit))

    scored = []
    for r, hit in matched:
        if len(coverage[r["session_id"]]) < _MIN_OVERLAP:
            continue
        relevance = max(0.0, -float(r["rank"]))  # FTS5 bm25(): smaller = better
        weight = scoring.effective_weight(
            {"importance": r["importance"], "first_seen": r["first_seen"]},
            _KIND_TO_TYPE.get(r["kind"], "recent_decision"), now)
        if r["superseded_by"]:
            weight *= 0.7  # flagged and ranked down, never hidden (#112)
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
