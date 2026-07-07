"""Checkpoint store: pure file ops over DAIMON_CHECKPOINT_DIR.

Layout:
    <dir>/<session_id>.json          one checkpoint per session (flat, all projects)
    <dir>/latest.json                global pointer: most recent SESSION (by the
                                     `created` stamp), ANY project — a write whose
                                     session is older than the pointer's is a
                                     blocked regression (#123), not a new latest
    <dir>/<project-slug>/latest.json per-project pointer: most recent session for
                                     that project (slug = cwd munged Claude Code
                                     style, e.g. /Users/x/proj -> -Users-x-proj)

Reads prefer the per-project pointer and fall back to the global one, so a
session in project B can never hijack project A's briefing — but existing
single-project installs keep working off the global pointer unchanged.

Writes are atomic (temp file + os.replace) so a crash mid-write never leaves a
torn checkpoint or an inconsistent latest pointer.

Per-session files would otherwise accumulate one-per-session forever (#92), so a
successful write opportunistically GCs the flat dir down to the newest
DAIMON_CHECKPOINT_KEEP checkpoints (default 100, 0 = keep forever), never pruning
one a live pointer still references. The default is generous on purpose so #33's
merged checkpoint history keeps a deep well of files to reconstruct from.
"""

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import config, serializer

_LATEST = "latest.json"
# Rotation pointers, not per-session checkpoints. Anything else ending in .json in
# the flat store dir is a <session_id>.json checkpoint eligible for GC (#92).
_POINTER_RE = re.compile(r"^(?:latest|prev-\d+)\.json$")


def project_slug(project_dir) -> str | None:
    """Filesystem-safe slug for a project working directory, or None if unknown.

    Same munging scheme Claude Code uses for its project dirs: every char that
    is not a word char or '-' becomes '-' (slashes, dots, spaces). Unicode word
    chars survive. The result can never contain a path separator, so it cannot
    escape the checkpoint dir.
    """
    if not project_dir:
        return None
    s = str(project_dir).strip()
    if not s:
        return None
    return re.sub(r"[^\w-]", "-", s) or None


def _safe_name(session_id: str) -> str:
    # session_id is host-provided; keep file ops from escaping the dir.
    return session_id.replace("/", "_").replace("\\", "_").replace("..", "_")


def _contained_path(d: Path, session_id: str) -> Path:
    """Path for a session's checkpoint, verified to resolve INSIDE the store dir.

    Name sanitization above is belt-and-braces; this resolved-path check is the
    actual guarantee. Raises ValueError on escape.
    """
    path = d / f"{_safe_name(session_id)}.json"
    if not path.resolve().is_relative_to(d.resolve()):
        raise ValueError(f"session_id escapes checkpoint dir: {session_id!r}")
    return path


def _atomic_write(path: Path, blob: str) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(blob, encoding="utf-8")
    os.replace(tmp, path)  # atomic on POSIX


_LOCK_NAME = ".pointer.lock"   # dotfile: invisible to _session_files (.json
                               # filter) and _pointer_stems (_POINTER_RE)
_LOCK_TRIES = 50               # x 20ms = ~1s bounded wait, then fail open
_LOCK_INTERVAL = 0.02

try:
    import fcntl as _fcntl
except ImportError:            # non-POSIX: lock degrades to a no-op
    _fcntl = None


@contextmanager
def _pointer_lock(d: Path):
    """Serialize the check-rotate-write pointer sequence in dir `d` (#31):
    two sessions ending together interleave _pointer_regresses / rotation /
    the latest write (multi-step TOCTOU) — one can clobber the prev-N chain
    or let an older checkpoint win `latest`. flock on a sidecar dotfile with
    a bounded wait; yields whether the lock was actually acquired. Fail-open
    everywhere (no fcntl, unwritable dir, contention past the wait): the
    caller proceeds unguarded, which is exactly the pre-lock behavior."""
    if _fcntl is None:
        yield False
        return
    fh = None
    held = False
    try:
        fh = open(d / _LOCK_NAME, "a+")
        for _ in range(_LOCK_TRIES):
            try:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                held = True
                break
            except OSError:
                time.sleep(_LOCK_INTERVAL)
    except OSError:
        pass
    try:
        yield held
    finally:
        if fh is not None:
            try:
                if held:
                    _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
                fh.close()
            except OSError:
                pass


def _rotate_pointers(d: Path, history: int) -> None:
    """Retain the last `history` checkpoint pointers in dir `d`: latest.json plus
    prev-1.json .. prev-(history-1).json. Called BEFORE the new latest is written,
    so the current latest becomes prev-1 and the oldest falls off the end.

    latest.json is COPIED (not moved) to prev-1 so a concurrent reader never sees
    it momentarily absent; the prev-* chain is shifted with atomic renames.
    No-op when history <= 1 (no retention) or when there is no latest yet."""
    if history <= 1:
        return
    # Shift the prev chain down from the oldest end so nothing is clobbered:
    # prev-(k-1) -> prev-k, ... ; the former prev-(history-1) is overwritten.
    for i in range(history - 1, 1, -1):
        src = d / f"prev-{i - 1}.json"
        if src.exists():
            os.replace(src, d / f"prev-{i}.json")
    latest = d / _LATEST
    if latest.exists():
        _atomic_write(d / "prev-1.json", latest.read_text(encoding="utf-8"))


def _created_epoch(created) -> float | None:
    """Epoch seconds for a checkpoint's ISO-8601 `created` stamp, or None when it
    is absent or malformed (legacy checkpoints, torn files). cli reuses this copy.

    Twin of hook/_daimon_hook_lib.py's copy, which is stdlib-only by design
    and cannot import this package. Keep both in sync."""
    if not isinstance(created, str):
        return None
    try:
        ts = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return ts.replace(tzinfo=timezone.utc).timestamp()


def _file_recency(path: Path) -> float:
    """Recency key for ordering per-session files: the #93 `created` stamp when
    present, file mtime as the fallback (legacy/torn checkpoints). Parallels
    cli._checkpoint_info's created-over-mtime age logic. 0.0 on a vanished file so
    it sorts oldest and gets pruned first."""
    try:
        created = json.loads(path.read_text(encoding="utf-8")).get("created")
    except (OSError, json.JSONDecodeError, AttributeError):
        created = None
    epoch = _created_epoch(created)
    if epoch is not None:
        return epoch
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _stamp_first_seen(checkpoint: dict, prev: dict | None) -> None:
    """Per-item birth stamp (#126), in place. Exact-text carry-over: an item whose
    text matches one in `prev` (the project's previous latest) inherits its
    first_seen — falling back to prev's `created` for legacy items — while new or
    reworded items are stamped with THIS checkpoint's `created`. Idempotent: an
    item already carrying first_seen is never re-stamped, so rotation/re-writes
    and heal keep original birth stamps. Deliberately exact-match only — fuzzy
    item identity is the ACB scope-creep graveyard."""
    born = {}
    if isinstance(prev, dict):
        prev_created = prev.get("created")
        for item in serializer.iter_items(prev):
            text = item.get("text")
            if isinstance(text, str) and text:
                born[text] = item.get("first_seen") or prev_created
    created = checkpoint.get("created")
    for item in serializer.iter_items(checkpoint):
        if item.get("first_seen"):
            continue
        stamp = born.get(item.get("text")) or created
        if stamp:
            item["first_seen"] = stamp


def _pointer_regresses(d: Path, new_epoch: float | None) -> bool:
    """True when overwriting `d`'s latest pointer with a checkpoint created at
    `new_epoch` would move "latest" BACKWARD in session time (#123) — the heal /
    re-serialize-an-old-transcript case. False on any doubt (no pointer yet,
    torn/legacy pointer without a `created`, unstamped incoming checkpoint):
    last-write-wins stays the default; only a provable regression is blocked."""
    if new_epoch is None:
        return False
    try:
        existing = json.loads((d / _LATEST).read_text(encoding="utf-8")).get("created")
    except (OSError, json.JSONDecodeError, AttributeError):
        return False
    prior = _created_epoch(existing)
    return prior is not None and prior > new_epoch


def _session_files(d: Path) -> list[Path]:
    """Per-session checkpoint files (<session_id>.json) in the flat store dir `d`.
    Excludes rotation pointers (latest.json / prev-N.json), per-project bucket
    subdirs, and in-flight *.tmp writes — so GC only ever touches checkpoints."""
    return [
        p
        for p in d.iterdir()
        if p.is_file() and p.suffix == ".json" and not _POINTER_RE.match(p.name)
    ]


def _pointer_stems(d: Path) -> set[str] | None:
    """File stems of every per-session checkpoint a LIVE pointer still references —
    latest.json / prev-N.json in the flat dir AND in every per-project bucket. GC
    must never prune these even when they fall outside the retention window, so a
    read_checkpoint or #26 self-heal off a prev pointer still finds its file.

    Returns None when the protection set is UNKNOWABLE — any pointer that can't be
    enumerated, read, parsed, or lacks a session_id. The caller must then delete
    nothing: a silently-shrunk set would let GC prune a still-referenced file."""
    stems: set[str] = set()
    try:
        entries = list(d.iterdir())
    except OSError:
        return None
    pointer_files: list[Path] = []
    for e in entries:
        try:
            if e.is_file() and _POINTER_RE.match(e.name):
                pointer_files.append(e)
            elif e.is_dir():
                pointer_files.extend(
                    s for s in e.iterdir() if s.is_file() and _POINTER_RE.match(s.name)
                )
        except OSError:
            return None
    for p in pointer_files:
        try:
            sid = json.loads(p.read_text(encoding="utf-8")).get("session_id")
        except (OSError, json.JSONDecodeError, AttributeError):
            return None
        if not sid:
            return None
        stems.add(_safe_name(sid))
    return stems


_TMP_REAP_SECONDS = 3600   # a *.tmp older than this is a kill-9 orphan, not an
                           # in-flight _atomic_write (#31 item 3)


def _reap_stale_tmps(d: Path) -> None:
    """Unlink orphaned *.tmp files (kill-9 mid-_atomic_write) in the flat dir
    and every bucket subdir. Age-gated so a write in flight right now is never
    touched. Best-effort: never raises (#31 item 3 — GC only pruned .json, so
    these accumulated forever)."""
    cutoff = time.time() - _TMP_REAP_SECONDS
    try:
        dirs = [d] + [e for e in d.iterdir() if e.is_dir()]
    except OSError:
        return
    for sub in dirs:
        try:
            tmps = [p for p in sub.iterdir()
                    if p.is_file() and p.name.endswith(".tmp")]
        except OSError:
            continue
        for p in tmps:
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass


def _max_importance(path: Path) -> int:
    """Max item importance in a checkpoint file, 0 when unreadable/unstamped.
    Torn or legacy files pin nothing — recency retention handles them as
    before; pinning is a best-effort bonus, never a gate."""
    try:
        cp = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    best = 0
    for item in serializer.iter_items(cp):
        imp = item.get("importance")
        if isinstance(imp, int) and not isinstance(imp, bool) and imp > best:
            best = imp
    return best


def _gc_checkpoints(d: Path, keep: int) -> None:
    """Prune old per-session checkpoint files, retaining the newest `keep` plus any
    a live pointer references, plus any pinned by importance (#31 item 1: max
    item importance >= config.gc_pin_importance(); 0 disables). keep <= 0
    disables GC (keep forever). Best-effort: never raises — a GC failure must
    not fail the serialize that triggered it (mirrors _rotate_pointers /
    cli._append_serialize_log's try/except OSError).

    Known race, accepted: the pointer scan is a snapshot, so a bucket + pointer
    written by a concurrent serialize between scan and unlink is invisible here.
    Harmless in practice — that pointer references a just-written file, which the
    newest-`keep` window already retains (default 100 is generous for this too)."""
    _reap_stale_tmps(d)  # independent of `keep`: orphaned tmps are never data
    if keep <= 0:
        return
    try:
        files = _session_files(d)
        if len(files) <= keep:
            return
        protected = _pointer_stems(d)
        if protected is None:
            return  # protection set unknowable — fail-safe, prune nothing
        files.sort(key=_file_recency, reverse=True)
        stale = files[keep:]
        pin = config.gc_pin_importance()
    except OSError:
        return
    for p in stale:
        if p.stem in protected:
            continue
        if pin and _max_importance(p) >= pin:
            continue  # pinned: high-importance memory outlives the window
        try:
            p.unlink()
        except OSError:
            pass


# The five list sections that hold checkpoint items. active_topic is a single
# per-session dict and never needs an id (it does not carry, #33).
_ITEM_LISTS = (
    ("working_context", "open_questions"),
    ("working_context", "recent_decisions"),
    ("epistemic_snapshot", "strong_beliefs"),
    ("epistemic_snapshot", "uncertainties"),
    ("epistemic_snapshot", "contradictions_flagged"),
)


def _stamp_item_ids(checkpoint: dict) -> None:
    """Stable per-item ids (#102): sha1 of kind:text, 6 hex chars, prefixed
    with the kind's initial. setdefault semantics — an item that already
    carries an id (a carried twin, a re-write) is never re-stamped, so
    identity survives rotation and re-serialization. Collisions within one
    checkpoint widen the slice; identical-text twins fall through to a
    counter suffix (same text, same kind, still two loops)."""
    seen: set = set()
    for section, key in _ITEM_LISTS:
        items = (checkpoint.get(section) or {}).get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                continue
            if item.get("id"):
                seen.add(item["id"])
                continue
            digest = hashlib.sha1(
                f"{key}:{item['text']}".encode("utf-8")).hexdigest()
            cand = ""
            for width in (6, 8, 12, 40):
                cand = f"{key[0]}-{digest[:width]}"
                if cand not in seen:
                    break
            n = 2
            while cand in seen:
                cand = f"{key[0]}-{digest[:6]}-{n}"
                n += 1
            item["id"] = cand
            seen.add(cand)


def write_checkpoint(session_id: str, checkpoint: dict, project_dir=None) -> Path:
    """Write the session checkpoint + the global latest pointer, and — when the
    project is known — the per-project latest pointer too. The global pointer is
    kept for backward compatibility (pre-routing consumers and the fallback).

    Each latest pointer is rotated first (#33 Phase 1): the previous latest is
    retained as prev-1.json, keeping the last DAIMON_CHECKPOINT_HISTORY writes."""
    d = config.checkpoint_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = _contained_path(d, session_id)
    # Stamp schema version + a fallback `created` here so every write path gets
    # them. cli serialize stamps `created` from the transcript's session end
    # BEFORE calling (#123); the setdefault-now covers the remaining paths (hooks,
    # raw write-checkpoint), keeps re-writes/rotation idempotent (a checkpoint
    # carrying its own stamp is never re-stamped), and lets readers prefer
    # `created` over file mtime, which pointer rotation rewrites (#93).
    checkpoint.setdefault("format_version", serializer.PROMPT_VERSION)
    checkpoint.setdefault("created", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    # Stamp the team author (#111) the same idempotent way — resolved in config so
    # store stays free of the git/subprocess dependency (scar 0). Present on every
    # checkpoint so read_team can attribute it later, even when team-write is off.
    checkpoint.setdefault("author", config.author())
    _stamp_item_ids(checkpoint)
    # Stamp project attribution the same idempotent way. Bucket pointers rotate
    # away after `history` writes, so pointer-derived attribution EXPIRES — a
    # session older than the pointer window would lose its project forever and
    # scoped recall could never surface it again (the exact "forgotten prior
    # work" proactive recall exists for). Team copies already carry this stamp
    # (#111); this makes the local flat file carry it too. Never stamped when
    # the project is unknown — a wrong slug is worse than none.
    slug = project_slug(project_dir)
    if slug:
        checkpoint.setdefault("project_slug", slug)
    # Birth stamps (#126) need the previous latest BEFORE this write moves it.
    # read_latest never raises (returns None on absent/torn pointers).
    _stamp_first_seen(checkpoint, read_latest(project_dir))
    blob = json.dumps(checkpoint, indent=2, ensure_ascii=False)
    history = config.checkpoint_history()
    _atomic_write(path, blob)
    # Guard each latest pointer independently (#123): a heal of an old session
    # writes its per-session file above but must not steal "latest" from a newer
    # session. Rotation is skipped together with the write so prev-N history
    # doesn't churn on a blocked update.
    new_epoch = _created_epoch(checkpoint.get("created"))
    # The regress check + rotation + latest write is one critical section per
    # pointer dir (#31 item 2): unguarded, two sessions ending together can
    # interleave the steps — clobbering the prev-N chain or letting an older
    # write win latest. _pointer_lock serializes it; on lock failure the
    # sequence proceeds unguarded (pre-lock behavior, fail-open).
    with _pointer_lock(d):
        if not _pointer_regresses(d, new_epoch):
            _rotate_pointers(d, history)
            _atomic_write(d / _LATEST, blob)
    if slug:
        pdir = d / slug
        pdir.mkdir(parents=True, exist_ok=True)
        with _pointer_lock(pdir):
            if not _pointer_regresses(pdir, new_epoch):
                _rotate_pointers(pdir, history)
                _atomic_write(pdir / _LATEST, blob)
    # Opportunistic retention: serialize already succeeded, so pruning old
    # per-session files here never touches the read/briefing hot path (#92).
    _gc_checkpoints(d, config.checkpoint_keep())
    # Team mirror (#111): opt-in, best-effort, GC-untouched. Runs LAST so it can
    # never affect the local write / pointers / GC above, nor this function's rc.
    if config.team_enabled():
        _dual_write_team(session_id, checkpoint, project_dir)
    return path


def global_latest_path() -> Path:
    """Where the global latest pointer lives (may not exist yet)."""
    return config.checkpoint_dir() / _LATEST


def project_latest_path(project_dir) -> Path | None:
    """Where a project's latest pointer lives, or None if project unknown."""
    slug = project_slug(project_dir)
    if not slug:
        return None
    return config.checkpoint_dir() / slug / _LATEST


def sibling_buckets(project_dir) -> list[dict]:
    """Phantom CHILD buckets of project_dir: checkpoint-dir entries whose slug is
    this project's slug + '-<suffix>' (a subdir of the git-root that forked its own
    bucket — the #74 shape). Pure file-ops, never raises. Returns [] when the slug
    is unknown or the checkpoint dir is absent."""
    slug = project_slug(project_dir)
    if not slug:
        return []
    prefix = slug + "-"
    d = config.checkpoint_dir()
    try:
        entries = sorted(d.iterdir())
    except OSError:
        return []
    out: list[dict] = []
    for child in entries:
        if not child.name.startswith(prefix):
            continue
        latest = child / _LATEST
        try:
            mtime = latest.stat().st_mtime
        except OSError:
            continue  # no latest.json in this bucket
        try:
            sid = json.loads(latest.read_text(encoding="utf-8")).get("session_id")
        except (OSError, json.JSONDecodeError, AttributeError):
            sid = None
        out.append({"slug": child.name, "path": str(latest),
                    "session_id": sid, "mtime": mtime})
    return out


def read_checkpoint(session_id: str) -> dict | None:
    try:
        path = _contained_path(config.checkpoint_dir(), session_id)
    except ValueError:
        return None
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_latest(project_dir=None, fallback: bool = True) -> dict | None:
    """Latest checkpoint, preferring the project's own pointer when known;
    falls back to the global pointer (pre-routing checkpoints, fresh projects).

    fallback=False reads ONLY the project's own pointer (#94): the global
    pointer holds the most recent checkpoint of ANY project, so callers that
    PERSIST what they read (carry) must never see it — display callers (brief)
    keep the fallback and label it."""
    d = config.checkpoint_dir()
    slug = project_slug(project_dir)
    if slug:
        path = d / slug / _LATEST
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    if not fallback:
        return None
    path = d / _LATEST
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ---- team memory (#111): opt-in shared mirror, derive-never-write shared state ----

# Phase 1 has a single local remote-slug; Phase 3 (#113) adds synced remotes as
# sibling dirs — git CLONES managed by teamsync (read_team fans in across ALL).
_TEAM_LOCAL_REMOTE = "local"


def _team_write_slug() -> str:
    """Which remote-slug dir _dual_write_team targets. When exactly ONE real
    remote (a sidecar git clone, detected purely by the presence of a .git
    entry — store stays git/subprocess-free) exists, write straight into it so
    `daimon team sync` picks the file up. Zero or MULTIPLE remotes -> the
    Phase 1 'local' dir: with several remotes there is no principled routing
    choice, so ambiguity degrades to the documented local mirror rather than a
    guess. Never raises."""
    try:
        clones = [
            p.name for p in config.team_dir().iterdir()
            if p.is_dir() and p.name != _TEAM_LOCAL_REMOTE and (p / ".git").exists()
        ]
    except OSError:
        return _TEAM_LOCAL_REMOTE
    return clones[0] if len(clones) == 1 else _TEAM_LOCAL_REMOTE


def _dual_write_team(session_id: str, checkpoint: dict, project_dir) -> None:
    """Mirror a just-written checkpoint into the shared team dir (opt-in, #111):
        <team_dir>/<remote-slug>/authors/<author-slug>/<session_id>.json
    where <remote-slug> is the single synced remote when one exists, else
    'local' (see _team_write_slug). Immutable append — NO pointers are EVER
    written here (the multi-writer git spike verdict: mutable pointers don't
    survive concurrent writers). Best-effort: never raises (mirrors the GC/log
    swallow) — a team-mirror failure must not fail the serialize that already
    succeeded.

    Stamps `project_slug` onto a COPY of the checkpoint so read_team can filter by
    project without a pointer; the local blob is left clean (no project routing of
    its own)."""
    try:
        # Full project_slug munging, NOT _safe_name: _safe_name maps "a/b" and
        # "a_b" to the same dir (silent two-humans merge in read_team) and lets
        # Windows-hostile chars (:*?<>|) through. project_slug munges every
        # non-word char to '-'. Post-munge collisions ("a b" vs "a-b") remain a
        # documented edge — distinct humans colliding there is unrealistic.
        author_slug = project_slug(config.author()) or "unknown"
        d = config.team_dir() / _team_write_slug() / "authors" / author_slug
        d.mkdir(parents=True, exist_ok=True)
        # Shallow copy is deliberate and sufficient: only top-level keys are
        # stamped below; nested structures are never mutated on this path.
        blob = dict(checkpoint)
        blob.setdefault("project_slug", project_slug(project_dir))
        _atomic_write(d / f"{_safe_name(session_id)}.json",
                      json.dumps(blob, indent=2, ensure_ascii=False))
    except OSError:
        pass


def team_retention_cutoff() -> float | None:
    """Epoch floor for the team-view retention window (#113), or None when
    retention is disabled (DAIMON_TEAM_RETENTION_DAYS=0). The SINGLE source for
    every team-dir reader — read_team and the recall index (#120) must agree on
    which teammate checkpoints have aged out."""
    days = config.team_retention_days()
    return (time.time() - days * 86400) if days > 0 else None


def read_team(project_dir=None) -> list[tuple[str, dict]]:
    """Newest checkpoint per author in the shared team dir, for the given project.

    Fan-in across every remote-slug: <team_dir>/*/authors/<author-slug>/*.json.
    Derive-at-read — there are no pointers to trust: the newest checkpoint per
    author is chosen by the #93 `created` stamp (file mtime fallback, via
    _file_recency), exactly as the local GC ranks files. Returns
    [(author, checkpoint), ...] newest-first by each author's newest checkpoint.

    Project filter: only checkpoints whose stamped `project_slug` matches this
    project's slug. When the project is unknown (slug None) no filter is applied.

    Retention (#113): checkpoints older than DAIMON_TEAM_RETENTION_DAYS (by the
    same _file_recency the ranking uses; 0 = keep all) are skipped AT READ TIME
    only — NO physical deletes, ever: the shared branch is append-only and
    deletes race appends (spike verdict).

    Pure file-ops, never raises — a missing/broken/torn team dir yields []."""
    root = config.team_dir()
    want_slug = project_slug(project_dir)
    cutoff = team_retention_cutoff()
    # author-slug (dir identity, one per author) -> (recency, author, checkpoint)
    best: dict[str, tuple[float, str, dict]] = {}
    try:
        remotes = list(root.iterdir())
    except OSError:
        return []
    for remote in remotes:
        try:
            author_dirs = list((remote / "authors").iterdir())
        except OSError:
            continue  # not a remote-shaped dir; skip
        for adir in author_dirs:
            try:
                files = [p for p in adir.iterdir()
                         if p.is_file() and p.suffix == ".json"]
            except OSError:
                continue
            for p in files:
                try:
                    cp = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue  # torn/foreign file — skip, never crash the fan-in
                if not isinstance(cp, dict):
                    continue
                if want_slug is not None and cp.get("project_slug") != want_slug:
                    continue
                rec = _file_recency(p)
                if cutoff is not None and rec < cutoff:
                    continue  # aged out of the read window; file stays on disk
                key = adir.name
                if key not in best or rec > best[key][0]:
                    best[key] = (rec, cp.get("author") or adir.name, cp)
    ordered = sorted(best.values(), key=lambda t: t[0], reverse=True)
    return [(author, cp) for _rec, author, cp in ordered]


# ---- #102: append-only resolution events ----


def _events_path(project_dir=None):
    slug = project_slug(project_dir)
    if not slug:
        return None
    return config.checkpoint_dir() / slug / "events.jsonl"


def append_event(item_ref: str, status: str, note: str = "",
                 kind: str = "resolution", source: str = "cli",
                 project_dir=None) -> bool:
    """One appended JSON line per lifecycle fact (#102). Append-only: the
    file is never rewritten — resolution is a derivation at read, so the
    audit trail must stay byte-stable. Silent no-op under the kill switch
    and when the project is unknown (an event without a bucket has no
    reader)."""
    if config.is_disabled():
        return False
    path = _events_path(project_dir)
    if path is None:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        evt = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "kind": kind, "item_ref": item_ref, "status": status,
               "source": source}
        if note:
            evt["note"] = note
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def resolutions(project_dir=None) -> dict:
    """events.jsonl -> {item_ref: latest event} — latest by ts, NEVER line
    order (concurrent writers may interleave). Unknown kinds and extra
    fields ride along untouched; unparseable lines are skipped best-effort:
    a reader must never drop the log over one bad line."""
    out: dict = {}
    path = _events_path(project_dir)
    if path is None:
        return out
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            evt = json.loads(line)
        except ValueError:
            continue
        if not isinstance(evt, dict):
            continue
        ref = str(evt.get("item_ref") or "")
        if not ref:
            continue
        cur = out.get(ref)
        new_e = _created_epoch(evt.get("ts"))
        cur_e = _created_epoch(cur.get("ts")) if cur else None
        if cur is None or (new_e is not None and (cur_e is None or new_e >= cur_e)):
            out[ref] = evt
    return out


def is_resolved(event) -> bool:
    """Liveness rule (#102): latest event wins; a status starting with
    'reopen' returns the item to live; anything else means resolved. Status
    is free-form text by design — never an enum, so unknown statuses resolve
    (the writer bothered to record a lifecycle fact) rather than vanish."""
    if not isinstance(event, dict):
        return False
    return not str(event.get("status") or "").lower().startswith("reopen")
