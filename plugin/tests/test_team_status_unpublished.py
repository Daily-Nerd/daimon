"""#1175: `daimon team status` reports the active quarantines of the routed
project whose latest activating row the routed clone does not hold.

Fake clones are a directory with a `.git` entry, which is all the router looks
for (the shape tests/test_quarantine_publish.py uses); `DAIMON_TEAM_PROJECT`
routes the project into them. Presence is by `event_id`, through the same
capped read the publisher checks presence with; the command writes nothing.
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from daimon_briefing import cli, config, store, teamproject, trust

PROJECT = "/p/status-unpublished"


@pytest.fixture(autouse=True)
def team(monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "squad/census")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)


def _clone(name="r1"):
    d = config.team_dir() / name
    (d / ".git").mkdir(parents=True, exist_ok=True)
    return d


def _file(name="r1"):
    return (config.team_dir() / name / "projects" / "squad" / "census"
            / "authors" / "ada" / "quarantines.jsonl")


def _propose(channel="cli-tty", text="the staging runbook step was fabricated"):
    return trust.propose(text=text, kind="decision", reason="no such PR",
                         evidence=["issue:1175"], channel=channel,
                         project_dir=PROJECT)


def _slug():
    return store.project_bucket(PROJECT)


def _status(*extra):
    return cli.main(["team", "status", *extra])


def _tree(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = path.read_bytes()
    return out


# ---- the count -----------------------------------------------------------------


def test_an_active_quarantine_the_routed_clone_lacks_is_counted(
        tmp_checkpoint_dir):
    _propose()                      # routed to `local` only: nothing published
    _clone()                        # the remote arrives later (#1175 "Why")
    report = trust.unpublished(project_dir=PROJECT)
    assert report.project == _slug()
    assert report.active == 1 and report.per_remote == {"r1": 1}
    assert report.unknown == () and report.routed is True


def test_republish_clears_the_count(tmp_checkpoint_dir):
    _propose()
    _clone()
    assert trust.unpublished(project_dir=PROJECT).active == 1
    published = trust.republish(channel="cli-tty", project_dir=PROJECT)
    assert len(published) == 1 and published.failed == ()
    report = trust.unpublished(project_dir=PROJECT)
    assert report.active == 0 and report.per_remote == {"r1": 0}


def test_presence_is_by_event_id_not_by_quarantine_id(tmp_checkpoint_dir):
    tid = _propose()
    _clone()
    local = [r for r in trust.events(project_dir=PROJECT)
             if r["event"] == "quarantined"][-1]
    other = {"version": 1, "ts": local["ts"], "order": local["order"],
             "event_id": hashlib.md5(b"other").hexdigest(),
             "quarantine_id": tid, "kind": "decision",
             "value_key": local["value_key"], "state": "active",
             "author": "ada"}
    _file().parent.mkdir(parents=True)
    _file().write_text(json.dumps(other) + "\n", encoding="utf-8")
    assert trust.unpublished(project_dir=PROJECT).active == 1
    _file().write_text(json.dumps({**other, "event_id": local["event_id"]})
                       + "\n", encoding="utf-8")
    assert trust.unpublished(project_dir=PROJECT).active == 0


def test_a_released_quarantine_never_counts(tmp_checkpoint_dir):
    tid = _propose()
    trust.release(tid, channel="cli-tty", project_dir=PROJECT)
    _clone()
    report = trust.unpublished(project_dir=PROJECT)
    assert report.active == 0 and report.routed is True


def test_an_agent_candidate_never_counts(tmp_checkpoint_dir):
    _propose(channel="cli-agent")
    _clone()
    assert trust.unpublished(project_dir=PROJECT).active == 0


def test_a_project_that_routes_nowhere_is_not_routed(tmp_checkpoint_dir,
                                                     monkeypatch):
    _propose()
    _clone()
    monkeypatch.delenv("DAIMON_TEAM_PROJECT")  # no grant: writes go to local
    report = trust.unpublished(project_dir=PROJECT)
    assert report.routed is False and report.active == 0
    assert report.per_remote == {}


def test_team_disabled_still_counts_what_is_unpublished(tmp_checkpoint_dir,
                                                        monkeypatch):
    # "before `team` was enabled" is one of the causes the line exists for:
    # the rows are unpublished whether or not DAIMON_TEAM is set today.
    _propose()
    _clone()
    monkeypatch.delenv("DAIMON_TEAM")
    assert trust.unpublished(project_dir=PROJECT).active == 1


# ---- a read that proves nothing says so ----------------------------------------


def test_an_unreadable_clone_ledger_is_unknown_not_zero(tmp_checkpoint_dir):
    _propose()
    _clone()
    _file().mkdir(parents=True)     # a directory where the ledger should be
    report = trust.unpublished(project_dir=PROJECT)
    assert report.active == 0 and report.per_remote == {}
    [(path, why)] = report.unknown
    assert Path(path) == _file()
    assert why == "its quarantine ledger is unreadable"
    assert report.unknown[0].remote == "r1"


def test_an_over_cap_clone_ledger_is_unknown(tmp_checkpoint_dir):
    _propose()
    _clone()
    _file().parent.mkdir(parents=True)
    _file().write_bytes(b'{"event_id": "x"}\n' * 70_000)
    [(_path, why)] = trust.unpublished(project_dir=PROJECT).unknown
    assert why == "its quarantine ledger is over the 1 MB cap"


def test_an_unproven_local_ledger_is_reported(tmp_checkpoint_dir):
    _propose()
    _clone()
    path = config.checkpoint_dir() / _slug() / "trust.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "not json\n",
                    encoding="utf-8")
    report = trust.unpublished(project_dir=PROJECT)
    assert report.ledger == "unreadable"


# ---- the CLI line, the JSON, and the write-nothing contract --------------------


def test_cli_prints_the_line_in_the_issue_format(tmp_checkpoint_dir, capsys):
    _propose()
    _propose(text="the second runbook step was fabricated as well")
    _clone()
    assert _status() == 0
    out = capsys.readouterr().out
    assert (f"{_slug()}: 2 active quarantine(s) not yet published; "
            "run daimon trust republish") in out.splitlines()


def test_cli_line_is_gone_after_republish(tmp_checkpoint_dir, capsys):
    _propose()
    _clone()
    assert _status() == 0
    assert "not yet published" in capsys.readouterr().out
    assert cli.main(["trust", "republish", "--project", PROJECT]) == 0
    capsys.readouterr()
    assert _status() == 0
    assert "not yet published" not in capsys.readouterr().out


def test_cli_says_cannot_tell_when_the_clone_ledger_is_unreadable(
        tmp_checkpoint_dir, capsys):
    _propose()
    _clone()
    _file().mkdir(parents=True)
    assert _status() == 0
    out = capsys.readouterr().out
    assert (f"{_slug()}: cannot tell whether its quarantines reached r1: "
            "its quarantine ledger is unreadable") in out.splitlines()
    assert "not yet published" not in out


def test_cli_says_cannot_tell_when_the_local_ledger_is_unproven(
        tmp_checkpoint_dir, capsys):
    _propose()
    _clone()
    path = config.checkpoint_dir() / _slug() / "trust.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "not json\n",
                    encoding="utf-8")
    assert _status() == 0
    out = capsys.readouterr().out
    assert (f"{_slug()}: trust ledger is unreadable; cannot tell which "
            "quarantines are published") in out.splitlines()
    assert "not yet published" not in out


def test_cli_prints_nothing_for_an_unrouted_project(tmp_checkpoint_dir,
                                                    monkeypatch, capsys):
    _propose()
    _clone()
    monkeypatch.delenv("DAIMON_TEAM_PROJECT")
    assert _status() == 0
    out = capsys.readouterr().out
    assert "quarantine" not in out
    assert "this project writes to: local" in out


def test_json_carries_the_count_per_project(tmp_checkpoint_dir, capsys):
    _propose()
    _clone()
    assert _status("--json") == 0
    [row] = json.loads(capsys.readouterr().out)
    assert row["slug"] == "r1"
    assert row["unpublished_quarantines"] == {_slug(): 1}


def test_json_says_cannot_tell_for_an_unreadable_clone_ledger(
        tmp_checkpoint_dir, capsys):
    _propose()
    _clone()
    _file().mkdir(parents=True)
    assert _status("--json") == 0
    [row] = json.loads(capsys.readouterr().out)
    assert row["unpublished_quarantines"] == {_slug(): None}
    assert row["unpublished_unknown"] == {
        _slug(): "its quarantine ledger is unreadable"}


def test_json_says_cannot_tell_for_an_unproven_local_ledger(
        tmp_checkpoint_dir, capsys):
    _propose()
    _clone()
    path = config.checkpoint_dir() / _slug() / "trust.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "not json\n",
                    encoding="utf-8")
    assert _status("--json") == 0
    [row] = json.loads(capsys.readouterr().out)
    assert row["unpublished_quarantines"] == {_slug(): None}
    assert row["unpublished_unknown"] == {
        _slug(): "trust ledger is unreadable; cannot tell which quarantines "
                 "are published"}


def test_json_proven_row_carries_no_unknown_key(tmp_checkpoint_dir, capsys):
    _propose()
    _clone()
    assert _status("--json") == 0
    [row] = json.loads(capsys.readouterr().out)
    assert row["unpublished_quarantines"] == {_slug(): 1}
    assert "unpublished_unknown" not in row


def test_json_unrouted_remote_carries_neither_key(tmp_checkpoint_dir,
                                                  monkeypatch, capsys):
    _propose()
    _clone()
    monkeypatch.delenv("DAIMON_TEAM_PROJECT")
    assert _status("--json") == 0
    [row] = json.loads(capsys.readouterr().out)
    assert "unpublished_quarantines" not in row
    assert "unpublished_unknown" not in row


def test_status_writes_nothing(tmp_checkpoint_dir, capsys):
    _propose()
    _clone()
    _file().parent.mkdir(parents=True)
    _file().write_text('{"event_id": "unrelated"}\n', encoding="utf-8")
    before = (_tree(config.team_dir()), _tree(config.checkpoint_dir()))
    assert _status() == 0 and _status("--json") == 0
    assert "not yet published" in capsys.readouterr().out
    assert (_tree(config.team_dir()), _tree(config.checkpoint_dir())) == before


# ---- several routed remotes ---------------------------------------------------


def _project_with_origin(tmp_path, origin):
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], check=True,
                   capture_output=True, timeout=30)
    subprocess.run(["git", "-C", str(proj), "remote", "add", "origin", origin],
                   check=True, capture_output=True, timeout=30)
    return proj


def test_each_routed_remote_is_checked_on_its_own(tmp_checkpoint_dir,
                                                  tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DAIMON_TEAM_PROJECT")  # two remotes: toml routes
    proj = _project_with_origin(tmp_path, "https://github.com/org/alpha")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(proj))
    for name in ("r1", "r2"):
        (_clone(name) / teamproject.CONFIG_NAME).write_text(
            '[scope]\nrepos = ["https://github.com/org/alpha"]\n',
            encoding="utf-8")
    (_clone("r3") / teamproject.CONFIG_NAME).write_text(
        '[scope]\nrepos = ["https://github.com/org/other"]\n',
        encoding="utf-8")
    trust.propose(text="the alpha runbook step was fabricated",
                  kind="decision", reason="no such PR",
                  evidence=["issue:1175"], channel="cli-tty",
                  project_dir=str(proj))
    assert trust.unpublished(project_dir=str(proj)).active == 0
    r2 = (config.team_dir() / "r2" / "projects" / "org" / "alpha" / "authors"
          / "ada" / "quarantines.jsonl")
    r2.unlink()
    report = trust.unpublished(project_dir=str(proj))
    assert report.active == 1 and report.per_remote == {"r1": 0, "r2": 1}
    assert _status("--json") == 0
    rows = {r["slug"]: r for r in json.loads(capsys.readouterr().out)}
    slug = store.project_bucket(str(proj))
    assert rows["r1"]["unpublished_quarantines"] == {slug: 0}
    assert rows["r2"]["unpublished_quarantines"] == {slug: 1}
    assert "unpublished_quarantines" not in rows["r3"]  # not routed there
    assert _status() == 0
    assert f"{slug}: 1 active quarantine(s) not yet published" in \
        capsys.readouterr().out
