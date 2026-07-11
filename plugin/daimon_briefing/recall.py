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

Supersession v1 is whole-checkpoint recency per (author, project): items from
any checkpoint that is not its author's newest for that project get
`superseded_by = <newest session_id>`. Ranked down in results and flagged —
never hidden (an old decision is still evidence).

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

# v2 (#125): items grew importance + first_seen for suggest()'s ranking; the
# version bump makes _ensure_fresh discard any v1 db and rebuild.
_SCHEMA_VERSION = "2"

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
    """Yield (kind, text, trust, quote, importance, first_seen) for every
    cognitive item in a checkpoint. Tolerant of shape drift: bare strings become
    text-only items; anything without usable text is skipped (an index row with
    no text matches nothing). importance/first_seen are None on pre-D-011 items."""
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
            yield (kind, text, str(item.get("trust") or ""),
                   str(item.get("quote") or ""), imp, fs)


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
                paths.extend(p for p in e.iterdir()
                             if p.is_file() and p.suffix == ".json")
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
                first_seen TEXT
            );
            CREATE VIRTUAL TABLE items_fts USING fts5(text, quote, content='');
            """
        )
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).lower():
            raise RecallError(_FTS5_MISSING_MSG) from exc
        raise


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
        # (author, project_slug) -> (recency, session_id) of the newest checkpoint.
        # session_id is the same-second tie-break (#31 item 7): scan order is
        # readdir order, which is unspecified — without a stable secondary key
        # the superseded flags flip across rebuilds. Tuple compare gives it.
        newest: dict[tuple, tuple[float, str]] = {}
        for sid, author, slug, recency, cp in _scan_sources():
            # Unattributed sessions never supersede each other (#31 item 6):
            # NULL slugs are UNRELATED projects sharing a non-identity, not
            # one project's history — they stay out of the newest map entirely.
            if slug is not None:
                key = (author, slug)
                if key not in newest or (recency, sid) > newest[key]:
                    newest[key] = (recency, sid)
            for kind, text, trust, quote, importance, first_seen in _items(cp):
                cur = conn.execute(
                    "INSERT INTO items"
                    " (text, quote, trust, kind, author, project_slug,"
                    "  session_id, created, importance, first_seen)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (text, quote, trust, kind, author, slug, sid, recency,
                     importance, first_seen),
                )
                conn.execute(
                    "INSERT INTO items_fts(rowid, text, quote) VALUES (?, ?, ?)",
                    (cur.lastrowid, text, quote),
                )
                count += 1
        # Supersession v1: whole-checkpoint recency per (author, project).
        for (author, slug), (_recency, sid) in newest.items():
            conn.execute(
                "UPDATE items SET superseded_by = ?"
                " WHERE author = ? AND project_slug IS ? AND session_id != ?",
                (sid, author, slug, sid),
            )
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
    if row is None:
        return None
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
           limit: int = 20) -> list[dict]:
    """FTS5 MATCH over the (auto-refreshed) index. Live items first, then by
    bm25 rank, newest checkpoint first within equal rank. Scope: project_dir's
    slug unless all_projects (or the project is unknown — no filter then,
    matching read_team's semantics). Never raises on hostile query text;
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
    want = None if all_projects else store.project_slug(project_dir)

    sql = (
        "SELECT i.text, i.quote, i.trust, i.kind, i.author, i.project_slug,"
        " i.session_id, i.created, i.superseded_by, i.invalidated_by,"
        " i.importance, i.first_seen,"
        " bm25(items_fts) AS rank"
        " FROM items_fts JOIN items i ON i.id = items_fts.rowid"
        " WHERE items_fts MATCH ?"
    )
    if want is not None:
        sql += " AND i.project_slug = ?"
    sql += (" ORDER BY (i.superseded_by IS NOT NULL) ASC, rank ASC,"
            " i.created DESC LIMIT ?")

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

    Superseded items are INCLUDED, ranked down and flagged — supersession v1
    is whole-checkpoint per (author, project), so "prior work" is superseded
    almost by definition; excluding it would leave nothing but the latest
    checkpoint, which the briefing already covered. Flag, never hide (#112).
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
