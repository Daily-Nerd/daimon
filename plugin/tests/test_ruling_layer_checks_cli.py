"""#1095: every command names a layer's ruling, and `ruling list --inherited`
is the CLI form of the merged read.

Depends on #1092/#1093/#1094. Fixtures follow test_inherited_rulings_brief.py's
`_home_work_repo` pattern (real git repo under a plain HOME ancestor) — never
hand-shaped ledger rows.

conftest does NOT patch HOME, so every test sets it itself.
"""

import json
import subprocess
from pathlib import Path

import pytest

from daimon_briefing import cli, config, refutations

MATCH = "gh pr create"
BODY = "#!/bin/sh\nexit 0\n"


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def _home_work_repo(tmp_path, monkeypatch):
    tmp_home = tmp_path / "home"
    work = tmp_home / "work"
    repo = work / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)
    monkeypatch.setenv("HOME", str(tmp_home))
    return tmp_home, work, repo


def _rule_at(directory, subject, *, scope="tests", **kw):
    return refutations.assert_ruling(
        subject=subject, verdict=f"the rule for {subject}", scope=scope,
        evidence=["issue:1095"], channel="cli-tty", ratified=True,
        project_dir=str(directory), **kw)


def _enforce_at(directory, subject, *, scope="publishing"):
    return refutations.assert_ruling(
        subject=subject, verdict=f"the rule for {subject} in {scope}",
        scope=scope, evidence=["issue:1095"], channel="cli-tty",
        ratified=True, check={"match": MATCH, "body": BODY, "intent": "enforce"},
        project_dir=str(directory))


# ---- `ruling list --inherited` -------------------------------------------


def test_ruling_list_json_has_no_layer_rows_by_default(tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _rule_at(work, "a layer rule")
    _rule_at(repo, "an own rule")

    assert cli.main(["ruling", "list", "--project", str(repo), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    subjects = {r["subject"] for r in rows}
    assert subjects == {"an own rule"}
    assert all("inherited_from" not in r for r in rows)


def test_ruling_list_inherited_adds_layer_rows_with_inherited_from(
        tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _rule_at(work, "a layer rule")
    _rule_at(repo, "an own rule")

    assert cli.main(["ruling", "list", "--project", str(repo), "--json",
                     "--inherited"]) == 0
    rows = json.loads(capsys.readouterr().out)
    by_subject = {r["subject"]: r for r in rows}
    assert set(by_subject) == {"a layer rule", "an own rule"}
    assert by_subject["a layer rule"]["inherited_from"] == str(work)
    assert "inherited_from" not in by_subject["an own rule"]


def test_ruling_list_inherited_refuses_under_tenant_scope(
        tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")

    rc = cli.main(["ruling", "list", "--project", str(repo), "--json",
                  "--inherited"])
    assert rc == 2
    assert config.TENANT_SCOPE_REFUSAL in capsys.readouterr().err


def test_ruling_list_inherited_never_shows_a_layer_refutation(
        tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    refutations.assert_refutation(
        subject="refuted at the layer", verdict="refuted text",
        scope="tests", evidence=["issue:1095"], channel="cli-tty",
        ratified=True, project_dir=str(work))
    _rule_at(repo, "an own rule")

    assert cli.main(["ruling", "list", "--project", str(repo), "--json",
                     "--inherited"]) == 0
    rows = json.loads(capsys.readouterr().out)
    subjects = {r["subject"] for r in rows}
    assert subjects == {"an own rule"}


def test_ruling_list_inherited_ignores_a_layer_candidate(
        tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    refutations.assert_ruling(
        subject="a candidate at the layer", verdict="candidate text",
        scope="tests", evidence=["issue:1095"], channel="cli-agent",
        ratified=False, project_dir=str(work))
    # An own row too, so the "no bucket written from this project yet"
    # diagnostic (unrelated to this control) does not also fire.
    _rule_at(repo, "an own rule so the bucket exists")

    assert cli.main(["ruling", "list", "--project", str(repo), "--json",
                     "--inherited"]) == 0
    rows = json.loads(capsys.readouterr().out)
    subjects = {r["subject"] for r in rows}
    assert subjects == {"an own rule so the bucket exists"}


# ---- `ruling show` / `ruling checks` / `ruling check try` ----------------


def test_ruling_show_on_an_inherited_id_prints_the_ruling_and_its_layer(
        tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    ruling_id = _rule_at(work, "a layer rule shown from the child")

    assert cli.main(["ruling", "show", ruling_id, "--project", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "a layer rule shown from the child" in out
    assert config.home_relative(str(work)) in out


def test_ruling_show_json_on_an_inherited_id_carries_inherited_from(
        tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    ruling_id = _rule_at(work, "a layer rule shown as json")

    assert cli.main(["ruling", "show", ruling_id, "--project", str(repo),
                     "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inherited_from"] == str(work)


def test_ruling_checks_on_an_inherited_id_prints_the_ruling_and_its_layer(
        tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    ruling_id = _enforce_at(work, "an enforced layer rule for checks")

    assert cli.main(["ruling", "checks", "--project", str(repo)]) == 0
    out = capsys.readouterr().out
    assert ruling_id in out
    assert config.home_relative(str(work)) in out


def test_ruling_checks_own_copy_wins_over_a_same_id_layer_copy(
        tmp_path, monkeypatch, capsys):
    """A child that ratified its own copy of the same subject+scope (and so
    the same ruling id) must not also get the layer's copy as a second row
    in `ruling checks` — own wins, the same rule `inherited_active` itself
    applies."""
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    subject, scope = "promoted to a layer rule for checks", "publishing"
    child_id = refutations.assert_ruling(
        subject=subject, verdict=f"the rule for {subject}", scope=scope,
        evidence=["issue:1095"], channel="cli-tty", ratified=True,
        check={"match": MATCH, "body": BODY, "intent": "enforce"},
        project_dir=str(repo))
    layer_id = refutations.assert_ruling(
        subject=subject, verdict=f"the rule for {subject}", scope=scope,
        evidence=["issue:1095"], channel="cli-tty", ratified=True,
        check={"match": MATCH, "body": BODY, "intent": "enforce"},
        project_dir=str(work))
    assert child_id == layer_id

    assert cli.main(["ruling", "checks", "--project", str(repo),
                     "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    matching = [r for r in payload["rows"] if r["ruling_id"] == child_id]
    assert len(matching) == len(set(r["host"] for r in matching)), matching
    assert all(r["inherited_from"] is None for r in matching)


def test_ruling_checks_ignores_an_inherited_ruling_with_no_check(
        tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _rule_at(work, "a plain layer rule with no check at all")

    assert cli.main(["ruling", "checks", "--project", str(repo),
                     "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows"] == []


@pytest.fixture
def _tty(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)


def test_check_try_on_an_inherited_id_prints_the_ruling_and_its_layer(
        tmp_path, monkeypatch, capsys, _tty):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    ruling_id = _enforce_at(work, "an enforced layer rule for a dry run")

    rc = cli.main(["ruling", "check", "try", ruling_id,
                  "--command", "gh pr create --title x",
                  "--project", str(repo)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "outcome: clean" in out
    assert config.home_relative(str(work)) in out


# ---- `status` / `stats` never call an inherited id unknown ---------------


def test_status_counts_an_inherited_armed_check(tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    ruling_id = _enforce_at(work, "an enforced layer rule counted by status")

    # rc is unrelated to this control: `status --json` returns non-zero for
    # "no checkpoint here yet" regardless of the checks block.
    cli.main(["status", "--project", str(repo), "--json"])
    payload = json.loads(capsys.readouterr().out)
    checks_block = payload.get("checks") or {}
    assert checks_block.get("armed", 0) >= 1, (ruling_id, payload)


def test_stats_counts_an_inherited_armed_check(tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    ruling_id = _enforce_at(work, "an enforced layer rule counted by stats")

    # `stats` has no `--project` flag; it always resolves through cwd.
    monkeypatch.chdir(repo)
    assert cli.main(["stats", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    checks_block = payload.get("checks") or {}
    assert checks_block.get("armed", 0) >= 1, (ruling_id, payload)


def _promote_same_id(work, repo, subject):
    """Found and ratify the identical (subject, scope) pair at both `repo`
    and `work`, so the two rulings share one id — the promotion-window
    shape `test_checks_layer_sync.py`'s manifest test exercises, here for
    `status`'/`stats`' own-id-collision skip."""
    scope = "publishing"
    child_id = refutations.assert_ruling(
        subject=subject, verdict=f"the rule for {subject}", scope=scope,
        evidence=["issue:1095"], channel="cli-tty", ratified=True,
        check={"match": MATCH, "body": BODY, "intent": "enforce"},
        project_dir=str(repo))
    layer_id = refutations.assert_ruling(
        subject=subject, verdict=f"the rule for {subject}", scope=scope,
        evidence=["issue:1095"], channel="cli-tty", ratified=True,
        check={"match": MATCH, "body": BODY, "intent": "enforce"},
        project_dir=str(work))
    assert child_id == layer_id
    return child_id


def test_status_counts_a_promoted_same_id_ruling_only_once(
        tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _promote_same_id(work, repo, "a status-counted same-id promotion")

    cli.main(["status", "--project", str(repo), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["checks"]["armed"] == 1


def test_stats_counts_a_promoted_same_id_ruling_only_once(
        tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _promote_same_id(work, repo, "a stats-counted same-id promotion")

    monkeypatch.chdir(repo)
    assert cli.main(["stats", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["checks"]["armed"] == 1
