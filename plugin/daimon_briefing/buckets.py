"""One-shot bucket migration across the 0.42.0 resolution change (#963).

THE DEFECT. Before 0.42.0 the library slugged the LITERAL project path it was
handed: `store.project_slug(project_dir)`, a pure character transform over the
string. Since #951/#957 every entry point resolves first
(`config.resolve_project_dir`: absolute, symlinks collapsed, normalized to the
git toplevel) and slugs THAT. For a path carrying a symlink component (on
macOS, anything reached through `/tmp`) or a path below a git toplevel, the two
rules name different directories. A bucket written by the library at 0.41.0
from such a path therefore sits under a name nothing reads: `ruling list`
reports no bucket at exit 1 while the old directory still holds the rulings.

WHY NOT A FALLBACK READ. The obvious fix is to check the literal slug on a
resolved-slug miss. It re-orphans the same rows the moment the first 0.42.0
write creates the resolved bucket: from then on the read hits, the fallback
never runs, and half the history is in the other directory. A fallback also
has to answer "which bucket did I read" on every surface forever. Moving the
bucket once answers it once, and the receipt below is what the alias-aware
readers join on afterwards.

`daimon slug` stays pinned to the literal rule (#913) and is untouched: it
answers for a path daimon has never seen, with no filesystem and no git.
"""

import hashlib
import json
import os
import time
from pathlib import Path

from . import config, store


class MigrationError(RuntimeError):
    """The move happened and the receipt did not — the one state a caller
    must be told about, because it is the one that loses reachability."""


MIGRATIONS_NAME = "migrations.jsonl"
RECORD_VERSION = 1

# Every JSONL ledger that lives inside a bucket. Kept as a literal tuple
# rather than derived from `surfaces.SURFACES`: the registry declares FILE
# SHAPES with deletion contracts, and a merge needs the narrower fact "this
# file is an append-only ledger whose fold is order-tolerant", which is a
# property of the three folds (refutations.fold and amendments.fold both sort
# by an explicit key; store.latest_receipt_verdicts / store.resolutions are
# latest-by-ts, never line order). A shape added to the registry must be
# considered here deliberately, and the test pins the two lists against each
# other so a new bucket ledger cannot land unnoticed.
LEDGERS = (
    "events.jsonl",
    "refutations.jsonl",
    "amendments.jsonl",
    "requests.jsonl",
    "verification.jsonl",
    "forget-hits.jsonl",
    "relations.jsonl",
)


# Files a merge may DELETE from the legacy bucket even though they are not
# ledgers. `.pointer.lock` is store._pointer_lock's flock sidecar: opened
# "a+", never written, declared in surfaces.py as exempt-no-plaintext because
# it holds nothing by construction. Every bucket the store has ever written to
# has one, so treating it as an unrecognized leftover kept the legacy
# directory alive forever and made a second run merge and receipt again
# (#963 review). Nothing else is ever removed on this list's word.
_REMOVABLE = frozenset({store._LOCK_NAME})


def climbs_out(project_dir) -> bool:
    """Whether the raw value contains a lexical `..` component.

    The ONE vector by which the two slug rules can name different DIRECTORIES
    rather than different names for one directory. `legacy_slug` uses
    `os.path.abspath`, which collapses `..` lexically, BEFORE resolving any
    symlink. `target_slug` resolves symlinks FIRST and applies `..` after. So
    `<root>/me/link/../../tenantB/proj`, where `link` points into
    `<root>/me/a/b/c`, has abspath naming tenantB's directory and resolve
    naming one under the caller's own tree. A migration would then rename the
    victim's bucket into the caller's and mint a permanent alias row for it.

    A symlink alone is the legitimate case #963 exists for and stays allowed:
    there the two rules name two NAMES for the same directory, which is the
    whole premise of the move.
    """
    text = str(project_dir or "")
    if not text:
        return False
    if os.altsep:
        text = text.replace(os.altsep, os.sep)
    return ".." in text.split(os.sep)


def _refuse_climbing(project_dir) -> None:
    if climbs_out(project_dir):
        raise MigrationError(
            "the pre-0.42.0 bucket rule cannot be applied to a path with a "
            "'..' component: that rule collapses '..' before following a "
            "symlink and the current one follows the symlink first, so the "
            "two name different directories. A bucket written before 0.42.0, "
            "if there is one, was written under the literal slug of the "
            "COLLAPSED path, so pass that path.")


def legacy_slug(project_dir) -> str | None:
    """The bucket name the LIBRARY derived for `project_dir` before 0.42.0.

    Transcribed from v0.41.0, where the ledgers called
    `store.project_slug(project_dir)` on the value the host handed them, with
    no `realpath` and no git walk. `os.path.abspath(os.path.expanduser(...))`
    is added in front for the one case the raw transform could not name at
    all: a relative value, which at 0.41.0 slugged to a bucket derived from
    the relative string itself. abspath is identity on an already-absolute
    path, so this reproduces the shipped rule exactly for every path that
    actually wrote a bucket, and the test pins that byte for byte.

    abspath NEVER collapses a symlink, and that is the whole point: the
    difference between this and `store.project_bucket` is exactly the set of
    paths #963 orphaned. It also never returns a bare bucket name, which is
    what keeps a slug-shaped `--project` from addressing a foreign bucket
    (scar 0071).
    """
    if not project_dir:
        return None
    text = str(project_dir).strip()
    if not text:
        return None
    return store.project_slug(os.path.abspath(os.path.expanduser(text)))


def target_slug(project_dir) -> str | None:
    """The bucket name this daimon uses for `project_dir`.

    `allow_slug=False`, deliberately: `--project` is documented as a PATH, and
    the slug passthrough exists for the bucket-iterating readers that hand a
    slug in as a project. Letting it through here would let a caller name a
    bucket by copying its directory name, which is the reach #899's tenant
    mode and scar 0071 exist to remove.
    """
    return store.project_slug(
        config.resolve_project_dir(project_dir, allow_slug=False))


def migrations_path() -> Path:
    return config.checkpoint_dir() / MIGRATIONS_NAME


def records() -> list[dict]:
    """Every migration receipt, oldest first. Empty when the file is absent.

    Torn lines are skipped, never fatal: this file is read from `status`,
    `recall`'s rebuild and the requests inbox join, and a half-written line
    must not take a reporting surface down."""
    try:
        text = migrations_path().read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("from_slug") and row.get("to_slug"):
            out.append(row)
    return out


def _is_complete(row: dict) -> bool:
    """Whether a receipt row claims the legacy bucket was fully absorbed.

    Rows written before this field existed have no `complete` key. They are
    read as complete: at the time they were written the verb removed the
    legacy directory or reported leftovers, and treating an older row as
    incomplete would silently drop an alias somebody's history depends on."""
    return bool(row.get("complete", True))


def latest_rows() -> dict[tuple, dict]:
    """{(from_slug, to_slug): the LAST row for that pair}.

    The receipt file is append-only, so one migration can carry several rows:
    a partial move, then the run that finishes it. Every reader wants the
    current answer for a pair, which is the last row written for it."""
    out: dict[tuple, dict] = {}
    for row in records():
        out[(str(row["from_slug"]), str(row["to_slug"]))] = row
    return out


def _edges() -> dict[str, str]:
    """The alias graph, built from COMPLETE rows only.

    An alias means "that bucket's history lives here now". A partial move has
    not made that true, and acting on it is worse than having no alias at
    all: `requests.recipient_join` skips any bucket in its own identity set,
    so an alias minted over a still-populated legacy bucket stops that bucket
    being scanned and its asks leave the inbox entirely (#963 review)."""
    return {pair[0]: pair[1] for pair, row in latest_rows().items()
            if _is_complete(row)}


def incomplete_for(slug) -> tuple[dict, ...]:
    """Receipt rows for `slug` that did NOT finish, newest last. `status`
    renders a `partial:` line off this: a migration that stopped half way is
    a state a person has to finish, not one to leave quietly on disk."""
    if not slug:
        return ()
    return tuple(row for pair, row in latest_rows().items()
                 if pair[1] == slug and not _is_complete(row))


def alias_map() -> dict[str, str]:
    """{legacy slug: the bucket it ends up in}, chains followed to the end.

    A cycle terminates at the point it closes rather than raising: the file is
    append-only and hand-editable, and a reader that dies on a malformed
    history is worse than one that answers something defensible."""
    edges = _edges()
    out: dict[str, str] = {}
    for start in edges:
        seen = {start}
        node = edges[start]
        while node in edges and edges[node] not in seen:
            seen.add(node)
            node = edges[node]
        out[start] = node
    return out


def aliases_for(slug) -> frozenset[str]:
    """Every legacy slug whose migration chain reaches `slug`."""
    if not slug:
        return frozenset()
    return frozenset(src for src, dest in alias_map().items()
                     if dest == slug and src != slug)


def alias_provenance(slug) -> tuple[dict, ...]:
    """({"slug": <legacy>, "ts": <stamp>}, ...) for `slug`, oldest first — the
    provenance line `daimon status` renders. Separate from `aliases_for`
    because that answers a membership question every read surface asks, and
    this carries the display fact only one surface needs."""
    wanted = aliases_for(slug)
    out = [{"slug": str(r["from_slug"]), "ts": str(r.get("ts") or "")}
           for r in records() if str(r["from_slug"]) in wanted]
    return tuple(sorted(out, key=lambda r: (r["ts"], r["slug"])))


def legacy_bucket(project_dir) -> str | None:
    """The orphaned pre-0.42.0 bucket for `project_dir`, or None.

    None in three silent cases: the path climbs with `..` (see `climbs_out` —
    `status` and `ruling list` state a legacy bucket as a FACT, and for such a
    path the fact would be about somebody else's bucket), the two rules agree
    on this path (nothing was ever orphaned), or the legacy directory is
    simply not there. Only a directory that EXISTS is reported, so a surface
    calling this can say "a legacy bucket exists" without hedging."""
    if climbs_out(project_dir):
        return None
    legacy = legacy_slug(project_dir)
    target = target_slug(project_dir)
    if not legacy or not target or legacy == target:
        return None
    return legacy if (config.checkpoint_dir() / legacy).is_dir() else None


# ---------------------------------------------------------------------------
# the move
# ---------------------------------------------------------------------------


def _read_lines(path: Path) -> tuple[list[str], bool]:
    """(non-empty lines, whether the file was read in full).

    The second half is load bearing and used to be thrown away. A ledger
    holding a byte sequence that is not UTF-8 raises `UnicodeDecodeError`,
    which is a `ValueError`; swallowing that into an empty list makes the
    merge below believe there was nothing to move, so it appends nothing,
    finds nothing missing from the target, and UNLINKS the source. The whole
    file is destroyed, the receipt says zero lines, and the exit code says
    success. A file that cannot be read cannot be compared, so it cannot be
    proven safe to delete."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [], False
    except ValueError:  # UnicodeDecodeError is one
        return [], False
    return [ln for ln in text.splitlines() if ln.strip()], True


def _lines(path: Path) -> list[str]:
    """The lines only, for the target side where an unreadable file is not a
    deletion decision: an unreadable target simply holds nothing this merge
    can prove is already there, so every legacy line is appended."""
    return _read_lines(path)[0]


def _append_lines(path: Path, lines: list[str]) -> None:
    """Append `lines`, healing a torn tail first — the same shape every ledger
    appender in this package uses."""
    try:
        tail = path.read_bytes()[-1:] if path.exists() else b"\n"
    except OSError:
        tail = b"\n"
    with path.open("a", encoding="utf-8") as handle:
        if tail not in (b"", b"\n"):
            handle.write("\n")
        for line in lines:
            handle.write(line + "\n")


def legacy_leftovers(project_dir) -> tuple[str, ...]:
    """What the legacy bucket holds that a migration will NOT move.

    A merge that could not empty the directory leaves it standing on purpose,
    and the `legacy:` warning then fires forever, which is correct: something
    IS still there. The warning has to name it, or the reader is told to run a
    verb that will keep saying the same thing.

    Only the files the verb will not touch are named. A bucket nobody has
    migrated yet holds ledgers and pointers, which is exactly what the verb
    moves; reporting those as things daimon will not move is false, and it
    sends a reader deleting their own history by hand."""
    legacy = legacy_bucket(project_dir)
    if not legacy:
        return ()
    d = config.checkpoint_dir() / legacy
    # A pointer left behind AFTER a migration was attempted is stranded: the
    # chain was full, so the verb will not move it however often it is run.
    # The same file before any attempt is simply pending, and naming it would
    # send a reader deleting a pointer the next run would have absorbed.
    attempted = any(pair[0] == legacy and not _is_complete(row)
                    for pair, row in latest_rows().items())
    out = []
    for name in _leftovers(d):
        if name in _REMOVABLE:
            continue  # the verb deletes this one
        if store._POINTER_RE.match(name) and not attempted:
            continue  # pending, not stranded
        if name in LEDGERS and _read_lines(d / name)[1]:
            continue  # a READABLE ledger is exactly what the verb moves
        out.append(name)
    return tuple(out)


def _pointer_files(d: Path) -> list[Path]:
    try:
        return sorted(p for p in d.iterdir()
                      if p.is_file() and store._POINTER_RE.match(p.name))
    except OSError:
        return []


def _pointer_payload(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _restamp(path: Path, legacy: str, target: str) -> None:
    """Point a MOVED pointer copy's `project_slug` at the bucket it now lives
    in. Only a copy still stamped with the legacy slug is touched, and only
    inside a bucket: the flat per-session files are receipt-signed over their
    exact bytes (receipts.mint hashes the blob store wrote), so editing one
    would break `daimon verify-receipt` for a session that did nothing
    wrong. Recall reaches those through the alias instead."""
    payload = _pointer_payload(path)
    if payload is None or payload.get("project_slug") != legacy:
        return
    payload["project_slug"] = target
    store._atomic_write(path, json.dumps(payload, ensure_ascii=False))


def _pointer_identity(payload: dict) -> str:
    """What makes two pointer copies the SAME capture: the session id.

    `session_id` is a code-owned envelope field (`field_table.ENVELOPE_RULES`,
    presence-validated, assigned by the serialize pipeline after model output
    is stripped), and `store._pointer_stems` already reads it off pointer
    files to protect those sessions from GC. It is the field that answers
    "same capture?" and it is the one used here.

    The content hash below is the FALLBACK, and only for a blob damaged or
    old enough to carry no session id. It cannot be the primary key: a
    checkpoint for one session does not stay byte-identical across buckets.
    `_stamp_first_seen`, an anchor rewrite, a receipts stamp and this
    module's own `_restamp` all change a field, so the legacy copy and the
    evolved target copy hash differently, both take a slot, and a genuinely
    distinct session falls off the end of a fixed-length chain (#963 review).

    `project_slug` and `project_name` are excluded from the fallback hash
    because they name the BUCKET, not the checkpoint, and a migration
    rewrites them."""
    session_id = payload.get("session_id")
    if isinstance(session_id, str) and session_id.strip():
        return f"sid:{session_id.strip()}"
    body = {k: v for k, v in payload.items()
            if k not in ("project_slug", "project_name")}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False,
                           default=str)
    return "blob:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _pointer_label(payload: dict) -> str:
    """How a dropped pointer is named to a human: its session id when it has
    one, else a short form of the content hash."""
    identity = _pointer_identity(payload)
    return identity[4:] if identity.startswith("sid:") else identity[:19]


def _merge_pointers(legacy_dir: Path, target_dir: Path, legacy: str,
                    target: str, *,
                    apply: bool) -> tuple[int, list[str], set[str], int]:
    """(pointers written, session ids that did not fit, legacy filenames
    holding those, legacy copies ABSORBED by this run).

    Rewrites the target's pointer chain over the union of both buckets'
    pointer checkpoints, newest first, honoring DAIMON_CHECKPOINT_HISTORY,
    ordered by the checkpoint's own `created` stamp with mtime as the fallback
    `store._file_recency` already uses, and deduped by `_pointer_identity`.

    What does not FIT is reported and left on disk, never unlinked. The chain
    has a fixed length and a union can exceed it; dropping the overflow
    silently is data loss in a smaller hat, because a session that leaves the
    chain also leaves `store._pointer_stems` and its flat checkpoint becomes
    GC-eligible. The legacy copies that did not make it stay where they are,
    which keeps the bucket non-empty, which is what makes the leftover report
    and the `legacy:` warning tell the truth."""
    if not _pointer_files(legacy_dir):
        # Nothing to merge in, so the target's chain is already the answer.
        # Rewriting it with its own contents would be churn on the one file
        # every briefing read goes through, for no change.
        return 0, [], set(), 0
    candidates: list[tuple[float, str, Path, bool]] = []
    for source, from_legacy in ((target_dir, False), (legacy_dir, True)):
        for path in _pointer_files(source):
            payload = _pointer_payload(path)
            if payload is None:
                continue
            candidates.append((store._file_recency(path),
                               _pointer_identity(payload), path, from_legacy))
    best: dict[str, tuple[float, str, Path, bool]] = {}
    for entry in candidates:
        current = best.get(entry[1])
        # Target copies are visited first, so a strict > keeps the target's
        # copy on an exact tie: the same session evolved in the live bucket is
        # the newer of the two, and the older legacy copy must not win.
        if current is None or entry[0] > current[0]:
            best[entry[1]] = entry
    ordered = sorted(best.values(), key=lambda e: (-e[0], e[1]))
    history = config.checkpoint_history()
    chain, overflow = ordered[:history], ordered[history:]
    # Keyed on IDENTITY, not on which file it came from. A legacy pointer for
    # a session the target already holds a newer copy of is ABSORBED: it never
    # reaches the chain and it never overflows, so a path check would leave it
    # on disk forever, keeping the bucket non-empty and the migration marked
    # partial for a move that actually finished (#963 review).
    kept_ids = {entry[1] for entry in chain}
    dropped: list[str] = []
    stranded: set[str] = set()
    for _, _, path, from_legacy in overflow:
        payload = _pointer_payload(path)
        dropped.append(_pointer_label(payload) if payload else path.name)
        if from_legacy:
            stranded.add(path.name)
    if not apply:
        absorbable = sum(1 for p in _pointer_files(legacy_dir)
                         if (_pointer_payload(p) or {}) and
                         _pointer_identity(_pointer_payload(p) or {}) in
                         {e[1] for e in chain})
        return len(chain), dropped, stranded, absorbable
    blobs = [(path.read_text(encoding="utf-8"), from_legacy)
             for _, _, path, from_legacy in chain]
    with store._pointer_lock(target_dir):
        for old in _pointer_files(target_dir):
            try:
                old.unlink()
            except OSError:
                pass
        for index, (blob, from_legacy) in enumerate(blobs):
            name = "latest.json" if index == 0 else f"prev-{index}.json"
            written = target_dir / name
            store._atomic_write(written, blob)
            if from_legacy:
                _restamp(written, legacy, target)
    # Only the legacy copies whose capture is now IN the chain are removed,
    # whether this exact file supplied it or a newer copy of the same session
    # did. What remains is the true overflow, and it stays where it is.
    absorbed = 0
    for path in _pointer_files(legacy_dir):
        payload = _pointer_payload(path)
        if payload is not None and _pointer_identity(payload) in kept_ids:
            try:
                path.unlink()
                absorbed += 1
            except OSError:
                pass
    return len(blobs), dropped, stranded, absorbed


def _leftovers(d: Path, *, prune: bool = True) -> list[str]:
    """What is still in the legacy directory. `prune=False` is the dry-run
    view, which must predict the same answer the real run will produce: the
    removable sidecars are gone by the time the real run counts."""
    try:
        names = sorted(p.name for p in d.iterdir())
    except OSError:
        return []
    return [n for n in names if prune or n not in _REMOVABLE]


def _record(mode: str, legacy: str | None, target: str | None, *,
            ledgers: dict | None = None, pointers: int = 0,
            leftovers: list | None = None, unreadable: list | None = None,
            dropped_pointers: list | None = None, by: str = "cli") -> dict:
    leftovers = leftovers or []
    unreadable = unreadable or []
    dropped_pointers = dropped_pointers or []
    return {
        "version": RECORD_VERSION,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "from_slug": legacy,
        "to_slug": target,
        "mode": mode,
        "ledgers": ledgers or {},
        "pointers": pointers,
        "leftovers": leftovers,
        # The files this run could not read, and therefore could not move or
        # delete.
        "unreadable": unreadable,
        # Sessions whose pointer did not fit the chain. Left on disk, never
        # unlinked: a session that leaves the chain leaves _pointer_stems too.
        "dropped_pointers": dropped_pointers,
        # Whether the legacy bucket was fully absorbed. ONLY a complete row
        # mints an alias: the alias claims "that bucket's history lives here
        # now", and while the legacy directory still holds rows that claim is
        # false in the one direction that hurts. `requests.recipient_join`
        # SKIPS a bucket it believes is its own, so an alias minted over a
        # still-populated bucket stops it being scanned at all and its asks
        # leave the inbox (#963 review).
        "complete": not (leftovers or unreadable or dropped_pointers),
        "by": by,
    }


def migrate(project_dir, *, dry_run: bool = False, by: str = "cli") -> dict:
    """Move the pre-0.42.0 bucket for `project_dir` into the resolved one.

    Returns the receipt record, which is also what `--json` prints. Four
    modes, and only two of them touch the disk or append a receipt:

      stable  — the two rules name the same bucket; nothing was orphaned;
      absent  — no legacy bucket exists (also every run after the first, which
                is what makes the verb idempotent);
      rename  — the legacy bucket exists and the target does not: one
                `os.rename`, same filesystem, no content is read or rewritten;
      merge   — both exist: every legacy ledger line the target does not
                already hold byte for byte is appended, the pointer chain is
                rebuilt over the union, and anything this function does not
                understand is LEFT WHERE IT IS and reported.

    A no-op run writes no receipt. A receipt whose from_slug equals its
    to_slug would make the bucket an alias of itself and put every reader
    that joins on the alias file into a loop for nothing.

    Never raises for a filesystem it cannot move: a failed rename falls
    through to the merge path, which is line-wise and restartable.
    """
    _refuse_climbing(project_dir)
    legacy = legacy_slug(project_dir)
    target = target_slug(project_dir)
    if not legacy or not target:
        return _record("unknown", legacy, target, by=by)
    if legacy == target:
        return _record("stable", legacy, target, by=by)
    root = config.checkpoint_dir()
    legacy_dir = root / legacy
    target_dir = root / target
    if not legacy_dir.is_dir():
        return _record("absent", legacy, target, by=by)

    if not target_dir.exists():
        pointers = len(_pointer_files(legacy_dir))
        if dry_run:
            return _record("rename", legacy, target, pointers=pointers, by=by)
        try:
            root.mkdir(parents=True, exist_ok=True)
            os.rename(legacy_dir, target_dir)
        except OSError:
            pass  # cross-device or a race: fall through to the line-wise path
        else:
            for path in _pointer_files(target_dir):
                _restamp(path, legacy, target)
            record = _record("rename", legacy, target, pointers=pointers,
                             by=by)
            _append_record(record)
            return record

    # ONCE, before any merge work, and not inside the ledger loop below. A
    # legacy bucket holding only a pointer chain skips that loop entirely, so
    # a directory created there left `_merge_pointers` writing into a path
    # that was never made: FileNotFoundError out of a verb documented never to
    # raise for a filesystem it cannot move, and no receipt (#963 review). The
    # cross-device fall-through above reaches here with no target directory at
    # all, which is exactly that case.
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    ledgers: dict[str, int] = {}
    unreadable: list[str] = []
    for name in LEDGERS:
        source = legacy_dir / name
        if not source.is_file():
            continue
        pending, readable = _read_lines(source)
        if not readable:
            # Never counted, never appended, never unlinked. The file stays
            # exactly as it is and the caller is told which one it was.
            unreadable.append(name)
            continue
        held = set(_lines(target_dir / name))
        new = [line for line in pending if line not in held]
        ledgers[name] = len(new)
        if dry_run:
            continue
        if new:
            _append_lines(target_dir / name, new)
        # Only drop the legacy copy once every one of its lines is provably
        # in the target: the append above is the one step that can half-fail
        # (a full disk, a killed process), and a delete that trusts it would
        # be the data loss this verb exists to prevent.
        if not (set(pending) - set(_lines(target_dir / name))):
            try:
                source.unlink()
            except OSError:
                pass
    pointers, dropped, stranded, absorbed = _merge_pointers(
        legacy_dir, target_dir, legacy, target, apply=not dry_run)
    if dry_run:
        # Predict what will REMAIN, not what is there now. Listing the files
        # the real run is about to consume makes the plan say a bucket will
        # survive when it is about to be removed, which is the one thing a
        # dry run exists to get right. A pointer that will not fit the chain
        # is not consumed, so it stays in the prediction.
        consumed = (set(ledgers) | set(_REMOVABLE)
                    | ({p.name for p in _pointer_files(legacy_dir)}
                       - stranded))
        return _record("merge", legacy, target, ledgers=ledgers,
                       pointers=pointers,
                       leftovers=[n for n in _leftovers(legacy_dir,
                                                        prune=False)
                                  if n not in consumed or n in unreadable],
                       unreadable=unreadable, dropped_pointers=dropped, by=by)
    for name in _REMOVABLE:
        try:
            (legacy_dir / name).unlink()
        except OSError:
            pass
    leftovers = _leftovers(legacy_dir)
    if not leftovers:
        try:
            legacy_dir.rmdir()
        except OSError:
            pass
    record = _record("merge", legacy, target, ledgers=ledgers,
                     pointers=pointers, leftovers=leftovers,
                     unreadable=unreadable, dropped_pointers=dropped, by=by)
    # A run that MOVED nothing has nothing to record. Measured as what this
    # run ABSORBED, never as the size of the chain it rebuilt: a re-run
    # rebuilds the same chain over the same union, so a pointer COUNT is
    # non-zero every time and would append a receipt per run for a bucket
    # nobody has cleared. The "a no-op run writes no receipt" rule in this
    # docstring has to hold for this shape too, not only for `stable` and
    # `absent`.
    #
    # The one exception is a run that FINISHES a migration somebody left
    # half done: it moves no bytes, but it changes the answer to "is this
    # bucket migrated", and without a row the alias is never minted and
    # `status` warns about an unfinished migration forever.
    moved = any(ledgers.values()) or absorbed
    completes_a_partial = (
        record["complete"]
        and any(not _is_complete(row) for pair, row in latest_rows().items()
                if pair == (legacy, target)))
    if not moved and not completes_a_partial:
        return record
    _append_record(record)
    return record


def _append_record(record: dict) -> None:
    """Append one receipt to the global append-only migrations file.

    Best-effort like every other ledger appender here — but unlike them, a
    failure is worth saying out loud rather than swallowing: this file is the
    only thing that makes the moved rows reachable under their old slug, so a
    migration whose receipt did not land leaves recall and the requests inbox
    blind to the history that just moved."""
    path = migrations_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _append_lines(path, [json.dumps(record, ensure_ascii=False)])
    except OSError as exc:  # pragma: no cover - surfaced, never silent
        raise MigrationError(
            f"the bucket moved but its receipt could not be written to "
            f"{path}: {exc}") from exc
