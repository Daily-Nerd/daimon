"""`testing.sandbox` and the seeders (#1179 PR 5, design D21 and D22).

The sandbox is a throwaway store a host test can render against: every
home-rooted directory is redirected under one root, the settings come from a
mapping alone, and the two seeders (which can mint a RATIFIED ruling) refuse
anywhere else. These tests pin the refusals, the isolation and the equivalence
of a seeded ruling with one asserted through the human channel."""

import os
import shutil
import sys
import threading
import time
from datetime import datetime, timezone

import pytest

from daimon_briefing import api, cli, config, heal, refutations, testing

DECISION = "adopt the sandbox for every host test"
RULING = "never seed a store outside the sandbox"


def _brief(capsys):
    assert cli.main(["brief"]) == 0
    return capsys.readouterr().out


def _snapshot(root, skip=(".sandbox",)):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.name not in skip}


def _on_thread(fn):
    out = []
    t = threading.Thread(target=lambda: out.append(fn()))
    t.start()
    t.join()
    return out[0]


@pytest.fixture
def root(tmp_path):
    return tmp_path / "sbx"


# ---- the root rules (D21) -----------------------------------------------------


def test_the_sandbox_creates_its_directories_and_returns_them(root):
    with testing.sandbox(root) as sb:
        assert sb.root == root.resolve()
        assert sb.checkpoint_dir == sb.root / "checkpoints"
        assert sb.log_dir == sb.root / "logs"
        assert config.checkpoint_dir() == sb.checkpoint_dir
        assert config.log_dir() == sb.log_dir
        for name in ("checkpoints", "logs"):
            assert (sb.root / name).is_dir()


def test_the_sandbox_accepts_an_existing_empty_root(root):
    root.mkdir()
    with testing.sandbox(root) as sb:
        assert sb.root == root.resolve()


def test_the_sandbox_refuses_a_non_empty_root(root):
    root.mkdir()
    (root / "something").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="not empty"):
        with testing.sandbox(root):
            pass


def test_the_sandbox_refuses_a_root_that_is_a_file(root):
    root.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="not empty"):
        with testing.sandbox(root):
            pass


def test_the_sandbox_refuses_a_root_at_or_under_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for bad in (home, home / "deep" / "sbx"):
        with pytest.raises(ValueError, match="home"):
            with testing.sandbox(bad):
                pass
    assert list(home.rglob("*")) == []


def test_the_sandbox_refuses_a_root_inside_a_git_work_tree(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(ValueError, match="git work tree"):
        with testing.sandbox(repo / "sub" / "sbx"):
            pass
    assert not (repo / "sub").exists()


@pytest.mark.parametrize("accessor", [
    "checkpoint_dir", "log_dir", "team_dir", "recall_seen_dir",
    "windsurf_state_dir", "keys_dir", "checks_dir", "claude_projects_dir"])
def test_the_sandbox_refuses_a_root_inside_a_configured_daimon_dir(
        tmp_path, monkeypatch, accessor):
    monkeypatch.setenv("DAIMON_KEYS_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("DAIMON_CLAUDE_PROJECTS_DIR", str(tmp_path / "cproj"))
    configured = getattr(config, accessor)()
    for bad in (configured, configured / "sbx"):
        with pytest.raises(ValueError, match="daimon directory"):
            with testing.sandbox(bad):
                pass


def test_the_sandbox_refuses_a_root_inside_the_recall_db_path(tmp_path, monkeypatch):
    db = tmp_path / "recall.db"
    monkeypatch.setenv("DAIMON_RECALL_DB", str(db))
    with pytest.raises(ValueError, match="daimon directory"):
        with testing.sandbox(db / "sbx"):
            pass


# ---- the seeders refuse outside it (D21) --------------------------------------


def test_the_seeders_raise_outside_a_sandbox(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    with pytest.raises(testing.SandboxRequired):
        testing.seed_checkpoint(str(project), testing.sample_checkpoint())
    with pytest.raises(testing.SandboxRequired):
        testing.seed_ruling(str(project), subject="s", verdict="v")
    assert not (config.checkpoint_dir()).exists()


def test_the_seeders_raise_after_the_sandbox_has_exited(root):
    with testing.sandbox(root) as sb:
        project = sb.root / "p"
        project.mkdir()
    with pytest.raises(testing.SandboxRequired):
        testing.seed_checkpoint(str(project), testing.sample_checkpoint())


def test_the_seeders_raise_when_the_checkpoint_dir_was_repointed(root, tmp_path):
    with testing.sandbox(root) as sb:
        project = sb.root / "p"
        project.mkdir()
        elsewhere = tmp_path / "elsewhere"
        with config.checkpoint_dir_override(elsewhere):
            with pytest.raises(testing.SandboxRequired):
                testing.seed_checkpoint(str(project), testing.sample_checkpoint())
            with pytest.raises(testing.SandboxRequired):
                testing.seed_ruling(str(project), subject="s", verdict="v")
        assert not elsewhere.exists()
        testing.seed_checkpoint(str(project), testing.sample_checkpoint())


def test_the_seeders_raise_for_a_project_dir_outside_the_root(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with testing.sandbox(root):
        with pytest.raises(testing.SandboxRequired, match="outside"):
            testing.seed_checkpoint(str(outside), testing.sample_checkpoint())
        with pytest.raises(testing.SandboxRequired, match="outside"):
            testing.seed_ruling(str(outside), subject="s", verdict="v")
    assert list(outside.iterdir()) == []


def test_a_seeding_failure_raises_instead_of_returning(root):
    with testing.sandbox(root, settings={"DAIMON_DISABLE": "1"}) as sb:
        project = sb.root / "p"
        project.mkdir()
        with pytest.raises(RuntimeError, match="DAIMON_DISABLE"):
            testing.seed_checkpoint(str(project), testing.sample_checkpoint())


# ---- seeding and rendering ----------------------------------------------------


def test_sample_checkpoint_is_pure_and_carries_what_it_was_given():
    before = set(os.listdir(config.checkpoint_dir().parent)) \
        if config.checkpoint_dir().parent.exists() else set()
    cp = testing.sample_checkpoint(DECISION, topic="the topic",
                                   questions=("who owns it",))
    assert cp["working_context"]["active_topic"]["text"] == "the topic"
    assert [d["text"] for d in cp["working_context"]["recent_decisions"]] == [
        DECISION]
    assert [q["text"] for q in cp["working_context"]["open_questions"]] == [
        "who owns it"]
    after = set(os.listdir(config.checkpoint_dir().parent)) \
        if config.checkpoint_dir().parent.exists() else set()
    assert before == after


def test_a_seeded_store_renders_the_decision_and_the_ruling(root, capsys):
    project = root.resolve() / "p"
    with testing.sandbox(root, settings={
            "DAIMON_CAPTURE_HOST": "claude-code",
            "DAIMON_PROJECT_DIR": str(project)}) as sb:
        project.mkdir()
        path = testing.seed_checkpoint(
            str(project), testing.sample_checkpoint(DECISION))
        testing.seed_ruling(str(project), subject="seeding", verdict=RULING)
        out = _brief(capsys)
        assert path.is_file() and sb.checkpoint_dir in path.parents
    assert DECISION in out
    assert RULING in out


def test_a_seeded_ruling_is_the_same_bytes_as_one_asserted_through_cli_tty(
        root, tmp_path):
    clock_args = (1_700_000_000 * 10**9, 10**9)

    def run(assert_it):
        shutil.rmtree(root, ignore_errors=True)
        with testing.sandbox(root, clock=testing.StepClock(*clock_args)) as sb:
            project = sb.root / "p"
            project.mkdir()
            rid = assert_it(str(project))
            return rid, _snapshot(sb.root)

    def seeded(project):
        return testing.seed_ruling(project, subject="subj", verdict=RULING,
                                   scope="this project",
                                   evidence=("artifact:test-seed",))

    def asserted(project):
        return refutations.assert_ruling(
            subject="subj", verdict=RULING, scope="this project",
            evidence=["artifact:test-seed"], channel="cli-tty", ratified=True,
            project_dir=project)

    a_id, a_files = run(seeded)
    b_id, b_files = run(asserted)
    assert a_id == b_id
    assert a_files == b_files
    assert any(name.endswith(".jsonl") for name in a_files)


# ---- isolation (D22) ----------------------------------------------------------


def test_render_and_both_seeders_create_nothing_under_home(
        root, tmp_path, monkeypatch, capsys):
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DAIMON_CHECKPOINT_DIR")
    monkeypatch.delenv("DAIMON_LOG_DIR")
    monkeypatch.delenv("DAIMON_TEAM_DIR")
    monkeypatch.delenv("DAIMON_RECALL_DB")
    monkeypatch.delenv("DAIMON_RECALL_SEEN_DIR")
    monkeypatch.delenv("DAIMON_WINDSURF_DIR")
    monkeypatch.delenv("DAIMON_CHECKS_DIR")
    monkeypatch.delenv("DAIMON_ENV_FILE")
    project = root.resolve() / "p"
    with testing.sandbox(root, settings={
            "DAIMON_PROJECT_DIR": str(project),
            "DAIMON_CAPTURE_HOST": "claude-code"}) as sb:
        project.mkdir()
        testing.seed_checkpoint(str(project),
                                testing.sample_checkpoint(DECISION))
        testing.seed_ruling(str(project), subject="s", verdict=RULING)
        assert DECISION in _brief(capsys)
        # every redirected directory lives under the root
        for fn in (config.team_dir, config.recall_seen_dir,
                   config.windsurf_state_dir, config.keys_dir,
                   config.checks_dir, config.claude_projects_dir,
                   config.recall_db):
            assert sb.root in fn().parents
    assert list(home.rglob("*")) == []


def test_the_overrides_do_not_reach_a_thread_and_reset_on_exit(root):
    outside = (config.checkpoint_dir(), config.log_dir())
    with testing.sandbox(root) as sb:
        assert _on_thread(lambda: (config.checkpoint_dir(),
                                   config.log_dir())) == outside
        assert _on_thread(config.active_settings) is None
        assert config.active_settings() is not None
        assert config.checkpoint_dir() == sb.checkpoint_dir
    assert (config.checkpoint_dir(), config.log_dir()) == outside
    assert config.active_settings() is None


def test_the_host_settings_are_applied_but_cannot_move_a_redirected_dir(root):
    with testing.sandbox(root, settings={
            "DAIMON_BRIEF_MAX_BYTES": "4321",
            "DAIMON_CHECKPOINT_DIR": "/elsewhere/cp",
            "DAIMON_LOG_DIR": "/elsewhere/lg",
            "DAIMON_KEYS_DIR": "/elsewhere/keys"}) as sb:
        assert config.brief_max_bytes() == 4321
        assert config.checkpoint_dir() == sb.checkpoint_dir
        assert config.log_dir() == sb.log_dir
        assert config.keys_dir() == sb.root / "keys"


def test_the_sandbox_inherits_no_flag_from_the_process_env(
        root, monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "someone")
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    monkeypatch.setenv("DAIMON_RECEIPTS", "1")
    with testing.sandbox(root):
        assert config.is_disabled() is False
        assert config.receipts_enabled() is False
        assert not config._flag("DAIMON_TEAM")
        assert not config._flag("DAIMON_TENANT_SCOPED")


def test_a_user_env_file_and_the_process_env_do_not_reach_a_sandboxed_read(
        root, tmp_path, monkeypatch, capsys):
    env_file = tmp_path / "user-env"
    env_file.write_text("DAIMON_LLM_MODEL=from-file\n"
                        "DAIMON_BRIEF_MAX_BYTES=60\n", encoding="utf-8")
    default_budget = config.brief_max_bytes()
    project = root.resolve() / "p"
    with testing.sandbox(root, settings={"DAIMON_PROJECT_DIR": str(project)}):
        project.mkdir()
        many = [f"decision number {i} with enough words to take room"
                for i in range(12)]
        testing.seed_checkpoint(str(project), testing.sample_checkpoint(*many))
        clean = _brief(capsys)
        monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
        monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "60")
        assert config.brief_max_bytes() == default_budget
        assert config.llm_model() is None
        assert _brief(capsys) == clean
    assert config.brief_max_bytes() == 60       # outside, the env does apply


# ---- the heal child (R2, R11) -------------------------------------------------


def _stamp(seconds_ago):
    return datetime.fromtimestamp(time.time() - seconds_ago, timezone.utc
                                  ).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_the_heal_child_in_a_sandbox_sees_the_sandbox_and_nothing_hidden(
        root, tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_API_KEY", "real-key")
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "5")
    monkeypatch.setenv("DAIMON_ENV_FILE", str(tmp_path / "real-env"))
    seen = []
    real = heal.launch

    with testing.sandbox(root, settings={"DAIMON_LLM_MODEL": "sandboxed"}) as sb:
        project = sb.root / "p"
        project.mkdir()
        transcript = sb.root / "S-1.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        log = sb.log_dir / "serialize.log"
        log.write_text(
            f"{_stamp(105)} session-end: spawned serialize for S-1 "
            f"(reason: exit, project: {project}) (transcript: {transcript})\n"
            f"error: boom (transcript: {transcript}) after 3s\n",
            encoding="utf-8")
        probe = ("import sys; from daimon_briefing import config as c; "
                 f"ok = (str(c.checkpoint_dir()) == {str(sb.checkpoint_dir)!r}"
                 f" and str(c.log_dir()) == {str(sb.log_dir)!r} "
                 "and c.llm_model() == 'sandboxed' "
                 "and c.llm_api_key() is None "
                 "and c.brief_max_bytes() > 100); "
                 "sys.exit(0 if ok else 3)")

        def run_probe(argv, env, *, wait=False):
            assert "DAIMON_LLM_API_KEY" not in env
            assert "DAIMON_BRIEF_MAX_BYTES" not in env
            assert env["DAIMON_ENV_FILE"] != str(tmp_path / "real-env")
            seen.append(real([sys.executable, "-c", probe], env, wait=True))
            return 0

        monkeypatch.setattr(heal, "launch", run_probe)
        report = api.heal_all(str(project))
    assert report.status == "started", report
    assert seen == [0]
