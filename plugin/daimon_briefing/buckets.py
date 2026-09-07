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


def _edges() -> dict[str, str]:
    return {str(r["from_slug"]): str(r["to_slug"]) for r in records()}


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

    None in the two silent cases: the two rules agree on this path (nothing
    was ever orphaned), or the legacy directory is simply not there. Only a
    directory that EXISTS is reported, so a surface calling this can say "a
    legacy bucket exists" as a fact rather than a possibility."""
    legacy = legacy_slug(project_dir)
    target = target_slug(project_dir)
    if not legacy or not target or legacy == target:
        return None
    return legacy if (config.checkpoint_dir() / legacy).is_dir() else None


# ---------------------------------------------------------------------------
# the move
# ---------------------------------------------------------------------------


def _lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    return [ln for ln in text.splitlines() if ln.strip()]


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


def _merge_pointers(legacy_dir: Path, target_dir: Path, legacy: str,
                    target: str, *, apply: bool) -> int:
    """Rewrite the target's pointer chain over the union of both buckets'
    pointer checkpoints, newest first, honoring DAIMON_CHECKPOINT_HISTORY.

    Ordered by the checkpoint's own written stamp (`created`, the field
    write_checkpoint setdefaults and the one that survives rotation), mtime
    only as the fallback store._file_recency already uses. Deduped by
    session_id: the same session can hold a pointer in both buckets, and the
    chain must not spend two of its slots on one session."""
    if not _pointer_files(legacy_dir):
        # Nothing to merge in, so the target's chain is already the answer.
        # Rewriting it with its own contents would be churn on the one file
        # every briefing read goes through, for no change.
        return 0
    candidates: list[tuple[float, str, Path, bool]] = []
    for source, from_legacy in ((target_dir, False), (legacy_dir, True)):
        for path in _pointer_files(source):
            payload = _pointer_payload(path)
            if payload is None:
                continue
            sid = str(payload.get("session_id") or path.name)
            candidates.append((store._file_recency(path), sid, path,
                               from_legacy))
    best: dict[str, tuple[float, str, Path, bool]] = {}
    for entry in candidates:
        current = best.get(entry[1])
        # Target copies are visited first, so a strict > keeps the target's
        # copy on an exact tie: the bucket that is staying wins.
        if current is None or entry[0] > current[0]:
            best[entry[1]] = entry
    chain = sorted(best.values(), key=lambda e: (-e[0], e[1]))
    history = config.checkpoint_history()
    chain = chain[:history]
    if not apply:
        return len(chain)
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
    for path in _pointer_files(legacy_dir):
        try:
            path.unlink()
        except OSError:
            pass
    return len(blobs)


def _leftovers(d: Path) -> list[str]:
    try:
        return sorted(p.name for p in d.iterdir())
    except OSError:
        return []


def _record(mode: str, legacy: str | None, target: str | None, *,
            ledgers: dict | None = None, pointers: int = 0,
            leftovers: list | None = None, by: str = "cli") -> dict:
    return {
        "version": RECORD_VERSION,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "from_slug": legacy,
        "to_slug": target,
        "mode": mode,
        "ledgers": ledgers or {},
        "pointers": pointers,
        "leftovers": leftovers or [],
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

    ledgers: dict[str, int] = {}
    for name in LEDGERS:
        source = legacy_dir / name
        if not source.is_file():
            continue
        pending = _lines(source)
        held = set(_lines(target_dir / name))
        new = [line for line in pending if line not in held]
        ledgers[name] = len(new)
        if dry_run:
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
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
    pointers = _merge_pointers(legacy_dir, target_dir, legacy, target,
                               apply=not dry_run)
    if dry_run:
        return _record("merge", legacy, target, ledgers=ledgers,
                       pointers=pointers, leftovers=_leftovers(legacy_dir),
                       by=by)
    leftovers = _leftovers(legacy_dir)
    if not leftovers:
        try:
            legacy_dir.rmdir()
        except OSError:
            pass
    record = _record("merge", legacy, target, ledgers=ledgers,
                     pointers=pointers, leftovers=leftovers, by=by)
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
