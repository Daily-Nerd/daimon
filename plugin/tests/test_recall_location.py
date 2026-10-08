"""Where the recall index lives (#1132 PR 9b, D9.6).

The default store keeps `~/.daimon/recall.db`; any other store gets its own
file under `~/.daimon/recall/`, so two stores never share (or fight over) one
index. conftest points DAIMON_RECALL_DB at tmp for every other test; this
module turns that override off and moves HOME instead."""
import hashlib
import os
import sqlite3
import time

import pytest

from daimon_briefing import cli, config, normalize, privacy, recall, store

PROJECT = "/p/recall-location"
CANARY = "zqxlocationcanary9921 rotate the signing key before the next deploy"
KEEPER = "an unrelated decision that must not be reported"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """HOME elsewhere, the autouse recall override off, every store knob
    back to its default."""
    h = tmp_path / "fakehome"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("DAIMON_RECALL_DB", raising=False)
    monkeypatch.delenv("DAIMON_CHECKPOINT_DIR", raising=False)
    monkeypatch.delenv("DAIMON_TEAM_DIR", raising=False)
    return h


def _digest(ckpt, team):
    raw = f"{ckpt.resolve()}\0{team.resolve()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def test_default_store_keeps_the_legacy_path(home):
    assert config.recall_db() == home / ".daimon" / "recall.db"


def test_default_store_spelled_through_a_symlink_is_still_default(home, tmp_path):
    real = home / ".daimon" / "checkpoints"
    real.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    # Same directory, different spelling: resolves to the default store.
    os.environ["DAIMON_CHECKPOINT_DIR"] = str(link)
    assert config.recall_db() == home / ".daimon" / "recall.db"


def test_other_checkpoint_dir_gets_a_hashed_file_under_recall(home, tmp_path,
                                                             monkeypatch):
    ckpt = tmp_path / "elsewhere"
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(ckpt))
    team = home / ".daimon" / "team"
    want = home / ".daimon" / "recall" / f"{_digest(ckpt, team)}.db"
    assert config.recall_db() == want


def test_team_dir_is_part_of_the_store_identity(home, tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "c"))
    a = config.recall_db()
    monkeypatch.setenv("DAIMON_TEAM_DIR", str(tmp_path / "t"))
    b = config.recall_db()
    assert a != b
    assert a.parent == b.parent == home / ".daimon" / "recall"


def test_the_location_follows_the_environment_at_call_time(home, tmp_path,
                                                          monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "one"))
    one = config.recall_db()
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "two"))
    two = config.recall_db()
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "one"))
    assert one != two
    assert config.recall_db() == one


def test_the_checkpoint_override_context_selects_its_own_index(home, tmp_path):
    outside = config.recall_db()
    with config.checkpoint_dir_override(tmp_path / "scratch"):
        inside = config.recall_db()
    assert inside != outside
    assert inside.parent == home / ".daimon" / "recall"
    assert config.recall_db() == outside


def test_the_explicit_override_still_wins(home, tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_RECALL_DB", str(tmp_path / "pinned.db"))
    assert config.recall_db() == tmp_path / "pinned.db"


def test_a_rebuild_records_which_store_it_indexes(home, tmp_path, monkeypatch):
    ckpt = tmp_path / "ckpt"
    team = tmp_path / "team"
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(ckpt))
    monkeypatch.setenv("DAIMON_TEAM_DIR", str(team))
    ckpt.mkdir()
    recall.rebuild()
    path = config.recall_db()
    assert path.parent == home / ".daimon" / "recall"
    meta = dict(sqlite3.connect(str(path)).execute(
        "SELECT key, value FROM meta"))
    assert meta["store"] == str(ckpt.resolve())
    assert meta["team"] == str(team.resolve())


def _build_in(monkeypatch, tmp_path, name, team=None):
    ckpt = tmp_path / name
    ckpt.mkdir(exist_ok=True)
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(ckpt))
    if team is not None:
        monkeypatch.setenv("DAIMON_TEAM_DIR", str(team))
    recall.rebuild()
    return ckpt, config.recall_db()


def test_store_caches_lists_every_cache_of_the_audited_store(
        home, tmp_path, monkeypatch):
    ckpt, first = _build_in(monkeypatch, tmp_path, "a")
    _, sibling = _build_in(monkeypatch, tmp_path, "a", team=tmp_path / "t2")
    _, other = _build_in(monkeypatch, tmp_path, "b")
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(ckpt))
    monkeypatch.delenv("DAIMON_TEAM_DIR", raising=False)
    got = recall.store_caches()
    assert first in got and sibling in got
    assert other not in got


def test_an_index_without_meta_store_at_the_legacy_path_is_the_default_store(
        home):
    legacy = home / ".daimon" / "recall.db"
    legacy.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(legacy))
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()
    assert legacy in recall.store_caches()


def test_a_legacy_index_is_not_claimed_by_another_store(home, tmp_path,
                                                       monkeypatch):
    legacy = home / ".daimon" / "recall.db"
    legacy.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(legacy))
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "x"))
    assert legacy not in recall.store_caches()


def test_with_the_explicit_override_only_that_index_is_a_cache(
        home, tmp_path, monkeypatch):
    _build_in(monkeypatch, tmp_path, "a")
    pinned = tmp_path / "pinned.db"
    monkeypatch.setenv("DAIMON_RECALL_DB", str(pinned))
    assert recall.store_caches() == [pinned]


def _age(path, days):
    t = time.time() - days * 86400
    os.utime(path, (t, t))


def test_reap_drops_a_cache_whose_store_is_gone(home, tmp_path, monkeypatch):
    gone, dead = _build_in(monkeypatch, tmp_path, "gone")
    _, alive = _build_in(monkeypatch, tmp_path, "alive")
    gone.rmdir()
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "alive"))
    assert recall.reap_stale_caches(apply=False) == [dead]
    assert dead.exists()
    assert recall.reap_stale_caches() == [dead]
    assert not dead.exists() and alive.exists()


def test_reap_drops_a_cache_untouched_for_thirty_days(home, tmp_path,
                                                     monkeypatch):
    _, old = _build_in(monkeypatch, tmp_path, "old")
    _, live = _build_in(monkeypatch, tmp_path, "live")
    _age(old, 31)
    _age(live, 31)
    # `live` is what the running store points at: never reaped.
    assert recall.reap_stale_caches() == [old]
    assert live.exists()


def test_reap_keeps_a_recent_cache_of_an_existing_store(home, tmp_path,
                                                       monkeypatch):
    _, other = _build_in(monkeypatch, tmp_path, "other")
    _build_in(monkeypatch, tmp_path, "live")
    _age(other, 29)
    assert recall.reap_stale_caches() == []


def test_reap_never_touches_the_legacy_default_index(home, monkeypatch,
                                                    tmp_path):
    legacy = home / ".daimon" / "recall.db"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"x")
    _age(legacy, 90)
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "x"))
    assert recall.reap_stale_caches() == []
    assert legacy.exists()


def test_reap_does_nothing_under_the_explicit_override(home, tmp_path,
                                                      monkeypatch):
    _, old = _build_in(monkeypatch, tmp_path, "old")
    _age(old, 90)
    monkeypatch.setenv("DAIMON_RECALL_DB", str(tmp_path / "pinned.db"))
    assert recall.reap_stale_caches() == []
    assert old.exists()


def test_dead_snapshots_are_reaped_in_the_recall_directory(home, tmp_path,
                                                          monkeypatch):
    _, live = _build_in(monkeypatch, tmp_path, "live")
    dead = live.with_name(f"{live.name}.4242.tmp.a1b2c3d4e5f6")
    journal = live.with_name(dead.name + "-journal")
    mine = live.with_name(live.name + ".bak.tmp")
    for p in (dead, journal, mine):
        p.write_bytes(b"x")
        _age(p, 1)
    # A strand left by a store this process is not pointing at.
    stranded = live.parent / "0123456789abcdef.db.777.tmp"
    stranded.write_bytes(b"x")
    _age(stranded, 1)
    got = {p.name for p in recall.reap_dead_snapshots()}
    assert got == {dead.name, journal.name, stranded.name}
    assert mine.exists()


def test_dead_snapshots_beside_the_legacy_index_are_reaped(home, tmp_path,
                                                          monkeypatch):
    legacy_dir = home / ".daimon"
    legacy_dir.mkdir()
    dead = legacy_dir / "recall.db.4242.tmp"
    dead.write_bytes(b"x")
    _age(dead, 1)
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "x"))
    assert [p.name for p in recall.reap_dead_snapshots()] == [dead.name]


def test_status_attribution_follows_the_store(home, tmp_path, monkeypatch):
    _build_in(monkeypatch, tmp_path, "a")
    assert recall.index_attribution() == {"items": 0, "unattributed": 0}
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "b"))
    assert recall.index_attribution() is None


def _plant_sibling_cache(ckpt, team, rows, fingerprint):
    """A cache of `ckpt` built under a different team dir, as a second
    process with another DAIMON_TEAM_DIR would leave it."""
    path = (config.recall_db().parent
            / f"{_digest(ckpt, team)}.db")
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE items(id INTEGER PRIMARY KEY, text TEXT, quote TEXT,"
        " scene TEXT, project_slug TEXT, author TEXT, item_id TEXT);")
    conn.execute("INSERT INTO meta VALUES ('fingerprint', ?)", (fingerprint,))
    conn.execute("INSERT INTO meta VALUES ('store', ?)", (str(ckpt.resolve()),))
    conn.execute("INSERT INTO meta VALUES ('team', ?)", (str(team.resolve()),))
    for text, slug in rows:
        conn.execute(
            "INSERT INTO items(text, project_slug, item_id) VALUES (?, ?, 'i-r')",
            (text, slug))
    conn.commit()
    conn.close()
    return path


def test_the_audit_scans_every_cache_of_the_audited_store(home, tmp_path,
                                                         monkeypatch):
    ckpt = tmp_path / "audited"
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(ckpt))
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": KEEPER, "trust": "inferred"}]}}, project_dir=PROJECT)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT, tombstone=True)
    recall.rebuild()
    live = config.recall_db()
    live.parent.mkdir(parents=True, exist_ok=True)
    fp = recall._fingerprint()
    sibling = _plant_sibling_cache(ckpt, tmp_path / "other-team",
                                   [(CANARY, store.project_slug(PROJECT))], fp)
    foreign = _plant_sibling_cache(tmp_path / "unrelated",
                                   tmp_path / "other-team",
                                   [(CANARY, store.project_slug(PROJECT))], fp)
    result = privacy.audit_project(project_dir=PROJECT)
    paths = {f["path"] for f in result["findings"]}
    assert str(sibling) in paths
    assert str(foreign) not in paths


def test_forget_leaves_no_cache_of_the_store_holding_the_value(
        home, tmp_path, monkeypatch):
    ckpt = tmp_path / "forgetting"
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(ckpt))
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": CANARY, "trust": "inferred"},
            {"text": KEEPER, "trust": "inferred"}]}}, project_dir=PROJECT)
    recall.rebuild()
    live = config.recall_db()
    sibling = _plant_sibling_cache(ckpt, tmp_path / "other-team",
                                   [(CANARY, store.project_slug(PROJECT))],
                                   "whatever")
    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0
    assert not sibling.exists()
    conn = sqlite3.connect(str(live))
    texts = [r[0] for r in conn.execute("SELECT text FROM items")]
    conn.close()
    assert CANARY not in texts and KEEPER in texts


def test_heal_reaps_stale_caches(home, tmp_path, monkeypatch, capsys):
    gone, dead = _build_in(monkeypatch, tmp_path, "gone")
    _build_in(monkeypatch, tmp_path, "alive")
    gone.rmdir()
    assert cli.main(["heal"]) in (0, 1)
    assert not dead.exists()
    assert "stale recall cache" in capsys.readouterr().out
