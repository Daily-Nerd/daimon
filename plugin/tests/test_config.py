import getpass
import os
import subprocess
from pathlib import Path

from daimon_briefing import config, store


def test_checkpoint_dir_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "ck"))
    assert config.checkpoint_dir() == tmp_path / "ck"


def test_checkpoint_dir_default(monkeypatch):
    monkeypatch.delenv("DAIMON_CHECKPOINT_DIR", raising=False)
    d = config.checkpoint_dir()
    assert d.name == "checkpoints"
    assert ".daimon" in str(d)


def test_disabled_flag(monkeypatch):
    monkeypatch.delenv("DAIMON_DISABLE", raising=False)
    assert config.is_disabled() is False
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    assert config.is_disabled() is True
    monkeypatch.setenv("DAIMON_DISABLE", "0")
    assert config.is_disabled() is False


def test_min_messages_default_and_override(monkeypatch):
    monkeypatch.delenv("DAIMON_MIN_MESSAGES", raising=False)
    assert config.min_messages() == 10
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    assert config.min_messages() == 3


def test_timeout_default_and_override(monkeypatch):
    # #284: default must cover the measured production range of the
    # zero-config claude backend (74s-25min observed; 120s killed real
    # first-serialize calls). 420 is the field-derived floor.
    monkeypatch.delenv("DAIMON_TIMEOUT", raising=False)
    assert config.timeout_seconds() == 420
    monkeypatch.setenv("DAIMON_TIMEOUT", "45")
    assert config.timeout_seconds() == 45


def test_timeout_garbage_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("DAIMON_TIMEOUT", "not-an-int")
    assert config.timeout_seconds() == 420


def test_llm_env_falls_back_to_litellm(monkeypatch):
    monkeypatch.delenv("DAIMON_LLM_BASE_URL", raising=False)
    monkeypatch.setenv("LITELLM_BASE_URL", "http://fallback:4000")
    assert config.llm_base_url() == "http://fallback:4000"
    monkeypatch.setenv("DAIMON_LLM_BASE_URL", "http://primary:5000")
    assert config.llm_base_url() == "http://primary:5000"


def test_llm_render_opt_in(monkeypatch):
    monkeypatch.delenv("DAIMON_LLM_BRIEFING", raising=False)
    assert config.llm_briefing() is False
    monkeypatch.setenv("DAIMON_LLM_BRIEFING", "1")
    assert config.llm_briefing() is True


def test_llm_no_cache_opt_in(monkeypatch):
    monkeypatch.delenv("DAIMON_LLM_NO_CACHE", raising=False)
    assert config.llm_no_cache() is False
    monkeypatch.setenv("DAIMON_LLM_NO_CACHE", "1")
    assert config.llm_no_cache() is True


def _point_env_file(monkeypatch, tmp_path, body):
    f = tmp_path / "env"
    f.write_text(body, encoding="utf-8")
    monkeypatch.setenv("DAIMON_ENV_FILE", str(f))
    return f


def test_env_file_provides_fallback_values(monkeypatch, tmp_path):
    for var in ("DAIMON_LLM_API_KEY", "LITELLM_API_KEY",
                "DAIMON_LLM_MODEL", "LITELLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    _point_env_file(monkeypatch, tmp_path, (
        "# daimon LLM config\n"
        "DAIMON_LLM_API_KEY=sk-from-file\n"
        "export DAIMON_LLM_MODEL=\"kimi-k2.6\"\n"
        "DAIMON_LLM_BASE_URL='http://litellm.local:4000'\n"
        "\n"
        "not a valid line\n"
    ))
    assert config.llm_api_key() == "sk-from-file"
    assert config.llm_model() == "kimi-k2.6"  # export prefix + quotes stripped
    assert config.llm_base_url() == "http://litellm.local:4000"


def test_process_env_beats_env_file(monkeypatch, tmp_path):
    _point_env_file(monkeypatch, tmp_path, "DAIMON_LLM_API_KEY=sk-from-file\n")
    monkeypatch.setenv("DAIMON_LLM_API_KEY", "sk-from-process")
    assert config.llm_api_key() == "sk-from-process"


def test_missing_env_file_is_fine(monkeypatch, tmp_path):
    monkeypatch.setenv("DAIMON_ENV_FILE", str(tmp_path / "does-not-exist"))
    for var in ("DAIMON_LLM_API_KEY", "LITELLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert config.llm_api_key() is None
    assert config.llm_base_url() == "http://localhost:4000"


def test_llm_temperature_default_and_override(monkeypatch):
    monkeypatch.delenv("DAIMON_LLM_TEMPERATURE", raising=False)
    assert config.llm_temperature() == 0.0
    monkeypatch.setenv("DAIMON_LLM_TEMPERATURE", "1")
    assert config.llm_temperature() == 1.0


def test_llm_temperature_bad_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_TEMPERATURE", "not-a-float")
    assert config.llm_temperature() == 0.0


def test_llm_temperature_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("DAIMON_LLM_TEMPERATURE", raising=False)
    _point_env_file(monkeypatch, tmp_path, "DAIMON_LLM_TEMPERATURE=0.7\n")
    assert config.llm_temperature() == 0.7


def test_project_dir_from_env(monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/Users/x/proj")
    assert config.project_dir() == "/Users/x/proj"


def test_project_dir_default_none(monkeypatch):
    monkeypatch.delenv("DAIMON_PROJECT_DIR", raising=False)
    assert config.project_dir() is None


def test_merge_group_size_default(monkeypatch):
    monkeypatch.delenv("DAIMON_MERGE_GROUP_SIZE", raising=False)
    assert config.merge_group_size() == 3


def test_merge_group_size_env_override(monkeypatch):
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "5")
    assert config.merge_group_size() == 5


def test_merge_group_size_below_minimum_clamps_to_2(monkeypatch):
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "1")
    assert config.merge_group_size() == 2


def test_merge_group_size_zero_clamps_to_2(monkeypatch):
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "0")
    assert config.merge_group_size() == 2


def test_merge_group_size_garbage_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "not-an-int")
    assert config.merge_group_size() == 3


def test_llm_backend_default_and_override(monkeypatch):
    monkeypatch.delenv("DAIMON_LLM_BACKEND", raising=False)
    assert config.llm_backend() == "auto"
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    assert config.llm_backend() == "command"


def test_llm_fallback_default_on_and_off(monkeypatch):
    monkeypatch.delenv("DAIMON_LLM_FALLBACK", raising=False)
    assert config.llm_fallback() is True            # default ON
    monkeypatch.setenv("DAIMON_LLM_FALLBACK", "0")
    assert config.llm_fallback() is False


def test_llm_command_and_output(monkeypatch):
    monkeypatch.delenv("DAIMON_LLM_COMMAND", raising=False)
    monkeypatch.delenv("DAIMON_LLM_COMMAND_OUTPUT", raising=False)
    assert config.llm_command() is None
    assert config.llm_command_output() is None
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "claude -p --output-format json")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_OUTPUT", "json:result")
    assert config.llm_command() == "claude -p --output-format json"
    assert config.llm_command_output() == "json:result"


# ---- #58: DAIMON_LLM_COMMAND_INPUT — stdin (default) | arg | file:<flag> ----


def test_llm_command_input_default_is_stdin(monkeypatch):
    monkeypatch.delenv("DAIMON_LLM_COMMAND_INPUT", raising=False)
    assert config.llm_command_input() == "stdin"


def test_llm_command_input_arg(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "arg")
    assert config.llm_command_input() == "arg"


def test_llm_command_input_file_with_flag(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "file:--prompt-file")
    assert config.llm_command_input() == "file:--prompt-file"


def test_llm_command_input_unknown_mode_fails_open_to_stdin_with_warning(
    monkeypatch, caplog
):
    # Matches the fail-open precedent of the sibling DAIMON_LLM_COMMAND_OUTPUT
    # axis: a typo here must never crash every chat() call — it silently
    # reverts to the safe default, but (unlike the output axis) logs a
    # warning since the input axis is easier to get wrong silently.
    import logging

    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "bogus-mode")
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.config"):
        assert config.llm_command_input() == "stdin"
    assert any("bogus-mode" in r.getMessage() for r in caplog.records)


def test_llm_command_input_file_without_flag_fails_open_to_stdin(monkeypatch, caplog):
    # "file:" with an empty flag is not a usable spec — treat like any other
    # unrecognized value.
    import logging

    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "file:")
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.config"):
        assert config.llm_command_input() == "stdin"


def test_llm_command_input_file_whitespace_only_flag_fails_open_to_stdin(
    monkeypatch, caplog
):
    # A flag that strips to empty is the empty-flag case in disguise.
    import logging

    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "file:   ")
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.config"):
        assert config.llm_command_input() == "stdin"


def test_hung_after_seconds_default(monkeypatch):
    monkeypatch.delenv("DAIMON_HUNG_AFTER", raising=False)
    assert config.hung_after_seconds() == 1800


def test_hung_after_seconds_env_override(monkeypatch):
    monkeypatch.setenv("DAIMON_HUNG_AFTER", "600")
    assert config.hung_after_seconds() == 600


def test_hung_after_seconds_malformed_falls_back(monkeypatch):
    monkeypatch.setenv("DAIMON_HUNG_AFTER", "not-a-number")
    assert config.hung_after_seconds() == 1800


# ---- resolve_project_root: normalize a subdir session to its git toplevel (#74) ----


def _init_git_repo(path: Path) -> None:
    """Init a bare-minimum git repo. `rev-parse --show-toplevel` works right after
    `git init` — no commit or identity needed."""
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def test_resolve_project_root_subdir_maps_to_git_toplevel(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    subdir = repo / "plugin" / "pkg"
    subdir.mkdir(parents=True)

    result = config.resolve_project_root(str(subdir))
    # git toplevel of a subdir is the repo root
    assert Path(result).resolve() == repo.resolve()


def test_resolve_project_root_non_git_dir_returns_raw(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert config.resolve_project_root(str(plain)) == str(plain)


def test_resolve_project_root_git_binary_missing_returns_raw(tmp_path, monkeypatch):
    plain = tmp_path / "whatever"
    plain.mkdir()

    def _no_git(*_a, **_k):
        raise FileNotFoundError("git: command not found")

    monkeypatch.setattr(subprocess, "run", _no_git)
    assert config.resolve_project_root(str(plain)) == str(plain)


def test_resolve_project_root_timeout_returns_raw(tmp_path, monkeypatch):
    plain = tmp_path / "slow"
    plain.mkdir()

    def _timeout(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=2)

    monkeypatch.setattr(subprocess, "run", _timeout)
    assert config.resolve_project_root(str(plain)) == str(plain)


def test_resolve_project_root_none_and_empty_passthrough():
    assert config.resolve_project_root(None) is None
    assert config.resolve_project_root("") == ""


def test_resolve_project_root_symmetry_subdir_and_root_share_slug(tmp_path):
    """Write-from-subdir and read-from-root must land in the SAME checkpoint bucket:
    the resolved dirs must produce identical store slugs (#74 core invariant)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    subdir = repo / "plugin"
    subdir.mkdir()

    from_subdir = config.resolve_project_root(str(subdir))
    from_root = config.resolve_project_root(str(repo))
    assert store.project_slug(from_subdir) == store.project_slug(from_root)


def test_scar_harvest_opt_in(monkeypatch):
    monkeypatch.delenv("DAIMON_SCAR_HARVEST", raising=False)
    assert config.scar_harvest_enabled() is False
    monkeypatch.setenv("DAIMON_SCAR_HARVEST", "1")
    assert config.scar_harvest_enabled() is True


# ---- git_branch: current branch stamp at capture time (#222) ----


def _init_git_repo_with_commit(path: Path, monkeypatch, branch: str = "main") -> None:
    """A real local repo with ONE commit on `branch` — `rev-parse --abbrev-ref
    HEAD` needs a born HEAD (an empty `git init` repo is unborn and rev-parse
    fails, see test below), so callers that need a resolvable branch name go
    through this instead of the bare `_init_git_repo` helper above."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)],
                   check=True, capture_output=True, timeout=30)
    (path / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "f.txt"],
                   check=True, capture_output=True, timeout=30)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=Test", "-c", "user.email=test@x",
         "commit", "-q", "-m", "init"],
        check=True, capture_output=True, timeout=30,
    )


def test_git_branch_returns_branch_name(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo_with_commit(repo, monkeypatch, branch="feat/thing")
    assert config.git_branch(str(repo)) == "feat/thing"


def test_git_branch_none_for_non_git_dir(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert config.git_branch(str(plain)) is None


def test_git_branch_none_for_unborn_head(tmp_path):
    # `git init` with zero commits: HEAD is a symbolic ref to an unborn branch —
    # rev-parse --abbrev-ref HEAD fails (ambiguous argument), not a name.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    assert config.git_branch(str(repo)) is None


def test_git_branch_none_for_detached_head(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo_with_commit(repo, monkeypatch)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--detach"],
                   check=True, capture_output=True, timeout=30)
    # rev-parse --abbrev-ref HEAD returns the literal string "HEAD" for a
    # detached checkout — treated as absent, never as a branch named "HEAD".
    assert config.git_branch(str(repo)) is None


def test_git_branch_none_when_git_binary_missing(tmp_path, monkeypatch):
    def _no_git(*_a, **_k):
        raise FileNotFoundError("git: command not found")

    monkeypatch.setattr(subprocess, "run", _no_git)
    assert config.git_branch(str(tmp_path)) is None


def test_git_branch_none_on_timeout(tmp_path, monkeypatch):
    def _timeout(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=2)

    monkeypatch.setattr(subprocess, "run", _timeout)
    assert config.git_branch(str(tmp_path)) is None


def test_git_branch_none_and_empty_passthrough():
    assert config.git_branch(None) is None
    assert config.git_branch("") is None


def test_max_briefing_decisions_default(monkeypatch):
    monkeypatch.delenv("DAIMON_MAX_BRIEFING_DECISIONS", raising=False)
    assert config.max_briefing_decisions() == 10


def test_max_briefing_decisions_override(monkeypatch):
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "3")
    assert config.max_briefing_decisions() == 3


def test_max_briefing_decisions_zero_is_unbounded(monkeypatch):
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "0")
    assert config.max_briefing_decisions() == 0


def test_max_briefing_decisions_noninteger_falls_back(monkeypatch):
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "abc")
    assert config.max_briefing_decisions() == 10


def test_checkpoint_keep_default(monkeypatch):
    monkeypatch.delenv("DAIMON_CHECKPOINT_KEEP", raising=False)
    assert config.checkpoint_keep() == 100


def test_checkpoint_keep_override(monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_KEEP", "25")
    assert config.checkpoint_keep() == 25


def test_checkpoint_keep_zero_disables(monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_KEEP", "0")
    assert config.checkpoint_keep() == 0


def test_checkpoint_keep_negative_clamps_to_zero(monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_KEEP", "-5")
    assert config.checkpoint_keep() == 0


def test_checkpoint_keep_noninteger_falls_back(monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_KEEP", "abc")
    assert config.checkpoint_keep() == 100


# ---- team memory (#111): opt-in dual-write, team dir, author identity ----


def test_team_enabled_opt_in(monkeypatch):
    monkeypatch.delenv("DAIMON_TEAM", raising=False)
    assert config.team_enabled() is False
    monkeypatch.setenv("DAIMON_TEAM", "1")
    assert config.team_enabled() is True
    monkeypatch.setenv("DAIMON_TEAM", "0")
    assert config.team_enabled() is False


def test_team_dir_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DAIMON_TEAM_DIR", str(tmp_path / "team"))
    assert config.team_dir() == tmp_path / "team"


def test_team_dir_default(monkeypatch):
    monkeypatch.delenv("DAIMON_TEAM_DIR", raising=False)
    d = config.team_dir()
    assert d.name == "team"
    assert ".daimon" in str(d)


def test_author_env_wins(monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    # git / getuser must not be consulted when the env var is set
    monkeypatch.setattr(config, "_git_user_name", lambda: "should-not-be-used")
    monkeypatch.setattr(getpass, "getuser", lambda: "should-not-be-used")
    assert config.author() == "ada"


def test_author_falls_back_to_git(monkeypatch):
    monkeypatch.delenv("DAIMON_AUTHOR", raising=False)
    monkeypatch.setattr(config, "_git_user_name", lambda: "Grace Hopper")
    monkeypatch.setattr(getpass, "getuser", lambda: "should-not-be-used")
    assert config.author() == "Grace Hopper"


def test_author_falls_back_to_getuser_when_git_empty(monkeypatch):
    monkeypatch.delenv("DAIMON_AUTHOR", raising=False)
    monkeypatch.setattr(config, "_git_user_name", lambda: "")
    monkeypatch.setattr(getpass, "getuser", lambda: "linus")
    assert config.author() == "linus"


def test_author_never_raises_returns_unknown(monkeypatch):
    monkeypatch.delenv("DAIMON_AUTHOR", raising=False)
    monkeypatch.setattr(config, "_git_user_name", lambda: "")

    def _boom():
        raise OSError("no login name")

    monkeypatch.setattr(getpass, "getuser", _boom)
    assert config.author() == "unknown"


def test_carry_enabled_default_and_kill_switch(monkeypatch):
    monkeypatch.delenv("DAIMON_CARRY", raising=False)
    assert config.carry_enabled() is True
    monkeypatch.setenv("DAIMON_CARRY", "0")
    assert config.carry_enabled() is False
    monkeypatch.setenv("DAIMON_CARRY", "1")
    assert config.carry_enabled() is True


def test_carry_floor_default_and_malformed(monkeypatch):
    monkeypatch.delenv("DAIMON_CARRY_FLOOR", raising=False)
    assert config.carry_floor() == 0.05
    monkeypatch.setenv("DAIMON_CARRY_FLOOR", "0.2")
    assert config.carry_floor() == 0.2
    monkeypatch.setenv("DAIMON_CARRY_FLOOR", "banana")
    assert config.carry_floor() == 0.05


def test_carry_max_default_clamp_malformed(monkeypatch):
    monkeypatch.delenv("DAIMON_CARRY_MAX", raising=False)
    assert config.carry_max() == 8
    monkeypatch.setenv("DAIMON_CARRY_MAX", "3")
    assert config.carry_max() == 3


def test_stale_days_default_override_malformed(monkeypatch):
    # #215: staleness-budget threshold. Default 7.0; garbage falls back
    # (fail-open), same try/except-float shape as carry_floor.
    monkeypatch.delenv("DAIMON_STALE_DAYS", raising=False)
    assert config.stale_days() == 7.0
    monkeypatch.setenv("DAIMON_STALE_DAYS", "3.5")
    assert config.stale_days() == 3.5
    monkeypatch.setenv("DAIMON_STALE_DAYS", "garbage")
    assert config.stale_days() == 7.0
    monkeypatch.setenv("DAIMON_CARRY_MAX", "0")
    assert config.carry_max() == 1
    monkeypatch.setenv("DAIMON_CARRY_MAX", "x")
    assert config.carry_max() == 8


# ---- #317: scene traces flag ----


def test_scene_traces_default_off(monkeypatch):
    monkeypatch.delenv("DAIMON_SCENE_TRACES", raising=False)
    assert config.scene_traces_enabled() is False


def test_scene_traces_flag_on(monkeypatch):
    monkeypatch.setenv("DAIMON_SCENE_TRACES", "1")
    assert config.scene_traces_enabled() is True


# ---- #360: heal escalation flag ----


def test_heal_escalation_default_off(monkeypatch):
    monkeypatch.delenv("DAIMON_HEAL_ESCALATION", raising=False)
    assert config.heal_escalation_enabled() is False


def test_heal_escalation_flag_on(monkeypatch):
    monkeypatch.setenv("DAIMON_HEAL_ESCALATION", "1")
    assert config.heal_escalation_enabled() is True


# ---- #341: DAIMON_FALLBACK_MIN_SECONDS ---------------------------------------


def test_fallback_min_seconds_defaults_to_timeout(monkeypatch):
    # Unset: the operator's DAIMON_TIMEOUT already declares "one backend call
    # may take this long" — the fallback floor inherits that judgment.
    monkeypatch.delenv("DAIMON_FALLBACK_MIN_SECONDS", raising=False)
    monkeypatch.setenv("DAIMON_TIMEOUT", "111")
    assert config.fallback_min_seconds() == 111


def test_fallback_min_seconds_env_override(monkeypatch):
    monkeypatch.setenv("DAIMON_TIMEOUT", "111")
    monkeypatch.setenv("DAIMON_FALLBACK_MIN_SECONDS", "77")
    assert config.fallback_min_seconds() == 77


def test_fallback_min_seconds_bad_value_falls_back_to_timeout(monkeypatch):
    monkeypatch.setenv("DAIMON_TIMEOUT", "222")
    monkeypatch.setenv("DAIMON_FALLBACK_MIN_SECONDS", "not-a-number")
    assert config.fallback_min_seconds() == 222


def test_chunk_cache_days_default_override_and_bad_value(monkeypatch):
    monkeypatch.delenv("DAIMON_CHUNK_CACHE_DAYS", raising=False)
    assert config.chunk_cache_days() == 3
    monkeypatch.setenv("DAIMON_CHUNK_CACHE_DAYS", "7")
    assert config.chunk_cache_days() == 7
    monkeypatch.setenv("DAIMON_CHUNK_CACHE_DAYS", "not-a-number")
    assert config.chunk_cache_days() == 3
