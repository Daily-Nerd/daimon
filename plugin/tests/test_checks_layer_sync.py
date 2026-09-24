"""#1095: arm layer checks on every machine.

Depends on #1092/#1093/#1094 (`config.layer_scopes`, `store.bucket_root`,
`refutations.inherited_active`, all shipped). `hooks install` and `check
sync` sync each root from `config.layer_scopes` in addition to the project's
own root, so an inherited enforce/warn check arms even on a machine that has
never synced the layer's project directly.

Fixtures are built through the shipping writers (`refutations.assert_ruling`
with `ratified=True` and a `check`, which founds and arms in one call — see
test_inherited_rulings_brief.py's `test_compact_enforce_row_from_a_layer_
keeps_the_suffix`) at real layer paths, following the #1092/#1093 pattern —
never hand-shaped ledger rows (#962's landmine).

conftest does NOT patch HOME (only DAIMON_CHECKS_DIR and friends), so every
test here sets HOME itself, same as the sibling layer test files. DAIMON_
CHECKS_DIR IS autouse-patched per test (conftest.py), so each test already
starts from an empty, isolated manifest — "a fresh machine" is simulated by
deleting that per-test manifest after a layer's own ratify has synced it.
"""

import subprocess
from pathlib import Path

from daimon_briefing import checks, checks_runtime, cli, config, refutations

MATCH = "gh pr create"
BODY = "#!/bin/sh\nexit 0\n"


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def _home_work_repo(tmp_path, monkeypatch):
    """`tmp_home/work/repo`, `repo` git-inited, HOME=tmp_home. Same shape
    test_inherited_rulings_brief.py's `_home_work_repo` uses, so a layer
    written at `tmp_home` or `work` is eligible by construction."""
    tmp_home = tmp_path / "home"
    work = tmp_home / "work"
    repo = work / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)
    monkeypatch.setenv("HOME", str(tmp_home))
    return tmp_home, work, repo


def _arm_enforce(directory, subject, *, match=MATCH, body=BODY, scope="publishing"):
    """Found and ratify an enforce-check ruling in one call — `assert_ruling`
    computes the check's sha256 itself (`_check`), so a same-call ratify
    needs no explicit `check_sha256` the way a separate `ratify` call does."""
    return refutations.assert_ruling(
        subject=subject, verdict=f"the rule for {subject} in {scope}",
        scope=scope, evidence=["issue:1095"], channel="cli-tty",
        ratified=True, check={"match": match, "body": body, "intent": "enforce"},
        project_dir=str(directory))


def _manifest_path():
    return config.checks_dir() / checks_runtime.MANIFEST_NAME


def _armed_for(cwd):
    loaded = checks_runtime.load_manifest(_manifest_path())
    return checks_runtime.armed_for(str(cwd), loaded)


# ---- group 5: manifest --------------------------------------------------


def test_layer_ratify_produces_manifest_entry_at_the_layer_root(tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    ruling_id = _arm_enforce(work, "a layer-armed rule")

    entries = _armed_for(str(repo))
    matching = [e for e in entries if e["ruling_id"] == ruling_id]
    assert len(matching) == 1
    assert matching[0]["project_dir"] == str(work)


def test_child_sync_of_its_own_root_never_touches_a_layer_entry(tmp_path, monkeypatch):
    """This test exists to FAIL the day `refutations.listing` starts walking
    layers itself: if `_wanted` ever picked up the layer's rows under the
    CHILD's root, `checks.sync(repo)` would add a SECOND manifest entry for
    the same ruling id keyed to the child, and the same body would fire
    twice per action."""
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    ruling_id = _arm_enforce(work, "a layer rule the child must not re-key")

    before = _manifest_path().read_text(encoding="utf-8")
    report = checks.sync(str(repo))
    after = _manifest_path().read_text(encoding="utf-8")

    assert report.ok
    assert before == after
    loaded = checks_runtime.load_manifest(_manifest_path())
    matching = [e for e in loaded.entries if e["ruling_id"] == ruling_id]
    assert len(matching) == 1
    assert matching[0]["project_dir"] == str(work)


def test_hooks_install_in_child_arms_the_layer_on_a_fresh_manifest(tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    ruling_id = _arm_enforce(work, "a layer rule armed before the child ever syncs")

    # A ratify at the layer already synced ITS manifest via `_sync_checks`;
    # for this single-process test that IS the file a second machine would
    # read, so deleting it stands in for a machine that never ran
    # `checks.sync` for the layer at all.
    _manifest_path().unlink()

    monkeypatch.chdir(repo)
    assert cli.main(["hooks", "install", "windsurf"]) == 0

    entries = _armed_for(str(repo))
    assert any(e["ruling_id"] == ruling_id and e["project_dir"] == str(work)
              for e in entries)


def test_hooks_install_reports_a_failed_layer_sync(tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _arm_enforce(work, "a layer rule whose hooks-install sync will be made to fail")
    monkeypatch.chdir(repo)

    def _fake_sync_layers(project_dir=None):
        return [(str(work), checks.SyncReport(False, 0, "", "boom"))]

    monkeypatch.setattr(checks, "sync_layers", _fake_sync_layers)
    assert cli.main(["hooks", "install", "windsurf"]) == 0
    out = capsys.readouterr().out
    assert f"boom (layer {config.home_relative(str(work))})" in out


def test_child_check_sync_check_reports_a_layer_body_hash_mismatch(tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    ruling_id = _arm_enforce(work, "a layer rule whose body gets edited by hand")

    entries = _armed_for(str(work))
    body_path = checks_runtime.body_path(
        [e for e in entries if e["ruling_id"] == ruling_id][0], config.checks_dir())
    body_path.chmod(0o600)  # armed bodies ship 0o500 (read+execute only)
    body_path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")

    assert cli.main(["check", "sync", "--check", "--project", str(repo)]) == 1


def test_promotion_window_child_and_layer_copies_both_armed_until_child_retires(
        tmp_path, monkeypatch):
    """The same id ratified at the layer and in the child (a layer founding
    its own rulings never checks descendants — see test_inherited_rulings_
    brief.py's cap test comment) yields BOTH manifest entries from
    `armed_for` until the child retires its copy."""
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    subject, scope = "promoted to a layer rule", "publishing"

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

    entries = _armed_for(str(repo))
    matching = [e for e in entries if e["ruling_id"] == child_id]
    assert len(matching) == 2
    assert {e["project_dir"] for e in matching} == {str(repo), str(work)}

    refutations.retire(child_id, channel="cli-tty", project_dir=str(repo))
    checks.sync(str(repo))

    entries = _armed_for(str(repo))
    matching = [e for e in entries if e["ruling_id"] == child_id]
    assert len(matching) == 1
    assert matching[0]["project_dir"] == str(work)


def test_firing_summary_folds_an_inherited_checks_firings_as_this_projects_own(
        tmp_path, monkeypatch):
    """`checks._firing_summary`'s `mine` set has to include an inherited
    active check's id, or a firing recorded for it never counts toward this
    project's totals (`status`/`stats` fold on exactly this set)."""
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    ruling_id = _arm_enforce(work, "a layer rule whose firings must count here")

    log_path = config.log_dir() / checks_runtime.FIRING_LOG_NAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    log_path.write_text(_json.dumps({
        "ts": "2026-09-23T00:00:00Z", "ruling_id": ruling_id, "host": "claude-code",
        "outcome": "clean",
    }) + "\n", encoding="utf-8")

    summary = checks.firing_summary(str(repo))
    fold = summary.for_ruling(ruling_id)
    assert fold["fired"] == 1
    assert fold["clean"] == 1


# ---- `check sync` / `check sync --check` CLI surfaces --------------------


def test_check_sync_reports_a_layer_armed_line(tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _arm_enforce(work, "a layer rule reported by check sync")

    assert cli.main(["check", "sync", "--project", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "1 armed for " in out
    assert f"(layer {config.home_relative(str(work))})" in out


def test_check_sync_json_carries_a_layers_key_when_a_layer_armed_something(
        tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _arm_enforce(work, "a layer rule reported by check sync --json")

    assert cli.main(["check", "sync", "--project", str(repo), "--json"]) == 0
    import json as _json
    payload = _json.loads(capsys.readouterr().out)
    assert payload["layers"][0]["project_dir"] == str(work)
    assert payload["layers"][0]["armed"] == 1


def test_check_sync_json_carries_no_layers_key_with_no_layers(
        tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))

    assert cli.main(["check", "sync", "--project", str(project),
                     "--json"]) == 0
    import json as _json
    payload = _json.loads(capsys.readouterr().out)
    assert "layers" not in payload


def test_check_sync_reports_a_failed_layer_sync(tmp_path, monkeypatch, capsys):
    """A layer sync that could not write (an unknown-bucket layer, a
    corrupt manifest) must fail the whole verb, not print a clean report
    while silently leaving the layer's check disarmed."""
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _arm_enforce(work, "a layer rule whose sync will be made to fail")

    def _fake_sync_layers(project_dir=None):
        return [(str(work), checks.SyncReport(False, 0, "", "boom"))]

    monkeypatch.setattr(checks, "sync_layers", _fake_sync_layers)
    rc = cli.main(["check", "sync", "--project", str(repo)])
    out = capsys.readouterr().out
    assert rc == 1
    assert f"boom (layer {config.home_relative(str(work))})" in out


def test_check_sync_check_json_carries_a_layers_key(tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _arm_enforce(work, "a layer rule reported by check sync --check --json")

    rc = cli.main(["check", "sync", "--check", "--project", str(repo),
                  "--json"])
    import json as _json
    payload = _json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["layers"][0]["project_dir"] == str(work)
    assert payload["layers"][0]["state"] == "read"


# ---- group 7: negative controls -----------------------------------------


def test_layer_bucket_holding_only_a_candidate_contributes_nothing_to_sync(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    refutations.assert_ruling(
        subject="a candidate at the layer, never ratified",
        verdict="a candidate rule text", scope="publishing",
        evidence=["issue:1095"], channel="cli-agent",
        check={"match": MATCH, "body": BODY, "intent": "enforce"},
        project_dir=str(work))

    layer_reports = checks.sync_layers(str(repo))
    assert all(report.armed == 0 for _layer, report in layer_reports)
    entries = _armed_for(str(repo))
    assert entries == []


def test_hooks_install_with_no_layers_is_byte_identical_to_before_1095(
        tmp_path, monkeypatch, capsys):
    """No ancestor of a plain temp project qualifies as a layer (no bucket
    root record anywhere above it), so `sync_layers` returns [] and the
    install output must carry no extra line."""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(project)
    assert cli.main(["hooks", "install", "windsurf"]) == 0
    out = capsys.readouterr().out
    assert "checks: 0 armed for " in out
    assert "(layer " not in out
