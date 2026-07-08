import json
import re
from pathlib import Path

from daimon_briefing import serializer, store

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def test_write_then_read_round_trip(tmp_checkpoint_dir, sample_checkpoint):
    path = store.write_checkpoint("S-prev", sample_checkpoint)
    assert path.exists()
    assert path == tmp_checkpoint_dir / "S-prev.json"

    loaded = store.read_checkpoint("S-prev")
    assert loaded == sample_checkpoint


def test_latest_pointer_updated_on_write(tmp_checkpoint_dir, sample_checkpoint):
    store.write_checkpoint("S-prev", sample_checkpoint)
    latest = store.read_latest()
    assert latest is not None
    assert latest["session_id"] == "S-prev"


def test_latest_reflects_most_recent_write(tmp_checkpoint_dir, sample_checkpoint):
    store.write_checkpoint("S-old", {**sample_checkpoint, "session_id": "S-old"})
    store.write_checkpoint("S-new", {**sample_checkpoint, "session_id": "S-new"})
    latest = store.read_latest()
    assert latest["session_id"] == "S-new"


def test_read_latest_none_when_empty(tmp_checkpoint_dir):
    assert store.read_latest() is None


def test_read_checkpoint_none_when_missing(tmp_checkpoint_dir):
    assert store.read_checkpoint("nope") is None


def test_write_creates_dir(tmp_path, monkeypatch, sample_checkpoint):
    nested = tmp_path / "a" / "b" / "checkpoints"
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(nested))
    store.write_checkpoint("S1", sample_checkpoint)
    assert nested.exists()


def test_latest_pointer_is_separate_file(tmp_checkpoint_dir, sample_checkpoint):
    store.write_checkpoint("S-prev", sample_checkpoint)
    files = {p.name for p in tmp_checkpoint_dir.iterdir()}
    assert "S-prev.json" in files
    assert "latest.json" in files


def test_write_leaves_no_temp_files(tmp_checkpoint_dir, sample_checkpoint):
    store.write_checkpoint("S-prev", sample_checkpoint)
    files = {p.name for p in tmp_checkpoint_dir.iterdir()}
    # .pointer.lock is deliberate infrastructure (#31 item 2), not a leak —
    # unlinking a flock file after release reintroduces the ABA race it
    # exists to prevent, so it stays. This guards against leaked *.tmp only.
    assert files == {"S-prev.json", "latest.json", store._LOCK_NAME}


# ---- schema stamping: format_version + created at write time (#93) ----


def test_write_stamps_format_version_and_created(tmp_checkpoint_dir, sample_checkpoint):
    store.write_checkpoint("S-stamp", sample_checkpoint)
    for name in ("S-stamp.json", "latest.json"):
        blob = json.loads((tmp_checkpoint_dir / name).read_text(encoding="utf-8"))
        assert blob["format_version"] == serializer.PROMPT_VERSION
        assert _ISO_RE.match(blob["created"])


def test_write_does_not_overwrite_existing_stamp(tmp_checkpoint_dir, sample_checkpoint):
    # Idempotent re-writes / rotation must not re-stamp an already-stamped checkpoint.
    pre = {**sample_checkpoint, "format_version": "D-000", "created": "2020-01-01T00:00:00Z"}
    store.write_checkpoint("S-keep", pre)
    blob = json.loads((tmp_checkpoint_dir / "S-keep.json").read_text(encoding="utf-8"))
    assert blob["format_version"] == "D-000"
    assert blob["created"] == "2020-01-01T00:00:00Z"


def test_write_is_atomic_via_os_replace(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    import os

    calls = []
    real_replace = os.replace

    def spy(src, dst):
        calls.append(str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(store.os, "replace", spy)
    store.write_checkpoint("S-prev", sample_checkpoint)
    # Both the checkpoint and the latest pointer land via os.replace (atomic on POSIX).
    assert len(calls) == 2
    assert any(c.endswith("S-prev.json") for c in calls)
    assert any(c.endswith("latest.json") for c in calls)


def test_write_traversal_session_id_stays_inside_dir(tmp_checkpoint_dir, sample_checkpoint):
    path = store.write_checkpoint("../../evil", sample_checkpoint)
    assert path.resolve().is_relative_to(tmp_checkpoint_dir.resolve())
    # Nothing escaped above the checkpoint dir.
    parent = tmp_checkpoint_dir.parent
    assert not (parent / "evil.json").exists()
    assert not (parent.parent / "evil.json").exists()


def test_write_escaping_path_raises(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    # Belt-and-braces: even if name sanitization is bypassed, the resolved-path
    # containment check must refuse to write outside the checkpoint dir.
    import pytest

    monkeypatch.setattr(store, "_safe_name", lambda s: s)
    with pytest.raises(ValueError):
        store.write_checkpoint("../../evil", sample_checkpoint)


def test_read_escaping_path_returns_none(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(store, "_safe_name", lambda s: s)
    assert store.read_checkpoint("../../../etc/passwd") is None


# ---- per-project routing: cwd-slugged latest with global fallback ----


def test_project_slug_matches_claude_code_scheme():
    assert store.project_slug("/Users/x/proj") == "-Users-x-proj"


def test_project_slug_spaces_and_dots():
    assert store.project_slug("/Users/x/My Proj.app") == "-Users-x-My-Proj-app"


def test_project_slug_unicode_preserved():
    assert store.project_slug("/Users/x/café") == "-Users-x-café"


def test_project_slug_empty_is_none():
    assert store.project_slug("") is None
    assert store.project_slug("   ") is None
    assert store.project_slug(None) is None


def test_project_slug_never_contains_separator():
    for raw in ("/a/b", "a\\b", "../escape", "a b.c/d"):
        slug = store.project_slug(raw)
        assert "/" not in slug and "\\" not in slug


def test_write_routes_to_project_dir_and_global(tmp_checkpoint_dir, sample_checkpoint):
    store.write_checkpoint("S1", sample_checkpoint, project_dir="/Users/x/projA")
    assert (tmp_checkpoint_dir / "-Users-x-projA" / "latest.json").exists()
    assert (tmp_checkpoint_dir / "latest.json").exists()
    assert (tmp_checkpoint_dir / "S1.json").exists()


def test_write_without_project_creates_no_slug_dirs(tmp_checkpoint_dir, sample_checkpoint):
    store.write_checkpoint("S1", sample_checkpoint)
    assert all(not p.is_dir() for p in tmp_checkpoint_dir.iterdir())


def test_write_stamps_project_slug(tmp_checkpoint_dir, sample_checkpoint):
    # Durable attribution: bucket pointers rotate away (depth = history), so a
    # session older than the pointer window would otherwise lose its project
    # forever — and scoped recall could never surface it again.
    store.write_checkpoint("S1", sample_checkpoint, project_dir="/Users/x/projA")
    blob = json.loads((tmp_checkpoint_dir / "S1.json").read_text(encoding="utf-8"))
    assert blob["project_slug"] == "-Users-x-projA"


def test_write_unknown_project_stamps_no_slug(tmp_checkpoint_dir, sample_checkpoint):
    store.write_checkpoint("S1", sample_checkpoint)
    blob = json.loads((tmp_checkpoint_dir / "S1.json").read_text(encoding="utf-8"))
    assert "project_slug" not in blob


def test_write_does_not_overwrite_existing_project_slug(tmp_checkpoint_dir, sample_checkpoint):
    # Same idempotence contract as format_version/created/author: a checkpoint
    # carrying its own stamp is never re-stamped.
    pre = {**sample_checkpoint, "project_slug": "-original-home"}
    store.write_checkpoint("S1", pre, project_dir="/Users/x/projA")
    blob = json.loads((tmp_checkpoint_dir / "S1.json").read_text(encoding="utf-8"))
    assert blob["project_slug"] == "-original-home"


def test_write_project_latest_is_atomic(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    import os

    calls = []
    real_replace = os.replace

    def spy(src, dst):
        calls.append(str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(store.os, "replace", spy)
    store.write_checkpoint("S1", sample_checkpoint, project_dir="/Users/x/projA")
    # session file + global latest + project latest, all via os.replace
    assert len(calls) == 3
    assert any(c.endswith("-Users-x-projA/latest.json") for c in calls)


def test_read_latest_prefers_project(tmp_checkpoint_dir, sample_checkpoint):
    # The original bug: session in A, then B; returning to A must NOT see B's checkpoint.
    store.write_checkpoint("S-a", {**sample_checkpoint, "session_id": "S-a"}, project_dir="/p/A")
    store.write_checkpoint("S-b", {**sample_checkpoint, "session_id": "S-b"}, project_dir="/p/B")
    assert store.read_latest(project_dir="/p/A")["session_id"] == "S-a"
    assert store.read_latest(project_dir="/p/B")["session_id"] == "S-b"
    # global latest still points at the most recent write (any project)
    assert store.read_latest()["session_id"] == "S-b"


def test_read_latest_falls_back_to_global(tmp_checkpoint_dir, sample_checkpoint):
    store.write_checkpoint("S-g", {**sample_checkpoint, "session_id": "S-g"})
    assert store.read_latest(project_dir="/p/never-seen")["session_id"] == "S-g"


def test_read_latest_none_when_both_absent(tmp_checkpoint_dir):
    assert store.read_latest(project_dir="/p/never-seen") is None


def test_read_latest_no_fallback_skips_global(tmp_checkpoint_dir, sample_checkpoint):
    # #94: carry's read path must not see another project's checkpoint through
    # the global pointer — a fresh project reads None, not a foreign session.
    store.write_checkpoint("S-other", {**sample_checkpoint, "session_id": "S-other"},
                           project_dir="/p/other")
    assert store.read_latest(project_dir="/p/fresh", fallback=False) is None


def test_read_latest_no_fallback_still_reads_own_project(tmp_checkpoint_dir, sample_checkpoint):
    store.write_checkpoint("S-a", {**sample_checkpoint, "session_id": "S-a"},
                           project_dir="/p/A")
    assert store.read_latest(project_dir="/p/A", fallback=False)["session_id"] == "S-a"


# ---- checkpoint rotation: keep last N pointers per dir (#33 Phase 1) ----


def _pointer_session(path):
    return json.loads(path.read_text(encoding="utf-8"))["session_id"]


def test_rotation_keeps_prev_pointers_per_project(tmp_checkpoint_dir, sample_checkpoint):
    for sid in ("S1", "S2", "S3"):
        store.write_checkpoint(sid, {**sample_checkpoint, "session_id": sid}, project_dir="/p/A")
    d = tmp_checkpoint_dir / "-p-A"
    assert _pointer_session(d / "latest.json") == "S3"
    assert _pointer_session(d / "prev-1.json") == "S2"
    assert _pointer_session(d / "prev-2.json") == "S1"


def test_rotation_drops_oldest_beyond_history(tmp_checkpoint_dir, sample_checkpoint):
    for sid in ("S1", "S2", "S3", "S4"):
        store.write_checkpoint(sid, {**sample_checkpoint, "session_id": sid}, project_dir="/p/A")
    d = tmp_checkpoint_dir / "-p-A"
    assert _pointer_session(d / "latest.json") == "S4"
    assert _pointer_session(d / "prev-1.json") == "S3"
    assert _pointer_session(d / "prev-2.json") == "S2"
    assert not (d / "prev-3.json").exists()  # default history=3 -> latest + 2 prevs


def test_rotation_history_env_configurable(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "2")
    for sid in ("S1", "S2", "S3"):
        store.write_checkpoint(sid, {**sample_checkpoint, "session_id": sid}, project_dir="/p/A")
    d = tmp_checkpoint_dir / "-p-A"
    assert _pointer_session(d / "latest.json") == "S3"
    assert _pointer_session(d / "prev-1.json") == "S2"
    assert not (d / "prev-2.json").exists()


def test_rotation_history_1_disables(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "1")
    for sid in ("S1", "S2"):
        store.write_checkpoint(sid, {**sample_checkpoint, "session_id": sid}, project_dir="/p/A")
    d = tmp_checkpoint_dir / "-p-A"
    assert _pointer_session(d / "latest.json") == "S2"
    assert not (d / "prev-1.json").exists()


def test_rotation_applies_to_global_pointer(tmp_checkpoint_dir, sample_checkpoint):
    store.write_checkpoint("S1", {**sample_checkpoint, "session_id": "S1"})
    store.write_checkpoint("S2", {**sample_checkpoint, "session_id": "S2"})
    assert _pointer_session(tmp_checkpoint_dir / "latest.json") == "S2"
    assert _pointer_session(tmp_checkpoint_dir / "prev-1.json") == "S1"


def test_rotation_latest_always_present(tmp_checkpoint_dir, sample_checkpoint):
    # latest.json is copied (not moved) to prev-1, so it never vanishes mid-rotation.
    store.write_checkpoint("S1", {**sample_checkpoint, "session_id": "S1"}, project_dir="/p/A")
    store.write_checkpoint("S2", {**sample_checkpoint, "session_id": "S2"}, project_dir="/p/A")
    d = tmp_checkpoint_dir / "-p-A"
    assert (d / "latest.json").exists()
    assert _pointer_session(d / "latest.json") == "S2"


# ---- sibling_buckets: phantom-child scan (#84) ----


def test_sibling_buckets_finds_newer_child(tmp_checkpoint_dir):
    from daimon_briefing import store
    root = "/Users/me/proj"
    slug = store.project_slug(root)
    d = tmp_checkpoint_dir
    (d / slug).mkdir(parents=True)
    (d / slug / "latest.json").write_text('{"session_id": "P"}')
    child = d / (slug + "-sub")
    child.mkdir()
    (child / "latest.json").write_text('{"session_id": "C"}')
    sibs = store.sibling_buckets(root)
    assert [s["slug"] for s in sibs] == [slug + "-sub"]
    assert sibs[0]["session_id"] == "C"
    assert isinstance(sibs[0]["mtime"], float)


def test_sibling_buckets_ignores_unrelated_and_self(tmp_checkpoint_dir):
    from daimon_briefing import store
    root = "/Users/me/proj"
    slug = store.project_slug(root)
    d = tmp_checkpoint_dir
    for name in (slug, "-Users-me-other", "-Users-me-projextra"):
        (d / name).mkdir(parents=True)
        (d / name / "latest.json").write_text('{"session_id": "x"}')
    # only a name starting with slug + "-" counts; "projextra" slugs differently
    sibs = store.sibling_buckets(root)
    assert sibs == [] or all(s["slug"].startswith(slug + "-") for s in sibs)
    assert slug not in [s["slug"] for s in sibs]


def test_sibling_buckets_torn_latest_reports_none_session(tmp_checkpoint_dir):
    from daimon_briefing import store
    root = "/Users/me/proj"
    slug = store.project_slug(root)
    child = tmp_checkpoint_dir / (slug + "-sub")
    child.mkdir(parents=True)
    (child / "latest.json").write_text("{not json")
    sibs = store.sibling_buckets(root)
    assert len(sibs) == 1 and sibs[0]["session_id"] is None


def test_sibling_buckets_empty_when_no_dir(tmp_checkpoint_dir):
    from daimon_briefing import store
    assert store.sibling_buckets("/Users/me/proj") == []


# ---- checkpoint GC: prune old per-session files, keep newest-N (#92) ----


def _session_files_present(d):
    """Per-session checkpoint filenames present in the flat store dir (excludes
    pointers latest.json / prev-N.json)."""
    return {
        p.name
        for p in d.iterdir()
        if p.is_file() and p.suffix == ".json" and not re.match(r"^(?:latest|prev-\d+)\.json$", p.name)
    }


def _write_seq(sample, sids, **kw):
    # Explicit distinct `created` stamps (write_checkpoint won't re-stamp them) so
    # newest-N ordering is deterministic — same-second ties are arbitrary by design.
    for i, sid in enumerate(sids):
        created = f"202{i + 1}-01-01T00:00:00Z"
        store.write_checkpoint(sid, {**sample, "session_id": sid, "created": created}, **kw)


def test_gc_prunes_beyond_keep(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    # History=1 so no prev pointers protect older sessions — isolates the keep window.
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "1")
    monkeypatch.setenv("DAIMON_CHECKPOINT_KEEP", "2")
    _write_seq(sample_checkpoint, ("S1", "S2", "S3", "S4"))
    present = _session_files_present(tmp_checkpoint_dir)
    assert present == {"S3.json", "S4.json"}  # newest 2 kept, S1/S2 pruned


def test_gc_keeps_pointer_referenced_beyond_keep(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    # Default history=3 → latest + prev-1 + prev-2 reference S4/S3/S2. keep=1 would
    # drop everything but S4, but pointer-referenced files must survive.
    monkeypatch.setenv("DAIMON_CHECKPOINT_KEEP", "1")
    _write_seq(sample_checkpoint, ("S1", "S2", "S3", "S4"), project_dir="/p/A")
    present = _session_files_present(tmp_checkpoint_dir)
    assert "S1.json" not in present  # not referenced by any live pointer → pruned
    assert {"S2.json", "S3.json", "S4.json"} <= present  # referenced → kept despite keep=1


def test_gc_aborts_on_torn_pointer(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    # A pointer that can't be parsed means the protection set is unknowable —
    # GC must delete NOTHING rather than risk pruning a still-referenced file.
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "1")
    _write_seq(sample_checkpoint, ("S1", "S2", "S3", "S4"), project_dir="/p/A")
    bucket = next(p for p in tmp_checkpoint_dir.iterdir() if p.is_dir())
    (bucket / "latest.json").write_text("{not json", encoding="utf-8")
    store._gc_checkpoints(tmp_checkpoint_dir, keep=1)
    present = _session_files_present(tmp_checkpoint_dir)
    assert present == {"S1.json", "S2.json", "S3.json", "S4.json"}  # fail-safe: no prune


def test_gc_aborts_on_pointer_missing_session_id(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    # Parseable but session_id-less pointer is equally unknowable — same fail-safe.
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "1")
    _write_seq(sample_checkpoint, ("S1", "S2", "S3"))
    (tmp_checkpoint_dir / "latest.json").write_text(json.dumps({"noise": True}), encoding="utf-8")
    store._gc_checkpoints(tmp_checkpoint_dir, keep=1)
    present = _session_files_present(tmp_checkpoint_dir)
    assert present == {"S1.json", "S2.json", "S3.json"}


def test_gc_disabled_at_zero(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "1")
    monkeypatch.setenv("DAIMON_CHECKPOINT_KEEP", "0")
    for sid in ("S1", "S2", "S3", "S4", "S5"):
        store.write_checkpoint(sid, {**sample_checkpoint, "session_id": sid})
    present = _session_files_present(tmp_checkpoint_dir)
    assert present == {"S1.json", "S2.json", "S3.json", "S4.json", "S5.json"}


def test_gc_error_does_not_break_write(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    # A disk error deleting a stale file must never fail the serialize that ran GC.
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "1")
    monkeypatch.setenv("DAIMON_CHECKPOINT_KEEP", "1")
    for sid in ("S1", "S2", "S3"):
        store.write_checkpoint(sid, {**sample_checkpoint, "session_id": sid})

    def boom(self, *a, **k):
        raise OSError("unlink failed")

    monkeypatch.setattr(store.Path, "unlink", boom)
    out = store.write_checkpoint("S4", {**sample_checkpoint, "session_id": "S4"})
    assert out.exists()  # write succeeded despite GC unlink failing
    assert "S4.json" in _session_files_present(tmp_checkpoint_dir)


def test_gc_orders_by_created_stamp_with_mtime_fallback(tmp_checkpoint_dir, monkeypatch):
    import os
    from datetime import datetime, timezone

    d = tmp_checkpoint_dir
    d.mkdir(parents=True, exist_ok=True)
    e2020 = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    e2025 = datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp()
    e2030 = datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp()

    # newest by created (2030) but oldest by mtime (2020) — created must win.
    (d / "newstamp.json").write_text(json.dumps({"created": "2030-01-01T00:00:00Z"}))
    os.utime(d / "newstamp.json", (e2020, e2020))
    # legacy: no created stamp → ranked by its mtime (2025).
    (d / "legacy.json").write_text(json.dumps({"session_id": "L"}))
    os.utime(d / "legacy.json", (e2025, e2025))
    # oldest by created (2020) but newest by mtime (2030) — created stamp sinks it.
    (d / "oldstamp.json").write_text(json.dumps({"created": "2020-01-01T00:00:00Z"}))
    os.utime(d / "oldstamp.json", (e2030, e2030))

    store._gc_checkpoints(d, keep=2)
    present = _session_files_present(d)
    assert present == {"newstamp.json", "legacy.json"}  # oldstamp (oldest created) pruned


# ---- team memory (#111): author stamping, dual-write, read_team ----

from daimon_briefing import config


def _team_author_dir(author_slug: str) -> Path:
    return config.team_dir() / "local" / "authors" / author_slug


def test_write_stamps_author(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-a", sample_checkpoint)
    blob = json.loads((tmp_checkpoint_dir / "S-a.json").read_text(encoding="utf-8"))
    assert blob["author"] == "ada"


def test_write_does_not_overwrite_existing_author(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-a", {**sample_checkpoint, "author": "grace"})
    blob = json.loads((tmp_checkpoint_dir / "S-a.json").read_text(encoding="utf-8"))
    assert blob["author"] == "grace"  # setdefault: idempotent, never re-stamps


def test_dual_write_off_by_default(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    monkeypatch.delenv("DAIMON_TEAM", raising=False)
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-a", sample_checkpoint)
    assert not config.team_dir().exists()  # DAIMON_TEAM unset → zero team files


def test_dual_write_creates_team_file(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/repo/x")
    path = _team_author_dir("ada") / "S-a.json"
    assert path.exists()
    blob = json.loads(path.read_text(encoding="utf-8"))
    assert blob["session_id"] == "S-prev"  # the checkpoint's own id field
    assert blob["author"] == "ada"


def test_dual_write_author_slug_full_munging(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    # Author names must use the full project_slug munging, not _safe_name:
    # under _safe_name "a/b" -> "a_b" collides with the literal author "a_b",
    # silently merging two humans in read_team. Windows-hostile chars (:*?<>|)
    # must be munged away too.
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "a/b")
    store.write_checkpoint("S-1", {**sample_checkpoint, "session_id": "S-1"})
    monkeypatch.setenv("DAIMON_AUTHOR", "a_b")
    store.write_checkpoint("S-2", {**sample_checkpoint, "session_id": "S-2"})
    authors_root = config.team_dir() / "local" / "authors"
    dirs = sorted(p.name for p in authors_root.iterdir())
    assert dirs == ["a-b", "a_b"]  # distinct dirs — no silent merge
    monkeypatch.setenv("DAIMON_AUTHOR", "eve:*?<>|smith")
    store.write_checkpoint("S-3", {**sample_checkpoint, "session_id": "S-3"})
    names = {p.name for p in authors_root.iterdir()}
    assert not any(c in n for n in names for c in ':*?<>|"')


def test_dual_write_stamps_project_slug(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    # project_slug was once a team-only stamp; the local file now carries it too
    # (durable attribution — pointer rotation expires, a stamp doesn't). Both
    # copies must agree.
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/repo/x")
    blob = json.loads((_team_author_dir("ada") / "S-a.json").read_text(encoding="utf-8"))
    assert blob["project_slug"] == store.project_slug("/repo/x")
    local = json.loads((tmp_checkpoint_dir / "S-a.json").read_text(encoding="utf-8"))
    assert local["project_slug"] == blob["project_slug"]


def test_dual_write_no_pointers_in_team_dir(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/repo/x")
    names = {p.name for p in _team_author_dir("ada").iterdir()}
    assert names == {"S-a.json"}  # immutable append-only, NEVER a latest.json pointer


def test_dual_write_failure_does_not_break_serialize(tmp_checkpoint_dir, sample_checkpoint, monkeypatch, tmp_path):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    # Point the team dir UNDER a regular file so mkdir(parents=True) raises OSError.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    monkeypatch.setattr(config, "team_dir", lambda: blocker / "team")
    out = store.write_checkpoint("S-a", sample_checkpoint)  # must NOT raise
    assert out.exists()  # local write intact
    assert store.read_latest() is not None


def test_gc_ignores_team_dir(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    monkeypatch.setenv("DAIMON_CHECKPOINT_KEEP", "1")  # aggressive GC
    _write_seq(sample_checkpoint, ["S1", "S2", "S3"], project_dir="/repo/x")
    # GC pruned the flat store down to 1, but every team mirror survives untouched.
    team_files = {p.name for p in _team_author_dir("ada").iterdir()}
    assert team_files == {"S1.json", "S2.json", "S3.json"}


def test_read_team_empty_when_no_dir(tmp_checkpoint_dir, monkeypatch):
    assert store.read_team(project_dir="/repo/x") == []


def test_read_team_newest_per_author_for_project(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    # Stamps are RELATIVE (days ago) so they stay inside the #113 default
    # 365-day read-time retention window regardless of the wall clock.
    import time as _t

    def _ago(days):
        return _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(_t.time() - days * 86400))

    # ada writes two checkpoints for /repo/x; newest by created must win.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    ada_new = _ago(10)
    store.write_checkpoint("a-old", {**sample_checkpoint, "created": _ago(100)}, project_dir="/repo/x")
    store.write_checkpoint("a-new", {**sample_checkpoint, "created": ada_new}, project_dir="/repo/x")
    # grace writes one checkpoint for /repo/x.
    monkeypatch.setenv("DAIMON_AUTHOR", "grace")
    store.write_checkpoint("g-1", {**sample_checkpoint, "created": _ago(50)}, project_dir="/repo/x")

    team = store.read_team(project_dir="/repo/x")
    by_author = {author: cp for author, cp in team}
    assert set(by_author) == {"ada", "grace"}
    assert by_author["ada"]["created"] == ada_new  # newest ada


def test_read_team_filters_by_project(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("a-x", sample_checkpoint, project_dir="/repo/x")
    store.write_checkpoint("a-y", sample_checkpoint, project_dir="/repo/y")
    team = store.read_team(project_dir="/repo/x")
    authors = [a for a, _ in team]
    assert authors == ["ada"]
    # only the /repo/x checkpoint is returned for ada
    assert team[0][1]["project_slug"] == store.project_slug("/repo/x")


def test_read_team_never_raises_on_garbage(tmp_checkpoint_dir, monkeypatch):
    author_dir = _team_author_dir("ada")
    author_dir.mkdir(parents=True, exist_ok=True)
    (author_dir / "torn.json").write_text("{not json", encoding="utf-8")
    # a torn file must be skipped, not crash the fan-in
    assert store.read_team(project_dir="/repo/x") == []


# ---- team retention (#113): read-time age filter, NO physical deletes ----


import time as _time


def _stamped(sample_checkpoint, sid, days_ago):
    created = _time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", _time.gmtime(_time.time() - days_ago * 86400)
    )
    return {**sample_checkpoint, "session_id": sid, "created": created}


def test_read_team_retention_filters_old_checkpoints(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_TEAM_RETENTION_DAYS", "30")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("a-old", _stamped(sample_checkpoint, "a-old", 40), project_dir="/repo/x")
    monkeypatch.setenv("DAIMON_AUTHOR", "grace")
    store.write_checkpoint("g-new", _stamped(sample_checkpoint, "g-new", 1), project_dir="/repo/x")

    team = store.read_team(project_dir="/repo/x")
    assert [a for a, _ in team] == ["grace"]  # ada aged out of the READ window
    # NO physical deletes — the shared branch is append-only; the file remains.
    assert (_team_author_dir("ada") / "a-old.json").exists()


def test_read_team_retention_zero_keeps_all(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_TEAM_RETENTION_DAYS", "0")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("a-ancient", _stamped(sample_checkpoint, "a-ancient", 4000), project_dir="/repo/x")
    team = store.read_team(project_dir="/repo/x")
    assert [a for a, _ in team] == ["ada"]


def test_read_team_retention_default_is_generous(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    # Default 365 days: a months-old teammate checkpoint still surfaces.
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("a-90d", _stamped(sample_checkpoint, "a-90d", 90), project_dir="/repo/x")
    assert [a for a, _ in store.read_team(project_dir="/repo/x")] == ["ada"]


# ---- latest-pointer regression guard: heal of an old session must not steal
# ---- "latest" from a newer one (#123) ----


def test_write_older_checkpoint_does_not_steal_global_latest(tmp_checkpoint_dir, sample_checkpoint):
    store.write_checkpoint("S-new", _stamped(sample_checkpoint, "S-new", 0))
    store.write_checkpoint("S-old", _stamped(sample_checkpoint, "S-old", 2))
    assert store.read_latest()["session_id"] == "S-new"
    # the per-session checkpoint itself still lands — only the pointer is guarded
    assert store.read_checkpoint("S-old") is not None


def test_write_older_checkpoint_does_not_steal_project_latest(tmp_checkpoint_dir, sample_checkpoint):
    store.write_checkpoint("S-new", _stamped(sample_checkpoint, "S-new", 0), project_dir="/p/A")
    store.write_checkpoint("S-old", _stamped(sample_checkpoint, "S-old", 2), project_dir="/p/A")
    assert store.read_latest(project_dir="/p/A")["session_id"] == "S-new"
    assert store.read_latest()["session_id"] == "S-new"


def test_write_older_checkpoint_guards_pointers_independently(tmp_checkpoint_dir, sample_checkpoint):
    # Project B has no pointer yet: its pointer must still be written even though
    # the global pointer is newer and stays put.
    store.write_checkpoint("S-new", _stamped(sample_checkpoint, "S-new", 0), project_dir="/p/A")
    store.write_checkpoint("S-old", _stamped(sample_checkpoint, "S-old", 2), project_dir="/p/B")
    assert store.read_latest()["session_id"] == "S-new"
    assert store.read_latest(project_dir="/p/B")["session_id"] == "S-old"


def test_write_newer_checkpoint_still_takes_latest(tmp_checkpoint_dir, sample_checkpoint):
    store.write_checkpoint("S-old", _stamped(sample_checkpoint, "S-old", 2))
    store.write_checkpoint("S-new", _stamped(sample_checkpoint, "S-new", 0))
    assert store.read_latest()["session_id"] == "S-new"


def test_write_over_legacy_pointer_without_created_takes_latest(tmp_checkpoint_dir, sample_checkpoint):
    # A legacy latest pointer with no `created` stamp never blocks an update.
    d = tmp_checkpoint_dir
    d.mkdir(parents=True, exist_ok=True)
    legacy = {**sample_checkpoint, "session_id": "S-legacy"}
    legacy.pop("created", None)
    (d / "latest.json").write_text(json.dumps(legacy), encoding="utf-8")
    store.write_checkpoint("S-old", _stamped(sample_checkpoint, "S-old", 2))
    assert store.read_latest()["session_id"] == "S-old"


# ---- first_seen: per-item birth stamp, exact-text carry-over (#126) ----


def _first_seens(ckpt):
    return {i["text"]: i.get("first_seen")
            for i in ckpt["working_context"]["open_questions"]}


def test_first_seen_stamped_on_new_items(tmp_checkpoint_dir, sample_checkpoint):
    ckpt = _stamped(sample_checkpoint, "S-1", 3)
    store.write_checkpoint("S-1", ckpt, project_dir="/p/A")
    back = store.read_checkpoint("S-1")
    for text, fs in _first_seens(back).items():
        assert fs == back["created"], text
    assert back["working_context"]["active_topic"].get("first_seen") == back["created"]


def test_first_seen_inherited_on_exact_text_match(tmp_checkpoint_dir, sample_checkpoint):
    first = _stamped(sample_checkpoint, "S-1", 3)
    store.write_checkpoint("S-1", first, project_dir="/p/A")
    t1 = store.read_checkpoint("S-1")["created"]

    second = _stamped(sample_checkpoint, "S-2", 1)  # same item texts, newer session
    second["working_context"]["open_questions"] = [
        dict(second["working_context"]["open_questions"][0]),  # carried verbatim
        {"text": "brand new question", "trust": "inferred"},   # born this session
    ]
    store.write_checkpoint("S-2", second, project_dir="/p/A")
    back = store.read_checkpoint("S-2")
    fs = _first_seens(back)
    carried_text = sample_checkpoint["working_context"]["open_questions"][0]["text"]
    assert fs[carried_text] == t1                      # inherited birth stamp
    assert fs["brand new question"] == back["created"]  # fresh stamp


def test_first_seen_changed_text_gets_fresh_stamp(tmp_checkpoint_dir, sample_checkpoint):
    store.write_checkpoint("S-1", _stamped(sample_checkpoint, "S-1", 3), project_dir="/p/A")
    second = _stamped(sample_checkpoint, "S-2", 1)
    second["working_context"]["open_questions"] = [
        {"text": "reworded question entirely", "trust": "inferred"}
    ]
    store.write_checkpoint("S-2", second, project_dir="/p/A")
    back = store.read_checkpoint("S-2")
    assert _first_seens(back)["reworded question entirely"] == back["created"]


def test_first_seen_legacy_prev_falls_back_to_prev_created(tmp_checkpoint_dir, sample_checkpoint):
    # Hand-written legacy latest: items carry no first_seen — inheritance uses
    # that checkpoint's created as the best available birth floor.
    d = tmp_checkpoint_dir
    d.mkdir(parents=True, exist_ok=True)
    legacy = json.loads(json.dumps(_stamped(sample_checkpoint, "S-legacy", 5)))
    (d / "latest.json").write_text(json.dumps(legacy), encoding="utf-8")

    second = _stamped(sample_checkpoint, "S-2", 1)
    store.write_checkpoint("S-2", second)
    back = store.read_checkpoint("S-2")
    carried_text = sample_checkpoint["working_context"]["open_questions"][0]["text"]
    assert _first_seens(back)[carried_text] == legacy["created"]


def test_first_seen_idempotent_on_rewrite(tmp_checkpoint_dir, sample_checkpoint):
    ckpt = _stamped(sample_checkpoint, "S-1", 1)
    ckpt["working_context"]["open_questions"][0]["first_seen"] = "2026-01-01T00:00:00Z"
    store.write_checkpoint("S-1", ckpt, project_dir="/p/A")
    back = store.read_checkpoint("S-1")
    carried_text = sample_checkpoint["working_context"]["open_questions"][0]["text"]
    assert _first_seens(back)[carried_text] == "2026-01-01T00:00:00Z"


# ---- #31 audit tail: importance-pinned GC, tmp reaping, pointer lock --------


def _imp_cp(sample, sid, created, importance):
    return {**sample, "session_id": sid, "created": created,
            "working_context": {
                "active_topic": {"text": f"topic {sid}", "trust": "inferred"},
                "open_questions": [{"text": f"question of {sid}",
                                    "trust": "inferred",
                                    "importance": importance}],
                "recent_decisions": [],
            }}


def test_gc_pins_high_importance_beyond_keep(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    # #31 item 1: recency-only GC killed an importance-10 decision when newer
    # low-importance checkpoints filled the window. Max item importance >= the
    # pin threshold (default 9) exempts the file from pruning.
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "1")
    monkeypatch.setenv("DAIMON_CHECKPOINT_KEEP", "2")
    store.write_checkpoint("S1", _imp_cp(sample_checkpoint, "S1",
                                         "2021-01-01T00:00:00Z", 10))
    for i, sid in enumerate(("S2", "S3", "S4")):
        store.write_checkpoint(sid, _imp_cp(sample_checkpoint, sid,
                                            f"202{i + 2}-01-01T00:00:00Z", 3))
    present = _session_files_present(tmp_checkpoint_dir)
    assert "S1.json" in present            # pinned by importance
    assert "S2.json" not in present        # normal prune
    assert {"S3.json", "S4.json"} <= present


def test_gc_pin_disabled_at_zero(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "1")
    monkeypatch.setenv("DAIMON_CHECKPOINT_KEEP", "2")
    monkeypatch.setenv("DAIMON_GC_PIN_IMPORTANCE", "0")
    store.write_checkpoint("S1", _imp_cp(sample_checkpoint, "S1",
                                         "2021-01-01T00:00:00Z", 10))
    for i, sid in enumerate(("S2", "S3", "S4")):
        store.write_checkpoint(sid, _imp_cp(sample_checkpoint, sid,
                                            f"202{i + 2}-01-01T00:00:00Z", 3))
    present = _session_files_present(tmp_checkpoint_dir)
    assert "S1.json" not in present        # pinning off -> pure recency window


def test_gc_reaps_stale_tmp_files(tmp_checkpoint_dir, sample_checkpoint):
    # #31 item 3: kill-9 mid-write orphans *.tmp forever (GC only touched
    # .json). Stale tmp (>1h) is reaped, in the flat dir AND buckets; a fresh
    # tmp (a write possibly in flight right now) is left alone.
    import os as _os
    import time as _time
    store.write_checkpoint("S1", {**sample_checkpoint, "session_id": "S1"},
                           project_dir="/p/A")
    d = tmp_checkpoint_dir
    bucket = next(p for p in d.iterdir() if p.is_dir())
    stale_flat = d / "dead.json.999.tmp"
    stale_bucket = bucket / "dead.json.999.tmp"
    fresh = d / "live.json.888.tmp"
    for p in (stale_flat, stale_bucket, fresh):
        p.write_text("{}", encoding="utf-8")
    old = _time.time() - 2 * 3600
    _os.utime(stale_flat, (old, old))
    _os.utime(stale_bucket, (old, old))
    store._gc_checkpoints(d, keep=100)
    assert not stale_flat.exists()
    assert not stale_bucket.exists()
    assert fresh.exists()


def test_pointer_lock_excludes_second_holder(tmp_checkpoint_dir):
    # #31 item 2: rotate-then-write latest is a multi-step TOCTOU when two
    # sessions end together. The critical section is now flock-guarded; a
    # second would-be holder cannot acquire while the first holds it.
    import pytest
    fcntl = pytest.importorskip("fcntl")
    d = tmp_checkpoint_dir
    d.mkdir(parents=True, exist_ok=True)
    with store._pointer_lock(d) as held:
        assert held
        probe = open(d / store._LOCK_NAME, "a+")
        try:
            with pytest.raises(OSError):
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            probe.close()


# ---- #102: stable item ids stamped at write ----


def _cp_with_items():
    return {
        "working_context": {
            "open_questions": [{"text": "will the cache hold", "trust": "inferred"}],
            "recent_decisions": [{"text": "ship the guard", "trust": "verbatim"}],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [{"text": "carry must stay pure"}],
            "uncertainties": [{"text": "is the window right"}],
            "contradictions_flagged": [{"text": "doc says X code says Y"}],
        },
    }


def test_write_stamps_id_on_every_list_item(tmp_checkpoint_dir):
    from daimon_briefing import store
    cp = _cp_with_items()
    store.write_checkpoint("S1", cp)
    assert cp["working_context"]["open_questions"][0]["id"].startswith("o-")
    assert cp["working_context"]["recent_decisions"][0]["id"].startswith("r-")
    assert cp["epistemic_snapshot"]["strong_beliefs"][0]["id"].startswith("s-")
    assert cp["epistemic_snapshot"]["uncertainties"][0]["id"].startswith("u-")
    assert cp["epistemic_snapshot"]["contradictions_flagged"][0]["id"].startswith("c-")


def test_id_stamping_is_idempotent_and_deterministic(tmp_checkpoint_dir):
    from daimon_briefing import store
    cp = _cp_with_items()
    store.write_checkpoint("S1", cp)
    first = cp["working_context"]["open_questions"][0]["id"]
    store.write_checkpoint("S1b", cp)
    assert cp["working_context"]["open_questions"][0]["id"] == first
    cp2 = _cp_with_items()
    store.write_checkpoint("S2", cp2)
    assert cp2["working_context"]["open_questions"][0]["id"] == first  # same kind+text -> same id


def test_identical_text_twins_get_distinct_ids(tmp_checkpoint_dir):
    from daimon_briefing import store
    cp = _cp_with_items()
    cp["working_context"]["open_questions"].append(
        {"text": "will the cache hold"})  # exact duplicate text
    store.write_checkpoint("S1", cp)
    ids = [i["id"] for i in cp["working_context"]["open_questions"]]
    assert len(set(ids)) == 2


def test_non_dict_and_empty_items_are_skipped(tmp_checkpoint_dir):
    from daimon_briefing import store
    cp = {"working_context": {"open_questions": ["bare string", {"text": ""}, {"no": "text"}]}}
    store.write_checkpoint("S1", cp)  # must not raise
    assert "id" not in cp["working_context"]["open_questions"][1]


# ---- #102: append-only event log + fold ----


def test_append_event_writes_jsonl_line(tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    assert store.append_event("o-abc123", "resolved", note="shipped",
                              project_dir="/p/A") is True
    slug = store.project_slug("/p/A")
    lines = (tmp_checkpoint_dir / slug / "events.jsonl").read_text().splitlines()
    evt = json.loads(lines[0])
    assert evt["item_ref"] == "o-abc123"
    assert evt["status"] == "resolved"
    assert evt["note"] == "shipped"
    assert evt["kind"] == "resolution"
    assert evt["ts"].endswith("Z")


def test_append_event_respects_kill_switch(tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    assert store.append_event("o-abc123", "resolved", project_dir="/p/A") is False
    slug = store.project_slug("/p/A")
    assert not (tmp_checkpoint_dir / slug / "events.jsonl").exists()


def test_resolutions_latest_wins_by_timestamp_not_line_order(tmp_checkpoint_dir):
    from daimon_briefing import store
    slug = store.project_slug("/p/A")
    d = tmp_checkpoint_dir / slug
    d.mkdir(parents=True, exist_ok=True)
    # NEWER event written FIRST in the file — fold must still prefer it
    (d / "events.jsonl").write_text(
        '{"ts": "2026-07-07T10:00:00Z", "kind": "resolution", "item_ref": "o-a", "status": "reopened"}\n'
        '{"ts": "2026-07-06T10:00:00Z", "kind": "resolution", "item_ref": "o-a", "status": "resolved"}\n'
        'not json at all\n'
        '{"ts": "2026-07-07T09:00:00Z", "kind": "future-kind", "item_ref": "o-b", "status": "done", "extra": 1}\n'
    )
    r = store.resolutions(project_dir="/p/A")
    assert r["o-a"]["status"] == "reopened"
    assert r["o-b"]["extra"] == 1  # unknown kind + field preserved


def test_is_resolved_semantics():
    from daimon_briefing import store
    assert store.is_resolved(None) is False
    assert store.is_resolved({"status": "resolved"}) is True
    assert store.is_resolved({"status": "superseded-by:o-x"}) is True
    assert store.is_resolved({"status": "REOPENED — regression"}) is False


def test_resolutions_missing_file_or_project_is_empty(tmp_checkpoint_dir):
    from daimon_briefing import store
    assert store.resolutions(project_dir="/p/NOPE") == {}
    assert store.resolutions(project_dir=None) == {}


def test_resolutions_equal_timestamp_tie_break_last_line_wins(tmp_checkpoint_dir):
    # CONTRACT (pinned, not a preference): when two events for the SAME
    # item_ref share a timestamp, the fold keeps the one appearing LATER in
    # file order. The fold replaces on `new_e >= cur_e`, so an equal ts lets
    # each subsequent line overwrite — the last write to the log wins the tie.
    from daimon_briefing import store
    slug = store.project_slug("/p/A")
    d = tmp_checkpoint_dir / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "events.jsonl").write_text(
        '{"ts": "2026-07-07T10:00:00Z", "item_ref": "o-a", "status": "resolved", "note": "first"}\n'
        '{"ts": "2026-07-07T10:00:00Z", "item_ref": "o-a", "status": "reopened", "note": "second"}\n'
    )
    r = store.resolutions(project_dir="/p/A")
    assert r["o-a"]["status"] == "reopened"
    assert r["o-a"]["note"] == "second"


def test_resolutions_invalid_utf8_bytes_return_empty(tmp_checkpoint_dir):
    # A corrupt log (invalid UTF-8) must fail open like a missing file, never
    # raise UnicodeDecodeError out of the read path — a reader can never let
    # one bad byte take down the whole fold.
    from daimon_briefing import store
    slug = store.project_slug("/p/A")
    d = tmp_checkpoint_dir / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "events.jsonl").write_bytes(b'{"item_ref": "o-a", "status": "resolved"}\n\xff\xfe bad bytes\n')
    assert store.resolutions(project_dir="/p/A") == {}


def test_append_event_stores_item_text_when_given(tmp_checkpoint_dir):
    from daimon_briefing import store
    store.append_event("o-abc", "resolved", project_dir="/p/A",
                       item_text="the exact loop wording")
    slug = store.project_slug("/p/A")
    evt = json.loads((tmp_checkpoint_dir / slug / "events.jsonl").read_text().splitlines()[0])
    assert evt["item_text"] == "the exact loop wording"


def test_append_event_omits_empty_item_text(tmp_checkpoint_dir):
    from daimon_briefing import store
    store.append_event("o-abc", "resolved", project_dir="/p/A")
    slug = store.project_slug("/p/A")
    evt = json.loads((tmp_checkpoint_dir / slug / "events.jsonl").read_text().splitlines()[0])
    assert "item_text" not in evt


# ---- #104: capture-time redaction wiring ----


def test_write_checkpoint_redacts_text_and_quote(tmp_checkpoint_dir):
    from daimon_briefing import store
    cp = {"working_context": {"open_questions": [
        {"text": "creds AKIAIOSFODNN7EXAMPLE leaked?",
         "quote": "he pasted DAIMON_LLM_API_KEY=sk-abcdef1234567890"}]}}
    store.write_checkpoint("S1", cp)
    item = cp["working_context"]["open_questions"][0]
    assert "AKIAIOSFODNN7EXAMPLE" not in item["text"]
    assert "sk-abcdef1234567890" not in item["quote"]
    assert cp["redactions"] == {"aws-key": 1, "api-key": 1}
    on_disk = (tmp_checkpoint_dir / "S1.json").read_text()
    assert "AKIAIOSFODNN7EXAMPLE" not in on_disk and "sk-abcdef" not in on_disk


def test_write_checkpoint_redacts_active_topic(tmp_checkpoint_dir):
    from daimon_briefing import store
    cp = {"working_context": {"active_topic": {
        "text": "rotating postgres://admin:hunter2secret@db/x"}}}
    store.write_checkpoint("S1", cp)
    assert "hunter2secret" not in cp["working_context"]["active_topic"]["text"]


def test_no_redactions_key_when_clean(tmp_checkpoint_dir):
    from daimon_briefing import store
    cp = {"working_context": {"open_questions": [{"text": "all clean here"}]}}
    store.write_checkpoint("S1", cp)
    assert "redactions" not in cp


def test_item_id_hashes_redacted_text(tmp_checkpoint_dir):
    import hashlib
    from daimon_briefing import store
    cp = {"working_context": {"open_questions": [
        {"text": "creds AKIAIOSFODNN7EXAMPLE leaked?"}]}}
    store.write_checkpoint("S1", cp)
    item = cp["working_context"]["open_questions"][0]
    digest = hashlib.sha1(
        f"open_questions:{item['text']}".encode("utf-8")).hexdigest()
    assert item["id"] == f"o-{digest[:6]}"  # id derived from REDACTED text


def test_rewrite_merges_redaction_counts(tmp_checkpoint_dir):
    # The anchor --attach path: read_latest -> mutate -> write_checkpoint on the
    # SAME dict. Old markers don't re-match patterns, so a naive overwrite of
    # checkpoint["redactions"] would drop kinds still physically present.
    from daimon_briefing import store
    cp = {"working_context": {"open_questions": [
        {"text": "creds AKIAIOSFODNN7EXAMPLE leaked?"}]}}
    store.write_checkpoint("S1", cp)
    assert cp["redactions"] == {"aws-key": 1}
    cp["working_context"]["open_questions"].append(
        {"text": "charge key sk_live_abcdef1234567890 exposed"})
    store.write_checkpoint("S1", cp)
    assert cp["redactions"] == {"aws-key": 1, "stripe-key": 1}


def test_rewrite_same_secret_keeps_redaction_count_stable(tmp_checkpoint_dir):
    # I3: api-key/credential-url markers satisfy their own patterns' value
    # class, so re-writing the SAME already-redacted dict (anchor --attach
    # path) must not inflate the count on every re-write.
    from daimon_briefing import store
    cp = {"working_context": {"open_questions": [
        {"text": "set DAIMON_LLM_API_KEY=sk-abcdef1234567890 in env"}]}}
    store.write_checkpoint("S1", cp)
    assert cp["redactions"] == {"api-key": 1}
    store.write_checkpoint("S1", cp)
    assert cp["redactions"] == {"api-key": 1}


def test_append_event_redacts_note_and_item_text(tmp_checkpoint_dir):
    from daimon_briefing import store
    store.append_event("o-a", "resolved", note="key was AKIAIOSFODNN7EXAMPLE",
                       item_text="Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.x",
                       project_dir="/p/A")
    slug = store.project_slug("/p/A")
    raw = (tmp_checkpoint_dir / slug / "events.jsonl").read_text()
    assert "AKIAIOSFODNN7EXAMPLE" not in raw and "eyJhbGci" not in raw
    assert "[redacted:aws-key]" in raw


def test_is_resolved_supersede_candidate_is_live():
    from daimon_briefing import store
    assert store.is_resolved({"status": "supersede-candidate:r-9f2c1a"}) is False
    assert store.is_resolved({"status": "superseded-by:r-9f2c1a"}) is True   # regression
    assert store.is_resolved({"status": "REOPENED"}) is False                 # regression


def test_redact_scrubs_link_targets(tmp_checkpoint_dir):
    from daimon_briefing import store
    cp = {"working_context": {"recent_decisions": [
        {"text": "use gateway B",
         "links": [{"type": "supersedes",
                    "target": "use gateway A with DAIMON_LLM_API_KEY=sk-abcdef1234567890"}]}]}}
    store.write_checkpoint("S1", cp)
    tgt = cp["working_context"]["recent_decisions"][0]["links"][0]["target"]
    assert "sk-abcdef1234567890" not in tgt and "[redacted:api-key]" in tgt
    assert cp["redactions"]["api-key"] == 1


def test_redact_tolerates_malformed_links(tmp_checkpoint_dir):
    from daimon_briefing import store
    cp = {"working_context": {"recent_decisions": [
        {"text": "x", "links": ["bare", {"no": "target"}, {"target": 7}]}]}}
    store.write_checkpoint("S1", cp)  # must not raise
