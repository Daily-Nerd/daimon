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


def test_module_cap_does_not_double_count_an_id_active_in_both_places(
        tmp_path, monkeypatch):
    """#1094 review: the same id can end up active in BOTH the child's own
    bucket and a layer above it (the child ratifies it first, then the
    layer independently founds and ratifies the identical subject+scope —
    nothing checks descendants when a layer founds its own ruling). The
    guard must not count that id twice: `inherited_ids` must exclude
    anything already in `active`."""
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    # X: ratified in the child FIRST (own), then independently founded and
    # ratified at the layer with the identical subject+scope (same id) —
    # the collision guard only checks a child against layers ABOVE it, so
    # the layer founding its own copy is untouched by it.
    shared_id = _rule_at(repo, subject="shared subject", scope="shared-scope",
                         verdict="shared verdict", channel="cli-tty",
                         ratified=True)
    _rule_at(work, subject="shared subject", scope="shared-scope",
             verdict="shared verdict", channel="cli-tty", ratified=True)
    own_ids = {shared_id}
    for i in range(5):
        own_ids.add(_rule_at(repo, subject=f"own subj {i}",
                             verdict=f"own verdict {i}",
                             scope=f"own-scope-{i}", channel="cli-tty",
                             ratified=True))
    assert len(own_ids) == 6  # 5 distinct + the shared id, all own-active
    seventh = _rule_at(repo, subject="own subj 6", verdict="own verdict 6",
                       scope="own-scope-6")
    # Before the fix this raised: `active` (6, including the shared id) +
    # `inherited_ids` (1, the same shared id counted again) >= cap(7).
    refutations.ratify(seventh, channel="cli-tty", project_dir=str(repo))
    assert refutations.get(
        seventh, project_dir=str(repo))["state"] == "active"

    eighth = _rule_at(repo, subject="own subj 7", verdict="own verdict 7",
                      scope="own-scope-7")
    with pytest.raises(refutations.RefutationError) as exc_info:
        refutations.ratify(eighth, channel="cli-tty", project_dir=str(repo))
    message = str(exc_info.value)
    assert "inherited" not in message
    assert message.count(shared_id) == 1


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


def test_propose_ratify_at_layer_ceremony_warns_about_descendants_over_cap(
        tmp_path, monkeypatch, capsys, _tty):
    """#1094 review: `propose --ratify` is the other activation path, and
    the design gives both the same ceremony — including the descendant
    over-cap warning, not just `ratify`'s own."""
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
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "propose", "--subject", "layer subject",
                   "--verdict", "layer verdict", "--scope", "layer-scope",
                   "--evidence", "issue:1094", "--ratify",
                   "--project", str(work)])
    assert rc == 0
    out = capsys.readouterr().out
    slug1 = store.project_slug(str(repo1))
    slug2 = store.project_slug(str(repo2))
    assert "ratifying here puts 2 projects over the cap" in out
    assert f"{slug1} (8)" in out
    assert f"{slug2} (8)" in out


def test_layer_overcap_warning_own_count_includes_policy_carrying_rulings(
        tmp_path, monkeypatch, capsys, _tty):
    """#1094 review: `_guard_ruling_cap` counts a project's own active
    rulings regardless of whether they carry a request_policy — only the
    INHERITED set drops policy rows. The warning's own-count must mirror
    that, or a descendant whose own guard would refuse gets no warning."""
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    _rule_at(repo, subject="policy subject", verdict="policy verdict",
             scope="policy-scope", channel="cli-tty", ratified=True,
             request_policy={"sender": "p-sender", "kind": "work",
                             "verb": "accept", "by": "agent"})
    for i in range(6):
        _rule_at(repo, subject=f"plain subj {i}", verdict=f"plain verdict {i}",
                 scope=f"plain-scope-{i}", channel="cli-tty", ratified=True)
    # repo now has 7 own active rulings, one of them carrying a policy —
    # without counting the policy row, that reads as only 6 own, which is
    # exactly at the cap boundary and would not warn once the layer's new
    # ruling is added; counting it correctly pushes the would-be count to 8.
    layer_id = _rule_at(work, subject="layer subject",
                        verdict="layer verdict", scope="layer-scope",
                        channel="cli-agent")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "ratify", layer_id, "--project", str(work)])
    assert rc == 0
    out = capsys.readouterr().out
    slug = store.project_slug(str(repo))
    assert "ratifying here puts 1 projects over the cap" in out
    assert f"{slug} (8)" in out


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


# --- Coverage: fail-open branches, guard edge cases, and the two module-  --
# --- level activation paths the CLI's own pre-checks never reach ----------


def test_home_relative_empty_string_returns_unchanged(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert config.home_relative("") == ""


def test_home_relative_returns_tilde_for_home_itself(tmp_path, monkeypatch):
    tmp_home, _work, _repo = _setup(tmp_path, monkeypatch)
    assert config.home_relative(str(tmp_home)) == "~"


def test_home_relative_outside_home_returns_unchanged(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert config.home_relative(str(outside)) == str(outside)


def test_home_relative_fails_open_on_unexpected_error(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    def _boom():
        raise RuntimeError("could not determine home directory")

    monkeypatch.setattr(config.Path, "home", staticmethod(_boom))
    assert config.home_relative("/anything") == "/anything"


def test_buckets_under_falsy_layer_dir_returns_empty(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert store.buckets_under("") == []
    assert store.buckets_under(None) == []


def test_buckets_under_skips_a_non_directory_entry(tmp_path, monkeypatch):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    _rule_at(repo, channel="cli-tty", ratified=True)
    stray = config.checkpoint_dir() / "not-a-bucket.txt"
    stray.write_text("stray file", encoding="utf-8")
    result = store.buckets_under(str(work))
    slugs = [slug for slug, _root in result]
    assert store.project_slug(str(repo)) in slugs
    assert "not-a-bucket.txt" not in slugs


def test_buckets_under_skips_a_bucket_with_no_root_record(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    slug = store.project_slug(str(repo))
    bucket_dir = config.checkpoint_dir() / slug
    bucket_dir.mkdir(parents=True)
    # A legacy bucket: exists, but no `root` file was ever stamped, so it
    # cannot be trusted to belong to any particular directory.
    assert store.buckets_under(str(work)) == []


def test_buckets_under_skips_a_root_whose_realpath_raises(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    slug = store.project_slug(str(repo))
    bucket_dir = config.checkpoint_dir() / slug
    bucket_dir.mkdir(parents=True)
    (bucket_dir / "root").write_text("bad\x00path\n", encoding="utf-8")
    assert store.buckets_under(str(work)) == []


class _FakeUnreadableCheckpointDir:
    def is_dir(self):
        return True

    def iterdir(self):
        raise OSError("permission denied")


def test_buckets_under_fails_open_on_an_unreadable_checkpoint_dir(
        tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        store.config, "checkpoint_dir", lambda: _FakeUnreadableCheckpointDir())
    assert store.buckets_under("/some/layer") == []


def test_inherited_active_skips_non_ruling_records(tmp_path, monkeypatch):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    refutations.assert_refutation(
        subject="a refutation", verdict="it failed", scope="a scope",
        evidence=["issue:1094"], channel="cli-tty", ratified=True,
        project_dir=str(work))
    assert refutations.inherited_active(str(repo)) == []


def test_inherited_active_skips_policy_carrying_rulings(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    _rule_at(work, subject="policy subject", verdict="policy verdict",
             scope="policy-scope", channel="cli-tty", ratified=True,
             request_policy={"sender": "p-sender", "kind": "work",
                             "verb": "accept", "by": "agent"})
    assert refutations.inherited_active(str(repo)) == []


def test_inherited_active_dedups_nearest_layer_wins(tmp_path, monkeypatch):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    # `work` first, THEN the identical subject+scope at `tmp_home` — the
    # reverse order would collide (a child founding a layer's active id is
    # refused; a layer founding what a DESCENDANT already holds is not
    # checked at all, so this order is the only one that can exist).
    _rule_at(work, subject="shared subject", scope="shared-scope",
             verdict="near verdict", channel="cli-tty", ratified=True)
    _rule_at(tmp_home, subject="shared subject", scope="shared-scope",
             verdict="far verdict", channel="cli-tty", ratified=True)
    rows = refutations.inherited_active(str(repo))
    assert len(rows) == 1
    assert rows[0]["verdict"] == "near verdict"
    assert rows[0]["inherited_from"] == str(work)


def test_inherited_active_fails_open_on_unexpected_error(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)

    def _boom(_project_dir):
        raise RuntimeError("boom")

    monkeypatch.setattr(refutations.config, "layer_scopes", _boom)
    assert refutations.inherited_active(str(repo)) == []


def test_guard_layer_only_id_returns_on_empty_id(tmp_path, monkeypatch):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    refutations._guard_layer_only_id("", str(repo))
    refutations._guard_layer_only_id(None, str(repo))


def test_guard_layer_active_collision_returns_on_empty_id(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    refutations._guard_layer_active_collision("", str(repo))
    refutations._guard_layer_active_collision(None, str(repo))


def test_module_ratify_direct_call_on_layer_only_id_pins_message(
        tmp_path, monkeypatch):
    """The CLI's own pre-check for `ratify` never reaches `refutations.
    ratify`'s `current is None` branch when the id is layer-only (it
    refuses first, see test_cli_ratify_on_layer_only_id_refuses_with_
    pointer); an in-process caller reaching `ratify` directly still needs
    the same guard."""
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    layer_id = _rule_at(work, channel="cli-tty", ratified=True)
    with pytest.raises(refutations.RefutationError) as exc_info:
        refutations.ratify(layer_id, channel="cli-tty", project_dir=str(repo))
    assert str(exc_info.value) == (
        f"{layer_id} inherited from ~/work; run with --project ~/work")


def test_module_revise_direct_call_on_layer_only_id_pins_message(
        tmp_path, monkeypatch):
    """Same as above, for `refutations.revise`'s own `current is None`
    branch — the CLI's own pre-check never reaches it either."""
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    layer_id = _rule_at(work, channel="cli-tty", ratified=True)
    with pytest.raises(refutations.RefutationError) as exc_info:
        refutations.revise(layer_id, channel="cli-tty",
                           evidence=["issue:1094"], verdict="new text",
                           project_dir=str(repo))
    assert str(exc_info.value) == (
        f"{layer_id} inherited from ~/work; run with --project ~/work")


def test_is_layer_project_fails_closed_on_unexpected_error(
        tmp_path, monkeypatch):
    from daimon_briefing.cli import _ledger
    real_dir = tmp_path / "somedir"
    real_dir.mkdir()

    def _boom(_path):
        raise RuntimeError("boom")

    monkeypatch.setattr(_ledger.config, "_git_shadowed", _boom)
    assert _ledger._is_layer_project(str(real_dir)) is False


def test_propose_ratify_ceremony_falls_back_when_policy_validation_fails(
        tmp_path, monkeypatch, capsys, _tty):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main([
        "ruling", "propose", "--subject", "policy subject",
        "--verdict", "policy verdict", "--scope", "policy-scope",
        "--evidence", "issue:1094", "--ratify",
        "--request-policy", "sender=p-sender",
        "--request-policy", "kind=totally-invalid",
        "--request-policy", "verb=accept",
        "--request-policy", "by=agent",
        "--project", str(repo)])
    # The ceremony must not crash on an unvalidated shape; the real
    # refusal comes from assert_ruling's own validation at write time.
    assert rc == 1
    out = capsys.readouterr().out
    assert "ruling not recorded" in out


def test_propose_ratify_ceremony_skips_overcap_check_on_invalid_identity(
        tmp_path, monkeypatch, capsys, _tty):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    long_scope = "x" * (refutations._MAX_TEXT + 1)
    rc = cli.main([
        "ruling", "propose", "--subject", "subject",
        "--verdict", "verdict", "--scope", long_scope,
        "--evidence", "issue:1094", "--ratify",
        "--project", str(repo)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "ratifying here puts" not in out
    assert "is too long" in out


# ---- #1102: the shared "own wins" set ------------------------------------


def test_own_active_ruling_ids_includes_only_active_own_rows(
        tmp_path, monkeypatch):
    """`own_active_ruling_ids` is the shared "own wins" test `ruling
    checks`, `status` and `stats` build their inherited-check exclusion set
    from — a candidate or overturned own row must not appear in it, only
    an active one, the same filter `briefing.rulings_read` already applies
    for the briefing's own answer."""
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)
    active_id = refutations.assert_ruling(
        subject="an active own ruling", verdict="v", scope="s1",
        evidence=["issue:1102"], channel="cli-tty", ratified=True,
        project_dir=str(repo))
    candidate_id = refutations.assert_ruling(
        subject="a candidate own ruling", verdict="v", scope="s2",
        evidence=["issue:1102"], channel="cli-agent", ratified=False,
        project_dir=str(repo))
    overturned_id = refutations.assert_ruling(
        subject="an overturned own ruling", verdict="v", scope="s3",
        evidence=["issue:1102"], channel="cli-tty", ratified=True,
        project_dir=str(repo))
    refutations.retire(overturned_id, channel="cli-tty",
                       evidence=["issue:1102"], project_dir=str(repo))

    ids = refutations.own_active_ruling_ids(str(repo))
    assert ids == {active_id}
    assert candidate_id not in ids
    assert overturned_id not in ids


def test_own_active_ruling_ids_fails_open_on_unexpected_error(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _setup(tmp_path, monkeypatch)

    def _boom(project_dir=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(refutations, "records", _boom)
    assert refutations.own_active_ruling_ids(str(repo)) == set()
