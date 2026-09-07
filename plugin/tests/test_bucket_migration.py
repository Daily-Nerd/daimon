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


def _checkpoint(marker: str, created: str) -> dict:
    return {
        "created": created,
        "working_context": {
            "active_topic": {"text": marker, "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [{"text": f"decision {marker}",
                                  "trust": "inferred"}],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }


def _write(project_dir, marker: str, created: str) -> None:
    """A REAL pointer, written by the store.

    Never a hand-built dict. A pointer payload written by
    `store.write_checkpoint` carries NO `session_id` at all: the keys are
    author, created, format_version, project_name, project_slug, and the
    checkpoint's own sections. A synthetic pointer that invents one hides the
    exact collision this file exists to catch, because two real pointers then
    look distinguishable when they are not."""
    store.write_checkpoint(marker, _checkpoint(marker, created),
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


def test_a_real_merge_is_idempotent(linked, tmp_checkpoint_dir):
    """Every bucket the store has ever written to carries a `.pointer.lock`,
    the empty flock sidecar. Treated as an unknown leftover it keeps the
    legacy directory alive forever: rmdir is skipped, the next run finds the
    bucket again, merges again, and appends a second receipt. The lock is
    declared in surfaces.py as an empty file holding no content, so a merge
    may remove it."""
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


def test_no_real_pointer_carries_a_session_id(linked, tmp_checkpoint_dir):
    """The premise every test below rests on, measured rather than assumed.

    `store.write_checkpoint` stamps author, created, format_version,
    project_name and project_slug onto the checkpoint it writes, and the
    pointer copy is that same blob. `session_id` is the function's ARGUMENT,
    never a field of the payload. Keying pointer identity on it therefore
    falls back to the FILENAME, and `latest.json` in one bucket collides with
    `latest.json` in the other."""
    link, _ = linked
    legacy = _populate_legacy(link, [("S-old", "2026-09-01T00:00:00Z")])
    payload = json.loads((legacy / "latest.json").read_text(encoding="utf-8"))
    assert "session_id" not in payload
    assert payload["created"] == "2026-09-01T00:00:00Z"


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
    assert record["pointers"] == 4
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
    assert record["pointers"] == 2
    assert [_marker(target / n) for n in ("latest.json", "prev-1.json")] == \
        ["S-new", "S-shared"]
    assert not (target / "prev-2.json").exists()


def test_the_chain_honors_the_configured_history(linked, tmp_checkpoint_dir,
                                                 monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "2")
    link, real = linked
    _populate_legacy(link, [("S-oldest", "2026-08-01T00:00:00Z"),
                            ("S-old", "2026-09-01T00:00:00Z")])
    _write(link, "S-new", "2026-09-05T00:00:00Z")

    buckets.migrate(link)

    target = tmp_checkpoint_dir / (store.project_bucket(real) or "")
    assert not (target / "prev-2.json").exists()
    assert _marker(target / "latest.json") == "S-new"
    assert _marker(target / "prev-1.json") == "S-old"


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
    assert record["pointers"] == 2
    assert [_marker(target / n) for n in ("latest.json", "prev-1.json")] == \
        ["S-new", "S-old"]


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

    assert record["pointers"] == 2
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
        two_tenants, tmp_checkpoint_dir, monkeypatch, capsys):
    climbing, victim = two_tenants
    planted = tmp_checkpoint_dir / (store.project_slug(victim) or "")
    _plant(planted, {"refutations.jsonl": _row("theirs")})
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")

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

    assert cli.main(["bucket", "migrate", f"--project={link}"]) == 0

    out = capsys.readouterr().out
    assert f"moved {legacy_slug} into {target_slug} (merge)" in out
    assert "events.jsonl: appended 1 line(s)" in out
    assert "pointers: kept 2 in the chain" in out
    assert "left in place, not understood: stray.txt" in out


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

    assert rc == 0
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
