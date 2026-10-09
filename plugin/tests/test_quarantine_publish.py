"""PR 13 (D6): publishing a human quarantine to the team sidecar.

`store.publish_quarantine` appends hash-only rows to the author's own
`quarantines.jsonl` in each clone the project routes to (activation) and to
every own sidecar that still folds the id active (release). Fake clones are a
directory with a `.git` entry, which is all the router looks for; the rows
here are built the way `trust` builds them."""

import json
import os
import subprocess

import pytest

from daimon_briefing import config, jsonl, policy, store

PROJECT = "/p/quarantine-publish"
KEY = "0123456789abcdef"
OTHER_KEY = "fedcba9876543210"
TID = "tr-0123456789ab"
TID2 = "tr-ba9876543210"
DECLARED = {"version", "ts", "order", "event_id", "quarantine_id", "kind",
            "value_key", "state", "author"}


@pytest.fixture(autouse=True)
def team(monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "squad/census")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)


def _clone(name="r1"):
    d = config.team_dir() / name
    (d / ".git").mkdir(parents=True, exist_ok=True)
    return d


def _row(state="active", *, tid=TID, key=KEY, kind="decision", order=1,
         event_id="e1", **extra):
    row = {"version": 1, "ts": "2026-10-09T12:00:00Z", "order": order,
           "event_id": event_id, "quarantine_id": tid, "kind": kind,
           "value_key": key, "state": state, "author": "ada"}
    row.update(extra)
    return row


def _file(clone, name="r1"):
    return (config.team_dir() / name / "projects" / "squad" / "census"
            / "authors" / "ada" / "quarantines.jsonl")


def _rows(path):
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln]


# ---- the enumerators ---------------------------------------------------------


def test_own_team_dirs_clones_only_drops_the_local_mirror(tmp_checkpoint_dir):
    [adir] = store._own_team_dirs(PROJECT)
    assert adir.relative_to(config.team_dir()).parts[0] == "local"
    assert store._own_team_dirs(PROJECT, clones_only=True) == []
    _clone()
    [adir] = store._own_team_dirs(PROJECT, clones_only=True)
    assert adir == _file(None).parent


def test_own_author_dirs_everywhere_spans_clones_and_eras(tmp_checkpoint_dir):
    a, b = _clone("r1"), _clone("r2")
    mine = [a / "authors" / "ada", b / "projects" / "x" / "y" / "authors" / "ada"]
    for d in (*mine, a / "authors" / "grace"):
        d.mkdir(parents=True)
    local = config.team_dir() / "local" / "authors" / "ada"
    local.mkdir(parents=True)
    assert sorted(store._own_author_dirs_everywhere()) == sorted(mine)


# ---- publish -----------------------------------------------------------------


def test_an_active_row_lands_in_the_routed_clone(tmp_checkpoint_dir):
    _clone()
    out = store.publish_quarantine([_row()], PROJECT)
    assert out == [str(_file(None))] and out.failed == ()
    assert out.keys == {KEY}
    assert _rows(_file(None)) == [_row()]


def test_team_disabled_publishes_nothing(tmp_checkpoint_dir, monkeypatch):
    _clone()
    monkeypatch.delenv("DAIMON_TEAM")
    out = store.publish_quarantine([_row()], PROJECT)
    assert out == [] and out.failed == ()
    assert not _file(None).exists()


def test_only_local_publishes_nothing_and_fails_nothing(tmp_checkpoint_dir):
    out = store.publish_quarantine([_row()], PROJECT)
    assert out == [] and out.failed == ()
    assert not (config.team_dir() / "local").exists()


def test_the_row_has_exactly_the_declared_keys_and_no_prose(tmp_checkpoint_dir):
    _clone()
    dirty = _row(reason="the staging password is hunter2",
                 evidence=["measurement:1"], item_id="o-aaaaaaaaaaaa",
                 scope_slug="p-j", channel="cli-tty", authority="human",
                 text="hunter2")
    store.publish_quarantine([dirty], PROJECT)
    [written] = _rows(_file(None))
    assert set(written) == DECLARED
    assert "hunter2" not in _file(None).read_text()


def test_a_retry_is_a_no_op_by_event_id(tmp_checkpoint_dir):
    _clone()
    store.publish_quarantine([_row()], PROJECT)
    again = store.publish_quarantine([_row()], PROJECT)
    assert again == [] and again.failed == ()
    assert len(_rows(_file(None))) == 1


def test_a_row_without_a_valid_shape_is_not_published(tmp_checkpoint_dir):
    _clone()
    out = store.publish_quarantine(
        [_row(state="candidate"), _row(kind="nonsense"),
         _row(key="XYZ"), "x", None], PROJECT)
    assert out == [] and out.failed == ()
    assert not _file(None).exists()


def test_routed_to_two_clones_writes_both(tmp_checkpoint_dir, tmp_path,
                                          monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True,
                   capture_output=True, timeout=30)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    "git@github.com:org/svc.git"], check=True,
                   capture_output=True, timeout=30)
    monkeypatch.delenv("DAIMON_TEAM_PROJECT")
    for name in ("r1", "r2"):
        clone = _clone(name)
        (clone / "daimon-team.toml").write_text(
            '[projects."squad/census"]\nrepos = ["git@github.com:org/svc.git"]\n')
    out = store.publish_quarantine([_row()], str(repo))
    assert sorted(out) == sorted(
        str(config.team_dir() / n / "projects" / "squad" / "census"
            / "authors" / "ada" / "quarantines.jsonl") for n in ("r1", "r2"))


def test_an_unproven_own_file_is_a_failure_naming_the_remote(tmp_checkpoint_dir):
    _clone()
    path = _file(None)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"<<<<<<< conflict\n")
    out = store.publish_quarantine([_row()], PROJECT)
    assert out == []
    [(failed_path, why)] = out.failed
    assert failed_path == path and "unreadable" in why
    assert out.failed[0].remote == "r1"
    assert path.read_bytes() == b"<<<<<<< conflict\n"


def test_an_over_cap_own_file_is_a_failure_and_not_appended(
        tmp_checkpoint_dir, monkeypatch):
    _clone()
    path = _file(None)
    path.parent.mkdir(parents=True)
    path.write_bytes(json.dumps(_row(event_id="old")).encode() + b"\n")
    monkeypatch.setattr(store, "_MAX_TOMBSTONE_BYTES", 5)
    before = path.read_bytes()
    out = store.publish_quarantine([_row()], PROJECT)
    assert out == []
    [(failed_path, why)] = out.failed
    assert failed_path == path and "1 MB cap" in why
    assert path.read_bytes() == before


def test_an_oserror_on_the_append_is_a_failure(tmp_checkpoint_dir, monkeypatch):
    _clone()

    def boom(*a, **k):
        raise PermissionError(13, "no")

    monkeypatch.setattr(jsonl, "append_lines", boom)
    out = store.publish_quarantine([_row()], PROJECT)
    assert out == []
    [(path, why)] = out.failed
    assert path == _file(None) and "EACCES" in why


# ---- release -----------------------------------------------------------------


def test_a_release_reaches_a_clone_that_no_longer_routes_the_project(
        tmp_checkpoint_dir, monkeypatch):
    _clone("r1")
    _clone("r2")
    # Two clones and no grant in either toml: the env grant is ignored, so
    # nothing routes. The active row was published earlier, when r1 routed.
    old = _file(None, "r1")
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps(_row()) + "\n")
    monkeypatch.delenv("DAIMON_TEAM_PROJECT")
    assert store._own_team_dirs(PROJECT, clones_only=True) == []
    out = store.publish_quarantine(
        [_row("released", order=2, event_id="e2")], PROJECT)
    assert out == [str(old)]
    rows = _rows(old)
    assert [r["state"] for r in rows] == ["active", "released"]
    assert policy.fold_published_quarantines(rows) == frozenset()


def test_a_release_skips_a_file_that_does_not_fold_the_id_active(
        tmp_checkpoint_dir):
    _clone()
    path = _file(None)
    path.parent.mkdir(parents=True)
    other = _row(tid=TID2, key=OTHER_KEY, event_id="e0")
    path.write_text(json.dumps(other) + "\n")
    out = store.publish_quarantine(
        [_row("released", order=2, event_id="e2")], PROJECT)
    assert out == [] and out.failed == ()
    assert _rows(path) == [other]


def test_a_release_for_an_already_released_id_is_a_no_op(tmp_checkpoint_dir):
    _clone()
    store.publish_quarantine([_row()], PROJECT)
    store.publish_quarantine([_row("released", order=2, event_id="e2")], PROJECT)
    again = store.publish_quarantine(
        [_row("released", order=2, event_id="e3")], PROJECT)
    assert again == []
    assert len(_rows(_file(None))) == 2


def test_a_release_is_not_sent_to_the_local_mirror(tmp_checkpoint_dir):
    local = config.team_dir() / "local" / "authors" / "ada"
    local.mkdir(parents=True)
    (local / "quarantines.jsonl").write_text(json.dumps(_row()) + "\n")
    out = store.publish_quarantine(
        [_row("released", order=2, event_id="e2")], PROJECT)
    assert out == []


# ---- edges -------------------------------------------------------------------


def test_the_enumerator_survives_a_missing_team_dir(tmp_checkpoint_dir):
    assert not config.team_dir().exists()
    assert store._own_author_dirs_everywhere() == []


def test_the_enumerator_survives_a_clone_that_cannot_be_walked(
        tmp_checkpoint_dir, monkeypatch):
    _clone()

    def boom(remote):
        raise OSError("EIO")

    monkeypatch.setattr(store, "_team_author_dirs", boom)
    assert store._own_author_dirs_everywhere() == []


def test_a_failure_outside_the_team_dir_names_no_remote():
    assert store.PublishFailure("/elsewhere/q.jsonl", "why").remote == ""
