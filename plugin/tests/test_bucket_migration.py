"""#963: a bucket written before 0.42.0 can be moved to the resolved bucket.

Before 0.42.0 the library slugged the LITERAL project path. Since #951/#957
every entry point resolves first (absolute, symlinks collapsed, git toplevel).
For a path with a symlink component, or a path below a git toplevel, the two
rules name different buckets, so the pre-0.42.0 bucket is unreachable: nothing
reads it and nothing says it is there.

There is deliberately NO fallback read. A miss-only fallback re-orphans those
rows the moment the first 0.42.0 write creates the resolved bucket. The verb
moves the bucket once and leaves a receipt, and the receipt is what every
alias-aware reader below joins on.
"""

import json
import os
import pathlib
import re
import subprocess
from pathlib import Path

import pytest

from daimon_briefing import buckets, cli, store

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)


@pytest.fixture
def linked(tmp_path):
    """(literal path through a symlink, the real directory it points at).

    The issue's own shape: on macOS everything under /tmp is reached through a
    symlink, so a host handing the library its cwd hands it a literal path the
    resolved rule collapses."""
    real = tmp_path / "real-project"
    real.mkdir()
    link = tmp_path / "link-project"
    link.symlink_to(real, target_is_directory=True)
    return str(link), str(real)


def _plant(bucket_dir: Path, files: dict) -> None:
    bucket_dir.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (bucket_dir / name).write_text(body, encoding="utf-8")


def _row(event_id: str) -> str:
    return json.dumps({"event_id": event_id, "event": "asserted"}) + "\n"


def _checkpoint(marker: str, created: str, decisions=None) -> dict:
    """The envelope shape `field_table.ENVELOPE_RULES` validates.

    `session_id` is a CODE-OWNED envelope field: the serialize pipeline
    assigns it by direct `=` after stripping model output, field_table
    presence-validates it, and `store._pointer_stems` reads it off pointer
    files to protect their sessions from GC. A fixture that omits it produces
    a pointer no shipped path can produce, and measuring that fixture is how
    the first version of this file concluded real pointers have no session id.
    """
    return {
        "session_id": marker,
        "created": created,
        "working_context": {
            "active_topic": {"text": marker, "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [{"text": t, "trust": "inferred"}
                                 for t in (decisions or [f"decision {marker}"])],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }


def _write(project_dir, marker: str, created: str, decisions=None) -> None:
    """A REAL pointer, written by the store from a full envelope."""
    store.write_checkpoint(marker, _checkpoint(marker, created, decisions),
                           project_dir=project_dir)


def _marker(path: Path) -> str:
    """The `active_topic` text of a written pointer — how a test names which
    checkpoint landed in which chain slot, using a field the store really
    writes."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["working_context"]["active_topic"]["text"]


def _populate_legacy(link, sessions) -> Path:
    """Write real checkpoints for this project, then move the whole bucket to
    the name the pre-0.42.0 library would have given it. This is the only way
    to build a legacy bucket that is byte-for-byte what 0.41.0 left behind:
    the store will only ever write the resolved name.

    Sessions are written oldest first: `store._pointer_regresses` blocks a
    pointer update whose checkpoint is older than the one already there."""
    from daimon_briefing import config

    for marker, created in sessions:
        _write(link, marker, created)
    root = config.checkpoint_dir()
    legacy = root / (buckets.legacy_slug(link) or "")
    os.rename(root / (store.project_bucket(link) or ""), legacy)
    return legacy


def _stage_legacy_pointers(link, legacy: Path, sessions,
                           decisions=None) -> None:
    """Add real pointers to an ALREADY EXISTING legacy bucket, beside a live
    target bucket that must not be disturbed.

    `_populate_legacy` cannot do this: it renames the whole live bucket away.
    Here the checkpoints are written to a scratch project, so the store
    produces genuine pointer bytes, and only those files are moved across,
    restamped with the legacy slug the way a pre-0.42.0 write left them."""
    from daimon_briefing import config

    scratch = Path(str(legacy)).parent / "-scratch-stage"
    root = config.checkpoint_dir()
    for marker, created in sessions:
        # The store also rewrites the flat <session>.json on every write, and
        # a test about the flat file surviving a migration must not have its
        # own fixture rewrite it first.
        flat = root / f"{marker}.json"
        held = flat.read_bytes() if flat.exists() else None
        cp = _checkpoint(marker, created, decisions)
        cp["project_slug"] = buckets.legacy_slug(link)
        store.write_checkpoint(marker, cp, project_dir=str(scratch))
        if held is not None:
            flat.write_bytes(held)
    made = config.checkpoint_dir() / (store.project_bucket(str(scratch)) or "")
    for path in sorted(made.iterdir()):
        if path.name.endswith(".json"):
            dest = legacy / path.name
            index = 1
            while dest.exists():
                dest = legacy / f"prev-{index}.json"
                index += 1
            path.replace(dest)
    for leftover in made.iterdir():
        leftover.unlink()
    made.rmdir()


# ---------------------------------------------------------------------------
# the legacy rule
# ---------------------------------------------------------------------------


def test_the_legacy_rule_is_pinned_to_what_0_41_0_actually_computed():
    """v0.41.0's store.project_slug, transcribed: every char that is not a
    Python \\w char or '-' becomes '-', over the literal path. If this drifts,
    every bucket written before 0.42.0 stops being findable — the verb would
    look for a name nothing ever wrote."""
    def v0_41_0(path: str) -> str:
        return re.sub(r"[^\w-]", "-", str(path).strip())

    for path in ("/tmp/a project", "/private/tmp/a.proj", "/Users/x/repo/sub"):
        assert buckets.legacy_slug(path) == v0_41_0(path)


def test_the_legacy_rule_absolutizes_but_never_resolves(linked, monkeypatch):
    link, real = linked
    assert buckets.legacy_slug(link) == store.project_slug(link)
    assert buckets.legacy_slug(link) != store.project_slug(real)
    monkeypatch.chdir(os.path.dirname(link))
    assert buckets.legacy_slug(os.path.basename(link)) == \
        store.project_slug(link)


def test_an_unknown_project_has_no_legacy_slug():
    assert buckets.legacy_slug(None) is None
    assert buckets.legacy_slug("   ") is None


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------


def test_a_path_stable_under_both_rules_has_nothing_to_migrate(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    record = buckets.migrate(str(plain))
    assert record["mode"] == "stable"
    assert record["from_slug"] == record["to_slug"]


def test_a_nested_git_subdir_is_the_other_affected_shape(tmp_path,
                                                        tmp_checkpoint_dir):
    repo = tmp_path / "repo"
    (repo / "plugin").mkdir(parents=True)
    _init_git_repo(repo)
    sub = str(repo / "plugin")
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(sub) or "")
    _plant(legacy, {"events.jsonl": _row("e1")})

    record = buckets.migrate(sub)
    assert record["mode"] == "rename"
    assert record["to_slug"] == store.project_slug(str(repo))
    assert (tmp_checkpoint_dir / record["to_slug"] / "events.jsonl").exists()


def test_no_legacy_bucket_is_not_an_error(linked, tmp_checkpoint_dir):
    link, _ = linked
    record = buckets.migrate(link)
    assert record["mode"] == "absent"


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------


def test_a_legacy_bucket_alone_is_renamed_whole(linked, tmp_checkpoint_dir):
    link, real = linked
    legacy = _populate_legacy(link, [("S1", "2026-09-01T00:00:00Z")])
    _plant(legacy, {"events.jsonl": _row("e1")})

    record = buckets.migrate(link)

    target = tmp_checkpoint_dir / store.project_bucket(real)
    assert record["mode"] == "rename"
    assert not legacy.exists()
    assert (target / "events.jsonl").read_text(encoding="utf-8") == _row("e1")
    moved = json.loads((target / "latest.json").read_text(encoding="utf-8"))
    assert moved["project_slug"] == store.project_bucket(real), \
        "a pointer that moved still claims the bucket it left"


def test_a_second_run_after_a_rename_migrates_nothing(linked,
                                                      tmp_checkpoint_dir):
    link, _ = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})
    assert buckets.migrate(link)["mode"] == "rename"
    second = buckets.migrate(link)
    assert second["mode"] == "absent"
    assert len(buckets.records()) == 1, "a no-op run must not append a receipt"


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


def test_a_merge_appends_only_the_lines_the_target_lacks(linked,
                                                         tmp_checkpoint_dir):
    link, real = linked
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    target = tmp_checkpoint_dir / store.project_bucket(real)
    _plant(legacy, {"events.jsonl": _row("e1") + _row("e2")})
    _plant(target, {"events.jsonl": _row("e2") + _row("e3")})

    record = buckets.migrate(link)

    assert record["mode"] == "merge"
    assert record["ledgers"] == {"events.jsonl": 1}
    lines = (target / "events.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert lines.count(_row("e2").strip()) == 1
    assert sorted(lines) == sorted([_row(e).strip() for e in ("e1", "e2", "e3")])


def test_every_bucket_ledger_the_census_lists_is_merged(linked,
                                                        tmp_checkpoint_dir):
    link, real = linked
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    target = tmp_checkpoint_dir / store.project_bucket(real)
    for name in buckets.LEDGERS:
        _plant(legacy, {name: _row(f"{name}-1")})
    _plant(target, {"events.jsonl": _row("keep")})

    record = buckets.migrate(link)

    assert set(record["ledgers"]) == set(buckets.LEDGERS)
    for name in buckets.LEDGERS:
        assert _row(f"{name}-1").strip() in (target / name).read_text(
            encoding="utf-8")


def test_a_merge_leaves_an_unknown_file_alone_and_reports_it(
        linked, tmp_checkpoint_dir):
    """An unrecognized file is never deleted and never merged, so the bucket
    it sits in survives. `.pointer.lock` is deliberately NOT the example: it
    is a declared empty sidecar every real bucket carries, and treating it as
    unknown is what made the merge non-idempotent (see the test below)."""
    link, real = linked
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    target = tmp_checkpoint_dir / store.project_bucket(real)
    _plant(legacy, {"events.jsonl": _row("e1"),
                    "notes-from-a-human.txt": "do not delete me"})
    (legacy / ".pointer.lock").write_text("", encoding="utf-8")
    _plant(target, {"events.jsonl": _row("e0")})

    record = buckets.migrate(link)

    assert record["leftovers"] == ["notes-from-a-human.txt"]
    assert legacy.exists(), "a bucket still holding a file is never removed"
    assert (legacy / "notes-from-a-human.txt").read_text(
        encoding="utf-8") == "do not delete me"
    assert not (legacy / "events.jsonl").exists()


def test_a_real_merge_is_idempotent(linked, tmp_checkpoint_dir,
                                    monkeypatch):
    """Every bucket the store has ever written to carries a `.pointer.lock`,
    the empty flock sidecar. Treated as an unknown leftover it keeps the
    legacy directory alive forever: rmdir is skipped, the next run finds the
    bucket again, merges again, and appends a second receipt. The lock is
    declared in surfaces.py as an empty file holding no content, so a merge
    may remove it."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "5")
    link, real = linked
    _populate_legacy(link, [("S-old1", "2026-09-01T00:00:00Z"),
                            ("S-old2", "2026-09-02T00:00:00Z")])
    _write(link, "S-new1", "2026-09-03T00:00:00Z")
    _write(link, "S-new2", "2026-09-04T00:00:00Z")
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    assert (legacy / ".pointer.lock").exists(), \
        "a real bucket always carries the lock sidecar"

    first = buckets.migrate(link)
    second = buckets.migrate(link)

    assert first["mode"] == "merge"
    assert first["leftovers"] == []
    assert not legacy.exists()
    assert second["mode"] == "absent"
    assert len(buckets.records()) == 1


def test_a_fully_merged_legacy_bucket_is_removed(linked, tmp_checkpoint_dir):
    link, real = linked
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    _plant(legacy, {"events.jsonl": _row("e1")})
    _plant(tmp_checkpoint_dir / store.project_bucket(real),
           {"events.jsonl": _row("e0")})

    record = buckets.migrate(link)

    assert record["leftovers"] == []
    assert not legacy.exists()


# ---------------------------------------------------------------------------
# pointers
# ---------------------------------------------------------------------------


def test_a_real_pointer_carries_its_session_id(linked, tmp_checkpoint_dir):
    """The premise every pointer test rests on, measured against the shape the
    serialize pipeline actually produces.

    `session_id` is declared in `field_table.ENVELOPE_RULES` as a code-owned,
    presence-validated envelope field; the serializer assigns it after
    stripping model output; and `store._pointer_stems` reads it back off
    pointer files to protect those sessions from GC, returning None (no
    protection at all) for a pointer that lacks it. So a pointer without one
    is not a legacy shape to tolerate, it is a broken write."""
    from daimon_briefing import field_table, store as _store

    rule = [r for r in field_table.ENVELOPE_RULES if r.name == "session_id"]
    assert rule and rule[0].owner == "code"

    link, _ = linked
    legacy = _populate_legacy(link, [("S-old", "2026-09-01T00:00:00Z")])
    payload = json.loads((legacy / "latest.json").read_text(encoding="utf-8"))
    assert payload["session_id"] == "S-old"
    assert _store._pointer_stems(legacy) == {"S-old"}


def test_the_merged_chain_keeps_every_real_pointer_from_both_buckets(
        linked, tmp_checkpoint_dir, monkeypatch):
    """The data-loss case. Two real checkpoints per bucket, four distinct
    pointer files, and a chain with room for five: all four must survive.

    Identity keyed on a field real pointers do not carry collapses the two
    `latest.json` copies into one, drops the legacy chain on the floor, and
    still reports a pointer count and exit 0."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "5")
    link, real = linked
    _populate_legacy(link, [("S-old1", "2026-09-01T00:00:00Z"),
                            ("S-old2", "2026-09-02T00:00:00Z")])
    _write(link, "S-new1", "2026-09-03T00:00:00Z")
    _write(link, "S-new2", "2026-09-04T00:00:00Z")

    record = buckets.migrate(link)

    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    chain = [_marker(target / n) for n in
             ("latest.json", "prev-1.json", "prev-2.json", "prev-3.json")]
    assert chain == ["S-new2", "S-new1", "S-old2", "S-old1"]
    assert record["pointers"] == 2, "two legacy pointers landed"
    assert record["stranded_pointers"] == []
    assert not (target / "prev-4.json").exists()


def test_a_moved_pointer_copy_is_restamped_with_the_target_slug(
        linked, tmp_checkpoint_dir):
    link, real = linked
    target_slug = store.project_bucket(real) or ""
    _populate_legacy(link, [("S-old", "2026-09-01T00:00:00Z")])
    _write(link, "S-new", "2026-09-05T00:00:00Z")

    buckets.migrate(link)

    moved = json.loads((tmp_checkpoint_dir / target_slug /
                        "prev-1.json").read_text(encoding="utf-8"))
    assert _marker(tmp_checkpoint_dir / target_slug / "prev-1.json") == "S-old"
    assert moved["project_slug"] == target_slug


def test_the_same_session_evolved_in_one_bucket_does_not_evict_another(
        linked, tmp_checkpoint_dir, monkeypatch):
    """Hashing the whole payload made one session look like two.

    A checkpoint for session S-1 does not stay byte-identical across buckets:
    _stamp_first_seen, an anchor rewrite, a receipts stamp and this verb's own
    _restamp all add or change a field. The legacy copy and the evolved target
    copy then hash differently, both take a chain slot, and a genuinely
    distinct session falls off the end and is unlinked. Identity is the
    session id, which is exactly the field that answers "same capture?"."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    link, real = linked
    _populate_legacy(link, [("S-0", "2026-09-01T00:00:00Z"),
                            ("S-1", "2026-09-02T00:00:00Z")])
    # The same session, evolved: one more recent_decisions item.
    _write(link, "S-1", "2026-09-02T00:00:00Z",
           decisions=["decision S-1", "a later decision on the same session"])
    _write(link, "S-2", "2026-09-03T00:00:00Z")

    record = buckets.migrate(link)

    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    chain = [_marker(target / n) for n in
             ("latest.json", "prev-1.json", "prev-2.json")]
    assert chain == ["S-2", "S-1", "S-0"], "a distinct session was evicted"
    assert record["pointers"] == 1, "S-0 landed; the legacy S-1 was absorbed"
    assert record["stranded_pointers"] == []
    kept = json.loads((target / "prev-1.json").read_text(encoding="utf-8"))
    assert len(kept["working_context"]["recent_decisions"]) == 2, \
        "the older copy of S-1 won over the evolved one"


def test_a_malformed_legacy_pointer_still_gets_an_identity(
        linked, tmp_checkpoint_dir, monkeypatch):
    """The content hash is the FALLBACK, for a blob so old or so damaged that
    it carries no session id. It must still not collide with another one."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    link, real = linked
    legacy = _populate_legacy(link, [("S-0", "2026-09-01T00:00:00Z"),
                                     ("S-1", "2026-09-02T00:00:00Z")])
    for name in ("latest.json", "prev-1.json"):
        payload = json.loads((legacy / name).read_text(encoding="utf-8"))
        payload.pop("session_id")
        (legacy / name).write_text(json.dumps(payload), encoding="utf-8")
    _write(link, "S-2", "2026-09-03T00:00:00Z")

    record = buckets.migrate(link)

    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    assert record["pointers"] == 2
    assert [_marker(target / n) for n in
            ("latest.json", "prev-1.json", "prev-2.json")] == \
        ["S-2", "S-1", "S-0"]


def test_one_checkpoint_present_in_both_buckets_takes_one_chain_slot(
        linked, tmp_checkpoint_dir, monkeypatch):
    """The same capture can sit in both buckets — a migration interrupted and
    re-run, or a host that wrote through two paths. Identity is the
    checkpoint's own content, so the bucket-name fields a move rewrites do
    not make one checkpoint look like two."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "5")
    link, real = linked
    _populate_legacy(link, [("S-shared", "2026-09-01T00:00:00Z")])
    _write(link, "S-shared", "2026-09-01T00:00:00Z")
    _write(link, "S-new", "2026-09-05T00:00:00Z")

    record = buckets.migrate(link)

    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    assert record["pointers"] == 0, "the legacy copy was absorbed, not moved"
    assert [_marker(target / n) for n in ("latest.json", "prev-1.json")] == \
        ["S-new", "S-shared"]
    assert not (target / "prev-2.json").exists()


def test_a_target_pointer_is_never_evicted_by_older_legacy_work(
        linked, tmp_checkpoint_dir, monkeypatch):
    """THE INVARIANT. The target's pointers are the live project's own chain.
    A merge may add to it and may replace one slot with a newer copy of the
    same session; it may never unlink or evict what is already there.

    The union-and-cap design leaked data in three consecutive shapes because
    it treated both sides as interchangeable candidates for a fixed number of
    slots. Here the LEGACY sessions are the newer ones, so capping the union
    evicts the live project's own history: its pointers are unlinked, its
    sessions leave `store._pointer_stems`, and their flat checkpoints become
    GC-eligible."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    link, real = linked
    target_slug = store.project_bucket(real) or ""
    # Target first, and OLD: S-t0, S-t1, S-t2 in 2026-01.
    for marker, created in (("S-t0", "2026-01-01T00:00:00Z"),
                            ("S-t1", "2026-01-02T00:00:00Z"),
                            ("S-t2", "2026-01-03T00:00:00Z")):
        _write(link, marker, created)
    target = tmp_checkpoint_dir / target_slug
    held_before = {p.name: p.read_bytes() for p in target.iterdir()
                   if p.name.endswith(".json")}
    # Legacy second, and NEWER: 2026-06. Built beside the live bucket.
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    legacy.mkdir(parents=True, exist_ok=True)
    _stage_legacy_pointers(link, legacy,
                           [("S-l0", "2026-06-01T00:00:00Z"),
                            ("S-l1", "2026-06-02T00:00:00Z")])

    record = buckets.migrate(link)

    for name, blob in held_before.items():
        assert (target / name).read_bytes() == blob, \
            f"the target's own {name} was rewritten or evicted"
    assert store._pointer_stems(target) >= {"S-t0", "S-t1", "S-t2"}
    assert set(record["stranded_pointers"]) == {"S-l1", "S-l0"}
    assert record["complete"] is False
    assert legacy.exists(), "a bucket holding stranded pointers is never removed"


def test_raising_the_history_then_rerunning_finishes_the_move(
        linked, tmp_checkpoint_dir, monkeypatch, capsys):
    """The remedy the rc-1 message names has to actually work."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    link, real = linked
    for marker, created in (("S-t0", "2026-01-01T00:00:00Z"),
                            ("S-t1", "2026-01-02T00:00:00Z"),
                            ("S-t2", "2026-01-03T00:00:00Z")):
        _write(link, marker, created)
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    legacy.mkdir(parents=True, exist_ok=True)
    _stage_legacy_pointers(link, legacy,
                           [("S-l0", "2026-06-01T00:00:00Z"),
                            ("S-l1", "2026-06-02T00:00:00Z")])
    rc = cli.main(["bucket", "migrate", f"--project={link}"])
    named = capsys.readouterr().out
    assert rc == 1
    # The number the message names has to be the number that works, so the
    # remedy is taken from the message rather than from the test's own guess.
    wanted = int(named.split("to at least ")[1].split()[0])
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", str(wanted))
    final = buckets.migrate(link)

    assert final["complete"] is True
    assert final["pointers"] == 2
    assert not legacy.exists()
    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    assert store._pointer_stems(target) == {"S-t0", "S-t1", "S-t2",
                                            "S-l0", "S-l1"}
    assert len(buckets.records()) == 1, \
        "the first run moved nothing, so it recorded nothing"
    assert buckets.aliases_for(store.project_bucket(real)) == \
        frozenset({buckets.legacy_slug(link)})


def test_a_newer_legacy_copy_replaces_that_slot_and_leaves_the_flat_file(
        linked, tmp_checkpoint_dir, monkeypatch):
    """The one write into an existing slot: the legacy bucket holds a NEWER
    copy of a session the target already points at. The older bytes are not
    lost, because the flat per-session file still holds them."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    link, real = linked
    _write(link, "S-1", "2026-01-01T00:00:00Z")
    flat_before = (tmp_checkpoint_dir / "S-1.json").read_bytes()
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    legacy.mkdir(parents=True, exist_ok=True)
    _stage_legacy_pointers(link, legacy, [("S-1", "2026-06-01T00:00:00Z")],
                           decisions=["d S-1", "the later copy"])

    record = buckets.migrate(link)

    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    latest = json.loads((target / "latest.json").read_text(encoding="utf-8"))
    assert latest["created"] == "2026-06-01T00:00:00Z"
    assert len(latest["working_context"]["recent_decisions"]) == 2
    assert latest["project_slug"] == store.project_bucket(real)
    assert not (target / "prev-1.json").exists(), "a replace takes no new slot"
    assert (tmp_checkpoint_dir / "S-1.json").read_bytes() == flat_before
    assert record["complete"] is True
    assert record["pointers"] == 1


def test_an_empty_target_admits_what_fits_and_strands_the_rest(
        linked, tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    link, real = linked
    legacy = _populate_legacy(link, [("S-a", "2026-09-01T00:00:00Z"),
                                     ("S-b", "2026-09-02T00:00:00Z"),
                                     ("S-c", "2026-09-03T00:00:00Z")])
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "2")
    assert not (tmp_checkpoint_dir /
                (store.project_bucket(real) or "")).exists()
    # A target bucket with no pointers at all, so the rename path is not taken.
    _plant(tmp_checkpoint_dir / (store.project_bucket(real) or ""),
           {"events.jsonl": _row("e0")})

    record = buckets.migrate(link)

    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    assert record["pointers"] == 2
    assert record["stranded_pointers"] == ["S-a"]
    assert record["complete"] is False
    assert {_marker(target / n) for n in ("latest.json", "prev-1.json")} == \
        {"S-c", "S-b"}
    assert legacy.exists()


def test_an_unparseable_target_pointer_stops_admission_and_is_reported(
        linked, tmp_checkpoint_dir, monkeypatch):
    """A target file the merge cannot read is still an OCCUPIED slot. Counting
    slots it cannot count, then writing into them, is how the previous design
    overwrote a pointer it had already decided to skip."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    link, real = linked
    _write(link, "S-t0", "2026-01-01T00:00:00Z")
    _write(link, "S-t1", "2026-01-02T00:00:00Z")
    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    (target / "prev-1.json").write_bytes(b'{"session_id": "S-t0", "cre')
    garbage = (target / "prev-1.json").read_bytes()
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    legacy.mkdir(parents=True, exist_ok=True)
    _stage_legacy_pointers(link, legacy, [("S-l0", "2026-06-01T00:00:00Z")])
    _plant(legacy, {"events.jsonl": _row("e1")})

    record = buckets.migrate(link)

    assert record["target_unreadable"] == ["prev-1.json"]
    assert record["pointers"] == 0
    assert record["complete"] is False
    assert (target / "prev-1.json").read_bytes() == garbage
    assert record["ledgers"]["events.jsonl"] == 1, "ledgers still merge"


def test_a_legacy_bucket_gone_by_hand_finishes_the_record(
        linked, tmp_checkpoint_dir, monkeypatch, capsys):
    """A person who clears the old directory themselves has finished the
    migration. Without a closing row `partial:` warns forever and no verb can
    clear it, which is exactly what the old rc-1 text invited."""
    import shutil

    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    link, real = linked
    legacy = _populate_legacy(link, [("S-a", "2026-09-01T00:00:00Z"),
                                     ("S-b", "2026-09-02T00:00:00Z"),
                                     ("S-c", "2026-09-03T00:00:00Z")])
    _write(link, "S-new", "2026-09-05T00:00:00Z")
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "2")
    first = buckets.migrate(link)
    assert first["complete"] is False and first["pointers"] == 1, \
        "the first run has to land something, or it records no row at all"
    assert len(buckets.records()) == 1
    shutil.rmtree(legacy)

    record = buckets.migrate(link)

    assert record["mode"] == "absent"
    assert record["complete"] is True
    assert record["observed"] == "legacy bucket no longer exists"
    assert len(buckets.records()) == 2
    assert buckets.aliases_for(store.project_bucket(real)) == \
        frozenset({buckets.legacy_slug(link)})

    capsys.readouterr()
    cli.main(["status", f"--project={link}"])
    out = capsys.readouterr().out
    assert "migrated: from" in out
    assert "(finished outside daimon)" in out
    assert "partial:" not in out


def test_a_superseded_duplicate_pointer_does_not_strand_the_bucket(
        linked, tmp_checkpoint_dir, monkeypatch):
    """A legacy pointer for a session the target already holds a NEWER copy of
    is absorbed, not orphaned.

    It is neither kept (the newer copy won the slot) nor dropped (it fits the
    chain), so a file-identity check leaves it on disk: the bucket never
    empties, the merge is marked partial forever, and `status` warns about a
    migration that is in fact finished. Identity is what was absorbed, not
    which file it came from."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "9")
    link, real = linked
    legacy = _populate_legacy(link, [("S-1", "2026-09-01T00:00:00Z")])
    _write(link, "S-1", "2026-09-04T00:00:00Z",
           decisions=["decision S-1", "the same session, later"])

    record = buckets.migrate(link)

    assert record["stranded_pointers"] == []
    assert record["leftovers"] == []
    assert record["complete"] is True
    assert not legacy.exists()
    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    assert record["pointers"] == 0, \
        "the target already held the newer copy, so nothing was written"
    assert _marker(target / "latest.json") == "S-1"


def test_the_chain_honors_the_configured_history(linked, tmp_checkpoint_dir,
                                                 monkeypatch):
    """What does not fit is REPORTED and LEFT, never silently unlinked. A
    dropped pointer takes its session out of `store._pointer_stems`, so the
    flat checkpoint it protected becomes GC-eligible: silent chain overflow is
    data loss wearing a smaller hat."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "2")
    link, real = linked
    legacy = _populate_legacy(link, [("S-oldest", "2026-08-01T00:00:00Z"),
                                     ("S-old", "2026-09-01T00:00:00Z")])
    _write(link, "S-new", "2026-09-05T00:00:00Z")

    record = buckets.migrate(link)

    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    assert not (target / "prev-2.json").exists()
    assert _marker(target / "latest.json") == "S-new"
    assert _marker(target / "prev-1.json") == "S-old"
    assert record["stranded_pointers"] == ["S-oldest"]
    assert record["complete"] is False
    assert legacy.exists(), "a stranded pointer is kept where it is"
    assert _marker(legacy / "prev-1.json") == "S-oldest"
    assert "prev-1.json" in record["leftovers"]


def test_a_chain_of_one_keeps_every_pointer_it_cannot_hold(
        linked, tmp_checkpoint_dir, monkeypatch, capsys):
    """The refuter's input: HISTORY=1 and three legacy pointers. All three
    used to be unlinked while the receipt reported pointers: 1 at rc 0."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    link, real = linked
    legacy = _populate_legacy(link, [("S-a", "2026-09-01T00:00:00Z"),
                                     ("S-b", "2026-09-02T00:00:00Z"),
                                     ("S-c", "2026-09-03T00:00:00Z")])
    _write(link, "S-new", "2026-09-05T00:00:00Z")
    # The chain shrinks AFTER the history was written, which is how a real
    # install reaches this: the knob is lowered, or the legacy bucket came
    # from a daimon configured to keep more.
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "1")

    rc = cli.main(["bucket", "migrate", f"--project={link}"])

    out = capsys.readouterr().out
    assert rc == 1
    assert "kept where they are: S-c, S-b, S-a" in out or \
        "S-a" in out and "S-b" in out and "S-c" in out
    assert legacy.exists()
    surviving = {_marker(p) for p in legacy.iterdir()
                 if p.name.endswith(".json")}
    assert surviving == {"S-a", "S-b", "S-c"}


def test_the_named_history_lands_the_pointer_when_the_target_is_overfull(
        linked, tmp_checkpoint_dir, monkeypatch, capsys):
    """The remedy has to be arithmetic on what is actually there.

    Deriving it from `DAIMON_CHECKPOINT_HISTORY` assumes the target occupies
    exactly that many slots. A target holding MORE than the current history
    (the knob was lowered after those pointers were written) makes the message
    name a number that strands the same pointer all over again."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "4")
    link, real = linked
    for index, created in enumerate(("2026-01-01T00:00:00Z",
                                     "2026-01-02T00:00:00Z",
                                     "2026-01-03T00:00:00Z",
                                     "2026-01-04T00:00:00Z")):
        _write(link, f"S-t{index}", created)
    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    assert len(list(target.glob("*.json"))) == 4
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    legacy.mkdir(parents=True, exist_ok=True)
    _stage_legacy_pointers(link, legacy, [("S-l0", "2026-06-01T00:00:00Z")])
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "2")

    assert cli.main(["bucket", "migrate", f"--project={link}"]) == 1
    named = capsys.readouterr().out
    wanted = int(named.split("to at least ")[1].split()[0])

    assert wanted == 5, "four occupied slots plus the one stranded pointer"
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", str(wanted))
    final = buckets.migrate(link)

    assert final["complete"] is True
    assert final["stranded_pointers"] == []
    assert store._pointer_stems(target) == {"S-t0", "S-t1", "S-t2", "S-t3",
                                            "S-l0"}
    assert buckets.aliases_for(store.project_bucket(real)) == \
        frozenset({buckets.legacy_slug(link)})


def test_a_leftover_daimon_could_not_read_is_not_called_someone_elses(
        linked, tmp_checkpoint_dir, capsys):
    """A dangling symlink named `prev-2.json` carries daimon's own naming.
    Saying it "is not written by daimon" claims an authorship nobody can
    check; the line describes what the file is instead."""
    link, real = linked
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    legacy.mkdir(parents=True, exist_ok=True)
    _plant(legacy, {"events.jsonl": _row("e1")})
    (legacy / "prev-2.json").symlink_to(legacy / "nothing-here.json")
    _plant(tmp_checkpoint_dir / (store.project_bucket(real) or ""),
           {"events.jsonl": _row("e0")})

    assert cli.main(["bucket", "migrate", f"--project={link}"]) == 1

    out = capsys.readouterr().out
    assert "prev-2.json" in out
    assert "not written by daimon" not in out
    assert f"move it out of {buckets.legacy_slug(link)} to finish" in out


def test_a_dry_run_lists_the_pointers_that_will_not_fit(
        linked, tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    link, _ = linked
    _populate_legacy(link, [("S-a", "2026-09-01T00:00:00Z"),
                            ("S-b", "2026-09-02T00:00:00Z")])
    _write(link, "S-new", "2026-09-05T00:00:00Z")
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "1")

    planned = buckets.migrate(link, dry_run=True)
    applied = buckets.migrate(link)

    assert planned["stranded_pointers"] == applied["stranded_pointers"]
    assert planned["stranded_pointers"] == ["S-b", "S-a"]


def test_a_legacy_bucket_with_no_pointers_leaves_the_chain_alone(
        linked, tmp_checkpoint_dir):
    """The target's chain is already the answer, and latest.json is the file
    every briefing read goes through: rewriting it with its own contents is
    churn on the hot path for no change."""
    link, real = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})
    _write(link, "S-new", "2026-09-05T00:00:00Z")
    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    before = (target / "latest.json").read_bytes()

    record = buckets.migrate(link)

    assert record["pointers"] == 0
    assert (target / "latest.json").read_bytes() == before


def test_every_bucket_ledger_shape_the_registry_declares_is_merged():
    """The registry is the single declaration of what daimon writes; this
    verb has to move all of it. A new bucket ledger that lands there and not
    in LEDGERS would be left behind by every migration, silently."""
    from daimon_briefing import surfaces

    declared = {s.shape.rsplit("/", 1)[-1] for s in surfaces.SURFACES
                if s.shape.startswith("checkpoints/{slug}/")
                and s.shape.endswith(".jsonl")}
    assert declared == set(buckets.LEDGERS)


def test_a_flat_checkpoint_file_is_never_touched(linked, tmp_checkpoint_dir):
    """The flat per-session files are receipt-signed over their exact bytes
    (receipts.outputs_hash). Editing one to restamp its slug would break
    `daimon verify-receipt` for a session that did nothing wrong."""
    link, _ = linked
    legacy = _populate_legacy(link, [("S-old", "2026-09-01T00:00:00Z")])
    flat = tmp_checkpoint_dir / "S-old.json"
    # A genuine 0.41.0 flat file carries the LITERAL-path slug, so stamp it
    # that way: the point is that a migration leaves those bytes alone even
    # when the stamp inside them is the one it is moving away from.
    payload = json.loads(flat.read_text(encoding="utf-8"))
    payload["project_slug"] = buckets.legacy_slug(link)
    flat.write_text(json.dumps(payload), encoding="utf-8")
    before = flat.read_bytes()
    _plant(legacy, {"events.jsonl": _row("e1")})

    buckets.migrate(link)

    assert flat.read_bytes() == before


# ---------------------------------------------------------------------------
# the receipt
# ---------------------------------------------------------------------------


def test_the_migration_appends_one_receipt_record(linked, tmp_checkpoint_dir):
    link, real = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})

    record = buckets.migrate(link)

    rows = buckets.records()
    assert len(rows) == 1
    assert rows[0]["version"] == 1
    assert rows[0]["from_slug"] == buckets.legacy_slug(link)
    assert rows[0]["to_slug"] == store.project_bucket(real)
    assert rows[0]["mode"] == "rename"
    assert rows[0]["by"] == "cli"
    assert rows[0]["ts"] and rows[0]["ledgers"] == record["ledgers"]
    assert (tmp_checkpoint_dir / "migrations.jsonl").exists()


def test_a_dry_run_writes_nothing(linked, tmp_checkpoint_dir):
    link, _ = linked
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    _plant(legacy, {"events.jsonl": _row("e1")})

    record = buckets.migrate(link, dry_run=True)

    assert record["mode"] == "rename"
    assert legacy.exists()
    assert not (tmp_checkpoint_dir / "migrations.jsonl").exists()
    assert buckets.records() == []


def test_a_dry_run_counts_the_lines_a_merge_would_append(linked,
                                                         tmp_checkpoint_dir):
    link, real = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1") + _row("e2")})
    target = tmp_checkpoint_dir / store.project_bucket(real)
    _plant(target, {"events.jsonl": _row("e2")})

    record = buckets.migrate(link, dry_run=True)

    assert record["mode"] == "merge"
    assert record["ledgers"] == {"events.jsonl": 1}
    assert (target / "events.jsonl").read_text(encoding="utf-8") == _row("e2")


# ---------------------------------------------------------------------------
# aliases
# ---------------------------------------------------------------------------


def test_no_migrations_file_means_no_aliases(tmp_checkpoint_dir):
    assert buckets.aliases_for("-any-slug") == frozenset()
    assert buckets.records() == []


def test_a_torn_line_never_takes_the_alias_read_down(tmp_checkpoint_dir):
    tmp_checkpoint_dir.mkdir(parents=True, exist_ok=True)
    (tmp_checkpoint_dir / "migrations.jsonl").write_text(
        '{"version": 1, "from_slug": "-a", "to_slug": "-b"}\n'
        '{"version": 1, "from_sl\n'
        '{"version": 1, "from_slug": "-c", "to_slug": "-b"}\n',
        encoding="utf-8")
    assert buckets.aliases_for("-b") == frozenset({"-a", "-c"})


def test_an_alias_chain_reaches_the_final_bucket(tmp_checkpoint_dir):
    tmp_checkpoint_dir.mkdir(parents=True, exist_ok=True)
    (tmp_checkpoint_dir / "migrations.jsonl").write_text(
        '{"version": 1, "from_slug": "-a", "to_slug": "-b"}\n'
        '{"version": 1, "from_slug": "-b", "to_slug": "-c"}\n',
        encoding="utf-8")
    assert buckets.aliases_for("-c") == frozenset({"-a", "-b"})
    assert buckets.alias_map()["-a"] == "-c"


def test_an_alias_cycle_terminates(tmp_checkpoint_dir):
    tmp_checkpoint_dir.mkdir(parents=True, exist_ok=True)
    (tmp_checkpoint_dir / "migrations.jsonl").write_text(
        '{"version": 1, "from_slug": "-a", "to_slug": "-b"}\n'
        '{"version": 1, "from_slug": "-b", "to_slug": "-a"}\n',
        encoding="utf-8")
    assert buckets.alias_map()  # terminates, whatever it decides
    assert "-a" in buckets.aliases_for("-b")


def test_alias_provenance_carries_the_stamp_status_renders(
        linked, tmp_checkpoint_dir):
    link, real = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})
    buckets.migrate(link)

    prov = buckets.alias_provenance(store.project_bucket(real))
    assert [p["slug"] for p in prov] == [buckets.legacy_slug(link)]
    assert prov[0]["ts"].endswith("Z")


# ---------------------------------------------------------------------------
# the legacy-bucket signal every reporting surface joins on
# ---------------------------------------------------------------------------


def test_legacy_bucket_names_the_orphan_only_when_it_is_really_there(
        linked, tmp_checkpoint_dir):
    link, _ = linked
    assert buckets.legacy_bucket(link) is None
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})
    assert buckets.legacy_bucket(link) == buckets.legacy_slug(link)
    buckets.migrate(link)
    assert buckets.legacy_bucket(link) is None


def test_legacy_bucket_is_silent_when_the_two_rules_agree(tmp_path,
                                                          tmp_checkpoint_dir):
    plain = tmp_path / "plain"
    plain.mkdir()
    _plant(tmp_checkpoint_dir / (store.project_bucket(str(plain)) or ""),
           {"events.jsonl": _row("e1")})
    assert buckets.legacy_bucket(str(plain)) is None


def test_a_blank_line_and_an_empty_slug_are_both_survivable(
        tmp_checkpoint_dir):
    tmp_checkpoint_dir.mkdir(parents=True, exist_ok=True)
    (tmp_checkpoint_dir / "migrations.jsonl").write_text(
        '\n'
        '{"version": 1, "from_slug": "-a", "to_slug": ""}\n'
        '{"version": 1, "from_slug": "-a", "to_slug": "-b"}\n',
        encoding="utf-8")
    assert buckets.aliases_for("") == frozenset()
    assert buckets.aliases_for(None) == frozenset()
    assert buckets.aliases_for("-b") == frozenset({"-a"})


def test_an_unreadable_project_migrates_nothing(tmp_checkpoint_dir):
    record = buckets.migrate("")
    assert record["mode"] == "unknown"
    assert buckets.records() == []


def test_a_torn_ledger_tail_is_healed_before_the_merge_appends(
        linked, tmp_checkpoint_dir):
    """Every ledger appender in the package heals a missing final newline
    first; a merge that did not would glue its first line onto a torn one."""
    link, real = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})
    target = tmp_checkpoint_dir / store.project_bucket(real)
    _plant(target, {"events.jsonl": _row("e0").rstrip("\n")})

    buckets.migrate(link)

    lines = (target / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines == [_row("e0").strip(), _row("e1").strip()]


def test_a_torn_pointer_copy_is_skipped_not_promoted(linked,
                                                     tmp_checkpoint_dir):
    link, real = linked
    legacy = _populate_legacy(link, [("S-old", "2026-09-01T00:00:00Z"),
                                     ("S-keep", "2026-09-02T00:00:00Z")])
    # The newest legacy pointer is torn; the older one is intact.
    (legacy / "latest.json").write_text('{"created": "2026-09-02T00',
                                        encoding="utf-8")
    _write(link, "S-new", "2026-09-05T00:00:00Z")

    record = buckets.migrate(link)

    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    assert record["pointers"] == 1
    assert [_marker(target / n) for n in ("latest.json", "prev-1.json")] == \
        ["S-new", "S-old"]
    # No identity, so nothing can be compared to it: left where it is and
    # named, never passed over silently.
    assert record["stranded_pointers"] == ["latest.json"]
    assert (legacy / "latest.json").exists()


def test_a_pointer_with_no_slug_stamp_is_left_as_it_is(linked,
                                                       tmp_checkpoint_dir):
    """Restamping is for a copy that still names the bucket it left. A copy
    stamped with something else was not this bucket's to relabel."""
    link, real = linked
    target_slug = store.project_bucket(real) or ""
    legacy = _populate_legacy(link, [("S-old", "2026-09-01T00:00:00Z")])
    # A pre-#672 pointer: written before write_checkpoint stamped the bucket.
    payload = json.loads((legacy / "latest.json").read_text(encoding="utf-8"))
    payload.pop("project_slug")
    (legacy / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    _write(link, "S-new", "2026-09-05T00:00:00Z")

    buckets.migrate(link)

    moved = json.loads((tmp_checkpoint_dir / target_slug /
                        "prev-1.json").read_text(encoding="utf-8"))
    assert _marker(tmp_checkpoint_dir / target_slug / "prev-1.json") == "S-old"
    assert "project_slug" not in moved


def test_a_dry_run_counts_the_pointer_chain_it_would_write(
        linked, tmp_checkpoint_dir):
    link, real = linked
    legacy = _populate_legacy(link, [("S-old", "2026-09-01T00:00:00Z")])
    _write(link, "S-new", "2026-09-05T00:00:00Z")
    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    before = (target / "latest.json").read_bytes()

    record = buckets.migrate(link, dry_run=True)

    assert record["pointers"] == 1
    assert (target / "latest.json").read_bytes() == before
    assert (legacy / "latest.json").exists()


def test_a_cross_device_rename_of_a_pointer_only_bucket_still_lands(
        linked, tmp_checkpoint_dir, monkeypatch):
    """EXDEV on a bucket holding only a pointer chain. The target directory
    used to be created inside the ledger loop, so a legacy bucket with no
    ledger at all reached the pointer write against a directory that was
    never made: FileNotFoundError, no receipt, and the caller sees a
    traceback rather than a migration."""
    link, real = linked
    _populate_legacy(link, [("S-old", "2026-09-01T00:00:00Z")])

    def _exdev(*_a, **_k):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(buckets.os, "rename", _exdev)

    record = buckets.migrate(link)

    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    assert record["mode"] == "merge"
    assert record["pointers"] == 1
    assert _marker(target / "latest.json") == "S-old"
    assert len(buckets.records()) == 1


def test_a_dry_run_predicts_the_leftovers_the_real_run_produces(
        linked, tmp_checkpoint_dir):
    """A plan that names files the real run consumes is a plan nobody can act
    on: it says a bucket will survive when it is about to be removed."""
    link, real = linked
    legacy = _populate_legacy(link, [("S-old", "2026-09-01T00:00:00Z")])
    _plant(legacy, {"events.jsonl": _row("e1"),
                    "notes-from-a-human.txt": "keep me"})
    _write(link, "S-new", "2026-09-05T00:00:00Z")
    _plant(tmp_checkpoint_dir / (store.project_bucket(real) or ""),
           {"events.jsonl": _row("e0")})

    planned = buckets.migrate(link, dry_run=True)
    applied = buckets.migrate(link)

    assert planned["leftovers"] == ["notes-from-a-human.txt"]
    assert applied["leftovers"] == planned["leftovers"]
    assert planned["ledgers"] == applied["ledgers"]
    assert planned["pointers"] == applied["pointers"]


def test_a_dry_run_predicts_a_bucket_that_will_be_removed_entirely(
        linked, tmp_checkpoint_dir):
    link, real = linked
    _populate_legacy(link, [("S-old", "2026-09-01T00:00:00Z")])
    _write(link, "S-new", "2026-09-05T00:00:00Z")

    planned = buckets.migrate(link, dry_run=True)
    applied = buckets.migrate(link)

    assert planned["leftovers"] == []
    assert applied["leftovers"] == []
    assert not (tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")).exists()


def test_a_rename_that_the_filesystem_refuses_falls_back_to_the_merge(
        linked, tmp_checkpoint_dir, monkeypatch):
    """A cross-device store, or a race that created the target between the
    check and the call. The line-wise path is restartable and reaches the
    same end state, so it is the fallback rather than a failure."""
    link, real = linked
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    _plant(legacy, {"events.jsonl": _row("e1")})
    monkeypatch.setattr(buckets.os, "rename",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("EXDEV")))

    record = buckets.migrate(link)

    assert record["mode"] == "merge"
    assert record["ledgers"] == {"events.jsonl": 1}
    assert (tmp_checkpoint_dir / store.project_bucket(real) /
            "events.jsonl").read_text(encoding="utf-8") == _row("e1")


# ---------------------------------------------------------------------------
# a ledger that cannot be read is never deleted
# ---------------------------------------------------------------------------


def test_an_undecodable_ledger_is_kept_reported_and_never_merged(
        linked, tmp_checkpoint_dir):
    """A legacy ledger holding a byte sequence that is not UTF-8 cannot be
    read, so it cannot be compared, so it cannot be proven present in the
    target. Swallowing the decode error into an empty line list makes the
    move look complete and unlinks the source: the whole file is destroyed,
    the receipt says zero lines, and the exit code says success."""
    link, real = linked
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    legacy.mkdir(parents=True, exist_ok=True)
    corrupt = b'{"event_id":"e-legacy-REAL-ROW"}\n\xff\xfe bad\n'
    (legacy / "events.jsonl").write_bytes(corrupt)
    _plant(legacy, {"refutations.jsonl": _row("r1")})
    _plant(tmp_checkpoint_dir / (store.project_bucket(real) or ""),
           {"events.jsonl": _row("e0")})

    record = buckets.migrate(link)

    assert (legacy / "events.jsonl").read_bytes() == corrupt
    assert record["unreadable"] == ["events.jsonl"]
    assert "events.jsonl" not in record["ledgers"]
    assert record["leftovers"] == ["events.jsonl"]
    assert legacy.is_dir(), "a bucket still holding data is never removed"
    # the readable ledger beside it still moves
    assert record["ledgers"]["refutations.jsonl"] == 1
    assert not (legacy / "refutations.jsonl").exists()


def test_a_partial_merge_is_visible_in_the_exit_code(linked,
                                                     tmp_checkpoint_dir,
                                                     capsys):
    """The caller must be able to tell a partial merge from a full one
    without parsing the receipt."""
    link, real = linked
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "events.jsonl").write_bytes(b'{"a":1}\n\xff\xfe\n')
    _plant(legacy, {"refutations.jsonl": _row("r1")})
    _plant(tmp_checkpoint_dir / (store.project_bucket(real) or ""),
           {"events.jsonl": _row("e0")})

    rc = cli.main(["bucket", "migrate", f"--project={link}"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "could not be read" in captured.out
    assert "events.jsonl" in captured.out


def test_a_full_merge_still_exits_zero(linked, tmp_checkpoint_dir):
    link, real = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})
    _plant(tmp_checkpoint_dir / (store.project_bucket(real) or ""),
           {"events.jsonl": _row("e0")})
    assert cli.main(["bucket", "migrate", f"--project={link}"]) == 0


# ---------------------------------------------------------------------------
# lexical .. is the tenant vector, and it is refused
# ---------------------------------------------------------------------------


@pytest.fixture
def two_tenants(tmp_path):
    """The reviewer's exact shape: a symlink whose target is deep enough that
    lexical `..` climbs out of it into another tenant's tree.

    `legacy_slug` uses os.path.abspath, which collapses `..` LEXICALLY, before
    the symlink. `target_slug` uses Path.resolve, which follows the symlink
    FIRST and then applies `..`. The two therefore name different real
    directories, which is the one thing the tenant argument assumes cannot
    happen."""
    root = tmp_path / "root"
    (root / "home" / "me" / "a" / "b" / "c").mkdir(parents=True)
    (root / "home" / "tenantB" / "proj").mkdir(parents=True)
    link = root / "home" / "me" / "link"
    link.symlink_to(root / "home" / "me" / "a" / "b" / "c",
                    target_is_directory=True)
    victim = str(root / "home" / "tenantB" / "proj")
    return str(link) + "/../../tenantB/proj", victim


def test_the_two_rules_really_can_name_different_directories(two_tenants):
    """The premise, measured. If this ever stops holding, the refusal below
    is no longer load bearing and should be revisited rather than kept as
    folklore."""
    climbing, victim = two_tenants
    assert buckets.legacy_slug.__module__  # the rule under test exists
    assert os.path.abspath(climbing) != str(Path(climbing).resolve())
    assert store.project_slug(os.path.abspath(climbing)) == \
        store.project_slug(victim)


def test_a_climbing_project_path_is_refused_and_the_victim_untouched(
        two_tenants, tmp_checkpoint_dir, monkeypatch):
    climbing, victim = two_tenants
    planted = tmp_checkpoint_dir / (store.project_slug(victim) or "")
    _plant(planted, {"refutations.jsonl": _row("theirs")})
    before = (planted / "refutations.jsonl").read_bytes()
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")

    with pytest.raises(buckets.MigrationError) as exc:
        buckets.migrate(climbing)

    assert ".." in str(exc.value)
    assert planted.is_dir()
    assert (planted / "refutations.jsonl").read_bytes() == before
    assert buckets.records() == []


def test_the_verb_refuses_a_climbing_path_without_a_traceback(
        two_tenants, tmp_checkpoint_dir, capsys):
    """Deliberately NOT tenant-scoped: the tenant guard would answer first and
    this would stop testing the `..` refusal it names."""
    climbing, victim = two_tenants
    planted = tmp_checkpoint_dir / (store.project_slug(victim) or "")
    _plant(planted, {"refutations.jsonl": _row("theirs")})

    rc = cli.main(["bucket", "migrate", f"--project={climbing}"])

    captured = capsys.readouterr()
    assert rc == 2
    assert ".." in captured.err
    assert "Traceback" not in captured.err
    assert (planted / "refutations.jsonl").exists()


def test_a_climbing_path_reports_no_legacy_bucket_to_any_read_surface(
        two_tenants, tmp_checkpoint_dir):
    """`status` and `ruling list` name a legacy bucket as a fact. Naming the
    victim's would tell the caller a foreign bucket exists."""
    climbing, victim = two_tenants
    _plant(tmp_checkpoint_dir / (store.project_slug(victim) or ""),
           {"refutations.jsonl": _row("theirs")})
    assert buckets.legacy_bucket(climbing) is None


def test_a_plain_symlink_with_no_dotdot_still_migrates(linked,
                                                       tmp_checkpoint_dir):
    """The refusal is narrow on purpose: a symlink alone is the legitimate
    case #963 exists for, and refusing it would refuse the whole feature."""
    link, _ = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})
    assert buckets.migrate(link)["mode"] == "rename"


# ---------------------------------------------------------------------------
# a partial move is not a completed one
# ---------------------------------------------------------------------------


def _incomplete(link, tmp_checkpoint_dir):
    """A merge that leaves the legacy bucket standing: one unreadable ledger
    beside rows that do move."""
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "events.jsonl").write_bytes(b'{"a":1}\n\xff\xfe\n')
    _plant(legacy, {"requests.jsonl": _row("q1")})
    return legacy


def test_a_partial_merge_does_not_mint_a_completed_alias(
        linked, tmp_checkpoint_dir):
    """The alias says "this bucket's history now lives over there". While the
    legacy bucket still holds rows that is not true, and acting on it is
    worse than not having it: `requests.recipient_join` skips a bucket it
    considers its own, so the still-populated legacy bucket stops being
    scanned at all and its asks vanish from the inbox."""
    link, real = linked
    _incomplete(link, tmp_checkpoint_dir)
    _plant(tmp_checkpoint_dir / (store.project_bucket(real) or ""),
           {"events.jsonl": _row("e0")})

    record = buckets.migrate(link)

    assert record["complete"] is False
    assert buckets.aliases_for(store.project_bucket(real)) == frozenset()
    assert buckets.alias_map() == {}


def test_a_complete_merge_does_mint_the_alias(linked, tmp_checkpoint_dir):
    link, real = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})
    _plant(tmp_checkpoint_dir / (store.project_bucket(real) or ""),
           {"events.jsonl": _row("e0")})

    record = buckets.migrate(link)

    assert record["complete"] is True
    assert buckets.aliases_for(store.project_bucket(real)) == \
        frozenset({buckets.legacy_slug(link)})


def test_a_rerun_over_a_leftover_bucket_appends_no_second_receipt(
        linked, tmp_checkpoint_dir, capsys):
    """Three runs used to mean three receipt rows and three `migrated:` lines
    in status. A run that moves nothing has nothing to record."""
    link, real = linked
    _incomplete(link, tmp_checkpoint_dir)
    _plant(tmp_checkpoint_dir / (store.project_bucket(real) or ""),
           {"events.jsonl": _row("e0")})

    cli.main(["bucket", "migrate", f"--project={link}"])
    capsys.readouterr()
    rc = cli.main(["bucket", "migrate", f"--project={link}"])
    out = capsys.readouterr().out
    cli.main(["bucket", "migrate", f"--project={link}"])

    assert rc == 1
    assert len(buckets.records()) == 1
    assert "events.jsonl could not be read" in out
    assert "fix or move that file, then run again" in out
    assert "remove" not in out, "the text must not invite deleting the bucket"


def test_running_it_twice_is_safe_in_every_standing_state(
        linked, tmp_checkpoint_dir, monkeypatch):
    """The docs say "safe to run twice". Pinned here rather than asserted in
    prose: a second run over each standing state writes no new row and returns
    the code that matches the state it found."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    link, real = linked
    # complete: a clean rename, then a re-run.
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})
    assert cli.main(["bucket", "migrate", f"--project={link}"]) == 0
    assert cli.main(["bucket", "migrate", f"--project={link}"]) == 0
    assert len(buckets.records()) == 1

    # partial: a stranded pointer, twice.
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    legacy.mkdir(parents=True, exist_ok=True)
    _stage_legacy_pointers(link, legacy,
                           [("S-x", "2026-06-01T00:00:00Z"),
                            ("S-y", "2026-06-02T00:00:00Z"),
                            ("S-z", "2026-06-03T00:00:00Z")])
    _write(link, "S-live1", "2026-07-01T00:00:00Z")
    _write(link, "S-live2", "2026-07-02T00:00:00Z")
    _write(link, "S-live3", "2026-07-03T00:00:00Z")
    first = cli.main(["bucket", "migrate", f"--project={link}"])
    rows_after_first = len(buckets.records())
    second = cli.main(["bucket", "migrate", f"--project={link}"])

    assert first == 1 and second == 1, "the standing state is still partial"
    assert len(buckets.records()) == rows_after_first, \
        "a second run over an unchanged partial state records nothing new"


def test_a_rerun_over_a_stranded_pointer_appends_no_second_receipt(
        linked, tmp_checkpoint_dir, monkeypatch):
    """The no-receipt rule has to measure what MOVED, not the chain length.

    A re-run rebuilds the same chain over the same union, so the pointer
    count is non-zero every time even though nothing was absorbed. Gating on
    that count appends a receipt per run for a bucket a human has not
    cleared yet."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    link, _ = linked
    _populate_legacy(link, [("S-0", "2026-09-01T00:00:00Z"),
                            ("S-1", "2026-09-02T00:00:00Z"),
                            ("S-2", "2026-09-03T00:00:00Z")])
    _write(link, "S-3", "2026-09-05T00:00:00Z")

    first = buckets.migrate(link)
    second = buckets.migrate(link)

    assert first["complete"] is False
    assert first["stranded_pointers"] == ["S-0"]
    assert second["ledgers"] == {}
    assert len(buckets.records()) == 1, "a run that moved nothing recorded one"


def test_finishing_a_partial_migration_by_hand_completes_the_receipt(
        linked, tmp_checkpoint_dir, monkeypatch, capsys):
    """A migration can finish across two runs: the first moves the rows and
    strands a pointer, a person clears it, the second finds nothing left.
    That second run moves no bytes, but it DOES change the answer to "is this
    bucket migrated", so it records one. Without it the alias is never minted
    and `status` warns about an unfinished migration forever."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    link, real = linked
    legacy = _populate_legacy(link, [("S-0", "2026-09-01T00:00:00Z"),
                                     ("S-1", "2026-09-02T00:00:00Z"),
                                     ("S-2", "2026-09-03T00:00:00Z")])
    _write(link, "S-3", "2026-09-05T00:00:00Z")
    assert buckets.migrate(link)["complete"] is False

    for stranded in list(legacy.glob("prev-*.json")):
        stranded.unlink()
    final = buckets.migrate(link)

    assert final["complete"] is True
    assert not legacy.exists()
    assert len(buckets.records()) == 2
    assert buckets.aliases_for(store.project_bucket(real)) == \
        frozenset({buckets.legacy_slug(link)})
    assert buckets.incomplete_for(store.project_bucket(real)) == ()

    capsys.readouterr()
    cli.main(["status", f"--project={link}"])
    out = capsys.readouterr().out
    assert "partial:" not in out
    assert "migrated: from" in out


def test_the_legacy_warning_names_a_stranded_pointer(
        linked, tmp_checkpoint_dir, monkeypatch, capsys):
    """A pointer left behind because the chain was full is something daimon
    will not move, so the warning has to name it. Before any migration the
    same file WOULD be moved, and the warning stays plain."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    link, _ = linked
    _populate_legacy(link, [("S-0", "2026-09-01T00:00:00Z"),
                            ("S-1", "2026-09-02T00:00:00Z"),
                            ("S-2", "2026-09-03T00:00:00Z")])
    _write(link, "S-3", "2026-09-05T00:00:00Z")
    buckets.migrate(link)
    capsys.readouterr()

    cli.main(["status", f"--project={link}"])

    out = capsys.readouterr().out
    assert "still holds" in out and "prev-" in out


@pytest.mark.parametrize("scoped", [False, True])
def test_status_prints_a_remedy_that_actually_runs(linked, tmp_checkpoint_dir,
                                                   monkeypatch, capsys,
                                                   scoped):
    """A tenant-scoped home refuses `--project` at rc 2, so printing it hands
    the reader a command that cannot work."""
    link, _ = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})
    if scoped:
        monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")

    cli.main(["status", f"--project={link}"])

    out = capsys.readouterr().out
    if scoped:
        assert "run daimon bucket migrate with DAIMON_PROJECT_DIR set to" in out
        assert "--project" not in out
    else:
        assert f"run daimon bucket migrate --project {link}" in out


@pytest.mark.parametrize("scoped", [False, True])
def test_ruling_list_prints_a_remedy_that_actually_runs(
        linked, tmp_checkpoint_dir, monkeypatch, capsys, scoped):
    link, _ = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"refutations.jsonl": _row("r1")})
    if scoped:
        monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")

    assert cli.main(["ruling", "list", f"--project={link}"]) == 1

    err = capsys.readouterr().err
    if scoped:
        assert "run daimon bucket migrate with DAIMON_PROJECT_DIR set to" in err
    else:
        assert f"run daimon bucket migrate --project {link}" in err


def test_status_shows_one_migrated_line_per_pair(linked, tmp_checkpoint_dir,
                                                 capsys):
    link, _ = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})
    buckets.migrate(link)
    # A duplicate row, as a hand-edited or re-run history would leave.
    _migration(tmp_checkpoint_dir, buckets.legacy_slug(link) or "",
               store.project_bucket(link) or "")
    capsys.readouterr()

    cli.main(["status", f"--project={link}"])

    out = capsys.readouterr().out
    assert out.count("migrated: from") == 1


def test_status_warns_while_a_migration_is_incomplete(linked,
                                                      tmp_checkpoint_dir,
                                                      capsys):
    link, real = linked
    _incomplete(link, tmp_checkpoint_dir)
    _plant(tmp_checkpoint_dir / (store.project_bucket(real) or ""),
           {"events.jsonl": _row("e0")})
    buckets.migrate(link)
    capsys.readouterr()

    cli.main(["status", f"--project={link}"])

    out = capsys.readouterr().out
    assert "partial:" in out
    assert "migrated: from" not in out


def test_the_legacy_warning_is_plain_before_any_migration_has_run(
        linked, tmp_checkpoint_dir, capsys):
    """A bucket nobody has tried to migrate yet has nothing "in the way": its
    ledgers and pointers are exactly what the verb moves. Telling the reader
    daimon will not move them is false and sends them deleting history by
    hand."""
    link, _ = linked
    _populate_legacy(link, [("S-old", "2026-09-01T00:00:00Z")])
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})

    cli.main(["status", f"--project={link}"])

    out = capsys.readouterr().out
    assert "legacy: bucket" in out
    assert "still holds" not in out
    assert "will not move or delete" not in out


def test_a_stranded_pointer_is_reported_once_and_named_for_what_it_is(
        linked, tmp_checkpoint_dir, monkeypatch, capsys):
    """A pointer that did not fit the chain is already named on the dropped
    line. Repeating its filename as "not understood" tells the reader daimon
    does not know what a pointer file is."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    link, _ = linked
    _populate_legacy(link, [("S-a", "2026-09-01T00:00:00Z"),
                            ("S-b", "2026-09-02T00:00:00Z")])
    _write(link, "S-c", "2026-09-03T00:00:00Z")
    _write(link, "S-d", "2026-09-04T00:00:00Z")
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "2")

    cli.main(["bucket", "migrate", f"--project={link}"])

    out = capsys.readouterr().out
    assert "found no free slot" in out
    assert "raise DAIMON_CHECKPOINT_HISTORY to at least" in out
    assert "not written by daimon" not in out


def test_the_legacy_warning_names_what_is_still_in_the_way(
        linked, tmp_checkpoint_dir, capsys):
    link, real = linked
    _incomplete(link, tmp_checkpoint_dir)
    _plant(tmp_checkpoint_dir / (store.project_bucket(real) or ""),
           {"events.jsonl": _row("e0")})
    buckets.migrate(link)
    capsys.readouterr()

    cli.main(["status", f"--project={link}"])

    out = capsys.readouterr().out
    assert "still holds" in out and "events.jsonl" in out


def test_the_record_fields_are_all_declared_in_the_registry():
    """surfaces.py is the single declaration of what daimon writes. A field
    added to the receipt and not to the entry is a shape nobody reviewed."""
    from daimon_briefing import surfaces

    entry = [s for s in surfaces.SURFACES
             if s.shape == "checkpoints/migrations.jsonl"]
    assert entry
    doc = surfaces.__doc__ or ""
    source = pathlib.Path(surfaces.__file__).read_text(encoding="utf-8")
    # Derived from the record itself, so a field added there and not to the
    # registry entry fails here rather than waiting for someone to notice.
    from daimon_briefing import buckets as _buckets

    for field in _buckets._record("merge", "-a", "-b"):
        assert field in source, f"{field} is not declared in surfaces.py"
    assert doc


# ---------------------------------------------------------------------------
# a tenant-scoped home does not let a caller aim this verb
# ---------------------------------------------------------------------------


@pytest.fixture
def foreign_targets(tmp_path):
    """Two symlinks that reach another tenant WITHOUT a literal `..` in the
    value the caller passes: one whose TARGET climbs, one that simply points
    at a foreign absolute directory."""
    root = tmp_path / "root"
    (root / "home" / "me").mkdir(parents=True)
    victim = root / "home" / "tenantB" / "proj"
    victim.mkdir(parents=True)
    climb = root / "home" / "me" / "climb"
    climb.symlink_to("../tenantB/proj", target_is_directory=True)
    plain = root / "home" / "me" / "plain"
    plain.symlink_to(victim, target_is_directory=True)
    return str(climb), str(plain), str(victim)


def test_a_symlink_target_can_climb_without_a_dotdot_in_the_value(
        foreign_targets):
    """climbs_out reads the VALUE, and the value here is clean. The refusal
    below cannot rest on it."""
    climb, plain, victim = foreign_targets
    assert not buckets.climbs_out(climb)
    assert not buckets.climbs_out(plain)
    assert str(Path(climb).resolve()) == str(Path(victim).resolve())


@pytest.mark.parametrize("which", [0, 1])
def test_a_tenant_scoped_home_refuses_an_explicit_project(
        foreign_targets, tmp_checkpoint_dir, monkeypatch, capsys, which):
    """#899: on a tenant-scoped home the project is host-set. A caller who can
    aim this verb can mint a permanent from->to row in the GLOBAL receipt
    file, and recall plus the requests inbox honor it for the victim
    afterwards. The path reach is not new; the durable alias is."""
    target = foreign_targets[which]
    victim = foreign_targets[2]
    planted = tmp_checkpoint_dir / (store.project_slug(victim) or "")
    _plant(planted, {"refutations.jsonl": _row("theirs")})
    before = (planted / "refutations.jsonl").read_bytes()
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")

    rc = cli.main(["bucket", "migrate", f"--project={target}"])

    err = capsys.readouterr().err
    assert rc == 2
    assert "tenant-scoped" in err
    assert buckets.records() == []
    assert (planted / "refutations.jsonl").read_bytes() == before


def test_a_tenant_scoped_home_still_migrates_its_own_project(
        linked, tmp_checkpoint_dir, monkeypatch, capsys):
    link, real = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})
    monkeypatch.setenv("DAIMON_PROJECT_DIR", link)
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")

    assert cli.main(["bucket", "migrate"]) == 0
    assert (tmp_checkpoint_dir / (store.project_bucket(real) or "") /
            "events.jsonl").exists()


def test_the_dotdot_refusal_says_what_to_pass_and_why(two_tenants):
    climbing, _ = two_tenants
    with pytest.raises(buckets.MigrationError) as exc:
        buckets.migrate(climbing)
    message = str(exc.value)
    assert "cannot be applied to a path with" in message
    assert "literal slug" in message


def test_the_json_flag_emits_the_refusal_as_json(two_tenants,
                                                 tmp_checkpoint_dir, capsys):
    """A caller asking for machine output never has to parse stderr to learn
    it was refused."""
    climbing, _ = two_tenants

    rc = cli.main(["bucket", "migrate", f"--project={climbing}", "--json"])

    captured = capsys.readouterr()
    assert rc == 2
    payload = json.loads(captured.out)
    assert ".." in payload["refused"]


# ---------------------------------------------------------------------------
# the renderer for the record: every reader that joins on a slug
# ---------------------------------------------------------------------------


def _migration(tmp_checkpoint_dir, from_slug, to_slug) -> None:
    tmp_checkpoint_dir.mkdir(parents=True, exist_ok=True)
    with (tmp_checkpoint_dir / "migrations.jsonl").open(
            "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"version": 1, "ts": "2026-09-07T00:00:00Z",
                             "from_slug": from_slug, "to_slug": to_slug,
                             "mode": "rename", "ledgers": {}, "pointers": 0,
                             "leftovers": [], "by": "cli"}) + "\n")


def test_recall_indexes_an_aliased_row_under_the_bucket_it_moved_to(
        linked, tmp_checkpoint_dir):
    """The flat checkpoint keeps its pre-migration stamp because it is
    receipt-signed. Recall has to map it, or the migrated project cannot
    search its own history."""
    from daimon_briefing import recall

    link, real = linked
    legacy = buckets.legacy_slug(link) or ""
    target = store.project_bucket(real) or ""
    tmp_checkpoint_dir.mkdir(parents=True, exist_ok=True)
    (tmp_checkpoint_dir / "S-legacy.json").write_text(json.dumps({
        "session_id": "S-legacy",
        "created": "2026-09-01T00:00:00Z",
        "author": "ada",
        "project_slug": legacy,
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [{"text": "the pangolin decision",
                                  "trust": "inferred"}],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }), encoding="utf-8")
    _migration(tmp_checkpoint_dir, legacy, target)

    recall.rebuild()
    rows = recall.search("pangolin", project_dir=real)

    assert rows, "the migrated project cannot find its own history"
    assert rows[0]["project_slug"] == target
    assert recall.describe_scope(rows[0], target) is None


def test_a_migration_record_makes_the_recall_index_stale(linked,
                                                         tmp_checkpoint_dir):
    """A fingerprint blind to the receipt would serve the pre-migration
    attribution until some unrelated write happened to invalidate it."""
    from daimon_briefing import recall

    link, real = linked
    tmp_checkpoint_dir.mkdir(parents=True, exist_ok=True)
    before = recall._fingerprint()
    _migration(tmp_checkpoint_dir, buckets.legacy_slug(link) or "",
               store.project_bucket(real) or "")
    assert recall._fingerprint() != before


def test_an_ask_addressed_to_the_old_slug_still_lands_in_the_inbox(
        linked, tmp_checkpoint_dir):
    """Request ids embed the sender's slug at mint time and the recipient
    join matches on `to`. Without the alias, every ask sent before the
    migration disappears from the inbox it was addressed to."""
    from daimon_briefing import requests

    link, real = linked
    legacy = buckets.legacy_slug(link) or ""
    target = store.project_bucket(real) or ""
    sender = tmp_checkpoint_dir / "-sender-project"
    _plant(sender, {"requests.jsonl": json.dumps({
        "request_id": "q-0123456789ab", "event": "opened",
        "to": legacy, "ask": "why did the bucket move",
        "why": "a pre-0.42.0 ask", "by": "human", "channel": "cli-tty",
        "ts": "2026-09-01T00:00:00Z", "order": 1,
        "event_id": "e-open"}) + "\n"})
    _migration(tmp_checkpoint_dir, legacy, target)

    inbox = requests.inbox_listing(project_dir=real)

    assert [r["request_id"] for r in inbox] == ["q-0123456789ab"]


def test_a_leftover_legacy_bucket_is_never_read_as_a_foreign_sender(
        linked, tmp_checkpoint_dir):
    """A merge that cannot empty the legacy directory leaves it standing.
    Scanned as somebody else's bucket, the rows it still holds would come
    back labeled as asks from a stranger — which is this project, before it
    moved. The merged copy in the project's own bucket is the origin."""
    from daimon_briefing import requests

    link, real = linked
    legacy = buckets.legacy_slug(link) or ""
    target = store.project_bucket(real) or ""
    row = json.dumps({
        "request_id": "q-0123456789ab", "event": "opened",
        "to": legacy, "ask": "an ask this project sent itself",
        "why": "planted", "by": "human", "channel": "cli-tty",
        "ts": "2026-09-01T00:00:00Z", "order": 1,
        "event_id": "e-open"}) + "\n"
    _plant(tmp_checkpoint_dir / target, {"requests.jsonl": row})
    _plant(tmp_checkpoint_dir / legacy, {"requests.jsonl": row})
    _migration(tmp_checkpoint_dir, legacy, target)

    inbox = requests.inbox_listing(project_dir=real)

    assert [r["request_id"] for r in inbox] == ["q-0123456789ab"]
    assert [r["from_slug"] for r in inbox] == [""], \
        "the bucket this project moved out of is not a foreign sender"


# ---------------------------------------------------------------------------
# status and the rc-1 read: saying that an orphan is there
# ---------------------------------------------------------------------------


def test_status_warns_that_a_legacy_bucket_is_not_being_read(
        linked, tmp_checkpoint_dir, capsys):
    link, _ = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})

    cli.main(["status", f"--project={link}"])

    out = capsys.readouterr().out
    assert f"legacy: bucket {buckets.legacy_slug(link)}" in out
    assert "daimon bucket migrate" in out


def test_status_is_silent_when_no_legacy_bucket_exists(linked, capsys):
    link, _ = linked
    cli.main(["status", f"--project={link}"])
    assert "legacy:" not in capsys.readouterr().out


def test_status_says_where_a_migrated_bucket_came_from(linked,
                                                       tmp_checkpoint_dir,
                                                       capsys):
    link, _ = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})
    buckets.migrate(link)
    capsys.readouterr()

    cli.main(["status", f"--project={link}"])

    out = capsys.readouterr().out
    assert f"migrated: from {buckets.legacy_slug(link)} on " in out
    assert "legacy:" not in out


def test_the_migrated_line_is_silent_when_nothing_migrated():
    """Quiet by default, the same rule the team and receipts lines follow: a
    provenance line for a bucket that came from nowhere is furniture."""
    from daimon_briefing import render

    assert render._migrated_lines({}) == []
    assert render._migrated_lines({"migrated": []}) == []
    assert render._migrated_lines({"migrated": None}) == []


def test_status_json_carries_the_legacy_slug_and_the_aliases(
        linked, tmp_checkpoint_dir, capsys):
    link, real = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})

    cli.main(["status", f"--project={link}", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["identity"]["legacy_slug"] == buckets.legacy_slug(link)
    assert payload["identity"]["aliases"] == []

    buckets.migrate(link)
    capsys.readouterr()
    cli.main(["status", f"--project={link}", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["identity"]["legacy_slug"] is None
    assert payload["identity"]["aliases"] == [buckets.legacy_slug(link)]
    assert payload["identity"]["slug"] == store.project_bucket(real)


def test_the_rich_status_render_carries_both_lines(linked,
                                                   tmp_checkpoint_dir,
                                                   monkeypatch, capsys):
    """The plain and rich renders are separate code paths, and a line that
    exists in only one is invisible to whichever half of the users has the
    other."""
    pytest.importorskip("rich")
    from daimon_briefing import render

    link, _ = linked
    monkeypatch.delenv("DAIMON_PLAIN", raising=False)
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})
    render._rich_status(cli._status_world(link))
    # rich hard-wraps a long path mid-token, so compare without the breaks.
    flat = capsys.readouterr().out.replace("\n", "")
    assert "legacy: bucket" in flat
    assert (buckets.legacy_slug(link) or "") in flat
    assert "daimon bucket migrate" in flat

    buckets.migrate(link)
    render._rich_status(cli._status_world(link))
    flat = capsys.readouterr().out.replace("\n", "")
    assert "migrated: from" in flat
    assert (buckets.legacy_slug(link) or "") in flat


def test_ruling_list_points_at_the_migration_when_a_legacy_bucket_exists(
        linked, tmp_checkpoint_dir, capsys):
    """#948 gave the empty read an rc-1 stderr line. When the cause is a
    bucket written before 0.42.0, the line has to name the remedy — stdout
    and the exit code stay exactly as they were (scar 0057)."""
    link, _ = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"refutations.jsonl": _row("r1")})

    rc = cli.main(["ruling", "list", f"--project={link}"])

    captured = capsys.readouterr()
    assert rc == 1
    assert f"a legacy bucket {buckets.legacy_slug(link)} exists" in captured.err
    assert "daimon bucket migrate" in captured.err


def test_ruling_list_keeps_its_plain_line_with_no_legacy_bucket(linked,
                                                                capsys):
    link, _ = linked
    rc = cli.main(["ruling", "list", f"--project={link}"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "no bucket for" in err and "legacy bucket" not in err


# ---------------------------------------------------------------------------
# the CLI verb
# ---------------------------------------------------------------------------


def test_the_verb_renames_and_says_so(linked, tmp_checkpoint_dir, capsys):
    link, real = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})

    rc = cli.main(["bucket", "migrate", f"--project={link}"])

    assert rc == 0
    out = capsys.readouterr().out
    assert store.project_bucket(real) in out
    assert (tmp_checkpoint_dir / store.project_bucket(real) /
            "events.jsonl").exists()


def test_the_verb_is_quiet_when_there_is_nothing_to_migrate(linked, capsys):
    link, _ = linked
    assert cli.main(["bucket", "migrate", f"--project={link}"]) == 0
    assert "nothing to migrate" in capsys.readouterr().out


def test_the_verb_says_nothing_to_migrate_for_a_stable_path(tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert cli.main(["bucket", "migrate", f"--project={plain}"]) == 0
    out = capsys.readouterr().out
    assert "nothing to migrate" in out and "stable under both rules" in out


def test_the_verb_dry_run_prints_the_plan_and_writes_nothing(
        linked, tmp_checkpoint_dir, capsys):
    link, _ = linked
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    _plant(legacy, {"events.jsonl": _row("e1")})

    assert cli.main(["bucket", "migrate", f"--project={link}",
                     "--dry-run"]) == 0

    assert "would" in capsys.readouterr().out
    assert legacy.exists()


def test_the_verb_json_prints_the_record_shape(linked, tmp_checkpoint_dir,
                                               capsys):
    link, real = linked
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})

    assert cli.main(["bucket", "migrate", f"--project={link}", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["to_slug"] == store.project_bucket(real)
    assert payload["mode"] == "rename"


def test_the_verb_reports_a_merge_in_full(linked, tmp_checkpoint_dir,
                                          capsys):
    link, real = linked
    legacy_slug = buckets.legacy_slug(link) or ""
    target_slug = store.project_bucket(real) or ""
    legacy = _populate_legacy(link, [("S-old", "2026-09-01T00:00:00Z")])
    _plant(legacy, {"events.jsonl": _row("e1"), "stray.txt": "not ours"})
    _write(link, "S-new", "2026-09-05T00:00:00Z")
    _plant(tmp_checkpoint_dir / target_slug, {"events.jsonl": _row("e0")})

    assert cli.main(["bucket", "migrate", f"--project={link}"]) == 1

    out = capsys.readouterr().out
    assert f"moved {legacy_slug} into {target_slug} (merge)" in out
    assert "events.jsonl: appended 1 line(s)" in out
    assert "pointers: moved 1 into the chain" in out
    assert "stray.txt could not be read as anything this verb moves" in out
    assert f"move it out of {legacy_slug} to finish" in out


def test_the_unknown_mode_says_so_rather_than_naming_a_bucket():
    """A project that resolves to nothing has no bucket to name, and a line
    printing an empty slug reads as a bucket called nothing."""
    lines = cli._bucket_migrate_lines(
        {"mode": "unknown", "from_slug": None, "to_slug": None,
         "ledgers": {}, "pointers": 0, "leftovers": []},
        "   ", dry_run=False)
    assert lines == ["nothing to migrate: no project resolves from    "]


def test_a_tenant_scoped_home_cannot_reach_a_foreign_bucket_by_slug(
        linked, tmp_checkpoint_dir, monkeypatch, capsys):
    """#899 + scar 0071: `--project` is a PATH and is always absolutized, so a
    slug-shaped value names a directory under the caller's own cwd and can
    never address the bucket whose name it copies."""
    link, _ = linked
    foreign_dir = tmp_checkpoint_dir / "-foreign-project"
    _plant(foreign_dir, {"events.jsonl": _row("theirs")})
    before = (foreign_dir / "events.jsonl").read_bytes()
    monkeypatch.chdir(link)
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")

    rc = cli.main(["bucket", "migrate", "--project=-foreign-project"])

    assert rc == 2, "a tenant-scoped home refuses an explicit --project"
    assert (foreign_dir / "events.jsonl").read_bytes() == before
    assert buckets.records() == []


def test_the_verb_defaults_to_the_current_project(linked, tmp_checkpoint_dir,
                                                  monkeypatch, capsys):
    link, real = linked
    monkeypatch.setenv("DAIMON_PROJECT_DIR", link)
    _plant(tmp_checkpoint_dir / (buckets.legacy_slug(link) or ""),
           {"events.jsonl": _row("e1")})

    assert cli.main(["bucket", "migrate"]) == 0
    assert (tmp_checkpoint_dir / store.project_bucket(real) /
            "events.jsonl").exists()


# ---------------------------------------------------------------------------
# fail-open branches (#967 codecov patch)
# ---------------------------------------------------------------------------


def test_climbs_out_normalizes_the_alt_separator_before_checking(monkeypatch):
    """`os.altsep` is None on macOS/Linux, so the normalization line only runs
    on a platform (or a patched os module) where it is set. Without it, a
    literal backslash never splits into its own path component and a '..'
    hidden behind it would be missed."""
    monkeypatch.setattr(buckets.os, "altsep", "\\")
    assert buckets.climbs_out("a\\..\\b") is True


def test_incomplete_for_with_no_slug_is_empty():
    assert buckets.incomplete_for(None) == ()
    assert buckets.incomplete_for("") == ()


def test_pointer_files_returns_empty_list_when_the_directory_is_unreadable(
        tmp_path):
    """`_pointer_files` is read from `migrate` and from planning surfaces that
    must never raise for a directory that vanished out from under them."""
    ghost = tmp_path / "does-not-exist"
    assert buckets._pointer_files(ghost) == []


def test_leftovers_returns_empty_list_when_the_directory_is_unreadable(
        tmp_path):
    ghost = tmp_path / "does-not-exist"
    assert buckets._leftovers(ghost) == []


def test_append_lines_defaults_the_tail_when_it_cannot_be_read(
        linked, tmp_checkpoint_dir, monkeypatch):
    """A ledger appender that cannot even READ the file it is appending to
    must still append rather than raise: the merge is the one thing #963
    exists to keep restartable."""
    link, real = linked
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    target = tmp_checkpoint_dir / store.project_bucket(real)
    _plant(legacy, {"events.jsonl": _row("e1")})
    _plant(target, {"events.jsonl": _row("e0")})
    target_file = target / "events.jsonl"
    orig_read_bytes = pathlib.Path.read_bytes

    def _boom(self):
        if self == target_file:
            raise OSError("boom")
        return orig_read_bytes(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", _boom)

    record = buckets.migrate(link)

    assert record["mode"] == "merge"
    assert record["ledgers"] == {"events.jsonl": 1}
    lines = target_file.read_text(encoding="utf-8").splitlines()
    assert sorted(lines) == sorted([_row("e0").strip(), _row("e1").strip()])


def test_a_pointer_that_cannot_be_unlinked_after_landing_is_left_in_place(
        linked, tmp_checkpoint_dir, monkeypatch):
    """A pointer copy that landed in the target chain but could not be
    unlinked from the legacy bucket is reported as a leftover, never raised:
    the bytes are safe in the target either way."""
    link, real = linked
    legacy = _populate_legacy(link, [("S-old", "2026-09-01T00:00:00Z")])
    _plant(legacy, {"events.jsonl": _row("e1")})
    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    _plant(target, {"events.jsonl": _row("e0")})
    source = legacy / "latest.json"
    orig_unlink = pathlib.Path.unlink

    def _boom(self, *a, **k):
        if self == source:
            raise OSError("permission denied")
        return orig_unlink(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "unlink", _boom)

    record = buckets.migrate(link)

    assert record["mode"] == "merge"
    assert record["pointers"] == 1, "the pointer landed in the target chain"
    assert source.exists(), "an unremovable source pointer is left in place"
    assert "latest.json" in record["leftovers"]
    assert _marker(target / "latest.json") == "S-old"


def test_a_ledger_that_cannot_be_unlinked_after_merging_is_left_in_place(
        linked, tmp_checkpoint_dir, monkeypatch):
    """Every legacy line already lives in the target, so the merge tries to
    drop the legacy copy. A failed unlink must not raise: the file is simply
    reported as still there."""
    link, real = linked
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    target = tmp_checkpoint_dir / store.project_bucket(real)
    _plant(legacy, {"events.jsonl": _row("e1")})
    _plant(target, {"events.jsonl": _row("e1")})
    source = legacy / "events.jsonl"
    orig_unlink = pathlib.Path.unlink

    def _boom(self, *a, **k):
        if self == source:
            raise OSError("boom")
        return orig_unlink(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "unlink", _boom)

    record = buckets.migrate(link)

    assert record["mode"] == "merge"
    assert record["ledgers"] == {"events.jsonl": 0}
    assert source.exists(), "an unremovable ledger is left in place, not raised"
    assert "events.jsonl" in record["leftovers"]


def test_a_legacy_directory_that_cannot_be_removed_is_reported_not_raised(
        linked, tmp_checkpoint_dir, monkeypatch):
    """Everything the merge understands was absorbed, so it tries to remove
    the now-empty legacy directory. A failed rmdir (a race, a permission
    error) must be swallowed: the directory is simply left standing."""
    link, real = linked
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    target = tmp_checkpoint_dir / store.project_bucket(real)
    _plant(legacy, {"events.jsonl": _row("e1")})
    _plant(target, {"events.jsonl": _row("e0")})
    orig_rmdir = pathlib.Path.rmdir

    def _boom(self, *a, **k):
        if self == legacy:
            raise OSError("directory not empty")
        return orig_rmdir(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "rmdir", _boom)

    record = buckets.migrate(link)

    assert record["mode"] == "merge"
    assert record["leftovers"] == []
    assert legacy.exists(), \
        "an unremovable empty legacy directory is left, not raised"


# ---------------------------------------------------------------------------
# CLI patch coverage (#967)
# ---------------------------------------------------------------------------


def test_a_tenant_scoped_refusal_in_json_carries_the_refused_key(
        linked, tmp_checkpoint_dir, monkeypatch):
    link, _ = linked
    foreign_dir = tmp_checkpoint_dir / "-foreign-project"
    _plant(foreign_dir, {"events.jsonl": _row("theirs")})
    monkeypatch.chdir(link)
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")

    import io
    from contextlib import redirect_stdout

    out = io.StringIO()
    with redirect_stdout(out):
        rc = cli.main(["bucket", "migrate", "--project=-foreign-project",
                       "--json"])

    assert rc == 2
    payload = json.loads(out.getvalue())
    assert "tenant-scoped" in payload["refused"]
    assert buckets.records() == []


def test_the_verb_names_an_unreadable_target_pointer_in_its_human_output(
        linked, tmp_checkpoint_dir, monkeypatch, capsys):
    """The rc-1 human render has to name the specific file the merge could
    not read, or the reader has nothing to go fix."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    link, real = linked
    _write(link, "S-t0", "2026-01-01T00:00:00Z")
    _write(link, "S-t1", "2026-01-02T00:00:00Z")
    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    (target / "prev-1.json").write_bytes(b'{"session_id": "S-t0", "cre')
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(link) or "")
    legacy.mkdir(parents=True, exist_ok=True)
    _stage_legacy_pointers(link, legacy, [("S-l0", "2026-06-01T00:00:00Z")])

    rc = cli.main(["bucket", "migrate", f"--project={link}"])

    out = capsys.readouterr().out
    assert rc == 1
    assert ("prev-1.json in " + (store.project_bucket(real) or "") +
            " could not be read, so no pointer was moved") in out


def test_status_swallows_a_bucket_read_failure_as_a_fail_open_fact(
        linked, tmp_checkpoint_dir, monkeypatch):
    """Every migration-derived status fact is best-effort: an exception from
    any of the reads must not take `status` down, and the identity fields
    fall back to the documented empty shape."""
    link, _ = linked

    def _boom(*_a, **_k):
        raise RuntimeError("checkpoint dir vanished")

    monkeypatch.setattr(buckets, "legacy_bucket", _boom)

    world = cli._status_world(link)

    assert world["identity"]["legacy_slug"] is None
    assert world["identity"]["aliases"] == []
    assert world["identity"]["migrated"] == []
    assert world["identity"]["incomplete"] == []


def test_status_dedupes_a_migration_pair_named_by_two_receipt_rows(
        linked, tmp_checkpoint_dir):
    """A hand-edited receipt file, or an older daimon that appended a row per
    re-run, can make the same (from, to) pair reachable through two distinct
    rows. `status` must print that migration once, not once per row."""
    link, real = linked
    target_slug = store.project_bucket(real) or ""
    tmp_checkpoint_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {"version": 1, "from_slug": "-a", "to_slug": target_slug,
         "complete": True, "ts": "2026-09-01T00:00:00Z", "ledgers": {}},
        {"version": 1, "from_slug": "-a", "to_slug": "-mid",
         "complete": True, "ts": "2026-09-02T00:00:00Z", "ledgers": {}},
        {"version": 1, "from_slug": "-mid", "to_slug": target_slug,
         "complete": True, "ts": "2026-09-03T00:00:00Z", "ledgers": {}},
    ]
    (tmp_checkpoint_dir / "migrations.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    world = cli._status_world(link)

    slugs = [entry["slug"] for entry in world["identity"]["migrated"]]
    assert slugs.count("-a") == 1, \
        "the same (from, to) pair reached through two rows prints once"
    assert sorted(slugs) == ["-a", "-mid"]
