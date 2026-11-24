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

import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

from . import config, identity, jsonl, scope, store, surfaces
from .surfaces import WritePosture


class MigrationError(RuntimeError):
    """The move happened and the receipt did not — the one state a caller
    must be told about, because it is the one that loses reachability."""


MIGRATIONS_NAME = "migrations.jsonl"
RECORD_VERSION = 1

# Which ledgers a merge moves is the registry's `mergeable` column
# (surfaces.mergeable_ledgers): "this file is an append-only ledger whose fold
# is order-tolerant", which is a property of the folds (refutations.fold and
# amendments.fold both sort by an explicit key; store.latest_receipt_verdicts
# / store.resolutions are latest-by-ts, never line order), and is independent
# of `plaintext`. A bucket ledger declared without it is left behind by every
# migration, so the registry test pins the set.


# Files a merge may DELETE from the legacy bucket even though they are not
# ledgers. `.pointer.lock` is jsonl.dir_lock's flock sidecar: opened
# "a+", never written, declared in surfaces.py as exempt-no-plaintext because
# it holds nothing by construction. Every bucket the store has ever written to
# has one, so treating it as an unrecognized leftover kept the legacy
# directory alive forever and made a second run merge and receipt again
# (#963 review). `root` (#1092) is the bucket-root marker
# `store.ensure_bucket` stamps on creation: once a legacy bucket
# merges into its correctly-resolved target, the legacy directory's record
# (if it has one at all, since most predate #1092 entirely) names a directory
# this migration is retiring, never the target the merge just moved rows
# into. The merge stamps the target itself from the resolved directory (#1337),
# first writer wins, and simply drops the legacy record.
# `.ledger-census` (#1132) is the same shape: a once-per-bucket
# stamp of the census run on the first checkpoint write, so every real legacy
# bucket carries one and it describes a directory this merge is retiring.
# Nothing else is ever removed on this list's word.
#
# `identity` (slice 3b) is the evidence record `bucket stamp` writes; a retired
# bucket's anchors describe a directory this merge is folding away, and the
# target keeps its own.
_REMOVABLE = frozenset({store._LOCK_NAME, store._BUCKET_ROOT_NAME,
                        store._LEDGER_CENSUS_NAME, store._IDENTITY_NAME})


# Staging files a crash can leave beside a bucket's record files: the
# stage-then-link of `store.create_exclusive` (`root.<12 hex>.tmp`,
# `identity.<12 hex>.tmp`) and the atomic write's `<name>.<pid>.tmp`. They hold
# no history, `store._reap_stale_tmps` reaps them by age, and until then they
# must not keep a merged-away bucket alive.
_STAGING_RE = re.compile(
    rf"^(?:{re.escape(store._BUCKET_ROOT_NAME)}"
    rf"|{re.escape(store._IDENTITY_NAME)})\.[0-9a-f]+\.tmp$")


# Removed by `_retire`, last and in this order, never by the sweep before it.
_RETIRED_LAST = frozenset({store._BUCKET_ROOT_NAME, store._LOCK_NAME})


def _is_removable(name: str) -> bool:
    """Whether a merge may delete `name` from the bucket it retires without
    calling it an unexplained leftover."""
    return name in _REMOVABLE or bool(_STAGING_RE.match(name))


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
    return store.location_slug(os.path.abspath(os.path.expanduser(text)))


def target_slug(project_dir) -> str | None:
    """The bucket name this daimon uses for `project_dir`.

    `allow_slug=False`, deliberately: `--project` is documented as a PATH, and
    the slug passthrough exists for the bucket-iterating readers that hand a
    slug in as a project. Letting it through here would let a caller name a
    bucket by copying its directory name, which is the reach #899's tenant
    mode and scar 0071 exist to remove.
    """
    return store.location_slug(
        config.resolve_project_dir(project_dir, allow_slug=False))


def migrations_path() -> Path:
    return config.checkpoint_dir() / MIGRATIONS_NAME


def records() -> list[dict]:
    """Every migration receipt, oldest first. Empty when the file is absent.

    Torn lines are skipped, never fatal: this file is read from `status`,
    `recall`'s rebuild and the requests inbox join, and a half-written line
    must not take a reporting surface down."""
    return [row for row in jsonl.read(migrations_path()).rows
            if row.get("from_slug") and row.get("to_slug")]


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


def unfinished_sources() -> set[str]:
    """Every bucket named as the SOURCE of a move that did not finish: a
    directory somebody still has to deal with."""
    return {pair[0] for pair, row in latest_rows().items()
            if not _is_complete(row)}


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
    out = [{"slug": pair[0], "ts": str(row.get("ts") or ""),
            "observed": str(row.get("observed") or "")}
           for pair, row in latest_rows().items()
           if pair[0] in wanted and _is_complete(row)]
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
    # Not on jsonl.read: the merge compares and carries RAW lines (torn ones
    # included), which `Read.rows` would drop.
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [], False
    except ValueError:  # UnicodeDecodeError is one
        return [], False
    return jsonl.split_rows(text), True


def _lines(path: Path) -> list[str]:
    """The lines only, for the target side where an unreadable file is not a
    deletion decision: an unreadable target simply holds nothing this merge
    can prove is already there, so every legacy line is appended."""
    return _read_lines(path)[0]


def _append_lines(path: Path, lines: list[str], *, lock: bool = True) -> None:
    """Append `lines`, healing a torn tail first, through the one append every
    ledger shares (`jsonl.append_lines`). `lock=False` is for a caller that
    already holds the bucket's directory lock: taking it again from the same
    process would wait on itself."""
    # A migration is a cure: it writes whatever the target ledger's health.
    jsonl.append_lines(path, lines, posture=WritePosture.PROCEED, lock=lock)


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
    mergeable = surfaces.mergeable_files()
    out = []
    for name in _leftovers(d):
        if _is_removable(name):
            continue  # the verb deletes this one
        if store._POINTER_RE.match(name) and not attempted:
            continue  # pending, not stranded
        if name in mergeable and _read_lines(d / name)[1]:
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


def _restamped(payload: dict, legacy: str, target: str) -> dict | None:
    """`payload` with `project_slug` pointed at `target`, or None when it is
    not stamped with the `legacy` slug. The ONE place a stamp is compared to
    the bucket being folded away: only a copy stamped with it is rewritten."""
    if payload.get("project_slug") != legacy:
        return None
    return {**payload, "project_slug": target}


def _restamp(path: Path, legacy: str, target: str) -> None:
    """Point a MOVED pointer copy's `project_slug` at the bucket it now lives
    in. Only a copy still stamped with the legacy slug is touched, and only
    inside a bucket: the flat per-session files are receipt-signed over their
    exact bytes (receipts.mint hashes the blob store wrote), so editing one
    would break `daimon verify-receipt` for a session that did nothing
    wrong. Recall reaches those through the alias instead."""
    payload = _pointer_payload(path)
    restamped = _restamped(payload, legacy, target) if payload else None
    if restamped is None:
        return
    store._atomic_write(path, json.dumps(restamped, ensure_ascii=False))


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


def _free_slot(target_dir: Path) -> str:
    """The next pointer filename nothing occupies: `latest.json` when the
    bucket has none, else the lowest unused `prev-N`."""
    taken = {p.name for p in _pointer_files(target_dir)}
    if "latest.json" not in taken:
        return "latest.json"
    index = 1
    while f"prev-{index}.json" in taken:
        index += 1
    return f"prev-{index}.json"


class _PointerMerge(NamedTuple):
    """What one pointer merge did, or in a dry run would do."""

    landed: int                       # G pointers that took a slot
    stranded_labels: list[str]        # left in G, still there
    stranded_names: set[str]          # their filenames
    consumed: int                     # G pointer files removed from G
    target_unreadable: list[str]      # B pointer files that could not be parsed
    target_slots: int                 # slots B occupies afterwards
    promoted: str | None = None       # G pointer made B's latest (fold only)
    evicted: tuple = ()               # B pointers the promotion rotated out
    dropped: tuple = ()               # G pointers that fit no slot (fold only)


def _pointer_labels(directory: Path) -> set[str]:
    """The labels of every readable pointer in `directory`, for naming what a
    promotion displaced."""
    out = set()
    for path in _pointer_files(directory):
        payload = _pointer_payload(path)
        if payload is not None:
            out.add(_pointer_label(payload))
    return out


def _scratch_copy(directory: Path) -> Path:
    """A throwaway copy of `directory`'s pointer files OUTSIDE the store, so a
    dry run can ask the real chain code what it would do (scar 0111: reuse the
    one owner of the semantics, never a second copy of them)."""
    scratch = Path(tempfile.mkdtemp(prefix="daimon-merge-plan-"))
    for path in _pointer_files(directory):
        shutil.copy2(path, scratch / path.name)
    return scratch


def _promote_newest(source_files: list[Path], target_dir: Path, legacy: str,
                    target: str) -> tuple[str | None, tuple]:
    """ONE rotation step: make G's newest payload B's latest when it is newer.

    The same thing one more live session in B does at `write_checkpoint`, and
    it goes through the same `store._advance_pointers`, so the regress guard,
    the rotation and the atomic write are not restated here. B's latest moves
    to a free slot, or B's oldest rotates out when the chain is full; what was
    displaced is returned by label.

    Returns (label of the promoted pointer or None, labels displaced). None
    when there is nothing to promote: B has no latest (the slot logic gives it
    one), G's newest is not newer, a session B already holds, or the guard
    refused. B's chain must be fully readable; the caller checks."""
    latest = target_dir / store._LATEST
    if not latest.is_file():
        return None, ()
    held = {_pointer_identity(payload)
            for payload in (_pointer_payload(p)
                            for p in _pointer_files(target_dir))
            if payload is not None}
    ranked = sorted(((store._file_recency(p), p) for p in source_files),
                    key=lambda entry: -entry[0])
    for recency, path in ranked:
        payload = _pointer_payload(path)
        if payload is None:
            continue  # unreadable: reported by the slot logic, never promoted
        if (_pointer_identity(payload) in held
                or recency <= store._file_recency(latest)):
            return None, ()
        before = _pointer_labels(target_dir)
        blob = json.dumps(_restamped(payload, legacy, target) or payload,
                          ensure_ascii=False)
        advanced = store._advance_pointers_held(
            target_dir, blob, store._created_epoch(payload.get("created")),
            True, config.checkpoint_history())
        if not advanced:
            return None, ()
        return (_pointer_label(payload),
                tuple(sorted(before - _pointer_labels(target_dir))))
    return None, ()


def _merge_pointers(legacy_dir: Path, target_dir: Path, legacy: str,
                    target: str, *, apply: bool, fold: bool = False
                    ) -> _PointerMerge:
    """THE INVARIANT, and it is the whole design: a pointer already in the target
    is never unlinked and never evicted. The target's chain belongs to the
    live project. This function may ADD to it, and it may replace one slot
    when the legacy bucket holds a NEWER copy of the session that slot already
    names, and it does nothing else to it.

    The rule it replaces built a union of both sides and capped it at
    DAIMON_CHECKPOINT_HISTORY. That treats the two buckets as interchangeable
    candidates for a fixed number of slots, and it leaked data in three
    consecutive shapes: a filename key collapsing two chains into one, a
    whole-payload hash splitting one session into two and evicting a third,
    and finally, when the LEGACY sessions were the newer ones, capping the
    union evicted the live project's own pointers. Each fix added a guard to
    the same wrong shape. Ordering the final chain by `created` is not a goal
    here; keeping bytes is.

    Slots are counted, not inferred: an unparseable target file occupies a
    slot like any other, and while one exists NOTHING is admitted, because a
    run that cannot count the chain cannot know which slots are free. The
    previous rule skipped such a file as a candidate and then overwrote it.

    `fold=True` is `bucket merge` into a LIVE main (owner decision D16) and
    adds two things to the rule above, both bounded. First, ONE rotation step
    promotes G's newest payload to latest when it is newer than B's latest
    (`_promote_newest`), the only way a pointer of B ever leaves its chain, and
    only as one more live session would. Second, a readable G pointer that
    finds no slot is DROPPED and reported by label instead of being stranded:
    its bytes are still in its flat signed `<session>.json`, and a merge that
    stranded it could never finish and would never mint the alias. A pointer
    that cannot be read is neither promoted nor dropped; it stays and blocks
    completion, as above."""
    legacy_files = _pointer_files(legacy_dir)
    if not legacy_files:
        # Nothing to merge in, so the target's chain is already the answer.
        return _PointerMerge(0, [], set(), 0, [],
                             len(_pointer_files(target_dir)))

    promoted: str | None = None
    evicted: tuple = ()
    view = target_dir  # where this call reads B's chain from
    scratch: Path | None = None
    # ONE critical section on B's chain: the plan below (which slots are
    # free, which are held, what is newest) and every write that follows it
    # run under the `_pointer_lock` of B that `merge_buckets` already holds
    # (taking it again would wait on itself), promotion included. A live
    # session that rotates B between a plan and its apply would otherwise have
    # a planned slot overwrite a DIFFERENT session's pointer, and a stale free
    # count would grow the chain past its history. A dry run reads a scratch
    # copy and takes nothing.
    try:
        readable = all(_pointer_payload(p) is not None
                       for p in _pointer_files(target_dir))
        if fold and readable:
            if apply:
                promoted, evicted = _promote_newest(
                    legacy_files, target_dir, legacy, target)
            else:
                scratch = _scratch_copy(target_dir)
                promoted, evicted = _promote_newest(
                    legacy_files, scratch, legacy, target)
                view = scratch

        target_unreadable: list[str] = []
        by_identity: dict[str, tuple[Path, float]] = {}
        occupied = 0
        for path in _pointer_files(view):
            occupied += 1
            payload = _pointer_payload(path)
            if payload is None:
                target_unreadable.append(path.name)
                continue
            by_identity[_pointer_identity(payload)] = (
                path, store._file_recency(path))

        free = 0 if target_unreadable else max(
            0, config.checkpoint_history() - occupied)

        candidates = sorted(
            ((store._file_recency(p), p, _pointer_payload(p))
             for p in legacy_files),
            key=lambda entry: -entry[0])

        replacements: list[tuple[Path, Path]] = []
        admissions: list[Path] = []
        consumed: list[Path] = []
        stranded_labels: list[str] = []
        stranded_names: set[str] = set()
        dropped: list[str] = []
        for recency, path, payload in candidates:
            if payload is None:
                # No identity, so it cannot be compared to anything. Left where
                # it is and reported, never discarded on the way past.
                stranded_labels.append(path.name)
                stranded_names.add(path.name)
                continue
            held = by_identity.get(_pointer_identity(payload))
            if held is not None:
                slot, slot_recency = held
                if recency > slot_recency:
                    # The one write into an existing slot. The bytes it
                    # replaces are still held by that session's flat
                    # checkpoint file.
                    replacements.append((slot, path))
                consumed.append(path)
                continue
            if free > 0:
                admissions.append(path)
                free -= 1
            elif fold and not target_unreadable:
                dropped.append(_pointer_label(payload))
                consumed.append(path)
            else:
                stranded_labels.append(_pointer_label(payload))
                stranded_names.add(path.name)

        landed = len(replacements) + len(admissions)
        if not apply:
            return _PointerMerge(
                landed, stranded_labels, stranded_names,
                len(consumed) + len(admissions), target_unreadable,
                occupied + len(admissions), promoted, evicted, tuple(dropped))

        for slot, source in replacements:
            store._atomic_write(slot, source.read_text(encoding="utf-8"))
            _restamp(slot, legacy, target)
        for source in admissions:
            written = target_dir / _free_slot(target_dir)
            store._atomic_write(written, source.read_text(encoding="utf-8"))
            _restamp(written, legacy, target)
        removed = 0
        for source in consumed + admissions:
            try:
                source.unlink()
                removed += 1
            except OSError:
                pass
        return _PointerMerge(
            landed, stranded_labels, stranded_names, removed,
            target_unreadable, occupied + len(admissions), promoted, evicted,
            tuple(dropped))
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


def _leftovers(d: Path, *, prune: bool = True) -> list[str]:
    """What is still in the legacy directory. `prune=False` is the dry-run
    view, which must predict the same answer the real run will produce: the
    removable sidecars are gone by the time the real run counts."""
    try:
        names = sorted(p.name for p in d.iterdir())
    except OSError:
        return []
    return [n for n in names if prune or not _is_removable(n)]


def _record(mode: str, legacy: str | None, target: str | None, *,
            ledgers: dict | None = None, pointers: int = 0,
            leftovers: list | None = None, unreadable: list | None = None,
            stranded_pointers: list | None = None,
            target_unreadable: list | None = None,
            target_slots: int = 0,
            promoted: str | None = None,
            pointers_evicted: list | None = None,
            pointers_dropped: list | None = None,
            observed: str = "", by: str = "cli") -> dict:
    leftovers = leftovers or []
    unreadable = unreadable or []
    stranded_pointers = stranded_pointers or []
    target_unreadable = target_unreadable or []
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
        # Legacy pointers that found no free slot. STRANDED, not dropped: they
        # are still on disk in the legacy bucket, and raising
        # DAIMON_CHECKPOINT_HISTORY and re-running lands them. "dropped" was
        # the honest word only while this verb deleted them, which is the bug
        # that named it.
        "stranded_pointers": stranded_pointers,
        # Target pointer files this run could not parse. Each one occupies a
        # slot the run cannot count, so nothing is admitted while one exists.
        "target_unreadable": target_unreadable,
        # How many pointer slots the target holds after this run. Recorded so
        # the remedy for a stranded pointer is arithmetic on what is actually
        # there: a bucket can hold MORE pointers than the current history,
        # when the knob was lowered after they were written, and deriving the
        # number from the knob then names a value that strands them again.
        "target_slots": target_slots,
        # `bucket merge` only (D16). The G pointer promoted to B's latest, the
        # B pointers that rotation displaced, and the G pointers that fit no
        # slot, all by session id. Dropped is not lost: each session's flat
        # signed file still holds the bytes, and falls under the normal GC
        # like any session that leaves the pointer window.
        "promoted": promoted,
        "pointers_evicted": pointers_evicted or [],
        "pointers_dropped": pointers_dropped or [],
        # Free text for a state the fields above cannot carry. Set when a run
        # closes a migration somebody finished by hand.
        "observed": observed,
        # Whether the legacy bucket was fully absorbed. ONLY a complete row
        # mints an alias: the alias claims "that bucket's history lives here
        # now", and while the legacy directory still holds rows that claim is
        # false in the one direction that hurts. `requests.recipient_join`
        # SKIPS a bucket it believes is its own, so an alias minted over a
        # still-populated bucket stops it being scanned at all and its asks
        # leave the inbox (#963 review).
        "complete": not (leftovers or unreadable or stranded_pointers
                         or target_unreadable),
        "by": by,
    }


def _restamp_root(target_dir: Path, resolved: str) -> None:
    """A renamed bucket arrives carrying the LEGACY bucket's root record, which
    names whatever directory wrote it before 0.42.0 (often a symlinked or
    nested path), not the directory the target slug resolves from. Drop it and
    stamp the resolved directory (#1337)."""
    try:
        (target_dir / store._BUCKET_ROOT_NAME).unlink()
    except OSError:
        pass
    store.make_bucket(target_dir.name, resolved)


def _absent(legacy: str, target: str, *, dry_run: bool, by: str) -> dict:
    """The legacy bucket is not there: nothing to move, and every run after the
    first one lands here, which is what makes both verbs idempotent."""
    record = _record("absent", legacy, target, by=by)
    # A person who cleared the old directory themselves has finished the
    # migration. Without a closing row `status` warns `partial:` forever
    # and no verb can clear it, which is a dead end the rc-1 text used to
    # invite by telling them to remove things by hand.
    if not dry_run and any(
            not _is_complete(row) for pair, row in latest_rows().items()
            if pair == (legacy, target)):
        record["observed"] = "legacy bucket no longer exists"
        _append_record(record)
    return record


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

    REFUSES when `project_dir` is already bound to a THIRD bucket (its recorded
    root is this directory but the name is neither of the two rules'): the
    two slug rules cannot see a rebind, and moving a legacy bucket into the
    rule's name would split the project across two buckets. `bucket merge`
    folds them on purpose.

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
    resolved = config.resolve_project_dir(project_dir, allow_slug=False)
    legacy_dir = root / legacy
    target_dir = root / target
    if not legacy_dir.is_dir():
        return _absent(legacy, target, dry_run=dry_run, by=by)
    # The two slug rules cannot see a rebind or a twin. Ask the resolver which
    # bucket this directory really is: anything but the legacy bucket or the
    # rule's own name means moving the legacy bucket there would split the
    # project, or hand its history to another location.
    answer = identity.resolve(resolved)
    if answer.bound and answer.slug not in (legacy, target):
        raise MigrationError(
            f"{resolved} is already bound to the bucket {answer.slug}, which "
            f"is neither {legacy} nor {target}. Moving {legacy} into {target} "
            f"would split the project across two buckets. Fold them "
            f"deliberately with `daimon bucket merge --from <bucket> --into "
            f"{answer.slug}`.")
    if not answer.bound and answer.slug != target:
        raise MigrationError(
            f"the bucket {target} belongs to another location, so {resolved} "
            f"is not bound to it. Moving {legacy} into {target} would hand "
            f"this project's history to that one. Find which bucket is this "
            f"directory's with `daimon bucket doctor --path .`, then fold "
            f"deliberately with `daimon bucket merge --from {legacy} --into "
            f"<bucket>`.")

    if not target_dir.exists():
        pointers = len(_pointer_files(legacy_dir))
        if dry_run:
            return _record("rename", legacy, target, pointers=pointers, by=by)
        # The rename removes `legacy_dir` from under anyone waiting on its
        # lock, so it is a fold like any other and runs under the same lock.
        # Nothing can lock a target that does not exist yet, and POSIX rename
        # replaces an EMPTY directory. Looking at the target again after the
        # lock narrows that to the two syscalls between `lexists` and
        # `rename`: an empty target created in that gap is still replaced
        # (benign: it holds nothing, and its writer recreates its files in
        # the same path; a non-empty one makes the rename fail and falls
        # through to the merge). A lock that is not granted falls through to
        # the merge path, which refuses with the usual message; a platform
        # with no locks at all renames unguarded, as it always did.
        renamed = False
        with jsonl.dir_lock(legacy_dir) as held:
            if ((held or not jsonl.locking_available())
                    and not os.path.lexists(target_dir)):
                try:
                    os.rename(legacy_dir, target_dir)
                    renamed = True
                except OSError:
                    pass  # cross-device or a race: line-wise path below
        if renamed:
            _restamp_root(target_dir, resolved)
            for path in _pointer_files(target_dir):
                _restamp(path, legacy, target)
            store._record_ledger_census(target, force=True)
            record = _record("rename", legacy, target, pointers=pointers,
                             by=by)
            _append_record(record)
            return record

    return merge_buckets(legacy, target, resolved=resolved, dry_run=dry_run,
                         by=by)


def merge(from_slug: str, into_slug: str, *, apply: bool = False,
          force: bool = False, by: str = "cli") -> dict:
    """`daimon bucket merge`: fold bucket `from_slug` (G) into `into_slug` (B),
    once, on purpose. The repair for a split that already exists: a worktree
    bucket, a ghost bucket, a fresh bucket born before the user rebound.

    Dry run unless `apply`. After an applied merge G no longer exists and the
    alias row `G -> B` is minted, so nothing stays synchronised and no second
    source of truth remains. A rerun finds G absent and writes nothing.

    REFUSES, naming why, when a name is not a bucket name, when G is B, when B
    does not exist (merge never creates a bucket), and, unless `force`, when
    G's recorded root is still a directory on disk: that location would birth
    G again, and alias rows are keyed by name. The exception is a live linked
    worktree that now resolves to B (`identity.worktree_binds_to`): its
    sessions use B, so nothing can birth G.

    The names are bucket NAMES taken as given, never paths: this is a repair
    verb for a person who has read `bucket doctor`, and its CLI refuses them
    on a tenant-scoped home the way `bucket migrate` refuses `--project`."""
    for name in (from_slug, into_slug):
        if scope.parse_bucket(name) is None:
            raise MigrationError(f"{name!r} is not a bucket name")
    if from_slug == into_slug:
        raise MigrationError("a bucket cannot be merged into itself")
    root = config.checkpoint_dir()
    if not (root / from_slug).is_dir():
        return _absent(from_slug, into_slug, dry_run=not apply, by=by)
    if not (root / into_slug).is_dir():
        raise MigrationError(
            f"there is no bucket {into_slug} to merge into; merge never "
            f"creates one")
    recorded = store.bucket_root(from_slug)
    if not recorded and not force:
        # Without a root there is no location to ask "is that project still
        # there", so liveness cannot be proven either way (every bucket from
        # before roots were recorded is in this state). A person who has
        # looked says so with --force.
        raise MigrationError(
            f"{from_slug} has no recorded root, so daimon cannot tell whether "
            f"its project still exists and a session there could still be "
            f"writing to it. Check, then pass --force to fold it.")
    if (recorded and not force and os.path.isdir(recorded)
            and not identity.worktree_binds_to(recorded, into_slug)):
        raise MigrationError(
            f"the root recorded for {from_slug} still exists on disk "
            f"({recorded}). A session there would create {from_slug} again "
            f"and every alias row is keyed by name. Merge only after that "
            f"directory is gone, or pass --force to fold it anyway.")
    return merge_buckets(from_slug, into_slug, resolved=None,
                         dry_run=not apply, by=by, fold=True)


class _Retired(NamedTuple):
    removed: bool
    where: Path       # the directory that still holds the bucket, if any
    observed: str = ""


def _retire(legacy_dir: Path) -> _Retired:
    """Remove the (already emptied) legacy bucket, its `root` LAST.

    Called with the bucket locked. The directory is first RENAMED to a
    tombstone in `.birth` (atomic, same filesystem), and everything after
    happens there: an opener of the old path now gets ENOENT, so it can no
    longer re-create the lock sidecar in a directory that is being removed and
    lock a zombie. Never `rmtree`: a file that is still in the tombstone is a
    fail-open writer's late append, and that data must survive.

    The record goes right before the `rmdir`, never earlier, so a bucket kept
    alive by anything is never left unstamped: an unstamped directory at a
    seed name is what a later resolution would adopt as its own. When the
    `rmdir` fails the record is put back and the bucket is renamed back to its
    own path, or, when something took the path meanwhile, left in the
    tombstone and said so."""
    root = legacy_dir / store._BUCKET_ROOT_NAME
    try:
        held = root.read_text(encoding="utf-8").strip()
    except OSError:
        held = ""
    try:
        tomb = store.move_aside(legacy_dir)
    except OSError:
        return _Retired(False, legacy_dir)
    # `merge_buckets` still holds the lock (on the inode now inside the
    # tombstone) and releases it only on return.
    for name in (store._LOCK_NAME, store._BUCKET_ROOT_NAME):
        try:
            (tomb / name).unlink()
        except OSError:
            pass
    try:
        tomb.rmdir()
    except OSError:
        if held:
            store.stamp_root(tomb, held)
        try:
            if os.path.lexists(legacy_dir):
                raise FileExistsError(legacy_dir)
            os.rename(tomb, legacy_dir)
        except OSError:
            return _Retired(
                False, tomb,
                f"the bucket directory could not be removed and its path was "
                f"taken, so it was left in {tomb}")
        return _Retired(False, legacy_dir)
    return _Retired(True, legacy_dir)


def _refuse_aliased(legacy_dir: Path, target_dir: Path) -> None:
    """Refuse a pair that is not two distinct real directories.

    "The same bucket" is a fact about the FILESYSTEM, never about two names:
    on a case-insensitive volume (the macOS default) `-Users-Me-Repo` and
    `-users-me-repo` are one directory, and a merge that treated it as source
    and target found every line "already in the target" and deleted the real
    ledger. A symlinked bucket directory is refused for the same reason, and
    because every unlink below would go through the link. Only a bucket that
    IS a symlink is refused: the checkpoint directory itself may be one."""
    for d in (legacy_dir, target_dir):
        if d.is_symlink():
            raise MigrationError(
                f"{d.name} is a symlink, not a bucket directory; refusing to "
                f"move files through it")
    if (legacy_dir.exists() and target_dir.exists()
            and os.path.samestat(os.stat(legacy_dir), os.stat(target_dir))):
        raise MigrationError(
            f"{legacy_dir.name} and {target_dir.name} are the same directory "
            f"(a case-insensitive filesystem, or a link): there is nothing to "
            f"merge, and merging would delete it")


def merge_buckets(legacy: str, target: str, *, resolved: str | None = None,
                  dry_run: bool = False, by: str = "cli",
                  fold: bool = False) -> dict:
    """Fold bucket `legacy` into the EXISTING bucket `target`: ledgers unioned
    (byte-equal dedup, folds order by their own keys), pointers per
    `_merge_pointers`, then the legacy directory removed when nothing is left.

    The one body behind `migrate` (0.42 rule pairs, `resolved` is the directory
    the target belongs to and its root is stamped from it, #1337) and `merge`
    (`fold=True`, any pair: the target's root is never touched and the pointer
    rule is the D16 one). A receipt is appended only when something moved, a
    partial migration was finished, or, for a fold, the legacy bucket was
    removed (an alias row is minted whenever it is, pointer-only buckets
    included).

    The legacy bucket is held under its directory lock for the whole run, so a
    writer cannot append a line between the read that decided "every line is
    in the target" and the unlink that follows it. A dry run takes no lock and
    writes nothing."""
    root = config.checkpoint_dir()
    legacy_dir = root / legacy
    target_dir = root / target
    _refuse_aliased(legacy_dir, target_dir)
    if not dry_run and resolved is not None:
        # ONCE, before any merge work and before any lock: a legacy bucket
        # holding only a pointer chain skips the ledger loop, so a directory
        # created there left `_merge_pointers` writing into a path that was
        # never made (#963 review), and the cross-device fall-through reaches
        # here with no target directory at all. The bucket and its root record
        # are made together (#1337): the target holds the rows this merge
        # moves, so it must say which directory it belongs to even though the
        # legacy record is dropped.
        store.make_bucket(target, resolved)
    with contextlib.ExitStack() as stack:
        if not dry_run:
            # BOTH buckets, in one global order (by name), so two merges that
            # name the same pair in opposite directions cannot each hold one
            # lock and wait on the other. `dir_lock` fails OPEN after about a
            # second, which is right for a hook that must never block and
            # wrong for a verb that deletes: here a lock that was not acquired
            # is a refusal, raised before anything is written.
            for d in sorted({legacy_dir, target_dir}, key=lambda p: p.name):
                held = stack.enter_context(jsonl.dir_lock(d))
                if held:
                    continue
                # Not held. `dir_lock` only reports a lock on the sidecar the
                # path names NOW, so a bucket another run folded away while
                # this one waited is "not held" too (#1411): the decisions
                # taken before the locks are stale, and what is left to say
                # is which of the two went.
                if not d.is_dir():
                    if d == legacy_dir:
                        return _absent(legacy, target, dry_run=False, by=by)
                    raise MigrationError(
                        f"the bucket {d.name} no longer exists (it was "
                        f"folded away or removed before this merge got its "
                        f"lock); nothing was changed.")
                raise MigrationError(
                    f"could not lock the bucket {d.name} (another daimon "
                    f"process is writing to it, or it cannot be locked); "
                    f"nothing was changed. Try again in a moment.")
            if resolved is not None:
                # Idempotent: stamps only a target that records no root. The
                # call before the locks cannot see a target that was folded
                # away and reborn while this waited; the reborn one is
                # unstamped, and this is the first moment it is ours to stamp.
                store.make_bucket(target, resolved)
        return _merge_locked(legacy, target, legacy_dir, target_dir,
                             dry_run=dry_run, by=by, fold=fold)


def _merge_locked(legacy: str, target: str, legacy_dir: Path,
                  target_dir: Path, *, dry_run: bool, by: str,
                  fold: bool) -> dict:
    """The body of `merge_buckets`, run with both buckets locked (a dry run
    holds nothing and writes nothing)."""

    ledgers: dict[str, int] = {}
    unreadable: list[str] = []
    for name in surfaces.mergeable_files():
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
            _append_lines(target_dir / name, new, lock=False)
        # Re-read the legacy file NOW, immediately before it is removed. A
        # writer that gave up waiting for the lock (a hook never blocks for
        # long) may have appended since `pending` was read, and that line is
        # in no list above. Anything the target lacks is carried first.
        final, still_readable = _read_lines(source)
        if not still_readable:
            unreadable.append(name)
            continue
        have = set(_lines(target_dir / name))
        late = [line for line in final if line not in have]
        if late:
            _append_lines(target_dir / name, late, lock=False)
            ledgers[name] += len(late)
        # Only drop the legacy copy once every one of its lines is provably
        # in the target: the append above is the one step that can half-fail
        # (a full disk, a killed process), and a delete that trusts it would
        # be the data loss this verb exists to prevent.
        if not (set(final) - set(_lines(target_dir / name))):
            try:
                source.unlink()
            except OSError:
                pass
    merged = _merge_pointers(legacy_dir, target_dir, legacy, target,
                             apply=not dry_run, fold=fold)
    promoted = merged.promoted
    evicted = list(merged.evicted)
    dropped = list(merged.dropped)
    if dry_run:
        # Predict what will REMAIN, not what is there now. Listing the files
        # the real run is about to consume makes the plan say a bucket will
        # survive when it is about to be removed, which is the one thing a
        # dry run exists to get right. A pointer that will not fit the chain
        # is not consumed, so it stays in the prediction.
        consumed = (set(ledgers) | set(_REMOVABLE)
                    | ({p.name for p in _pointer_files(legacy_dir)}
                       - merged.stranded_names))
        return _record("merge", legacy, target, ledgers=ledgers,
                       pointers=merged.landed,
                       leftovers=[n for n in _leftovers(legacy_dir,
                                                        prune=False)
                                  if n not in consumed or n in unreadable],
                       unreadable=unreadable,
                       stranded_pointers=merged.stranded_labels,
                       target_unreadable=merged.target_unreadable,
                       target_slots=merged.target_slots, promoted=promoted,
                     pointers_evicted=evicted, pointers_dropped=dropped,
                     by=by)
    # Everything removable goes EXCEPT the two files `_retire` takes last: the
    # `root` record (a bucket kept alive is never unstamped) and the lock
    # sidecar. The sidecar goes only in `_retire`, right before the `rmdir`:
    # unlinking it earlier would hand a writer that arrives during the rest of
    # this run a fresh inode to lock, and the exclusion is gone. Unlinked
    # there, a waiter already holding the old inode is not fooled by it:
    # `dir_lock` re-checks that its inode is still the one the path names.
    for entry in list(legacy_dir.iterdir()):
        if entry.name not in _RETIRED_LAST and _is_removable(entry.name):
            try:
                entry.unlink()
            except OSError:
                pass
    # The target now holds rows it did not hold when its marker was stamped.
    store._record_ledger_census(target, force=True)
    leftovers = [n for n in _leftovers(legacy_dir)
                 if n not in _RETIRED_LAST]

    def receipt(leftovers: list, observed: str = "") -> dict:
        return _record("merge", legacy, target, ledgers=ledgers,
                       pointers=merged.landed, leftovers=leftovers,
                       unreadable=unreadable,
                       stranded_pointers=merged.stranded_labels,
                       target_unreadable=merged.target_unreadable,
                       target_slots=merged.target_slots, promoted=promoted,
                       pointers_evicted=evicted, pointers_dropped=dropped,
                       observed=observed, by=by)

    record = receipt(leftovers)
    # A run that MOVED nothing has nothing to record. Measured as what this
    # run ABSORBED, never as the size of the chain it rebuilt: a re-run
    # rebuilds the same chain over the same union, so a pointer COUNT is
    # non-zero every time and would append a receipt per run for a bucket
    # nobody has cleared. The "a no-op run writes no receipt" rule in
    # `migrate` has to hold for this shape too, not only for `stable` and
    # `absent`.
    #
    # The exceptions are runs that change the answer to "is this bucket
    # migrated": one that FINISHES a migration somebody left half done (no
    # bytes move, but without a row the alias is never minted and `status`
    # warns about an unfinished migration forever), and a fold that REMOVED
    # the bucket, which always mints the alias even when nothing but a
    # `root` was in it.
    moved = any(ledgers.values()) or merged.consumed
    last = latest_rows().get((legacy, target))
    open_partial = last is not None and not _is_complete(last)
    completes_a_partial = record["complete"] and open_partial
    retiring = not leftovers
    wanted = moved or completes_a_partial or (fold and retiring)
    # The durable record comes BEFORE the irreversible removal. The alias row
    # is the only way readers follow the old name, so a crash, a full disk or
    # a failed append between the two must find the bucket still standing (a
    # rerun then finishes), never a bucket gone with no row to say where its
    # history went. The one case that waits for the removal is a rerun that
    # only finishes an already-recorded partial move: its incomplete row is
    # already on file, and a bucket that stays un-removable must not grow the
    # file (or flip the alias) on every attempt.
    recorded = wanted and (moved or not open_partial)
    if recorded:
        _append_record(record)
    if not retiring:
        return record
    retired = _retire(legacy_dir)
    if retired.removed:
        if wanted and not recorded:
            _append_record(record)
        return record
    # Not gone after all: a writer got there first, or the directory could not
    # be removed. A row above claimed completion, and the last row for a pair
    # wins, so correct it, once. Complete means REALLY gone.
    record = receipt(
        [n for n in _leftovers(retired.where) if n not in _RETIRED_LAST],
        observed=(retired.observed
                  or "the bucket directory could not be removed"))
    record["complete"] = False
    if recorded:
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
