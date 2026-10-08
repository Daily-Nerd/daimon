"""A forget that did not reach the team says so (#1132 PR 10b, D10.5).

`publish_tombstone` returns `Published`, a list of the paths written (so
`== []` still holds when nothing was) that also carries what FAILED: an
over-cap or unproven own sidecar is never re-appended as "absent". `daimon
forget` exits 4 after the local scrub, and `forget --republish` is the cure.
"""

import json

import pytest

from daimon_briefing import cli, normalize, refutations, store
from daimon_briefing.store import Published

PROJECT = "/p/forget-publish"
VALUE = "zqxpublishcanary7741 the signing seed lives in the old vault"
KEY = normalize.content_key(VALUE)
OTHER = normalize.content_key("another forgotten value")


@pytest.fixture(autouse=True)
def team(monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")


def _sidecar():
    [adir] = store._own_team_dirs(PROJECT)
    return adir / store._TOMBSTONE_NAME


def _refute():
    refutations.assert_refutation(
        subject=VALUE, verdict="v", scope="s", evidence=["measurement:1"],
        channel="cli-tty", ratified=True, project_dir=PROJECT)


def _rows(path):
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln]


# ---- Published ---------------------------------------------------------------

def test_a_clean_publish_returns_the_path_and_no_failures(tmp_checkpoint_dir):
    out = store.publish_tombstone(KEY, project_dir=PROJECT)
    assert isinstance(out, Published) and isinstance(out, list)
    assert out == [str(_sidecar())] and out.failed == ()


def test_nothing_to_publish_still_compares_equal_to_an_empty_list(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.delenv("DAIMON_TEAM")
    out = store.publish_tombstone(KEY, project_dir=PROJECT)
    assert out == [] and out.failed == ()


def test_a_key_already_published_is_neither_written_nor_a_failure(
        tmp_checkpoint_dir):
    store.publish_tombstone(KEY, project_dir=PROJECT)
    out = store.publish_tombstone(KEY, project_dir=PROJECT)
    assert out == [] and out.failed == ()
    assert len(_rows(_sidecar())) == 1


def test_an_over_cap_own_sidecar_is_a_failure_and_is_not_appended_to(
        tmp_checkpoint_dir, monkeypatch):
    path = _sidecar()
    path.parent.mkdir(parents=True)
    path.write_bytes(json.dumps({"key": OTHER}).encode() + b"\n")
    monkeypatch.setattr(store, "_MAX_TOMBSTONE_BYTES", 5)
    before = path.read_bytes()
    out = store.publish_tombstone(KEY, project_dir=PROJECT)
    assert out == []
    assert [(p, why) for p, why in out.failed] == [
        (path, "its tombstone ledger is over the 1 MB cap")]
    assert path.read_bytes() == before


def test_an_unproven_own_sidecar_is_a_failure_not_an_absent_key(
        tmp_checkpoint_dir):
    path = _sidecar()
    path.parent.mkdir(parents=True)
    path.write_bytes(b"<<<<<<< conflict\n")
    out = store.publish_tombstone(KEY, project_dir=PROJECT)
    assert out == []
    [(failed_path, why)] = out.failed
    assert failed_path == path and "unreadable" in why
    assert path.read_bytes() == b"<<<<<<< conflict\n"


def test_a_key_found_in_an_unproven_sidecar_is_already_published(
        tmp_checkpoint_dir):
    path = _sidecar()
    path.parent.mkdir(parents=True)
    path.write_bytes(json.dumps({"key": KEY}).encode()
                     + b"\n<<<<<<< conflict\n")
    out = store.publish_tombstone(KEY, project_dir=PROJECT)
    assert out == [] and out.failed == ()


def test_a_torn_own_sidecar_is_proven_and_still_published_to(
        tmp_checkpoint_dir):
    path = _sidecar()
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"key": "tor')
    out = store.publish_tombstone(KEY, project_dir=PROJECT)
    assert out == [str(path)] and out.failed == ()


def test_an_oserror_on_the_append_is_a_failure(tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import jsonl

    def boom(*a, **k):
        raise PermissionError(13, "no")
    monkeypatch.setattr(jsonl, "append_lines", boom)
    out = store.publish_tombstone(KEY, project_dir=PROJECT)
    assert out == []
    [(path, why)] = out.failed
    assert path == _sidecar() and "EACCES" in why


# ---- the verb ----------------------------------------------------------------

def test_forget_warns_and_exits_4_when_the_publish_failed(
        tmp_checkpoint_dir, capsys):
    _refute()
    path = _sidecar()
    path.parent.mkdir(parents=True)
    path.write_bytes(b"<<<<<<< conflict\n")
    rc = cli.main(["forget", VALUE, "--project", PROJECT])
    out = capsys.readouterr().out
    assert rc == 4
    assert "warning: forget not published to the team (" in out
    assert "unreadable" in out
    assert VALUE not in refutations._path(PROJECT).read_text()


def test_forget_exits_0_when_the_publish_landed(tmp_checkpoint_dir, capsys):
    _refute()
    assert cli.main(["forget", VALUE, "--project", PROJECT]) == 0
    assert KEY in _sidecar().read_text()


def test_forget_without_a_team_has_nothing_to_publish_and_exits_0(
        tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.delenv("DAIMON_TEAM")
    _refute()
    assert cli.main(["forget", VALUE, "--project", PROJECT]) == 0


# ---- --republish --------------------------------------------------------------

def _forget_locally(key=KEY, ref="o-1"):
    store.append_event(ref, f"forgotten:{key}", kind="tombstone",
                       tombstone=True, project_dir=PROJECT,
                       writer=__import__("daimon_briefing.surfaces",
                                         fromlist=["Writer"]).Writer.HUMAN)


def test_republish_needs_no_target_and_publishes_every_missing_key(
        tmp_checkpoint_dir, capsys):
    _forget_locally(KEY, "o-1")
    _forget_locally(OTHER, "o-2")
    assert cli.main(["forget", "--republish", "--project", PROJECT]) == 0
    assert {r["key"] for r in _rows(_sidecar())} == {KEY, OTHER}
    assert "republished 2 tombstone(s)" in capsys.readouterr().out


def test_republish_is_presence_checked_and_idempotent(tmp_checkpoint_dir,
                                                      capsys):
    _forget_locally(KEY, "o-1")
    store.publish_tombstone(KEY, project_dir=PROJECT)
    _forget_locally(OTHER, "o-2")
    assert cli.main(["forget", "--republish", "--project", PROJECT]) == 0
    assert cli.main(["forget", "--republish", "--project", PROJECT]) == 0
    assert sorted(r["key"] for r in _rows(_sidecar())) == sorted([KEY, OTHER])
    assert "republished 0 tombstone(s)" in capsys.readouterr().out.splitlines()[-1]


def test_republish_is_gated_on_the_team_being_enabled(
        tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.delenv("DAIMON_TEAM")
    _forget_locally(KEY, "o-1")
    assert cli.main(["forget", "--republish", "--project", PROJECT]) == 0
    assert "no team is enabled" in capsys.readouterr().out
    assert not _sidecar().exists()


def test_republish_refuses_with_a_warning_on_an_unproven_sidecar(
        tmp_checkpoint_dir, capsys):
    _forget_locally(KEY, "o-1")
    path = _sidecar()
    path.parent.mkdir(parents=True)
    path.write_bytes(b"<<<<<<< conflict\n")
    rc = cli.main(["forget", "--republish", "--project", PROJECT])
    out = capsys.readouterr().out
    assert rc == 4
    assert "warning: forget not published to the team (" in out
    assert path.read_bytes() == b"<<<<<<< conflict\n"


def test_forget_with_neither_a_target_nor_republish_is_a_usage_error(
        tmp_checkpoint_dir, capsys):
    assert cli.main(["forget", "--project", PROJECT]) == 2
    assert "needs a target" in capsys.readouterr().err


def test_republish_refuses_an_unproven_events_ledger_instead_of_reporting_zero(
        tmp_checkpoint_dir, capsys):
    _forget_locally(KEY, "o-1")
    events = store._events_path(PROJECT)
    events.write_bytes(events.read_bytes() + b"<<<<<<< conflict\n")
    rc = cli.main(["forget", "--republish", "--project", PROJECT])
    out = capsys.readouterr()
    assert rc == 2
    assert "events.jsonl is unreadable" in out.err
    assert "republished" not in out.out
    assert not _sidecar().exists()
