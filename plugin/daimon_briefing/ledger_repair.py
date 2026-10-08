"""Ledger repair and the one single-key scrub forget shares with it (#1132 2c-2)."""
from __future__ import annotations

import errno
import json
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from . import (amendments, config, jsonl, normalize, refutations,
               relations, requests, store, surfaces, trust)


class Unreached(NamedTuple):
    """A surface forget could not vouch for (#1132 PR 10b, D10.5): which
    ledger file, what `read` judged it, how many torn lines it holds, and
    whether it carries plaintext (a ledger that does not never fails forget;
    it is only reported)."""
    name: str
    state: jsonl.Health
    torn: int
    plaintext: bool = True
    detail: str = ""
    unscannable: str = ""


class Purged(NamedTuple):
    """What `forget_quarantined_lines` did: envelope rows removed and kept,
    and the sidecars it could not vouch for."""
    purged: int
    kept: int
    unreached: tuple = ()


def _bucket_dir(project_dir) -> Path | None:
    # #948: resolve BEFORE slugging, like every ledger path in this package.
    slug = store.project_slug(config.resolve_project_dir(project_dir))
    return config.checkpoint_dir() / slug if slug else None


def sidecars(bucket: Path) -> list[Path]:
    """The quarantine sidecar files a bucket holds, by name."""
    try:
        return sorted(p for p in bucket.iterdir()
                      if p.is_file() and surfaces.is_quarantine_sidecar(p.name))
    except OSError:
        return []


def _spellings(value: str) -> tuple[str, ...]:
    """A value as it sits inside a torn JSON line: escaped for a string
    (quotes, backslashes, newlines), with and without \\uXXXX for non-ASCII."""
    return (value, json.dumps(value, ensure_ascii=False)[1:-1],
            json.dumps(value)[1:-1])


def matched_keys(text: str, keys) -> set[str]:
    """The members of `keys` a quarantined line carries: the canonical key of
    the whole line, or of any JSON string literal inside it."""
    return set(keys) & ({normalize.content_key(text)}
                        | normalize.literal_content_keys(text))


def holds_forgotten_key(text: str, keys) -> bool:
    return bool(matched_keys(text, keys))


def forget_quarantined_lines(content_key: str, *, text: str = "",
                             project_dir=None) -> Purged:
    """Purge the quarantine-sidecar envelope rows that hold a forgotten value.

    Two matchers, either one purges a row. With `text` (forget has the value)
    a row whose `text` contains the value, raw or JSON-escaped, goes. Without
    it (repair has only the tombstone) a row goes when the whole line or a
    JSON string literal inside it folds to `content_key`: matching is by key
    only, so a value fused into a longer string of a torn row is not found.
    Every other row, and every line that is not an envelope row, is written
    back untouched. Returns how many rows went and how many stayed."""
    bucket = _bucket_dir(project_dir)
    if bucket is None or not content_key:
        return Purged(0, 0)
    spellings = _spellings(text) if text else ()
    purged = kept = 0

    def drop(line, row):
        nonlocal kept
        body = row.get("text") if isinstance(row, dict) else None
        if not isinstance(body, str):
            return line
        if (any(s in body for s in spellings)
                or holds_forgotten_key(body, {content_key})):
            return None
        kept += 1
        return line

    unreached = []
    for path in sidecars(bucket):
        before = kept
        try:
            purged += jsonl.rewrite(path, drop, write=_stage)
        except OSError:
            kept = before
        got = jsonl.judge_reach(path, [], lambda row: _holds_value(
            row, content_key, spellings))
        if not got.reached:
            unreached.append(Unreached(path.name, got.state, got.torn, True,
                                       got.detail, got.unscannable))
    return Purged(purged, kept, tuple(unreached))


def _holds_value(row, content_key, spellings) -> bool:
    """Does a sidecar envelope row still carry the forgotten value."""
    body = row.get("text") if isinstance(row, dict) else None
    return isinstance(body, str) and (
        any(s in body for s in spellings)
        or holds_forgotten_key(body, {content_key}))


class Scrubbed(NamedTuple):
    """What `scrub_forgotten_key` reached, per ledger."""
    events: int = 0
    refutations: tuple = ()
    relations: tuple = ()
    amendments: tuple = ()
    requests: tuple = ()
    quarantines: tuple = ()
    lines: Purged = Purged(0, 0)
    # The surfaces forget could not vouch for (`Unreached`), in ledger order.
    unreached: tuple = ()


def scrub_forgotten_key(content_key: str, *, item_id: str = "",
                        sibling_ids=(), text: str = "",
                        project_dir=None) -> Scrubbed:
    """Run every ledger deleter for ONE forgotten value, in the order forget
    has always run them. `daimon forget` and `daimon ledger repair` both call
    this, so a deleter added to forget cannot be missed by repair.

    `item_id` is the tombstone's `item_ref` (the id the user named) and
    `sibling_ids` the other checkpoint items that held the value, which only
    forget can see; the id-keyed deleters (relations, amendments) take
    both. `text` is the value itself when the caller has it: forget does,
    repair does not. A repair has only the tombstone, so everything it
    reaches is matched by `content_key` (and by id), never by text: a value
    a ledger holds inside a longer string, which no whole-value key matches,
    is the part only a forget with the text can reach.

    Each deleter is best-effort and never raises, so one unwritable ledger
    does not stop the rest."""
    events = store.scrub_event_fields(content_key, project_dir=project_dir)
    refuted = refutations.forget_content_key(content_key,
                                             project_dir=project_dir)
    related = relations.forget_item_id(item_id, project_dir=project_dir)
    amend_parts = [amendments.forget_content_key(content_key,
                                                 project_dir=project_dir)]
    for doomed in sorted(({item_id} | set(sibling_ids)) - {""}):
        amend_parts.append(amendments.forget_item_id(doomed,
                                                     project_dir=project_dir))
    amended = {rid for part in amend_parts for rid in part}
    requested = requests.forget_content_key(content_key,
                                            project_dir=project_dir)
    # A quarantine is a human verdict that withholds a value: redacted in
    # place, never dropped (scar trust-deleter-redacts-never-drops).
    quarantined = trust.redact_content_key(content_key,
                                           project_dir=project_dir)
    lines = forget_quarantined_lines(content_key, text=text,
                                     project_dir=project_dir)
    unreached = [u for u in (
        _unreached("refutations.jsonl", [refuted], True),
        _unreached("relations.jsonl", [related], False),
        _unreached("amendments.jsonl", amend_parts, True),
        _unreached("requests.jsonl", [requested], True),
        _unreached("trust.jsonl", [quarantined], True)) if u]
    return Scrubbed(events, tuple(refuted), tuple(related),
                    tuple(sorted(amended)), tuple(requested),
                    tuple(quarantined), lines,
                    tuple(unreached) + tuple(lines.unreached))


def _unreached(name: str, parts, plaintext: bool) -> Unreached | None:
    """The `Unreached` for a ledger one or more deleter calls ran on: None
    when every call reached it (a per-sibling deleter ANDs across its
    calls)."""
    # A deleter that returned a plain list says nothing about its ledger and
    # counts as reached (a stub in a test, a deleter not yet converted).
    bad = [p for p in parts if not getattr(p, "reached", True)]
    if not bad:
        return None
    return Unreached(name, bad[0].state, max(p.torn for p in bad), plaintext,
                     bad[0].detail, bad[0].unscannable)


class Report(NamedTuple):
    """What `repair` found or did. Counts and names only, never row content."""
    name: str
    outcome: str = "nothing"   # "repaired" | "nothing" | "error"
    rejoined: int = 0
    torn: int = 0
    garbage: int = 0
    sidecar: str = ""          # the sidecar path, when lines moved to it
    sidecar_held: int = 0      # envelope rows it already held before this run
    sidecar_rows: int = 0      # envelope rows it holds after this run
    keys: int = 0              # forget tombstone keys re-scrubbed
    rows: int = 0              # records removed or redacted by that scrub
    scrub_skipped: str = ""    # why the re-scrub could not run
    rulings: tuple = ()        # ACTIVE rulings the re-scrub reaches
    refusal: str = ""          # set when the ruling gate stopped the re-scrub
    error: str = ""
    dry_run: bool = False


def declared_names() -> tuple[str, ...]:
    """The ledgers `repair` accepts: every declared bucket ledger."""
    return surfaces.bucket_ledger_names()


def resolve_name(name: str) -> str | None:
    """"events" or "events.jsonl" -> the declared ledger file name, or None."""
    for declared in surfaces.bucket_ledger_names():
        if name in (declared, declared.removesuffix(".jsonl")):
            return declared
    return None


_MARKER = "forgotten:"


def _forgotten_keys(project_dir) -> dict[str, list[str]]:
    """{content key: the item ids tombstoned for it}, from the events fold.
    A reopened tombstone is not in the fold, so it is not here either."""
    keys: dict[str, list[str]] = {}
    for ref, evt in sorted(store.resolutions(project_dir=project_dir).items()):
        status = str(evt.get("status") or "")
        if store.is_tombstone_status(status):
            key = status.strip()[len(_MARKER):].strip()
            if key:
                keys.setdefault(key, []).append(ref)
    return keys


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _errno_name(exc: OSError) -> str:
    return errno.errorcode.get(exc.errno or 0, "OSError")


def _envelope(ledger: str, kind: str, line: str) -> dict:
    """One sidecar row. `text` decodes the line with backslashreplace, so a
    byte that is not UTF-8 survives as a \\xNN escape and the file stays
    valid JSONL."""
    raw = line.encode("utf-8", errors="surrogateescape")
    return {"ledger": ledger, "quarantined_at": _now(), "kind": kind,
            "text": raw.decode("utf-8", errors="backslashreplace")}


def _stage(target: Path, blob: str) -> None:
    """The stager `jsonl.replace` and `jsonl.rewrite` hand text to: store's
    `_atomic_write`, which the write-audit guard observes, with undecodable
    bytes written back as they were."""
    store._atomic_write(target, blob, errors="surrogateescape")


def _quarantine(path: Path, sidecar: Path, part: jsonl.Partition
                ) -> tuple[int, int]:
    """Move `part.moved` into the sidecar, then rewrite the ledger with the
    rows only. The sidecar lands first, so a crash between the two writes
    leaves a copy of every moved line and no loss; the next run does not
    duplicate what the sidecar already holds. The caller holds the ledger
    lock (`_run`), so the swaps do not take it again. Returns (envelope rows
    the sidecar held before, rows it holds now)."""
    try:
        held_text = sidecar.read_text(encoding="utf-8",
                                      errors="surrogateescape")
    except FileNotFoundError:
        held_text = ""
    held = jsonl.split_rows(held_text)
    seen = set()
    for line in held:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            seen.add((row.get("kind"), row.get("text")))
    fresh = []
    for kind, line in part.moved:
        env = _envelope(path.name, kind, line)
        if (kind, env["text"]) not in seen:
            fresh.append(json.dumps(env, ensure_ascii=False))
    if fresh:
        body = "".join(line + "\n" for line in held + fresh)
        jsonl.replace(sidecar, body, write=_stage, lock=False)
    jsonl.replace(path, "".join(row + "\n" for row in part.rows),
                  write=_stage, lock=False)
    return len(held), len(held) + len(fresh)


def refusal_for(rulings: list) -> str:
    return ("refused: re-scrubbing would remove ACTIVE ruling(s) "
            + ", ".join(rulings) + ", a human decision. Run it from a "
            "terminal, or ask the user; `daimon ruling retire` records the "
            "verdict instead.")


def _rescrub(project_dir, approve, dry_run: bool
             ) -> tuple[int, int, str, tuple, str]:
    """(keys, rows reached, why it was skipped, active rulings reached, the
    gate's refusal). The tombstones live in events.jsonl; when that file
    cannot be trusted no key is known, and "nothing to scrub" must not be
    mistaken for "scrubbed".

    The refutations deleter drops whole rulings, and a ruling is a human
    verdict (#693). Before it runs, every ACTIVE ruling the forgotten keys
    reach is handed to `approve`, which returns "" to proceed or the reason
    to stop. A dry run never asks: it reports the rulings and goes on, on
    its scratch copy."""
    bucket = _bucket_dir(project_dir)
    assert bucket is not None
    health = jsonl.read(bucket / "events.jsonl").health
    if health in (jsonl.Health.UNREADABLE, jsonl.Health.TRANSIENT):
        return 0, 0, (f"events.jsonl is {health.value}, so the forgotten "
                      "keys cannot be read; run `daimon ledger repair "
                      "events` first"), (), ""
    forgotten = _forgotten_keys(project_dir)
    doomed = tuple(refutations.active_rulings_reached(
        forgotten, item_ids={r for refs in forgotten.values() for r in refs},
        project_dir=project_dir)) if forgotten else ()
    if doomed and not dry_run:
        refusal = approve(list(doomed))
        if refusal:
            return 0, 0, "", doomed, refusal
    rows = 0
    for key, refs in forgotten.items():
        done = scrub_forgotten_key(key, item_id=refs[0],
                                   sibling_ids=refs[1:],
                                   project_dir=project_dir)
        rows += (done.events + len(done.refutations) + len(done.relations)
                 + len(done.amendments) + len(done.requests)
                 + len(done.quarantines) + done.lines.purged)
    return len(forgotten), rows, "", doomed, ""


def _run(project_dir, ledger: str, approve, dry_run: bool) -> Report:
    bucket = _bucket_dir(project_dir)
    assert bucket is not None
    path = bucket / ledger
    sidecar = bucket / surfaces.quarantine_sidecar(ledger)
    read = jsonl.read(path)
    if read.health is jsonl.Health.ABSENT:
        return Report(ledger)
    if read.health is jsonl.Health.TRANSIENT:
        return Report(ledger, "error", error=(
            f"transient ({read.detail}); not touched, try again"))
    if read.health is jsonl.Health.UNREADABLE and read.detail != "garbage":
        return Report(ledger, "error", error=f"unreadable ({read.detail})")
    rejoined = torn = garbage = held = total = 0
    try:
        if read.health is not jsonl.Health.OK:
            # One lock hold from the read to the last swap: an append that
            # lands between them would be overwritten with the stale rows.
            # The re-scrub below stays outside it, its deleters take the
            # lock themselves through `jsonl.rewrite`.
            with jsonl.ledger_lock(path):
                text = path.read_text(encoding="utf-8",
                                      errors="surrogateescape")
                part = jsonl.partition(text)
                held, total = _quarantine(path, sidecar, part)
            rejoined, torn, garbage = (part.split, len(part.torn),
                                       len(part.garbage))
        keys, rows, skipped, rulings, refusal = _rescrub(
            project_dir, approve, dry_run)
    except OSError as exc:
        return Report(ledger, "error", error=f"{_errno_name(exc)} while "
                      "writing; the ledger is as it was")
    changed = bool(rejoined or torn or garbage or rows)
    return Report(ledger, "repaired" if changed else "nothing", rejoined,
                  torn, garbage, str(sidecar) if torn or garbage else "",
                  held if torn or garbage else 0, total if torn or garbage else 0,
                  keys, rows, skipped, rulings, refusal)


@contextmanager
def _scratch_store(bucket: Path):
    """A throwaway copy of the bucket's ledger files, with the checkpoint dir
    overridden to it for this context only, so a dry run executes the real
    repair and reports its real counts while nothing it writes reaches the
    store."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / bucket.name
        copy.mkdir()
        for entry in bucket.iterdir():
            if entry.is_file() and (entry.suffix == ".jsonl"
                                    or surfaces.is_quarantine_sidecar(
                                        entry.name)):
                shutil.copy2(entry, copy / entry.name)
        with config.checkpoint_dir_override(Path(tmp)):
            yield


def repair(project_dir, ledger: str, *, dry_run: bool = False,
           approve=None) -> Report:
    """Heal one bucket ledger, in this order, through governed writes:
    rejoin split rows; move torn and garbage lines into the sidecar
    `<stem>.quarantined-lines` (the ledger keeps only valid rows, so
    `jsonl.read` reports OK); re-run `scrub_forgotten_key` for every forget
    tombstone of the bucket, which reaches rows a forget missed while they
    were split; re-stamp the census marker so `status` reads the new state.

    Idempotent: a second run changes no byte. A transient or unreadable
    ledger is reported and never touched. `dry_run` runs all of it against a
    scratch copy and writes nothing to the store.

    `approve(rulings)` is the human gate in front of the re-scrub (see
    `_rescrub`); without one the re-scrub refuses to remove a ruling."""
    approve = approve or refusal_for
    bucket = _bucket_dir(project_dir)
    if bucket is None:
        return Report(ledger, "error", error="no project to address",
                      dry_run=dry_run)
    if not bucket.is_dir():
        return Report(ledger, dry_run=dry_run)   # no bucket, no ledger
    if dry_run:
        try:
            with _scratch_store(bucket):
                report = _run(project_dir, ledger, approve, True)
        except OSError as exc:
            return Report(ledger, "error", error=_errno_name(exc),
                          dry_run=True)
        real = str(bucket / surfaces.quarantine_sidecar(ledger))
        return report._replace(dry_run=True,
                               sidecar=real if report.sidecar else "")
    report = _run(project_dir, ledger, approve, False)
    if report.outcome == "repaired":
        store._record_ledger_census(bucket.name, force=True)
    return report
