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

import dataclasses
import enum
import json
import logging
import os
import re
import stat
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import config, normalize, policy, receipts, redact, schema, serializer, teamproject

log = logging.getLogger("daimon_briefing")

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


def _plaintext_surfaces(d: Path) -> list[Path]:
    """Every file under the store that holds checkpoint items as PLAINTEXT:
    per-session checkpoints and the rotation pointers, in the flat dir and in
    every per-project bucket.

    This list is the deletion contract's surface set. #419 settled that holding
    plaintext — not being append-only, not being "history" — is what puts a file
    inside it. `forget` previously reasoned only about the LIVE checkpoint, so
    prev-N and superseded session files kept the value after a successful
    deletion. Anything added here later must be added to this walk, or it
    silently inherits the same hole."""
    out: list[Path] = []
    try:
        entries = list(d.iterdir())
    except OSError:
        return out
    for entry in entries:
        try:
            if entry.is_file() and entry.suffix == ".json":
                out.append(entry)
            elif entry.is_dir():
                out.extend(p for p in entry.iterdir()
                           if p.is_file() and p.suffix == ".json")
        except OSError:
            continue
    return out


def project_surfaces(project_dir=None) -> list[Path]:
    """`_plaintext_surfaces` narrowed to ONE project.

    forget is project-scoped — its tombstone lands in one project's ledger — so
    every surface it reads or rewrites must belong to that project. The flat
    dir is shared: `latest.json` there is the GLOBAL pointer and may hold any
    project's session, and session files sit side by side regardless of origin.
    Membership is therefore decided by the payload's own `project_slug`, never
    by the file's location.

    A file whose slug cannot be read is EXCLUDED. Deleting from a surface whose
    ownership is unknown is the failure this function exists to prevent."""
    slug = project_slug(project_dir)
    if not slug:
        return []
    out: list[Path] = []
    for path in _plaintext_surfaces(config.checkpoint_dir()):
        if path.parent.name == slug:
            out.append(path)
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("project_slug") == slug:
            out.append(path)
    return out


def items_for_project(project_dir=None) -> list[tuple[Path, str, str, dict]]:
    """Every identified item this project holds on ANY surface, live or not.

    forget resolved targets against the live checkpoint alone, so a value that
    had been superseded was reported as "no item matches" while its plaintext
    sat in prev-N. Resolution has to see what deletion has to reach."""
    found: list[tuple[Path, str, str, dict]] = []
    for path in project_surfaces(project_dir):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        for section, key in _ITEM_LISTS:
            for item in ((payload.get(section) or {}).get(key) or []):
                if isinstance(item, dict) and item.get("id"):
                    found.append((path, section, key, item))
    return found


def scrub_content_key(content_hash: str, project_dir=None) -> list[str]:
    """Remove every item folding to `content_hash` from all plaintext surfaces.

    Returns the paths rewritten, so the caller can report what deletion
    actually reached instead of asserting it. Best-effort per file: one
    unreadable or unwritable surface must not abort the rest of the scrub, and
    a file that cannot be parsed is left byte-identical rather than truncated —
    the same posture the ledger rewrite takes toward rows it cannot interpret.
    """
    if not content_hash:
        return []
    rewritten: list[str] = []
    for path in project_surfaces(project_dir):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        # #599: one predicate for every path — the same full-enumeration
        # scrub the write gate runs (items by text, survivors' quote/scene,
        # links[].target, the active_topic singleton).
        _, changed = policy.scrub_forgotten_payload(payload, {content_hash})
        if not changed:
            continue
        try:
            _atomic_write(path, json.dumps(payload, indent=2,
                                           ensure_ascii=False) + "\n")
        except OSError:
            continue
        rewritten.append(str(path))
    return rewritten


def scrub_team_copies(content_hash: str, project_dir=None) -> list[str]:
    """#600 slice A: scrub the author's OWN team-mirror checkpoint copies.

    The mirror holds full plaintext copies of every mirrored session, and
    the deletion walk never reached it (team_dir is a SIBLING of the
    checkpoint store). #419's rule puts it squarely inside the contract:
    holding plaintext, not its role, is what counts.

    Scope discipline is the whole design:
    - OWN author dirs only — `authors/<project_slug(config.author())>`,
      the same identity _dual_write_team writes and teamsync._own_pathspecs
      commits, so a later `team sync` stages the rewrite as an ordinary
      own-file modification. The spike verdict forbids mutable POINTERS and
      cross-author deletes (those race appends); a rewrite inside the
      disjoint own-author dir is non-racing by the same construction that
      makes appends safe. Teammates' files are never touched — their
      copies converge via tombstone propagation (slice B), and the audit
      keeps reporting them meanwhile.
    - THIS project only — nested layout segments must be one of
      teamproject.read_candidates' logical paths, or the payload's own
      project_slug stamp (written by _dual_write_team) must match. Another
      project's mirror copy of the same sentence is that project's belief
      state.
    - Runs regardless of config.team_enabled(): deletion parallels the
      kill-switch exemption; a toggle flipped off later must not orphan
      plaintext the mirror already holds.

    Each rewrite passes through policy.admit_checkpoint — re-admission
    under the project's forgotten keys (the key that motivated this call
    included) — so the write-audit guard binds the bytes on disk to a real
    admission, the scrub_event_fields precedent. Upstream git history and
    the remote remain out of reach (the registry's `.git/**` known-gap).

    Known blindness, shared with the audit: a pre-stamp-era flat mirror
    file with no project_slug payload field cannot be attributed and is
    skipped — privacy._scan_team_dir misses it by the identical rule, so
    scrub and audit at least agree.

    Best-effort per file; returns the paths rewritten."""
    if not content_hash:
        return []
    team = config.team_dir()
    own = project_slug(config.author()) or "unknown"
    slug = project_slug(project_dir)
    keys = forgotten_content_keys(project_dir) | {content_hash}
    try:
        segs = {tuple(s) for s in teamproject.read_candidates(project_dir)}
    except Exception:
        segs = set()
    try:
        remotes = [d for d in team.iterdir() if d.is_dir()]
    except OSError:
        return []
    rewritten: list[str] = []
    for remote in remotes:
        candidates = list(remote.glob(f"authors/{own}/*.json"))
        candidates += remote.glob(f"projects/**/authors/{own}/*.json")
        for path in sorted(set(candidates)):
            # nested layout: projects/<seg…>/authors/<own>/<sid>.json —
            # the logical segments are parts[1:-3] (the same slice the
            # audit takes; [1:-2] kept the "authors" segment and made this
            # check unreachable). A copy of the SAME logical project
            # written from another checkout carries that path's slug, so
            # the logical-path match is what rescues it.
            parts = path.relative_to(remote).parts
            nested_ok = (len(parts) > 4 and parts[0] == "projects"
                         and parts[-3] == "authors"
                         and parts[1:-3] in segs)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            if not nested_ok and payload.get("project_slug") != slug:
                continue
            # Probe on a copy first: only files the forget actually changes
            # are re-admitted and rewritten — admission drift (missing ids,
            # a new redact pattern) must not masquerade as a scrub, and a
            # torn payload the probe cannot change is skipped before the
            # stricter admission can trip on it.
            probe = json.loads(json.dumps(payload))
            _, changed = policy.scrub_forgotten_payload(probe, keys)
            if not changed:
                continue
            try:
                policy.admit_checkpoint(payload, keys)
                _atomic_write(path, json.dumps(payload, indent=2,
                                               ensure_ascii=False) + "\n")
            except Exception:
                # best-effort per file: one unwritable or half-torn surface
                # must not abort the rest of the scrub — nor the forget's
                # remaining steps (event scrub, cache purge) behind it.
                continue
            rewritten.append(str(path))
    return rewritten


# Daimon-authored Windsurf state that holds conversation text (#607). The
# activity/serialize stamps beside it are epoch integers and stay.
_WINDSURF_TEXT_GLOBS = ("transcripts/*.md", "unparsed-*.json")


def _windsurf_text_files() -> list:
    """Containment before deletion: a symlinked `transcripts` directory
    would otherwise have the purge unlink files outside the state root
    entirely. provenance.SourceResolver already resolves-and-contains
    before merely READING this directory; the deleting path must not be
    laxer than the reading one."""
    root = config.windsurf_state_dir()
    try:
        real_root = root.resolve()
    except OSError:
        return []
    out: list = []
    for pattern in _WINDSURF_TEXT_GLOBS:
        for path in root.glob(pattern):
            try:
                if not path.is_file():
                    continue
                if real_root not in path.resolve().parents:
                    continue
            except OSError:
                continue
            out.append(path)
    return sorted(set(out))


def purge_windsurf_state() -> tuple:
    """#607: wholesale removal of the transcripts the Windsurf adapter wrote.

    `daimon forget` calls this after the tombstone+rewrite, for the same
    reason it purges the chunk cache (#422): the value cannot be located
    selectively. A transcript is prose, the forgotten sentence is a
    substring of a line, and the tombstone is a canonical HASH — forget
    stores the key and never the text (#321), so no component downstream
    holds the plaintext a substring search would need. Detection is
    impossible by construction, so the whole store goes.

    Accepted cost: quote provenance resolving against these files reports
    `absent-local` afterward — a state provenance.SourceResolver already
    models, so a purged transcript degrades a receipt rather than breaking
    it. Host-authored transcripts (Codex rollouts, Claude Code JSONL) are
    untouched: daimon reads those by path and never copies them, so they
    are not daimon's to delete — and `forget` has never removed them.

    Returns (purged_count, error_or_None) and NEVER raises: deleting belief
    state is the primary contract; a failed purge is reported, not fatal."""
    try:
        targets = _windsurf_text_files()
    except OSError as e:
        return 0, str(e)
    purged, error = 0, None
    for path in targets:
        try:
            path.unlink()
            purged += 1
        except OSError as e:
            error = error or str(e)
    return purged, error


def reap_windsurf_state(now: float | None = None, apply: bool = True) -> list:
    """#607: age-bound what accumulates between forgets.

    The purge above only fires when someone runs `forget`; without a window
    the adapter's transcript store grows without limit, and every turn of
    every conversation stays on disk forever. config.windsurf_state_days()
    (default 7) is that bound — a privacy window, not a disk one. Wired
    into `daimon heal` beside the index-snapshot reap; `apply=False` lists
    only (heal --dry-run). Best-effort per file."""
    if now is None:
        now = time.time()
    cutoff = now - config.windsurf_state_days() * 86400
    reaped: list = []
    try:
        targets = _windsurf_text_files()
    except OSError:
        return reaped
    for path in targets:
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            if apply:
                path.unlink()
        except OSError:
            continue
        reaped.append(path)
    return reaped


def purge_crash_log() -> tuple:
    """#605: the serializer child's RAW stderr sink, inside the contract.

    `logs/serialize-crash.log` is the fd spawn_serialize hands a detached
    child (and the Windsurf finalizer its sleeper), so an uncaught traceback
    lands there verbatim — exception message, repr'd arguments, whatever the
    crashing frame was holding. `status` redacted that tail on READ (#513)
    while the bytes underneath stayed; nothing ever removed them.

    Wholesale for the same reason as the chunk cache (#422) and the Windsurf
    transcript store (#607): forget keeps the canonical HASH and never the
    text (#321), so no component downstream holds the plaintext a substring
    search of a traceback would need. Detection is impossible by
    construction, so the whole file goes — and unlike those two stores there
    is no age reaper behind it, because the write seam trims the file to a
    bounded tail instead (_daimon_hook_lib.trim_crash_log).

    Accepted cost: `daimon status` reports no last-crash line until the next
    one happens. That is the same trade `forget` already makes against quote
    provenance — a receipt degrades, a diagnostic goes quiet, and neither
    outranks removing plaintext the user asked to be gone.

    Returns (purged_count, error_or_None) and NEVER raises: deleting belief
    state is the primary contract; a failed purge is reported, not fatal."""
    return _purge_fixed_log("serialize-crash.log")


def purge_backend_stderr_log() -> tuple:
    """#616: the CLI-backend diagnostics sink, inside the contract.

    `logs/backend-stderr.log` holds backend stderr AND stdout — and CLI
    backends can echo prompt fragments (transcript text) into either stream
    (#141, llm._log_backend_stderr's own concession). The write seam
    secret-redacts and byte-bounds the file, but item text is not a secret
    shape, so a forgotten value echoed by a backend survived on disk while
    the registry called the file plaintext-free.

    Wholesale for the crash sink's reason (#605): the tombstone is a
    canonical HASH (#321), so a value inside prose diagnostics cannot be
    located to remove selectively. Accepted cost mirrors #605's: the next
    backend failure has no history behind it — a diagnostic goes quiet,
    and that never outranks removing plaintext the user asked to be gone.

    Returns (purged_count, error_or_None) and NEVER raises: deleting belief
    state is the primary contract; a failed purge is reported, not fatal."""
    return _purge_fixed_log("backend-stderr.log")


def _purge_fixed_log(name: str) -> tuple:
    """Unlink ONE daimon-authored file under the log dir, defensively.

    Shared by the fixed-name log purges above; returns (purged, err)."""
    try:
        path = config.log_dir() / name
    except Exception as e:  # noqa: BLE001 — e.g. UnicodeDecodeError from a
        # corrupt ~/.daimon/env: a ValueError, so the OSError net below never
        # sees it. The writer resolves through the same accessors and raises
        # identically, so nothing was written where this purge cannot look.
        return 0, str(e)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return 0, None                 # never written, or already purged
    except OSError as e:
        # A log dir this process cannot see is not a clean purge. The count
        # is a privacy CLAIM — "the bytes are gone" — and a silent zero
        # over an unreadable directory is that claim made without evidence.
        return 0, str(e)
    # lstat rather than is_file(): those follow the link and answer False for
    # every failure alike. daimon creates this file itself with open("a"), so
    # a symlink or a directory standing in its place is not a shape daimon
    # wrote. This is _windsurf_text_files' containment rule (#607) narrowed
    # to a FIXED filename, where a resolve()-parents check would be strictly
    # weaker: a link pointing back inside the log dir passes containment,
    # yet unlink() removes only the link and leaves the plaintext behind a
    # count that says it went.
    if stat.S_ISLNK(st.st_mode):
        return 0, (f"{path} is a symlink — refusing to unlink through a file "
                   "daimon did not create")
    if not stat.S_ISREG(st.st_mode):
        return 0, f"{path} is not a regular file — refusing to unlink it"
    try:
        os.unlink(path)
    except OSError as e:
        return 0, str(e)
    return 1, None


# #616: the two line shapes pre-fix serializers wrote into serialize.log with
# the item's OWN text as payload. Anchored on the logging router's record
# prefix (`<iso> WARNING daimon_briefing.serializer: `, cli's
# _attach_serialize_log_handler) so a ledger result line — raw, untimestamped
# — can never match. Current writers log `(content hash <key>)` with no `: `
# payload separator after these heads, so clean lines never match either.
_LEGACY_DOWNGRADE_RE = re.compile(
    r"^(?P<head>\S+ WARNING \S+: "
    r"(?:quote verification: downgraded verbatim->inferred"
    r"(?: \(echo-only: quote appears only in daimon's own injected output\))?"
    r"|outcome grounding: unwitnessed outcome claim downgraded "
    r"verbatim->inferred \(no signal cited\)): ).*$")

_LOG_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")

_SCRUB_MARKER = "[payload scrubbed #616]"


def scrub_serialize_log() -> tuple:
    """#616: redact LEGACY downgrade payloads out of serialize.log in place.

    Pre-fix, the quote-verification and outcome-grounding downgrade lines
    logged the item's own text (secret-redacted, never item-redacted) and
    the CLI routed them here. Current writers log a content hash instead,
    but the old payloads persist on disk in every existing install.

    NOT a wholesale purge, deliberately: serialize.log is also the ledger
    `status` parses for capture stats (_SPAWN_RE / _RESULT_*_RE), and
    destroying the measurement record on every forget trades one contract
    for another. The legacy payloads sit behind two exactly-known line
    shapes, so the scrub is SHAPE-targeted — value-blind, like every purge
    on this path (the tombstone is a hash; the value cannot be located).

    A matched line keeps its head and gets its payload replaced; the lines
    AFTER it are dropped until the next router-timestamped line, because a
    multi-line item text logs as untimestamped continuation lines. That can
    in principle eat a raw ledger line that directly follows a multi-line
    payload — accepted: over-scrubbing costs one stat row, under-scrubbing
    persists a line of text the user asked to be gone, and content_key
    already picks the same fail-safe direction (over-blocking).

    Rewrite is atomic (temp + os.replace) and refuses symlinks/non-regular
    files for _purge_fixed_log's reason. Returns (scrubbed_line_count,
    error_or_None) and NEVER raises."""
    try:
        path = config.log_dir() / "serialize.log"
    except Exception as e:  # noqa: BLE001 — same corrupt-env net as above
        return 0, str(e)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return 0, None                 # never serialized here — nothing to scrub
    except OSError as e:
        return 0, str(e)
    if stat.S_ISLNK(st.st_mode):
        return 0, (f"{path} is a symlink — refusing to rewrite through a "
                   "file daimon did not create")
    if not stat.S_ISREG(st.st_mode):
        return 0, f"{path} is not a regular file — refusing to rewrite it"
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, ValueError) as e:
        return 0, str(e)
    out: list = []
    scrubbed = 0
    dropping = False
    for line in lines:
        if dropping and not _LOG_STAMP_RE.match(line):
            continue                   # continuation of a scrubbed payload
        dropping = False
        m = _LEGACY_DOWNGRADE_RE.match(line.rstrip("\n"))
        if m:
            if line.rstrip("\n") == m.group("head") + _SCRUB_MARKER:
                out.append(line)       # already scrubbed — a marker is a
                continue               # payload shape too; do not re-count
            out.append(m.group("head") + _SCRUB_MARKER + "\n")
            scrubbed += 1
            dropping = True
            continue
        out.append(line)
    if not scrubbed:
        return 0, None
    tmp = path.with_name(path.name + ".scrub.tmp")
    try:
        tmp.write_text("".join(out), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        return 0, str(e)
    return scrubbed, None


# #600 slice B: the per-author tombstone ledger published into the team
# mirror. `.jsonl`, so every `*.json` walk in the codebase (read_team,
# privacy's team scan, recall's fingerprint) steps over it by construction,
# and it sits INSIDE the own-author dir so teamsync._commit_own carries it
# with no protocol change.
_TOMBSTONE_NAME = "tombstones.jsonl"


def _own_team_dirs(project_dir=None) -> list:
    """This author's directories in every sidecar the project routes to —
    the same identity and routing _dual_write_team writes checkpoints with,
    so a published tombstone lands where the sync already looks."""
    own = project_slug(config.author()) or "unknown"
    segs = teamproject.resolve(project_dir)
    out = []
    for slug in _team_write_slugs(project_dir):
        base = config.team_dir() / slug
        if segs:
            base = base.joinpath("projects", *segs)
        out.append(base / "authors" / own)
    return out


def publish_tombstone(content_hash: str, project_dir=None) -> list[str]:
    """Publish a forget so teammates can act on it (#600 slice B).

    A hash-only row — {ts, key, author}, never the text (#321) — appended
    to the author's own tombstone ledger in each sidecar this project
    routes to. Append-only and idempotent: a key already present is not
    re-appended, so repeated forgets of the same value do not grow the
    file, and re-publishing after a pull is a no-op.

    Gated on config.team_enabled(): nothing is published into a team the
    user has not opted into. Best-effort — a failed publish never costs the
    local deletion, which already happened by the time this runs."""
    if not content_hash or not config.team_enabled():
        return []
    written: list[str] = []
    row = json.dumps({
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "key": content_hash,
        "author": config.author(),
    }, ensure_ascii=False)
    for adir in _own_team_dirs(project_dir):
        path = adir / _TOMBSTONE_NAME
        try:
            if path.exists() and content_hash in _tombstone_keys(path):
                continue
            adir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(row + "\n")
        except OSError:
            continue
        written.append(str(path))
    return written


# One ledger is read on the briefing path, so it cannot be unbounded: a
# teammate publishing a huge file must not make every read pay for it.
# ~1 MB is ~12k rows, far past any real forget history.
_MAX_TOMBSTONE_BYTES = 1_000_000


def _tombstone_keys(path) -> set[str]:
    keys: set[str] = set()
    try:
        if path.stat().st_size > _MAX_TOMBSTONE_BYTES:
            log.warning("daimon team: %s exceeds %d bytes — reading the "
                        "first %d only", path.name, _MAX_TOMBSTONE_BYTES,
                        _MAX_TOMBSTONE_BYTES)
            with path.open("r", encoding="utf-8", errors="replace") as f:
                raw = f.read(_MAX_TOMBSTONE_BYTES)
        else:
            raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return keys
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue          # a corrupt row must not hide the rest
        if isinstance(row, dict):
            key = row.get("key")
            if isinstance(key, str) and key.strip():
                keys.add(key.strip())
    return keys


def foreign_forgotten_content_keys() -> set[str]:
    """Tombstone keys published by OTHER authors in synced sidecars.

    Own rows are excluded, and that exclusion is load-bearing rather than
    tidiness: the local ledger is a latest-event-wins fold, so a `reopen`
    lifts a local tombstone — but a published row has no retraction. Folding
    your own rows back in would let a key you deliberately reopened suppress
    the value forever, and (with the opt-in on) re-scrub it on every sync,
    because reopen removes it from apply_foreign_tombstones' local subtrahend.

    The machine-local mirror is excluded for the same reason: nothing there
    is another author's, it never syncs, and a solo user with DAIMON_TEAM=1
    would otherwise poison their own reopen through a dead-end path.

    Bounded on purpose: only `authors/*/` directories inside real sidecars
    are walked, so a clone's .git object store is never traversed, and each
    ledger is capped — a teammate cannot make every briefing pay for an
    unbounded file. Never raises."""
    keys: set[str] = set()
    own = project_slug(config.author()) or "unknown"
    try:
        remotes = [d for d in config.team_dir().iterdir()
                   if d.is_dir() and d.name != _TEAM_LOCAL_REMOTE]
    except OSError:
        return keys
    for remote in remotes:
        try:
            paths = [p for p in remote.rglob(f"authors/*/{_TOMBSTONE_NAME}")
                     if p.parent.name != own]
        except OSError:
            continue
        for path in paths:
            keys |= _tombstone_keys(path)
    return keys


def apply_foreign_tombstones(project_dir=None, all_projects=False) -> list[str]:
    """Opt-in (#600 slice B): rewrite THIS machine's own checkpoints under
    a teammate's tombstone.

    Off by default and deliberately so — see config.team_apply_forget. When
    off this is a pure no-op that returns [], which is the whole authority
    guarantee: a teammate writing a hash cannot delete local belief state,
    it can only stop that value being read (the suppression path, which is
    always on).

    Returns the surfaces rewritten. The keys are the foreign ledger minus
    what this project already tombstoned locally — re-applying a local
    forget would be work without effect."""
    if not config.team_apply_forget():
        return []
    if all_projects:
        try:
            targets = [d.name for d in config.checkpoint_dir().iterdir()
                       if d.is_dir() and d.name != ".chunk-cache"]
        except OSError:
            targets = []
    else:
        targets = [project_dir]
    foreign = foreign_forgotten_content_keys()
    rewritten: list[str] = []
    for target in targets:
        # Per project, minus what that project already tombstoned locally:
        # re-applying a local forget is work without effect, and a project
        # that REOPENED a value must not have it scrubbed by a stale row.
        for key in sorted(foreign - forgotten_content_keys(target)):
            rewritten.extend(scrub_content_key(key, project_dir=target))
    return rewritten


def checkpoints_written_since(cutoff: float) -> int:
    """How many per-session checkpoints were WRITTEN since `cutoff` (epoch
    seconds), counted by file mtime — the write-side signal for the silent-
    capture alarm (#265). Pointers and in-flight *.tmp writes are excluded
    (via _session_files), so this is checkpoints actually landed, not rotations.
    Fails open to 0 when the store dir is absent or unreadable — no store yet is
    zero writes, never a crash."""
    d = config.checkpoint_dir()
    try:
        files = _session_files(d)
    except OSError:
        return 0
    count = 0
    for p in files:
        try:
            if p.stat().st_mtime >= cutoff:
                count += 1
        except OSError:
            continue
    return count


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


# Reserved decision-item field (#125): `receipt_hash` is an optional slot on
# recent_decisions items, reserved for future signed-provenance support. Nothing
# writes it yet; it defaults to absent and readers use .get. The write path
# preserves it untouched when present — carry copies whole items (carry.merge
# deepcopy) and redaction/id-stamping below only ever touch named fields — so no
# code here needs to name it; this note is the reservation.

# The five list sections that hold checkpoint items, from the shared schema
# (#146 — one definition; serializer/recall/carry derive theirs from the same
# table). active_topic is a single per-session dict and never needs an id (it
# does not carry, #33). Aliased because briefing.withhold and cli iterate
# store._ITEM_LISTS.
_ITEM_LISTS = schema.ITEM_LISTS


# #421: the admission pipeline (redact -> forget-gate -> id-stamp) moved to
# policy.py, the pure module that owns its order. Aliased here because cli,
# bench and tests call the store names; the behavior is byte-identical.
_redact_checkpoint = policy.redact_checkpoint
_stamp_item_ids = policy.stamp_item_ids


def _drop_ruling_echoes(checkpoint: dict, project_dir=None) -> list:
    """#693: drop exact echoes of ACTIVE ruling text at the admission
    boundary. The standing-rulings section renders into every session's
    context, so the next extraction can mint the ruling back as a fresh
    belief — a copy that decays, drifts, and double-renders. Note "next
    extraction" includes carried items: capture re-admits what it carries,
    so a belief admitted BEFORE its ruling was ratified is deduped at the
    next capture — deliberate, counted, and logged like any other echo.
    Whole-item drops ONLY (policy.drop_matching_items): a ruling carries no
    deletion promise, so an item merely QUOTING the ruling keeps its quote,
    its trust class, and its receipts — the forget gate's field scrubs are
    the wrong tool here. The key set covers every form the briefing
    actually renders — bare verdict, render-clipped, "§ "-prefixed, and
    authority-suffixed lines — so the renderer and this filter cannot
    disagree on "the same text". Paraphrase shadows are out of scope by
    design — they decay as ordinary beliefs.

    A COUNTED drop, never silent: each one is reason-coded on the hit
    ledger (`ruling-echo`, its own counter and its own timestamp — the echo
    rate stays an endogenous measurement) and logged as a content hash,
    never the text. The WHOLE body fails open: no echo-filter failure of
    any kind may cost the capture it observes."""
    try:
        # Function-local import: refutations imports store at module level,
        # so the reverse edge must be deferred to call time.
        from . import refutations
        rows = refutations.listing(states={"active"}, polarity="ruling",
                                   project_dir=project_dir)
        keys = set()
        for row in rows:
            verdict = str(row.get("verdict") or "")
            if not verdict:
                continue
            clipped = verdict[:refutations._MAX_RULING_TEXT]
            bases = {verdict, clipped, clipped + "…"}
            authored = row.get("text_authored_by")
            if authored and authored != "human":
                bases.update({f"{b}  [{authored}-written]" for b in set(bases)})
            for base in bases:
                keys.add(normalize.content_key(base))
                keys.add(normalize.content_key(f"§ {base}"))
        dropped = policy.drop_matching_items(checkpoint, keys)
        if dropped:
            record_forget_hits(dropped, project_dir, reason="ruling-echo")
            for item in dropped:
                # INFO, not WARNING: a stable ruling being re-minted every
                # session is the expected, measured event this filter exists
                # for — not an anomaly worth an alert per capture.
                log.info(
                    "ruling echo: item dropped at admission "
                    "(content hash %s)",
                    normalize.content_key(item.get("text") or ""))
        return dropped
    except Exception:
        # Fail-open must still leave a trace: if this fires AFTER the drop
        # mutated the checkpoint, the accounting above never ran.
        log.debug("ruling echo filter failed open", exc_info=True)
        return []


def write_checkpoint(session_id: str, checkpoint: dict, project_dir=None,
                     allow_disabled: bool = False,
                     rotate: bool = True,
                     admit: bool = False) -> Path | None:
    """Write the session checkpoint + the global latest pointer, and — when the
    project is known — the per-project latest pointer too. The global pointer is
    kept for backward compatibility (pre-routing consumers and the fallback).

    Each latest pointer is rotated first (#33 Phase 1): the previous latest is
    retained as prev-1.json, keeping the last DAIMON_CHECKPOINT_HISTORY writes.

    Kill switch (#421): the FIRST gate, before any directory is even created —
    disabled means no belief mutations, one consistent answer at the write
    boundary regardless of entry point (hook, CLI serialize, model-authored
    write-checkpoint, anchor rewrite). Refusal is a None return, never an
    exception — the same never-fatal posture as the ledger appenders. The ONE
    exemption is `allow_disabled=True`, passed only by cli._cmd_forget's
    rewrite: the maintainer ratified that deletion must still work while
    disabled (the deletion promise outranks "disabled writes nothing").

    `admit=True` (#693) marks this write as an ADMISSION of new cognitive
    content — passed ONLY by the two admission callers (capture.run, the
    `write-checkpoint` stdin path) — and switches on the ruling echo filter.
    Rewrite callers stay at the default: `daimon anchor` must not strip
    previously-admitted items, and the forget rewrite must not retroactively
    delete outside its own contract."""
    if config.is_disabled() and not allow_disabled:
        return None
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
    # #421: the ordered admission pipeline — redact, then the #402 value-keyed
    # forget gate (drop any item whose canonical value was forgotten for this
    # project BEFORE it is stamped, signed, indexed, or mirrored; the gate runs
    # after redaction so the compared text matches the stored post-redaction
    # text the forget command keyed the tombstone on), then id-stamping — lives
    # in policy.admit_checkpoint, pure by contract. The forgotten-keys ledger
    # read is its one I/O dependency, so it happens HERE and is injected.
    forget_dropped = policy.admit_checkpoint(
        checkpoint, forgotten_content_keys(project_dir))
    if forget_dropped:
        # #404: account each suppression on the telemetry ledger. Best-effort
        # (never fatal) — a hit record must never fail the capture it observes.
        record_forget_hits(forget_dropped, project_dir)
    if admit:
        _drop_ruling_echoes(checkpoint, project_dir)
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
        # #672: the slug is a lossy flattening (slash, space, and hyphen all
        # collapse to "-"), so the directory's real name is recoverable only
        # here, at the write boundary. Optional additive field; readers fall
        # back to slug-derived display when absent. Guarded by `slug` so the
        # unknown-project convention stays: absent field = unknown.
        name = Path(project_dir).name
        if name:
            checkpoint.setdefault("project_name", name)
    # Stamp the git branch at capture time (#222), same idempotent setdefault
    # shape. Capture-side only — read-side filtering is a follow-up. Resolved
    # from `project_dir` (the session's OWN project), never os.getcwd(): heal
    # re-serializes from the FAILED session's project on purpose
    # (cli._run_serialize routes to it deliberately, not the heal-time cwd).
    # Absent (never None/empty) when project_dir is None, isn't a git repo, or
    # HEAD is detached — absent field = unknown, same convention as project_slug.
    branch = config.git_branch(project_dir)
    if branch:
        checkpoint.setdefault("git_branch", branch)
    # Birth stamps (#126) need the previous latest BEFORE this write moves it.
    # read_latest never raises (returns None on absent/torn pointers). When the
    # _stamp_first_seen PERSISTS what it reads (#139), so this read is the
    # own-stream one: a known project inherits nothing foreign, an unknown
    # project keeps the global pointer as its own prior (#126 legacy).
    _stamp_first_seen(checkpoint, read_own_stream_latest(project_dir))
    # Signed provenance receipt (#204): decide + prep key material BEFORE the blob
    # is serialized, so the `receipts` era marker rides INSIDE the signed bytes
    # (outputs_hash covers the exact blob). plan_mint returns None (and stamps
    # nothing) when the feature is off or a receipt can't be produced — gate off,
    # no transcript_hash, no CLI/openssl/seed — so serialize proceeds receiptless.
    # The sidecar itself is written AFTER the checkpoint file succeeds (below).
    # setdefault: a re-write of an already-marked checkpoint keeps its marker.
    receipt_plan = receipts.plan_mint(checkpoint)
    if receipt_plan is not None:
        checkpoint.setdefault("receipts", True)
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
            if rotate:
                _rotate_pointers(d, history)
            _atomic_write(d / _LATEST, blob)
    if slug:
        pdir = d / slug
        pdir.mkdir(parents=True, exist_ok=True)
        with _pointer_lock(pdir):
            if not _pointer_regresses(pdir, new_epoch):
                if rotate:
                    _rotate_pointers(pdir, history)
                _atomic_write(pdir / _LATEST, blob)
    # Mint the receipt now that the checkpoint file is durably on disk (#204):
    # outputs_hash covers those exact bytes. Best-effort and self-contained —
    # receipts.mint swallows every failure — so it can never affect the write /
    # pointers / GC above, nor this function's rc.
    if receipt_plan is not None:
        receipts.mint(receipt_plan, checkpoint, blob, path)
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


def list_buckets() -> list[dict]:
    """Every per-project bucket in the checkpoint dir, for `daimon projects`
    (#243): [{slug, checkpoint, mtime}], unsorted — ordering is a display
    concern. A bucket is any subdir holding a latest.json; flat per-session
    files and the global pointer are not buckets. Torn/corrupt pointers are
    listed with checkpoint=None (the bucket exists — hiding it would read as
    "no such project"), matching the module's tolerant readers. Pure file-ops,
    never raises."""
    d = config.checkpoint_dir()
    try:
        entries = sorted(d.iterdir())
    except OSError:
        return []
    out: list[dict] = []
    for child in entries:
        latest = child / _LATEST
        try:
            mtime = latest.stat().st_mtime
        except OSError:
            continue  # not a dir, or a bucket that never got a pointer
        try:
            cp = json.loads(latest.read_text(encoding="utf-8"))
            if not isinstance(cp, dict):
                cp = None
        except (OSError, json.JSONDecodeError):
            cp = None
        out.append({"slug": child.name, "checkpoint": cp, "mtime": mtime})
    return out


def transcript_unchanged(session_id: str, transcript_hash: str | None) -> bool:
    """True when `transcript_hash` matches the `transcript_hash` already stamped
    on the PER-SESSION checkpoint for `session_id` (#185): the identical-bytes
    guard both serialize entry points (cli._run_serialize, hooks.on_session_end)
    call BEFORE any LLM work, so a duplicate/late SessionEnd on an unchanged
    transcript (e.g. a `claude --resume` fork's dead original session) is
    skipped instead of burning a full LLM call to reproduce a byte-identical
    checkpoint. Fail-open on every edge — no fresh hash, no existing checkpoint,
    or a missing/malformed stored hash all return False (proceed with a normal
    serialize); only an exact hex-digest match, computed the same way
    (transcript.file_sha256, over raw pre-render bytes — #125), justifies a skip.

    #293: identical bytes alone are not enough — the guard's own rationale is
    "identical bytes AND identical prompt ⇒ identical output ⇒ skip", and it
    never checked the second half. A checkpoint stamped under an older
    `serializer.PROMPT_VERSION` cannot be reproduced by the current prompt, so
    it is stale, not a duplicate-SessionEnd case, and must not be skipped — this
    is what makes the format-drift warning's "re-serialize to refresh" remedy
    actually work. A legacy checkpoint with no `format_version` at all (pre-#93)
    can't be confirmed to match the current prompt either, so — consistent with
    every other uncertain edge here — it also fails open (no skip)."""
    if not transcript_hash:
        return False
    existing = read_checkpoint(session_id)
    if not existing:
        return False
    stored = existing.get("transcript_hash")
    if not isinstance(stored, str) or not stored:
        return False
    if existing.get("format_version") != serializer.PROMPT_VERSION:
        return False
    return stored == transcript_hash


def read_checkpoint(session_id: str) -> dict | None:
    try:
        path = _contained_path(config.checkpoint_dir(), session_id)
    except ValueError:
        return None
    if not path.exists():
        return None
    # Torn/corrupt file is treated as absent, matching the module's tolerant
    # readers (_pointer_regresses, _file_recency, sibling_buckets) (#139).
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


class Route(enum.Enum):
    """Which pointers a latest-read may consult (#795). Deliberately not a
    str mixin: a bare route="own" must raise, never silently match."""

    OWN = "own"                            # the project's own bucket pointer only
    OWN_ELSE_GLOBAL = "own_else_global"    # own first, global if own yields nothing


class Admit(enum.Enum):
    """Which payloads a latest-read may return as a body (#795). Ownership is
    a PAYLOAD fact decided by the `project_slug` stamp, independent of which
    pointer produced the value — that split is the whole point (#784/#791).

    #791's taxonomy, which OWN_OR_UNROUTED encodes: the global pointer holds
    three different things and only one is foreign — the project's own
    checkpoint (always fine), an UN-ROUTED checkpoint written before a project
    was known and stamped with nobody (stays readable so pre-routing stores
    keep working), and another project's checkpoint (the one to refuse).
    Membership is decided the way team fan-in decides it, by the payload's own
    stamp, never by which pointer the read came through."""

    ANY = "any"
    OWN_OR_UNROUTED = "own_or_unrouted"    # + checkpoints belonging to nobody (#791)


@dataclasses.dataclass(frozen=True)
class Marker:
    """What a refused payload leaves behind: the two header fields `brief`
    already prints, and NOTHING more. Stdout is a write (scar 0055) — a wider
    marker would copy foreign content into this project's checkpoint."""

    slug: str | None
    created: str | None


@dataclasses.dataclass(frozen=True)
class ReadResult:
    """One read, both facts. `fell_back` is a pure ROUTE fact: the global
    pointer yielded an OBJECT (never "parseable", never "produced the value" —
    a refused payload still sets it). `refused` is the PAYLOAD fact: set iff
    ADMIT rejected a payload the route found. This type must never reach the
    17 body-only call sites: it is truthy, not None and not a dict, so a
    dropped `.checkpoint` there is a clean wrong answer, not an error."""

    checkpoint: dict | None
    fell_back: bool
    refused: Marker | None


def _read_pointer_object(path) -> dict | None:
    """The pointer's payload iff it is a JSON object. Absent, torn (#139) and
    valid-but-non-object (#817) all read as None: "yields nothing" is ONE
    state with three faces, and the contract table asserts them identical."""
    if not path.exists():
        return None
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return got if isinstance(got, dict) else None


def _admit_payload(checkpoint: dict, slug, admit: "Admit", fell_back: bool) -> "ReadResult":
    if admit is Admit.OWN_OR_UNROUTED:
        # Verbatim the shipped comparison: None / "" / whitespace / falsy
        # stamps all read as un-routed, and a non-string stamp is coerced
        # before comparing. Refuse only when BOTH sides are truthy and
        # unequal — an identity-less reader (falsy slug) refuses nothing,
        # because nothing is foreign to a session with no project identity.
        stamped = str(checkpoint.get("project_slug") or "").strip()
        if slug and stamped and stamped != slug:
            created = str(checkpoint.get("created") or "").strip() or None
            return ReadResult(None, fell_back, Marker(slug=stamped, created=created))
    return ReadResult(checkpoint, fell_back, None)


def _scoped_read(project_dir, route: "Route", admit: "Admit") -> "ReadResult":
    """THE latest-read (#795): every projection below goes through here, so
    there is exactly one implementation of route and admit."""
    if not isinstance(route, Route):
        raise TypeError(f"route must be a Route, got {route!r}")
    if not isinstance(admit, Admit):
        raise TypeError(f"admit must be an Admit, got {admit!r}")
    d = config.checkpoint_dir()
    slug = project_slug(project_dir)
    if slug:
        got = _read_pointer_object(d / slug / _LATEST)
        if got is not None:
            return _admit_payload(got, slug, admit, fell_back=False)
    if route is Route.OWN:
        return ReadResult(None, False, None)
    got = _read_pointer_object(d / _LATEST)
    if got is None:
        return ReadResult(None, False, None)
    return _admit_payload(got, slug, admit, fell_back=True)


def read_latest_body(project_dir=None, *, route: "Route", admit: "Admit") -> dict | None:
    """The latest checkpoint BODY under an explicit route and admit, or None.

    Same return type as `read_latest`, for the call sites that only want the
    body. Both keywords are required with no default so an omitted argument is
    a TypeError instead of a silently unsafe answer. Callers that need to know
    HOW the value arrived use `read_latest_result` instead — the choice is
    "do I reference `fell_back`?", nothing else."""
    return _scoped_read(project_dir, route, admit).checkpoint


def read_latest_result(project_dir=None, *, route: "Route", admit: "Admit") -> "ReadResult":
    """`read_latest_body` plus the route fact, as a `ReadResult` — for the one
    caller (`brief`) that must LABEL a fallback rather than infer it (#787,
    scar 0058). Everyone else takes the body projection above."""
    return _scoped_read(project_dir, route, admit)


def read_own_stream_latest(project_dir=None) -> dict | None:
    """The previous checkpoint of THIS stream, for the persist path (#126).

    A KNOWN project inherits only its own bucket (a foreign `first_seen` must
    never be carried); an UNKNOWN project keeps the global fallback because
    the global pointer IS that stream's own prior checkpoint (pre-routing
    legacy). Takes NO policy argument on purpose: nothing an env var can
    reach may change what write_checkpoint persists."""
    route = Route.OWN if project_slug(project_dir) else Route.OWN_ELSE_GLOBAL
    return read_latest_body(project_dir=project_dir, route=route, admit=Admit.ANY)


# The legacy surface — read_latest(fallback=) and read_latest_reportable —
# was deleted at #795 stage 4. fallback named a mechanism while callers
# reasoned about a policy; four defects shipped from its default (#785 #788
# #790 #792). Route and Admit above are the replacement, both required.


# ---- team memory (#111): opt-in shared mirror, derive-never-write shared state ----

# Phase 1 has a single local remote-slug; Phase 3 (#113) adds synced remotes as
# sibling dirs — git CLONES managed by teamsync (read_team fans in across ALL).
_TEAM_LOCAL_REMOTE = "local"


def _team_write_slugs(project_dir) -> list[str]:
    """Which remote-slug dir(s) _dual_write_team targets (#387). Each sidecar
    clone (detected purely by the presence of a .git entry — clone LISTING
    stays git/subprocess-free) declares its own membership in daimon-team.toml,
    and that allowlist is the router: the checkpoint goes into EVERY sidecar
    that grants this project membership, and to the Phase 1 'local' dir when
    none does (withheld, never lost — the #279 default-closed semantics are
    unchanged; only routing among willing sidecars is new).

    Single remote keeps the exact #279 contract, including the
    DAIMON_TEAM_PROJECT env grant. With MULTIPLE remotes the env grant is
    ignored for routing (honor_env=False): it is machine-global and cannot
    say which remote it means — honoring it would broadcast every project on
    the machine into every team. Never raises."""
    try:
        clones = [
            p.name for p in config.team_dir().iterdir()
            if p.is_dir() and p.name != _TEAM_LOCAL_REMOTE and (p / ".git").exists()
        ]
    except OSError:
        return [_TEAM_LOCAL_REMOTE]
    if not clones:
        return [_TEAM_LOCAL_REMOTE]
    if len(clones) == 1:
        ok = teamproject.in_scope(project_dir, config.team_dir() / clones[0])
        return clones if ok else [_TEAM_LOCAL_REMOTE]
    if config.team_project():
        log.warning(
            "daimon team: DAIMON_TEAM_PROJECT cannot route among %d remotes "
            "— grant membership in each sidecar's daimon-team.toml instead",
            len(clones))
    dests = sorted(
        c for c in clones
        if teamproject.in_scope(project_dir, config.team_dir() / c,
                                honor_env=False)
    )
    return dests or [_TEAM_LOCAL_REMOTE]


def _dual_write_team(session_id: str, checkpoint: dict, project_dir) -> None:
    """Mirror a just-written checkpoint into the shared team dir (opt-in, #111):
        <team_dir>/<remote-slug>/projects/<seg…>/authors/<author-slug>/<sid>.json
    under the #200 logical project path when one resolves, else the legacy flat
        <team_dir>/<remote-slug>/authors/<author-slug>/<sid>.json
    where <remote-slug> is every scope-granting synced remote (#387), else
    'local' (see _team_write_slugs). Immutable append — NO pointers are EVER
    written here (the multi-writer git spike verdict: mutable pointers don't
    survive concurrent writers). Best-effort: never raises (mirrors the GC/log
    swallow) — a team-mirror failure must not fail the serialize that already
    succeeded.

    Stamps `project_slug` onto a COPY of the checkpoint so read_team can filter by
    project without a pointer, plus `team_project` (the "/"-joined logical path,
    #200) on nested writes; the local blob is left clean (no project routing of
    its own)."""
    try:
        # Full project_slug munging, NOT _safe_name: _safe_name maps "a/b" and
        # "a_b" to the same dir (silent two-humans merge in read_team) and lets
        # Windows-hostile chars (:*?<>|) through. project_slug munges every
        # non-word char to '-'. Post-munge collisions ("a b" vs "a-b") remain a
        # documented edge — distinct humans colliding there is unrealistic.
        author_slug = project_slug(config.author()) or "unknown"
        # #279 default-closed membership + #387 scope routing live together in
        # _team_write_slugs: every sidecar whose daimon-team.toml grants this
        # project receives the checkpoint; none willing -> the local mirror
        # (withheld from remotes, never lost). Multi-destination is safe by
        # construction: paths are append-only and per-author.
        # #200: env/config/origin-derived logical path (segments are munged in
        # teamproject and can never escape the sidecar); None = flat era.
        segs = teamproject.resolve(project_dir)
        # Shallow copy is deliberate and sufficient: only top-level keys are
        # stamped below; nested structures are never mutated on this path.
        blob = dict(checkpoint)
        blob.setdefault("project_slug", project_slug(project_dir))
        if segs:
            blob.setdefault("team_project", "/".join(segs))
        payload = json.dumps(blob, indent=2, ensure_ascii=False)
        for slug in _team_write_slugs(project_dir):
            base = config.team_dir() / slug
            if segs:
                d = base.joinpath("projects", *segs, "authors", author_slug)
            else:
                d = base / "authors" / author_slug
            d.mkdir(parents=True, exist_ok=True)
            _atomic_write(d / f"{_safe_name(session_id)}.json", payload)
    except OSError:
        pass


def team_retention_cutoff() -> float | None:
    """Epoch floor for the team-view retention window (#113), or None when
    retention is disabled (DAIMON_TEAM_RETENTION_DAYS=0). The SINGLE source for
    every team-dir reader — read_team and the recall index (#120) must agree on
    which teammate checkpoints have aged out."""
    days = config.team_retention_days()
    return (time.time() - days * 86400) if days > 0 else None


def _team_author_dirs(remote: Path) -> list[Path]:
    """Every authors/<author-slug> dir under one remote, BOTH eras (#200):
    legacy flat authors/* plus nested projects/**/authors/* at any depth.
    Descent stops at each authors/ dir — author dirs hold only checkpoint
    files, never further layout. Pure file-ops, never raises."""
    out: list[Path] = []
    try:
        out.extend(p for p in (remote / "authors").iterdir() if p.is_dir())
    except OSError:
        pass
    for cur, dirnames, _files in os.walk(remote / "projects"):
        if Path(cur).name == "authors":
            out.extend(Path(cur) / name for name in sorted(dirnames))
            dirnames[:] = []
    return out


def read_team(project_dir=None) -> list[tuple[str, dict]]:
    """Newest checkpoint per author in the shared team dir, for the given project.

    Fan-in across every remote-slug, BOTH layout eras (#200) combined:
      - projects/<candidate…>/authors/* — the logical-path era, for EVERY
        candidate path (teamproject.read_candidates: the winning tier PLUS the
        lower tiers' paths when they differ — a repo mapped or env-overridden
        AFTER it synced keeps its earlier nested history readable, never
        orphaned). The path IS the project filter, so no stamp check is needed
        (and several repos mapped to one logical project share one read pool
        by construction).
      - authors/*                       — the legacy flat era, filtered by the
        stamped `project_slug` as always ("old flat sidecars stay readable
        forever; no migration")
    When no logical path resolves (teamproject tier 4), only the legacy era is
    read — exactly the pre-#200 behavior.

    Derive-at-read — there are no pointers to trust: the newest checkpoint per
    author is chosen by the #93 `created` stamp (file mtime fallback, via
    _file_recency), exactly as the local GC ranks files. Returns
    [(author, checkpoint), ...] newest-first by each author's newest checkpoint.

    Legacy project filter: only checkpoints whose stamped `project_slug` matches
    this project's slug. When the project is unknown (slug None) no filter applies.

    Retention (#113): checkpoints older than DAIMON_TEAM_RETENTION_DAYS (by the
    same _file_recency the ranking uses; 0 = keep all) are skipped AT READ TIME
    only — NO physical deletes, ever: the shared branch is append-only and
    deletes race appends (spike verdict).

    Inbound gate (#423): content from a synced remote passes
    policy.admit_foreign before it can reach the result — scope membership
    (the same teamproject.in_scope answer the outbound router uses, default
    closed), local re-redaction, the local forget tombstones, and the foreign
    verbatim->inferred trust clamp. In-memory only: sidecar files are never
    rewritten. The machine-local mirror ('local') is this machine's own
    writes and stays ungated, as do this author's own dual-written copies
    synced back through a clone (their verbatim claims ARE locally
    verifiable).

    Pure file-ops, never raises — a missing/broken/torn team dir yields []."""
    root = config.team_dir()
    want_slug = project_slug(project_dir)
    cutoff = team_retention_cutoff()
    candidates = teamproject.read_candidates(project_dir)
    # #600 slice B: a teammate's published tombstone suppresses their value
    # here too. Always on — suppression is not deletion, it costs a teammate
    # nothing but the sight of a value they asked to be forgotten, and their
    # own scrubbed file may not have reached this clone yet.
    forgotten = forgotten_content_keys(project_dir) | foreign_forgotten_content_keys()
    self_author = project_slug(config.author())
    # author-slug (dir identity, one per author) -> (recency, author, checkpoint)
    best: dict[str, tuple[float, str, dict]] = {}

    def _consider(adir: Path, check_stamp: bool, member) -> None:
        try:
            files = [p for p in adir.iterdir()
                     if p.is_file() and p.suffix == ".json"]
        except OSError:
            return
        for p in files:
            try:
                cp = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue  # torn/foreign file — skip, never crash the fan-in
            if not isinstance(cp, dict):
                continue
            if check_stamp and want_slug is not None \
                    and cp.get("project_slug") != want_slug:
                continue
            rec = _file_recency(p)
            if cutoff is not None and rec < cutoff:
                continue  # aged out of the read window; file stays on disk
            if member is not None \
                    and project_slug(str(cp.get("author") or adir.name)) \
                    != self_author:
                cp = policy.admit_foreign(
                    cp, member=member, forgotten_keys=forgotten,
                    redact_fn=redact.redact_text)
                if cp is None:
                    continue  # not admitted; the file stays on disk untouched
            key = adir.name
            if key not in best or rec > best[key][0]:
                best[key] = (rec, cp.get("author") or adir.name, cp)

    try:
        remotes = list(root.iterdir())
    except OSError:
        return []
    # #423 scope, mirroring _team_write_slugs: with a single synced clone the
    # DAIMON_TEAM_PROJECT env grant counts as explicit intent; with several it
    # cannot say which remote it means, so only each sidecar's toml answers.
    clones = [r for r in remotes if r.is_dir()
              and r.name != _TEAM_LOCAL_REMOTE and (r / ".git").exists()]
    honor_env = len(clones) <= 1
    for remote in remotes:
        # member=None -> the machine-local mirror, ungated; any other dir is
        # foreign-shaped and must earn admission (default closed, like #279).
        member = None if remote.name == _TEAM_LOCAL_REMOTE else \
            teamproject.in_scope(project_dir, remote, honor_env=honor_env)
        # Nested era (#200): only THIS project's subtrees — every candidate
        # path (winner + prior-tier locations); the paths filter.
        for segs in candidates:
            nested = remote.joinpath("projects", *segs, "authors")
            try:
                for adir in nested.iterdir():
                    _consider(adir, check_stamp=False, member=member)
            except OSError:
                pass  # no such subtree in this remote (yet)
        # Legacy flat era: stamp-filtered, readable forever.
        try:
            author_dirs = list((remote / "authors").iterdir())
        except OSError:
            continue  # not a remote-shaped dir; skip
        for adir in author_dirs:
            _consider(adir, check_stamp=True, member=member)
    ordered = sorted(best.values(), key=lambda t: t[0], reverse=True)
    return [(author, cp) for _rec, author, cp in ordered]


# ---- #102: append-only resolution events ----


def _events_path(project_dir=None):
    slug = project_slug(project_dir)
    if not slug:
        return None
    return config.checkpoint_dir() / slug / "events.jsonl"


def sessions_since_count(ts: str, project_dir=None) -> int:
    """Count of distinct non-introspection sessions that have serialized a
    checkpoint POINTER (latest.json / prev-N.json) for `project_dir` with a
    `created` stamp strictly AFTER `ts`.

    Extracted from `active_handoff`'s baton consumption count (#523,
    store.py — this WAS the inline block at its old line 1698-1721): a
    crashed session never serializes, so it never counts; an introspection
    checkpoint (a /daimon-end provisional, superseded by its own
    reconstruction) is excluded so one real session is never counted twice.
    Distinct SESSION IDS are the unit, not pointer files — rotation can
    leave more than one pointer from a single re-serialize or heal.
    Fail-open: an unreadable bucket, a torn pointer, or an empty `ts`
    contributes nothing, never raises.

    Shared by `active_handoff`'s 2-session threshold and #694 PR 3's
    request staleness derivation (D3) — the SAME counting shape at a
    caller-chosen threshold, never a hardcoded 2."""
    ts = str(ts or "")
    if not ts:
        return 0
    slug = project_slug(project_dir)
    if not slug:
        return 0
    d = config.checkpoint_dir() / slug
    try:
        entries = list(d.iterdir())
    except OSError:
        return 0
    consumers = set()
    for p in entries:
        if not _POINTER_RE.match(p.name):
            continue
        try:
            ck = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(ck, dict) or ck.get("source") == "introspection":
            continue
        if str(ck.get("created") or "") > ts and ck.get("session_id"):
            consumers.add(str(ck["session_id"]))
    return len(consumers)


def active_handoff(project_dir=None) -> dict | None:
    """The project's active baton (#523), or None.

    A handoff is an AUTHORED, ref-less `handoff` event — never a cognitive
    item, so it cannot enter ranking, dedup, carry or budget trimming, and
    (scar 0025) it must never name an `item_ref`: the resolutions fold
    ignores ref-less lines, so a baton can never resolve an item. Latest
    handoff-kind event wins; `status: cleared` (or an empty note) retracts.

    Consumption: the baton stays active until TWO distinct non-introspection
    sessions have serialized after its write — the first post-write
    checkpoint is the writer's own session end, the second belongs to the
    session that was briefed with it. A crashed session on either side never
    serializes, so it never eats the baton; under same-project concurrency
    the baton can over-survive one session, which is the safe direction for
    a baton. Introspection checkpoints are excluded: a /daimon-end
    provisional plus its superseding reconstruction would otherwise count
    one session twice and kill the baton before any consumer saw it.
    Fail-open: unreadable files read as "no baton", never an exception."""
    path = _events_path(project_dir)
    if path is None or not path.exists():
        return None
    latest = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (not isinstance(evt, dict) or evt.get("kind") != "handoff"
                    or evt.get("item_ref")):
                continue
            if latest is None or (str(evt.get("ts") or "")
                                  >= str(latest.get("ts") or "")):
                latest = evt
    except OSError:
        return None
    if (latest is None or latest.get("status") == "cleared"
            or not str(latest.get("note") or "").strip()):
        return None
    ts = str(latest.get("ts") or "")
    if sessions_since_count(ts, project_dir) >= 2:
        return None
    return {"ts": ts, "note": str(latest["note"])}


def _ledger_path(project_dir=None):
    slug = project_slug(project_dir)
    if not slug:
        return None
    return config.checkpoint_dir() / slug / "verification.jsonl"


def append_verification(item_ref: str, check: str, reason: str,
                        project_dir=None) -> bool:
    """One appended line per REJECTION the checker made (#376): a verbatim
    quote that missed the transcript, an outcome claim with no signal cited.

    Deliberately a SECOND stream, not an `events.jsonl` row with a new `kind`.
    `resolutions()` folds events on `item_ref` alone and never inspects
    `kind`, and `is_resolved()` resolves any status outside reopen/
    supersede-candidate — so a rejection written there would HIDE the very
    item it describes, from the briefing, from carry and from recall. A
    downgraded item must stay visible and merely read as inferred.

    Stores a POINTER and a REASON CODE, never the rejected text: quote
    verification runs pre-redaction (#141), so the raw item text must not
    reach a log sink. `check` and `reason` are scrubbed anyway (defence in
    depth — a caller could pass something secret-shaped).

    Same never-fatal contract as append_event: silent no-op under the kill
    switch or an unknown project, False on OSError. A ledger write must
    never fail a capture."""
    if config.is_disabled():
        return False
    path = _ledger_path(project_dir)
    if path is None:
        return False
    try:
        # #431: the scrub runs through policy.admit_row — same redaction as
        # before, but mounted on the policy seam so the write-audit guard can
        # correlate the row on disk with its admission.
        row = policy.admit_row(
            {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "check": check, "item_ref": item_ref, "reason": reason},
            redact_fields=("check", "reason"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def verification_counts(project_dir=None) -> dict:
    """{check: count} over the rejection ledger. Answers the question the
    trust classes otherwise cannot: has verification ever caught anything on
    THIS install. Fails open to {} (missing/corrupt log, unknown project) —
    same posture as the resolutions fold."""
    out: dict = {}
    path = _ledger_path(project_dir)
    if path is None:
        return out
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        check = str(row.get("check") or "")
        if check:
            out[check] = out.get(check, 0) + 1
    return out


# ---- #404: forget-suppression hit accounting ----

# A capture-time forget suppression is otherwise silent. Account it on a
# SECOND telemetry stream (never events.jsonl: scar 0025 — any new event kind
# there resolves-and-hides the item it names), exactly as append_verification
# does for the rejection ledger.
_FORGET_HITS = "forget-hits.jsonl"


def _forget_hits_path(project_dir=None):
    slug = project_slug(project_dir)
    if not slug:
        return None
    return config.checkpoint_dir() / slug / _FORGET_HITS


def record_forget_hits(items, project_dir=None, reason: str = "") -> bool:
    """Append one row per capture-time forget suppression (#404): {ts, key}.
    Mirrors append_verification's contract — silent no-op under the kill switch
    or an unknown project, never fatal (a telemetry write must never fail a
    capture).

    `reason` (#693): a non-empty value stamps each row and routes it to its
    own counter in forget_hit_stats — a ruling-echo drop is a different
    measurement from a forget suppression, and folding them together would
    inflate the number the project already publishes.

    Records ONLY the canonical hash key + timestamp — NEVER the text or any
    prefix of it. forget's whole guarantee (#321) is that a forgotten value's
    content leaves disk; redact_text catches known secret shapes but not the
    free-text PII users actually forget (a name, a client, "the X office
    closed"), so re-persisting even a short snapshot here would reopen the very
    leak forget closes. The published value is the COUNT, not the content."""
    if config.is_disabled():
        return False
    path = _forget_hits_path(project_dir)
    if path is None or not items:
        return False
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = normalize.content_key(item.get("text") or "")
                row = {"ts": ts, "key": key}
                if reason:
                    row["reason"] = reason
                f.write(json.dumps(row) + "\n")
        return True
    except OSError:
        return False


def forget_hit_stats(project_dir=None) -> dict:
    """Read-side rollup of the forget-hit ledger (#404): total suppressions and
    the most recent timestamp. The number the project already publishes for
    capture health and verification downgrades, now answerable for "how often
    did the tombstone catch a re-assertion here". Count + timestamp only — the
    ledger holds no content to surface. Fails open to zeroes (missing/corrupt
    log, unknown project) — same posture as verification_counts and the
    resolutions fold.

    Reason-stamped rows (#693) count under their own key AND their own
    timestamp — `ruling-echo` rows land in `ruling_echo_count` /
    `ruling_echo_last_at`, never `count` / `last_hit_at`, so the published
    forget-suppression number and the stamp printed beside it both stay
    what they always measured."""
    out: dict = {"count": 0, "ruling_echo_count": 0, "last_hit_at": None,
                 "ruling_echo_last_at": None}
    path = _forget_hits_path(project_dir)
    if path is None:
        return out
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("reason") == "ruling-echo":
            count_key, ts_key = "ruling_echo_count", "ruling_echo_last_at"
        else:
            count_key, ts_key = "count", "last_hit_at"
        out[count_key] += 1
        ts = row.get("ts")
        if ts and (out[ts_key] is None or ts > out[ts_key]):
            out[ts_key] = ts
    return out


# Event-ledger `source` values whose authority tier is HUMAN. Declared here,
# beside the writer that stamps the field, so consumers derive one view of
# "a person decided this" instead of each hardcoding a literal — the same
# single-declaration shape surfaces.py uses for delete strategies.
#
# `ui` is a peer of `cli`, not a replacement: the ledger is append-only and
# every row already written says `cli`, so the set widens and never renames.
# It matches refutations.CHANNEL_AUTHORITY["ui"] == "human", which is the
# same claim in the refutation ledger's own vocabulary. The two vocabularies
# stay separate on purpose — that ledger's channels distinguish `cli-tty`
# from `cli-agent`, a split this ledger expresses through `source="agent"`.
#
# Widening this set widens WHO counts, never WHAT counts: callers must still
# filter on `kind` themselves (scar 0025 — kind never isolates a fold on its
# own), or forget's tombstones and log's freeform rows ride in as decisions.
HUMAN_EVENT_SOURCES = frozenset({"cli", "ui"})


def append_event(item_ref: str, status: str, note: str = "",
                 kind: str = "resolution", source: str = "cli",
                 project_dir=None, item_text: str = "",
                 allow_disabled: bool = False) -> bool:
    """One appended JSON line per lifecycle fact (#102). Append-only: the
    file is never rewritten — resolution is a derivation at read, so the
    audit trail must stay byte-stable. The ONE exception is
    scrub_event_fields (#599), forget's redaction-in-place, which replaces a
    field VALUE but never drops, adds, or reorders rows. Silent no-op under
    the kill switch
    and when the project is unknown (an event without a bucket has no
    reader). `allow_disabled` (#421) is the narrow deletion exemption:
    passed ONLY by cli._cmd_forget's tombstone append — forget must work
    while disabled, and #418 mandates its tombstone lands before the
    rewrite, so the tombstone shares the rewrite's exemption. No other
    caller may pass it."""
    if config.is_disabled() and not allow_disabled:
        return False
    path = _events_path(project_dir)
    if path is None:
        return False
    try:
        # #431: the scrub runs through policy.admit_row — same redaction as
        # before (status is free-form by design, readers prefix-match, so it
        # can carry a secret-shaped value and is scrubbed too, #141), but
        # mounted on the policy seam so the write-audit guard can correlate
        # the row on disk with its admission.
        evt = policy.admit_row(
            {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "kind": kind, "item_ref": item_ref, "status": status,
             "source": source, "note": note, "item_text": item_text},
            redact_fields=("status", "note", "item_text"))
        # Empty optional fields never land on the row — unchanged shape.
        if not evt["note"]:
            del evt["note"]
        if not evt["item_text"]:
            del evt["item_text"]
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


# The in-place redaction marker for event fields (#599). Carries the
# tombstoned key so the row stays auditable — and a hash can never collide
# with the plaintext it names, so re-running the scrub is idempotent.
_FORGOTTEN_FIELD_MARKER = "[forgotten:{}]"

# Every status reader classifies by PREFIX (is_resolved, _tie_rank, _demotes,
# recall's fold) — so a scrubbed status must keep its class token or the
# redaction re-classifies the row: a revival whose free-form wording IS the
# forgotten sentence would fold back to "resolved" and hide an unrelated
# item forever. Longest-match order: candidate forms before their shorter
# cousins. The kept token is generic lifecycle vocabulary, not the value.
_STATUS_CLASS_PREFIXES = ("supersede-candidate", "superseded-by:",
                          "resolving-candidate", "reopen", "forgotten")


def _class_preserving_marker(status: str, marker: str) -> str:
    low = status.lower()
    for prefix in _STATUS_CLASS_PREFIXES:
        if low.startswith(prefix):
            return f"{status[:len(prefix)]} {marker}"
    return marker


def scrub_event_fields(content_hash: str, project_dir=None) -> int:
    """#599: the narrow carve-out to append_event's append-only contract,
    called ONLY by cli._cmd_forget after the tombstone lands. events.jsonl
    rows written BEFORE a forget can carry the value in `item_text` (resolve/
    reopen pass the item's full text), free-form `status`, or `note` — and an
    append-only file is otherwise unreachable by deletion (#419: holding
    plaintext is what puts a file inside the contract).

    Redaction-in-place, never row removal: the resolutions fold keys on
    `item_ref`/`status`/row order (scar 0025 — a dropped row changes what
    counts as resolved), so only a field whose WHOLE value folds to the
    tombstoned key is replaced with the visible marker; every other byte of
    every row survives verbatim, and uninterpretable lines are copied through
    untouched. Best-effort like scrub_content_key: an unreadable or
    unwritable ledger returns 0 rather than aborting the forget. Known
    window: an append racing the read→rename pair is lost. A lock exists
    (_pointer_lock) but is deliberately not taken — append_event locks
    nothing, so locking only this side closes nothing, and adding a lock to
    every append buys a per-event cost for a window one rare interactive
    command opens. The atomic replace keeps the file parseable either way.

    Accepted residuals (adversarial review, #599): (1) a same-second tie
    (_tie_wins) involving a scrubbed row can flip its content tie-break —
    class rank is preserved, only the arbitrary-but-deterministic byte
    comparison moves, and content removal cannot leave content unchanged;
    (2) briefing.withhold's legacy fuzzy pool loses entries whose item_text
    was scrubbed, so an id-less PARAPHRASE of the value may resurface —
    that suppression only ever worked because this plaintext residue
    existed, and keeping it would mean keeping the value.
    Returns the number of rows redacted."""
    if not content_hash:
        return 0
    path = _events_path(project_dir)
    if path is None:
        return 0
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    marker = _FORGOTTEN_FIELD_MARKER.format(content_hash)
    out_lines = []
    scrubbed = 0
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            out_lines.append(line)
            continue
        if not isinstance(row, dict):
            out_lines.append(line)
            continue
        changed = False
        for field in ("item_text", "status", "note"):
            value = row.get(field)
            if (isinstance(value, str) and value
                    and normalize.content_key(value) == content_hash):
                row[field] = (_class_preserving_marker(value, marker)
                              if field == "status" else marker)
                changed = True
        if changed:
            scrubbed += 1
            # Same admission seam the append took (#431): the rewrite is
            # governed for real, not merely correlated — the write-audit
            # guard can bind the row that lands on disk to this admission,
            # and any secret shape the row carried pre-#141 is re-scrubbed.
            row = policy.admit_row(row, redact_fields=("status", "note",
                                                       "item_text"))
            out_lines.append(json.dumps(row, ensure_ascii=False))
        else:
            out_lines.append(line)
    if not scrubbed:
        return 0
    try:
        _atomic_write(path, "\n".join(out_lines) + "\n")
    except OSError:
        return 0
    return scrubbed


def _tie_rank(evt: dict) -> int:
    """Same-second precedence (#143), from event content only. reopen beats
    resolving: when order is unknowable the item stays visible — hiding a
    live item costs more than showing a resolved one. supersede-candidate and
    resolving-candidate (#480 slice 2) lose to everything: a machine
    SUGGESTION must never shadow a same-second definitive statement (mirrors
    is_resolved's no-suppression rule)."""
    status = str(evt.get("status") or "").lower()
    if status.startswith(("supersede-candidate", "resolving-candidate")):
        return 0
    if status.startswith("reopen"):
        return 2
    return 1


def _tie_wins(new_evt: dict, cur_evt: dict) -> bool:
    """Same-second tie rule (#143), derived from event CONTENT only so the
    fold is identical under any line order (concurrent writers interleave;
    a future log rewrite may reorder). Higher _tie_rank wins; equal ranks
    fall to canonical-JSON comparison: arbitrary, but deterministic."""
    new_r, cur_r = _tie_rank(new_evt), _tie_rank(cur_evt)
    if new_r != cur_r:
        return new_r > cur_r
    return (json.dumps(new_evt, sort_keys=True, ensure_ascii=False)
            > json.dumps(cur_evt, sort_keys=True, ensure_ascii=False))


def resolutions(project_dir=None) -> dict:
    """events.jsonl -> {item_ref: latest event} — latest by ts, NEVER line
    order (concurrent writers may interleave, and a rewritten/reordered log
    must fold identically). Equal-ts ties break on content via _tie_wins
    (#143): reopen > resolving > supersede-candidate, then canonical-JSON
    order — never the line's position in the file. Unknown
    kinds and extra fields ride along untouched; unparseable lines are
    skipped best-effort: a reader must never drop the log over one bad
    line."""
    out: dict = {}
    path = _events_path(project_dir)
    if path is None:
        return out
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
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
        if cur is None:
            out[ref] = evt
            continue
        new_e = _created_epoch(evt.get("ts"))
        if new_e is None:
            continue  # an unstamped event never displaces a stamped one
        cur_e = _created_epoch(cur.get("ts"))
        if (cur_e is None or new_e > cur_e
                or (new_e == cur_e and _tie_wins(evt, cur))):
            out[ref] = evt
    return out


def is_resolved(event) -> bool:
    """Liveness rule (#102, #14): latest event wins; three states — a status
    starting with 'reopen' returns the item to live; 'supersede-candidate',
    'corroborat*', and 'resolving-candidate' (#480 slice 2) are non-resolving
    by construction (see below); anything else means resolved. Status is
    free-form text by design — never an enum, so unknown statuses resolve
    (the writer bothered to record a lifecycle fact) rather than vanish."""
    if not isinstance(event, dict):
        return False
    status = str(event.get("status") or "").lower()
    if status.startswith(("supersede-candidate", "corroborat", "resolving-candidate")):
        return False  # a machine SUGGESTION is live by construction (#14):
                      # every consumer (carry, withhold, future) inherits
                      # no-suppression without knowing candidates exist.
                      # #268 corroboration is the same shape one step
                      # further: a row that RAISES trust must never gain the
                      # power to lower it. Corroboration rows already land on
                      # a namespaced ref (corroboration_ref) that no item id
                      # can equal, so this branch is the belt to that brace —
                      # scar 0025 (any event kind on a bare ref hides its
                      # item) is too expensive a failure to guard once.
                      # #480 slice 2: resolving-candidate is the SAME shape —
                      # an agent's unverified claim — one status further; it
                      # withholds only once slice 3's serialize-time
                      # verification (or a human) writes the next event.
    return not status.startswith("reopen")


# ---- #268: corroboration events ----

# A corroboration row NAMES an item without addressing it. `resolutions` folds
# on `item_ref` alone and `is_resolved` resolves any status outside reopen/
# supersede-candidate/corroborat* (scar 0025), so a row written on the BARE
# item id would hide the very item it supports — and would displace a human's
# superseded-by verdict as that item's latest event (the #376 trap). The
# namespace is what makes both structurally impossible: no id this codebase
# mints can contain a colon (see _stamp_item_ids), so no item ref can ever
# collide with one of these.
_CORROBORATION_PREFIX = "corroboration:"
_CORROBORATED_BY = "corroborated-by:"


def corroboration_ref(item_id: str) -> str:
    """The event ref a corroboration row for `item_id` lands on. One literal,
    shared by the emitter (capture._emit_corroborations) and the reader."""
    return f"{_CORROBORATION_PREFIX}{item_id}"


def _demotes(evt: dict, status: str) -> bool:
    """Does this lifecycle row CONTRADICT the item it names (#268)?

    Everything `is_resolved` calls resolved (a closed loop, a human's
    superseded-by, a `forgotten:` tombstone, any free-form lifecycle fact),
    plus supersede-candidate — the one status is_resolved deliberately keeps
    live. A machine's supersession guess is not strong enough to HIDE an
    item, but it is a standing contradiction, and corroboration is trust
    going up: the bar for discounting it is lower than the bar for hiding.

    `reopen*` is absent on purpose. Reviving an item does not revive the
    agreement it lost — a witness has to speak again."""
    return is_resolved(evt) or status.startswith("supersede-candidate")


def corroborations(project_dir=None) -> dict:
    """events.jsonl -> {bare item id: {origins, recorded, latest_demotion_ts}}
    (#268 slice 3).

    A FULL pass, not the latest-wins `resolutions` fold: every witness counts,
    so the newest row can never erase an older session's independent
    agreement. Callers index by the item's own id — the namespace above is an
    on-disk detail no reader has to know about.

      origins  — the sessions whose corroboration currently COUNTS: recorded
                 strictly after the latest contradiction. This is the number
                 a render may show.
      recorded — every session that ever wrote a row for this item, whatever
                 happened afterwards. Idempotency binds HERE, never to
                 `origins`: were the emitter to key on what currently counts,
                 a demotion would let the same session re-emit and re-earn its
                 own corroboration with no new evidence.
      latest_demotion_ts — the contradiction `origins` was measured against,
                 or None. Kept visible so a reader can say WHY a recorded
                 witness stopped counting.

    Items with no corroboration row are absent: this fold answers "what has
    been corroborated", not "what has been resolved". Timestamps compare as
    strings — every writer stamps the same fixed-width UTC format, so
    lexicographic order IS chronological (the idiom forget_hit_stats already
    uses), and an unstamped row sorts oldest, never displacing a stamped one
    (the same posture `resolutions` takes). Fails open to {} on a missing,
    unreadable or corrupt log; unparseable lines are skipped best-effort."""
    out: dict = {}
    path = _events_path(project_dir)
    if path is None:
        return out
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return out
    witnesses: dict = {}   # item id -> {observing session: latest row ts}
    demotions: dict = {}   # item id -> latest contradicting row ts
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
        status = str(evt.get("status") or "")
        # Prefix reads are case-insensitive like every other status reader
        # here; the PAYLOAD after the prefix is sliced off the raw status —
        # a session id is case-sensitive and must survive verbatim.
        lowered = status.lower()
        ts = str(evt.get("ts") or "")
        if ref.startswith(_CORROBORATION_PREFIX):
            item_id = ref[len(_CORROBORATION_PREFIX):]
            observer = (status[len(_CORROBORATED_BY):].strip()
                        if lowered.startswith(_CORROBORATED_BY) else "")
            if not item_id or not observer:
                continue  # malformed — a row that names no item or no witness
            seen = witnesses.setdefault(item_id, {})
            if ts > seen.get(observer, ""):
                seen[observer] = ts
        elif _demotes(evt, lowered) and ts > demotions.get(ref, ""):
            demotions[ref] = ts
    for item_id, seen in witnesses.items():
        demoted = demotions.get(item_id)
        out[item_id] = {
            "origins": {sid for sid, ts in seen.items()
                        if demoted is None or ts > demoted},
            "recorded": set(seen),
            "latest_demotion_ts": demoted,
        }
    return out


# ---- #402: value-keyed forget suppression ----

# `forgotten:` status prefix -> canonical content key. The forget command
# (cli._cmd_forget) writes `forgotten:<normalize.content_key(text)>`; this
# derives the live set of tombstoned values at read time.
_FORGOTTEN_PREFIX = "forgotten:"


def forgotten_content_keys(project_dir=None) -> set[str]:
    """The set of canonical content keys the forget ledger has tombstoned for
    this project (#402). Derived at read time from the `forgotten:` events — no
    new store surface. Scoped GLOBALLY per project and keyed on the canonical
    VALUE only (never a subject/predicate/scope tuple): free-text items have no
    such tuple, and per-key scoping would let the same value be re-asserted
    under a different framing. Only the LATEST event per ref counts, so a later
    `reopen` lifts the tombstone (same fold recall/withhold use). Fails open to
    an empty set (missing/corrupt log, unknown project)."""
    keys: set[str] = set()
    for evt in resolutions(project_dir=project_dir).values():
        status = str(evt.get("status") or "")
        if status.lower().startswith(_FORGOTTEN_PREFIX):
            key = status[len(_FORGOTTEN_PREFIX):].strip()
            if key:
                keys.add(key)
    return keys


def all_forgotten_content_keys() -> set[str]:
    """Union of EVERY local project's forget tombstones (#423). The recall
    index is machine-global and a foreign checkpoint cannot name the local
    project dir its content corresponds to, so the inbound forget gate
    suppresses a value forgotten in ANY local project. Over-suppression is
    the fail-safe direction (drop_forgotten's documented posture) — a
    forgotten value re-surfacing via a teammate is the worse failure.
    Never raises; degrades to the empty set."""
    keys: set[str] = set()
    try:
        children = list(config.checkpoint_dir().iterdir())
    except OSError:
        return keys
    for child in children:
        # Bucket dirs are named by slug; project_slug is idempotent on slugs,
        # so the name rides through the project_dir-shaped ledger API.
        if child.is_dir():
            keys |= forgotten_content_keys(child.name)
    return keys


# #421: the pure splice half of the gate moved to policy.drop_forgotten;
# write_checkpoint injects forgotten_content_keys(project_dir) (the ledger
# read above — the one I/O half that stays in the store).
