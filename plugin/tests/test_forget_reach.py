"""PR 12: a deleter reports its own reach, judged by a read afterwards.

`scrub_content_key`, `scrub_team_copies` and `recall.refresh_store_caches`
used to swallow every failure and return only what they rewrote, so a file
that could not be written looked like a clean sweep. Each now returns a
`jsonl.Reached`, and the failure is made with a real unwritable directory,
never a patched raise.
"""
import json
import os
import stat

import pytest

from daimon_briefing import config, jsonl, normalize, store
from daimon_briefing.surfaces import Writer

PROJECT = "/p/forget-reach"
VALUE = "zqxreachcanary7741 the payroll export token lives in vault七"
KEEPER = "an unrelated decision that must survive"
KEY = normalize.content_key(VALUE)


def _cp(sid, items):
    return {"session_id": sid, "created": "2026-08-01T00:00:00Z",
            "working_context": {"recent_decisions": [
                {"text": t, "trust": "inferred"} for t in items]}}


@pytest.fixture
def locked():
    """Make directories unwritable for the test and restore them after."""
    held = []

    def lock(path):
        held.append((path, path.stat().st_mode))
        os.chmod(path, stat.S_IRUSR | stat.S_IXUSR)

    yield lock
    for path, mode in held:
        os.chmod(path, mode)


def _seed(sessions=("S1", "S2")):
    for sid in sessions:
        store.write_checkpoint(sid, _cp(sid, [VALUE, KEEPER]),
                               project_dir=PROJECT, writer=Writer.HUMAN)
    return config.checkpoint_dir() / store.project_slug(PROJECT)


def test_scrub_content_key_reports_what_it_rewrote_and_that_it_reached(
        tmp_checkpoint_dir):
    bucket = _seed()
    got = store.scrub_content_key(KEY, project_dir=PROJECT)
    assert isinstance(got, jsonl.Reached)
    assert got.reached is True
    assert len(got) >= 2 and all(p.endswith(".json") for p in got)
    # enumerated from the directory, never from the scrubber's own walk
    assert not any(VALUE in p.read_text(encoding="utf-8")
                   for p in bucket.glob("*.json"))


def test_scrub_content_key_is_unreached_when_a_surface_cannot_be_written(
        tmp_checkpoint_dir, locked):
    bucket = _seed()
    locked(bucket)
    locked(config.checkpoint_dir())      # the flat pointers live here
    got = store.scrub_content_key(KEY, project_dir=PROJECT)
    assert got.reached is False
    assert list(got) == []
    assert got.detail == "unwritable"
    assert VALUE in (bucket / "latest.json").read_text(encoding="utf-8")


def test_scrub_content_key_is_unreached_for_a_surface_it_cannot_read(
        tmp_checkpoint_dir):
    bucket = _seed(("S1",))
    (bucket / "S9.json").write_bytes(b'{"project_slug": "x", "truncated": ')
    got = store.scrub_content_key(KEY, project_dir=PROJECT)
    assert got.reached is False
    assert got.detail == "unreadable"


def test_scrub_content_key_with_nothing_to_scrub_is_reached(tmp_checkpoint_dir):
    _seed()
    store.scrub_content_key(KEY, project_dir=PROJECT)
    again = store.scrub_content_key(KEY, project_dir=PROJECT)
    assert again.reached is True and list(again) == []


def _mirror(monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    _seed(("S1", "S2"))
    own = sorted(config.team_dir().rglob("S1.json"))
    assert own, "the dual write mirrored a copy"
    return own[0].parent


def test_scrub_team_copies_is_reached_when_it_rewrote_the_copies(
        tmp_checkpoint_dir, monkeypatch):
    _mirror(monkeypatch)
    got = store.scrub_team_copies(KEY, project_dir=PROJECT)
    assert isinstance(got, jsonl.Reached) and got.reached is True
    assert got and all(VALUE not in open(p, encoding="utf-8").read()
                       for p in got)


def test_scrub_team_copies_is_unreached_when_the_author_dir_is_unwritable(
        tmp_checkpoint_dir, monkeypatch, locked):
    author_dir = _mirror(monkeypatch)
    locked(author_dir)
    got = store.scrub_team_copies(KEY, project_dir=PROJECT)
    assert got.reached is False
    assert list(got) == []
    assert got.detail == "unwritable"
    assert any(VALUE in p.read_text(encoding="utf-8")
               for p in author_dir.glob("*.json"))


def test_scrub_team_copies_never_judges_a_teammates_file(
        tmp_checkpoint_dir, monkeypatch):
    _mirror(monkeypatch)
    mate = config.team_dir() / "local" / "authors" / "Grace" / "SX.json"
    mate.parent.mkdir(parents=True, exist_ok=True)
    mate.write_text(json.dumps({**_cp("SX", [VALUE]),
                                "project_slug": store.project_slug(PROJECT)}))
    got = store.scrub_team_copies(KEY, project_dir=PROJECT)
    assert got.reached is True
    assert "zqxreachcanary7741" in mate.read_text(encoding="utf-8")


def test_scrub_team_copies_is_unreached_for_a_nested_copy_it_cannot_parse(
        tmp_checkpoint_dir, monkeypatch):
    """A copy under the project's own logical path belongs to the project by
    its path, so a torn one cannot be proven clean and is said, not skipped."""
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "acme/backend")
    author_dir = _mirror(monkeypatch)
    assert "projects" in author_dir.parts
    (author_dir / "S1.json").write_bytes(b'{"torn": ')
    got = store.scrub_team_copies(KEY, project_dir=PROJECT)
    assert got.reached is False and got.detail == "unreadable"
    assert (author_dir / "S1.json").read_bytes() == b'{"torn": '
