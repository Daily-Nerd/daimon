"""PR 13 (D6): publishing a human quarantine to the team sidecar.

`store.publish_quarantine` appends hash-only rows to the author's own
`quarantines.jsonl` in each clone the project routes to (activation) and to
every own sidecar that still folds the id active (release). Fake clones are a
directory with a `.git` entry, which is all the router looks for; the rows
here are built the way `trust` builds them."""

import hashlib
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
    # a real event id is 32 hex; a short label stands for one
    event_id = (event_id if len(event_id) == 32 and set(event_id) <= set(
        "0123456789abcdef") else hashlib.md5(event_id.encode()).hexdigest())
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
        [_row(state="candidate"), "x", None], PROJECT)
    assert out == [] and out.failed == ()
    assert not _file(None).exists()
    # a claim that is off shape is refused AND reported, never written
    out = store.publish_quarantine(
        [_row(kind="nonsense"), _row(key="XYZ"),
         _row(tid="the text of the secret"), _row(ts="soon"),
         _row(order="1"), _row(order=float("inf"))], PROJECT)
    assert out == [] and len(out.failed) == 6
    assert "hash-only" in out.failed[0].reason
    assert not _file(None).exists()


def test_a_prose_author_is_written_as_its_directory_slug(tmp_checkpoint_dir):
    _clone()
    store.publish_quarantine(
        [_row(author="Ada Lovelace the secret: hunter2")], PROJECT)
    [written] = _rows(_file(None))
    assert written["author"] == "Ada-Lovelace-the-secret--hunter2"


def test_a_planted_infinite_order_in_the_own_file_cannot_break_a_release(
        tmp_checkpoint_dir):
    _clone()
    path = _file(None)
    path.parent.mkdir(parents=True)
    with open(path, "ab") as handle:        # independent byte writer
        handle.write(json.dumps(_row()).encode() + b"\n"
                     + b'{"kind":"decision","value_key":"%s","state":'
                     b'"active","order":Infinity}\n' % KEY.encode())
    out = store.publish_quarantine(
        [_row("released", order=2, event_id="e2")], PROJECT)
    assert len(out) == 1 and out.failed == ()


def test_a_raise_while_planning_is_a_reported_failure_not_a_traceback(
        tmp_checkpoint_dir, monkeypatch):
    _clone()
    store.publish_quarantine([_row()], PROJECT)

    def boom(rows, qid):
        raise RuntimeError("fold failed")

    monkeypatch.setattr(store, "_id_folds_active", boom)
    out = store.publish_quarantine(
        [_row("released", order=2, event_id="e2")], PROJECT)
    assert out == [] and len(out.failed) == 1
    assert "RuntimeError" in out.failed[0].reason


def test_trust_release_exits_4_with_the_direction_when_planning_raises(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    _clone()
    tid = _propose()

    def boom(rows, qid):
        raise RuntimeError("fold failed")

    monkeypatch.setattr(store, "_id_folds_active", boom)
    assert cli.main(["trust", "release", tid, "--project", PROJECT]) == 4
    assert "keep masking" in "".join(capsys.readouterr())
    assert trust.get(tid, project_dir=PROJECT)["state"] == "released"


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


# ---- the trust hooks ---------------------------------------------------------

from daimon_briefing import cli, trust  # noqa: E402

VALUE = "the deploy key rotation runbook was fabricated by the agent"


def _propose(channel="cli-tty", text=VALUE, **kw):
    return trust.propose(
        text=text, kind="decision", reason="PROSE-REASON-CANARY looks fabricated",
        evidence=["issue:1109"], item_id="o-1234567890ab", channel=channel,
        project_dir=PROJECT, **kw)


def _states(path):
    return [(r["state"], r["quarantine_id"]) for r in _rows(path)]


def test_human_propose_publishes_active(tmp_checkpoint_dir):
    _clone()
    tid = _propose()
    assert isinstance(tid, str) and tid.startswith("tr-")
    assert len(tid.published) == 1 and tid.published.failed == ()
    assert _states(_file(None)) == [("active", tid)]
    [row] = _rows(_file(None))
    assert row["value_key"] == trust.value_key(VALUE)
    assert row["kind"] == "decision"
    local = trust.events(project_dir=PROJECT)[-1]
    assert (row["order"], row["event_id"], row["ts"]) == (
        local["order"], local["event_id"], local["ts"])
    assert "PROSE-REASON-CANARY" not in _file(None).read_text()


def test_agent_propose_publishes_nothing(tmp_checkpoint_dir):
    _clone()
    tid = _propose(channel="cli-agent")
    assert tid.published == [] and not _file(None).exists()


def test_confirm_publishes_active(tmp_checkpoint_dir):
    _clone()
    tid = _propose(channel="cli-agent")
    out = trust.confirm(tid, channel="cli-tty", project_dir=PROJECT)
    assert len(out) == 1 and _states(_file(None)) == [("active", tid)]


def test_dismiss_publishes_nothing(tmp_checkpoint_dir):
    _clone()
    tid = _propose(channel="cli-agent")
    out = trust.dismiss(tid, channel="cli-tty", project_dir=PROJECT)
    assert out == [] and not _file(None).exists()


def test_release_publishes_released(tmp_checkpoint_dir):
    _clone()
    tid = _propose()
    out = trust.release(tid, channel="cli-tty", project_dir=PROJECT)
    assert len(out) == 1
    assert _states(_file(None)) == [("active", tid), ("released", tid)]
    assert policy.fold_published_quarantines(_rows(_file(None))) == frozenset()


def test_reproposing_after_a_release_publishes_a_new_active_on_the_same_id(
        tmp_checkpoint_dir):
    _clone()
    tid = _propose()
    trust.release(tid, channel="cli-tty", project_dir=PROJECT)
    again = _propose()
    assert again == tid
    assert [s for s, _ in _states(_file(None))] == [
        "active", "released", "active"]
    assert policy.fold_published_quarantines(_rows(_file(None)))


def test_team_disabled_the_hook_publishes_nothing(tmp_checkpoint_dir,
                                                  monkeypatch):
    _clone()
    monkeypatch.delenv("DAIMON_TEAM")
    tid = _propose()
    assert tid.published == [] and not _file(None).exists()
    assert trust.get(tid, project_dir=PROJECT)["state"] == "active"


def test_a_failed_publish_leaves_the_local_transition_and_reports(
        tmp_checkpoint_dir):
    _clone()
    path = _file(None)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"<<<<<<< conflict\n")
    tid = _propose()
    assert trust.get(tid, project_dir=PROJECT)["state"] == "active"
    assert tid.published == [] and len(tid.published.failed) == 1


def test_propose_still_behaves_as_a_plain_string(tmp_checkpoint_dir):
    tid = _propose(channel="cli-agent")
    assert tid == str(tid) and tid.published == []
    assert trust.get(tid, project_dir=PROJECT) is not None


# ---- the verbs say what did not publish --------------------------------------


def _args(*extra):
    return ["trust", "propose", "--text", VALUE, "--kind", "decision",
            "--reason", "looks fabricated", "--evidence", "issue:1109",
            "--project", PROJECT, *extra]


def test_cli_propose_exit_4_names_the_failed_sidecar_and_the_direction(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    _clone()
    path = _file(None)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"<<<<<<< conflict\n")
    assert cli.main(_args()) == 4
    out = capsys.readouterr()
    text = out.out + out.err
    assert "r1" in text
    assert "teammates still see" in text
    assert "daimon trust republish" in text


def test_cli_release_exit_4_says_teammates_keep_masking(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    _clone()
    tid = _propose()
    path = _file(None)
    path.write_text(path.read_text() + "<<<<<<< conflict\n")
    assert cli.main(["trust", "release", tid, "--project", PROJECT]) == 4
    text = "".join(capsys.readouterr())
    assert "keep masking" in text and "r1" in text


def test_cli_propose_exit_0_when_published(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    _clone()
    assert cli.main(_args()) == 0
    assert _file(None).exists()


def test_cli_dismiss_is_unchanged(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    _clone()
    tid = _propose(channel="cli-agent")
    assert cli.main(["trust", "dismiss", tid, "--project", PROJECT]) == 0
    assert not _file(None).exists()


def test_a_transition_that_does_not_move_the_state_publishes_nothing(
        tmp_checkpoint_dir):
    _clone()
    event = {"event": "confirmed", "quarantine_id": TID, "kind": "decision",
             "value_key": KEY, "order": 1, "event_id": "a" * 32,
             "ts": "2026-10-09T12:00:00Z", "channel": "cli-tty",
             "author": "ada"}
    assert trust._publish_after(TID, "active", event, PROJECT) == []
    assert not _file(None).exists()
    assert trust._publish_after(TID, "candidate", event, PROJECT) != []


# ---- trust republish ---------------------------------------------------------

OTHER_PROJECT = "/p/quarantine-publish-other"


def _republish(*extra, project=PROJECT):
    return cli.main(["trust", "republish", "--project", project, *extra])


def test_republish_resends_the_latest_active_row_verbatim(tmp_checkpoint_dir,
                                                          monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.delenv("DAIMON_TEAM")  # made while the team was off
    tid = _propose()
    monkeypatch.setenv("DAIMON_TEAM", "1")
    _clone()
    assert not _file(None).exists()
    assert _republish() == 0
    [row] = _rows(_file(None))
    local = [r for r in trust.events(project_dir=PROJECT)
             if r["event"] == "quarantined"][-1]
    assert (row["order"], row["event_id"], row["ts"]) == (
        local["order"], local["event_id"], local["ts"])
    assert row["state"] == "active" and row["quarantine_id"] == tid
    out = capsys.readouterr().out
    assert "1" in out and "republished" in out
    assert VALUE not in out and row["value_key"] not in out


def test_republish_is_idempotent(tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    _clone()
    _propose()
    assert _republish() == 0 and _republish() == 0
    assert len(_rows(_file(None))) == 1
    assert "republished 0" in capsys.readouterr().out.splitlines()[-1]


def test_republish_resends_a_release_to_a_dir_that_still_folds_active(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    _clone()
    tid = _propose()
    monkeypatch.delenv("DAIMON_TEAM")  # the release happened team-less
    trust.release(tid, channel="cli-tty", project_dir=PROJECT)
    monkeypatch.setenv("DAIMON_TEAM", "1")
    assert policy.fold_published_quarantines(_rows(_file(None)))
    assert _republish() == 0
    assert policy.fold_published_quarantines(_rows(_file(None))) == frozenset()
    released = [r for r in _rows(_file(None)) if r["state"] == "released"][0]
    local = [r for r in trust.events(project_dir=PROJECT)
             if r["event"] == "released"][0]
    assert released["event_id"] == local["event_id"]


def test_republish_leaves_another_projects_pair_alone(tmp_checkpoint_dir,
                                                      monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    _clone()
    path = _file(None)
    path.parent.mkdir(parents=True)
    foreign_to_this_bucket = _row(tid=TID2, key=OTHER_KEY, event_id="elsewhere")
    path.write_text(json.dumps(foreign_to_this_bucket) + "\n")
    _propose()
    assert _republish() == 0
    assert foreign_to_this_bucket in _rows(path)
    assert policy.fold_published_quarantines(_rows(path)) >= {
        ("decision", OTHER_KEY)}


def test_republish_refuses_an_agent(tmp_checkpoint_dir, capsys):
    _clone()
    assert _republish("--by", "agent") == 1
    assert "human" in capsys.readouterr().out
    assert not _file(None).exists()


def test_republish_needs_a_terminal_without_by(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert _republish() == 1


def test_republish_exit_4_on_a_failed_sidecar(tmp_checkpoint_dir, monkeypatch,
                                              capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.delenv("DAIMON_TEAM")
    _propose()
    monkeypatch.setenv("DAIMON_TEAM", "1")
    _clone()
    path = _file(None)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"<<<<<<< conflict\n")
    assert _republish() == 4
    assert "r1" in capsys.readouterr().out


def test_republish_without_a_team_says_so(tmp_checkpoint_dir, monkeypatch,
                                          capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.delenv("DAIMON_TEAM")
    assert _republish() == 0
    assert "no team is enabled" in capsys.readouterr().out


def test_republish_ignores_candidates_and_dismissed(tmp_checkpoint_dir,
                                                    monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    _clone()
    cand = _propose(channel="cli-agent", text="a candidate nobody settled yet")
    dis = _propose(channel="cli-agent", text="a candidate a human dismissed")
    trust.dismiss(dis, channel="cli-tty", project_dir=PROJECT)
    assert cand != dis
    assert _republish() == 0
    assert not _file(None).exists()


def test_the_library_refuses_a_non_human_channel(tmp_checkpoint_dir):
    with pytest.raises(trust.TrustError, match="human"):
        trust.republish(channel="cli-agent", project_dir=PROJECT)


def test_ledger_repair_trust_points_at_republish(tmp_checkpoint_dir, capsys,
                                                 monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    _propose()
    ledger = config.checkpoint_dir() / store.project_slug(PROJECT) / "trust.jsonl"
    ledger.write_bytes(ledger.read_bytes() + b'{"torn": ')
    assert cli.main(["ledger", "repair", "trust", "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "daimon trust republish" in out
    assert out.count("daimon trust republish") == 1


def test_ledger_repair_of_another_ledger_has_no_hint(tmp_checkpoint_dir,
                                                     capsys):
    assert cli.main(["ledger", "repair", "events", "--project", PROJECT]) == 0
    assert "republish" not in capsys.readouterr().out


def test_a_non_oserror_while_appending_is_that_sidecars_failure(
        tmp_checkpoint_dir, monkeypatch):
    _clone()

    def boom(*a, **k):
        raise RuntimeError("append exploded")

    monkeypatch.setattr(jsonl, "append_lines", boom)
    out = store.publish_quarantine([_row()], PROJECT)
    assert out == []
    [(path, why)] = out.failed
    assert path == _file(None) and "RuntimeError" in why
