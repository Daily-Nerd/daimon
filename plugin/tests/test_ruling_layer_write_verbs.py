"""#1094: write verbs, ceremony and the cap guard learn about ruling layers
(#1092's `config.layer_scopes`).

Four mechanisms under test:
  1. `retire`/`ratify`/`revise` (and a `--check` attach on revise) on an id
     that exists only in a layer refuse with a pointer, never answer
     "unknown".
  2. `propose`/`ratify`/`revise` refuse a same-id collision against a
     layer's ACTIVE ruling — the only conflict code can detect.
  3. Both activation paths (`ratify`, `propose --ratify`) print the layer's
     active rulings before the confirm, and a LAYER's own ceremony warns
     about descendant buckets the ratification would push over cap.
  4. `_guard_ruling_cap` counts inherited rulings alongside a child's own.

Fixtures are built through the shipping writers only (#962): `assert_ruling`
founds every row, `ratify`/`revise` change it. `_setup` mirrors
tests/test_config_layer_scopes.py's own tmp-home/work/repo shape, since the
layer walk exercised here is the identical one.
"""
import subprocess
from pathlib import Path

import pytest

from daimon_briefing import cli, config, refutations, store


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def _setup(tmp_path, monkeypatch):
    """`tmp_home/work/repo`, `repo` git-inited, HOME=tmp_home. Returns
    (tmp_home, work, repo). `work` is the layer: no `.git` anywhere from it
    up to the filesystem root."""
    tmp_home = tmp_path / "home"
    work = tmp_home / "work"
    repo = work / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)
    monkeypatch.setenv("HOME", str(tmp_home))
    return tmp_home, work, repo


def _rule_at(project_dir, *, channel="cli-agent", ratified=False, **overrides):
    values = {
        "subject": "a subject",
        "verdict": "a verdict",
        "scope": "a scope",
        "evidence": ["issue:1094"],
        "channel": channel,
        "ratified": ratified,
        "project_dir": str(project_dir),
    }
    values.update(overrides)
    return refutations.assert_ruling(**values)


def _row_count(project_dir) -> int:
    return len(refutations.events(project_dir=str(project_dir)))


@pytest.fixture
def _tty(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)


# --- Q2: id found only in a layer — pointer, never "unknown" --------------


def test_module_retire_layer_only_id_pins_exact_message(tmp_path, monkeypatch):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    layer_id = _rule_at(work, channel="cli-tty", ratified=True)
    with pytest.raises(refutations.RefutationError) as exc_info:
        refutations.retire(layer_id, channel="cli-tty", project_dir=str(repo))
    assert str(exc_info.value) == (
        f"{layer_id} inherited from ~/work; run with --project ~/work")


def test_cli_retire_on_layer_only_id_refuses_with_pointer(
        tmp_path, monkeypatch, capsys, _tty):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    layer_id = _rule_at(work, channel="cli-tty", ratified=True)
    before = _row_count(work)
    rc = cli.main(["ruling", "retire", layer_id, "--project", str(repo)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "inherited from ~/work; run with --project ~/work" in out
    assert _row_count(work) == before
    assert not refutations.bucket_exists(str(repo))


def test_cli_ratify_on_layer_only_id_refuses_with_pointer(
        tmp_path, monkeypatch, capsys, _tty):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    layer_id = _rule_at(work, channel="cli-tty", ratified=True)
    before = _row_count(work)
    rc = cli.main(["ruling", "ratify", layer_id, "--project", str(repo)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "inherited from ~/work; run with --project ~/work" in out
    assert _row_count(work) == before
    assert not refutations.bucket_exists(str(repo))


def test_cli_revise_on_layer_only_id_refuses_with_pointer(
        tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    layer_id = _rule_at(work, channel="cli-tty", ratified=True)
    before = _row_count(work)
    rc = cli.main(["ruling", "revise", layer_id, "--verdict", "new text",
                   "--evidence", "issue:1094", "--project", str(repo)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "inherited from ~/work; run with --project ~/work" in out
    assert _row_count(work) == before
    assert not refutations.bucket_exists(str(repo))


def test_cli_revise_check_attach_on_layer_only_id_refuses(
        tmp_path, monkeypatch, capsys):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    layer_id = _rule_at(work, channel="cli-tty", ratified=True)
    body = tmp_path / "check.sh"
    body.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    before = _row_count(work)
    rc = cli.main(["ruling", "revise", layer_id, "--evidence", "issue:1094",
                   "--check-body-file", str(body), "--check-match", "rm -rf",
                   "--project", str(repo)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "inherited from ~/work; run with --project ~/work" in out
    assert _row_count(work) == before
    assert not refutations.bucket_exists(str(repo))


# --- Q3: propose/ratify/revise refuse a same-id collision with a layer ----


def test_module_propose_colliding_with_layer_active_id_refuses(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    _rule_at(work, subject="shared subject", scope="shared-scope",
             channel="cli-tty", ratified=True)
    before = _row_count(work)
    with pytest.raises(refutations.RefutationError, match="already active"):
        refutations.assert_ruling(
            subject="shared subject", verdict="child's own verdict",
            scope="shared-scope", evidence=["issue:1094"], channel="cli-agent",
            project_dir=str(repo))
    assert _row_count(work) == before
    assert not refutations.bucket_exists(str(repo))


def test_module_ratify_colliding_with_layer_active_id_refuses(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    candidate_id = _rule_at(repo, subject="shared subject",
                            scope="shared-scope", channel="cli-agent")
    _rule_at(work, subject="shared subject", scope="shared-scope",
             channel="cli-tty", ratified=True)
    before_work = _row_count(work)
    before_repo = _row_count(repo)
    with pytest.raises(refutations.RefutationError, match="already active"):
        refutations.ratify(candidate_id, channel="cli-tty",
                           project_dir=str(repo))
    assert _row_count(work) == before_work
    assert _row_count(repo) == before_repo
    assert refutations.get(
        candidate_id, project_dir=str(repo))["state"] == "candidate"


def test_module_revise_minting_a_layer_active_id_refuses(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    _rule_at(work, subject="shared subject", scope="shared-scope",
             channel="cli-tty", ratified=True)
    own_id = _rule_at(repo, subject="own subject", verdict="own verdict",
                      scope="own-scope", channel="cli-tty", ratified=True)
    before_work = _row_count(work)
    with pytest.raises(refutations.RefutationError, match="already active"):
        refutations.revise(own_id, channel="cli-tty",
                           evidence=["issue:1094"],
                           subject="shared subject", scope="shared-scope",
                           project_dir=str(repo))
    assert _row_count(work) == before_work
    assert refutations.get(
        own_id, project_dir=str(repo))["subject"] == "own subject"


def test_module_agent_revise_proposal_minting_layer_id_refuses(
        tmp_path, monkeypatch):
    """The mint refusal is unconditional — an agent's PROPOSAL, which never
    touches the active text, still names a subject+scope, and that identity
    still cannot take over a layer's."""
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    _rule_at(work, subject="shared subject", scope="shared-scope",
             channel="cli-tty", ratified=True)
    own_id = _rule_at(repo, subject="own subject", verdict="own verdict",
                      scope="own-scope", channel="cli-tty", ratified=True)
    with pytest.raises(refutations.RefutationError, match="already active"):
        refutations.revise(own_id, channel="cli-agent",
                           evidence=["issue:1094"],
                           subject="shared subject", scope="shared-scope",
                           project_dir=str(repo))


# --- Ceremony: inherited rulings before the confirm, both activation paths


def test_cli_ratify_ceremony_shows_inherited_rulings(
        tmp_path, monkeypatch, capsys, _tty):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    _rule_at(work, subject="layer subj", verdict="layer verdict text",
             scope="layer-scope", channel="cli-tty", ratified=True)
    own_id = _rule_at(repo, subject="own subj", verdict="own verdict",
                      scope="own-scope", channel="cli-agent")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "ratify", own_id, "--project", str(repo)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Inherited rulings in force here:" in out
    assert "layer verdict text" in out
    assert "[from ~/work]" in out


def test_cli_ratify_ceremony_prints_nothing_extra_with_no_layers(
        tmp_path, monkeypatch, capsys, _tty):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    own_id = _rule_at(repo, subject="own subj", verdict="own verdict",
                      scope="own-scope", channel="cli-agent")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "ratify", own_id, "--project", str(repo)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Inherited rulings in force here:" not in out


def test_cli_propose_ratify_ceremony_shows_inherited_rulings_and_confirms(
        tmp_path, monkeypatch, capsys, _tty):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    _rule_at(work, subject="layer subj", verdict="layer verdict text",
             scope="layer-scope", channel="cli-tty", ratified=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "propose", "--subject", "new subject",
                   "--verdict", "new verdict text", "--scope", "new-scope",
                   "--evidence", "issue:1094", "--ratify",
                   "--project", str(repo)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Inherited rulings in force here:" in out
    assert "layer verdict text" in out
    record = refutations.get(
        refutations.make_id("new subject", "new-scope"), project_dir=str(repo))
    assert record is not None
    assert record["state"] == "active"


def test_cli_propose_ratify_declined_writes_nothing(
        tmp_path, monkeypatch, capsys, _tty):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    rc = cli.main(["ruling", "propose", "--subject", "declined subject",
                   "--verdict", "declined verdict", "--scope",
                   "declined-scope", "--evidence", "issue:1094", "--ratify",
                   "--project", str(repo)])
    assert rc == 1
    assert refutations.get(
        refutations.make_id("declined subject", "declined-scope"),
        project_dir=str(repo)) is None
    assert not refutations.bucket_exists(str(repo))


def test_cli_propose_ratify_json_keeps_ceremony_off_stdout(
        tmp_path, monkeypatch, capsys, _tty):
    """The ceremony text must not corrupt `--json`'s stdout contract — the
    same split `ruling ratify --json` already holds (ceremony to stderr,
    the record to stdout)."""
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    _rule_at(work, subject="layer subj", verdict="layer verdict text",
             scope="layer-scope", channel="cli-tty", ratified=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "propose", "--subject", "json subject",
                   "--verdict", "json verdict text", "--scope", "json-scope",
                   "--evidence", "issue:1094", "--ratify", "--json",
                   "--project", str(repo)])
    assert rc == 0
    import json as _json
    captured = capsys.readouterr()
    record = _json.loads(captured.out)
    assert record["state"] == "active"
    assert "Inherited rulings in force here:" in captured.err


def test_cli_propose_ratify_refuses_agent_channel(
        tmp_path, monkeypatch, capsys):
    """#1094: `propose --ratify` gains ceremony and manifest sync, but the
    channel gate it already had is unchanged — an agent still cannot
    activate, and now cannot even reach the ceremony to try."""
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    rc = cli.main(["ruling", "propose", "--subject", "agent subject",
                   "--verdict", "agent verdict", "--scope", "agent-scope",
                   "--evidence", "issue:1094", "--by", "agent", "--ratify",
                   "--project", str(repo)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "human channel" in out
    assert refutations.get(
        refutations.make_id("agent subject", "agent-scope"),
        project_dir=str(repo)) is None


# --- propose --ratify at a layer, with a check, syncs the manifest --------


def test_cli_propose_ratify_at_layer_with_check_syncs_manifest(
        tmp_path, monkeypatch, capsys, _tty):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    body = tmp_path / "check.sh"
    body.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main([
        "ruling", "propose", "--subject", "layer check subject",
        "--verdict", "layer check verdict", "--scope", "layer-check-scope",
        "--evidence", "issue:1094", "--ratify",
        "--check-body-file", str(body), "--check-match", "rm -rf",
        "--project", str(work)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "warning" not in out.lower()

    from daimon_briefing import checks_runtime as rt
    manifest = rt.load_manifest(config.checks_dir() / rt.MANIFEST_NAME)
    armed = rt.armed_for(str(repo), manifest)
    assert len(armed) == 1
    assert armed[0]["project_dir"] == str(work)


# --- Q2/W1: the cap guard counts inherited rulings alongside a child's own


def test_module_cap_refuses_sixth_own_ratification_naming_inherited(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    inherited_ids = sorted(
        _rule_at(work, subject=f"layer subj {i}", verdict=f"layer verdict {i}",
                scope=f"layer-scope-{i}", channel="cli-tty", ratified=True)
        for i in range(2))
    for i in range(5):
        _rule_at(repo, subject=f"own subj {i}", verdict=f"own verdict {i}",
                 scope=f"own-scope-{i}", channel="cli-tty", ratified=True)
    sixth = _rule_at(repo, subject="own subj 5", verdict="own verdict 5",
                     scope="own-scope-5")
    with pytest.raises(refutations.RefutationError) as exc_info:
        refutations.ratify(sixth, channel="cli-tty", project_dir=str(repo))
    message = str(exc_info.value)
    assert "2 inherited" in message
    for rid in inherited_ids:
        assert rid in message
    assert refutations.get(
        sixth, project_dir=str(repo))["state"] == "candidate"


def test_module_cap_still_refuses_on_own_alone_at_default_cap(
        tmp_path, monkeypatch):
    """Regression pin: with no layers at all, the guard's own-only message
    is unchanged (#693's original behavior)."""
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    for i in range(7):
        _rule_at(repo, subject=f"own subj {i}", verdict=f"own verdict {i}",
                 scope=f"own-scope-{i}", channel="cli-tty", ratified=True)
    eighth = _rule_at(repo, subject="own subj 7", verdict="own verdict 7",
                      scope="own-scope-7")
    with pytest.raises(refutations.RefutationError) as exc_info:
        refutations.ratify(eighth, channel="cli-tty", project_dir=str(repo))
    assert "inherited" not in str(exc_info.value)


# --- Layer ceremony: descendant buckets a ratification would push over cap


def test_layer_ceremony_warns_about_multiple_descendants_over_cap(
        tmp_path, monkeypatch, capsys, _tty):
    tmp_home, work, _repo = _setup(tmp_path, monkeypatch)
    repo1 = work / "repo1"
    repo1.mkdir()
    _init_git_repo(repo1)
    repo2 = work / "repo2"
    repo2.mkdir()
    _init_git_repo(repo2)
    for repo in (repo1, repo2):
        for i in range(7):
            _rule_at(repo, subject=f"{repo.name} subj {i}",
                    verdict=f"{repo.name} verdict {i}",
                    scope=f"{repo.name}-scope-{i}", channel="cli-tty",
                    ratified=True)
    layer_id = _rule_at(work, subject="layer subject",
                        verdict="layer verdict", scope="layer-scope",
                        channel="cli-agent")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "ratify", layer_id, "--project", str(work)])
    assert rc == 0
    out = capsys.readouterr().out
    slug1 = store.project_slug(str(repo1))
    slug2 = store.project_slug(str(repo2))
    assert "ratifying here puts 2 projects over the cap" in out
    assert f"{slug1} (8)" in out
    assert f"{slug2} (8)" in out


def test_layer_ceremony_prints_nothing_when_no_descendant_goes_over(
        tmp_path, monkeypatch, capsys, _tty):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    _rule_at(repo, subject="own subj", verdict="own verdict",
             scope="own-scope", channel="cli-tty", ratified=True)
    layer_id = _rule_at(work, subject="layer subject",
                        verdict="layer verdict", scope="layer-scope",
                        channel="cli-agent")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "ratify", layer_id, "--project", str(work)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "over the cap" not in out


def test_layer_ceremony_render_line_names_every_project_under_home(
        tmp_path, monkeypatch, capsys, _tty):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    _rule_at(repo, channel="cli-tty", ratified=True)  # gives repo a bucket
    layer_id = _rule_at(work, channel="cli-agent")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "ratify", layer_id, "--project", str(work)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "every project under ~/work (1 buckets today)" in out
    assert "render into every future session for this project" not in out


def test_revise_ceremony_render_line_is_layer_aware(
        tmp_path, monkeypatch, capsys, _tty):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    layer_id = _rule_at(work, channel="cli-tty", ratified=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "revise", layer_id, "--verdict", "revised text",
                   "--evidence", "issue:1094", "--project", str(work)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "every project under ~/work" in out


# --- --slug routing bypasses the ancestor walk entirely --------------------


def test_slug_routed_ratify_does_not_walk_layers(
        tmp_path, monkeypatch, capsys, _tty):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    # The candidate is founded BEFORE the layer's colliding ruling exists —
    # `assert_ruling` would otherwise refuse the founding itself, and this
    # test is about the ratify-time walk, not the founding-time one.
    candidate_id = _rule_at(repo, subject="collide subject",
                            scope="collide-scope", channel="cli-agent")
    _rule_at(work, subject="collide subject", scope="collide-scope",
             channel="cli-tty", ratified=True)
    slug = store.project_slug(str(repo))
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "ratify", candidate_id, "--slug", slug])
    assert rc == 0
    assert refutations.get(
        candidate_id, project_dir=str(repo))["state"] == "active"
