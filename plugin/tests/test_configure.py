import stat

import pytest

from daimon_briefing import configure, llm


_LLM_VARS = (
    "DAIMON_LLM_BACKEND",
    "DAIMON_LLM_API_KEY", "LITELLM_API_KEY",
    "DAIMON_LLM_MODEL", "LITELLM_MODEL",
    "DAIMON_LLM_BASE_URL", "LITELLM_BASE_URL",
    "DAIMON_LLM_COMMAND", "DAIMON_LLM_COMMAND_OUTPUT",
)


@pytest.fixture
def clean_llm_env(monkeypatch):
    """Start from a known-empty LLM config so the host env can't leak in.
    The autouse conftest fixture already points DAIMON_ENV_FILE at a
    nonexistent path, so only process env needs clearing."""
    for var in _LLM_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def _set_claude(monkeypatch, present):
    """Patch shutil.which as seen by both configure and llm (same module obj)."""
    monkeypatch.setattr(
        llm.shutil, "which",
        lambda name: "/usr/bin/claude" if (present and name == "claude") else None,
    )


# ---- resolved_backend / status detection matrix (mirrors llm.chat() `auto`) ----


def test_claude_on_path_no_key_resolves_command(clean_llm_env):
    _set_claude(clean_llm_env, True)
    assert configure.resolved_backend() == "command"
    st = configure.status()
    assert st["resolved_backend"] == "command"
    assert st["ready"] is True
    assert st["claude_on_path"] is True
    assert st["command_source"] == "claude-cli"
    assert st["command"] == llm._CLAUDE_PRESET[0]


def test_api_key_and_model_no_claude_resolves_litellm(clean_llm_env):
    _set_claude(clean_llm_env, False)
    clean_llm_env.setenv("DAIMON_LLM_API_KEY", "sk-test")
    clean_llm_env.setenv("DAIMON_LLM_MODEL", "kimi-k2.6")
    assert configure.resolved_backend() == "litellm"
    st = configure.status()
    assert st["resolved_backend"] == "litellm"
    assert st["ready"] is True
    assert st["has_api_key"] is True
    assert st["has_model"] is True


def test_api_key_without_model_resolves_litellm_not_ready(clean_llm_env):
    _set_claude(clean_llm_env, False)
    clean_llm_env.setenv("DAIMON_LLM_API_KEY", "sk-test")
    assert configure.resolved_backend() == "litellm"
    st = configure.status()
    assert st["resolved_backend"] == "litellm"
    assert st["ready"] is False
    assert st["has_api_key"] is True
    assert st["has_model"] is False


def test_nothing_configured_resolves_litellm_not_ready(clean_llm_env):
    _set_claude(clean_llm_env, False)
    assert configure.resolved_backend() == "litellm"
    st = configure.status()
    assert st["resolved_backend"] == "litellm"
    assert st["ready"] is False
    assert st["claude_on_path"] is False
    assert st["command"] is None
    assert st["command_source"] is None


def test_explicit_backend_overrides_auto(clean_llm_env):
    # claude on PATH would auto-resolve to command, but an explicit setting wins.
    _set_claude(clean_llm_env, True)
    clean_llm_env.setenv("DAIMON_LLM_BACKEND", "litellm")
    assert configure.resolved_backend() == "litellm"
    st = configure.status()
    assert st["resolved_backend"] == "litellm"
    # litellm needs both key and model; neither is set -> not ready.
    assert st["ready"] is False


def test_explicit_command_source(clean_llm_env):
    _set_claude(clean_llm_env, False)
    clean_llm_env.setenv("DAIMON_LLM_COMMAND", "mycli -p")
    st = configure.status()
    assert st["resolved_backend"] == "command"
    assert st["ready"] is True
    assert st["command"] == "mycli -p"
    assert st["command_source"] == "explicit"


# ---- write_env: merge, preserve, chmod 600, no-empty-file ----


def test_write_env_merges_and_preserves(tmp_path, monkeypatch):
    env_file = tmp_path / "env"
    env_file.write_text(
        "DAIMON_CHECKPOINT_DIR=/keep/me\nDAIMON_LLM_MODEL=old\n", encoding="utf-8"
    )
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))

    out = configure.write_env({"DAIMON_LLM_MODEL": "new", "DAIMON_LLM_API_KEY": "sk-1"})
    assert out == env_file

    from daimon_briefing import config

    values = config._file_values()
    assert values["DAIMON_CHECKPOINT_DIR"] == "/keep/me"  # unrelated key preserved
    assert values["DAIMON_LLM_MODEL"] == "new"            # overridden
    assert values["DAIMON_LLM_API_KEY"] == "sk-1"         # added


def test_write_env_chmod_600(tmp_path, monkeypatch):
    env_file = tmp_path / "env"
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
    configure.write_env({"DAIMON_LLM_API_KEY": "sk-1"})
    mode = env_file.stat().st_mode & 0o777
    assert mode == 0o600


def test_write_env_creates_parent_dir(tmp_path, monkeypatch):
    env_file = tmp_path / "nested" / "dir" / "env"
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
    configure.write_env({"DAIMON_LLM_API_KEY": "sk-1"})
    assert env_file.exists()


def test_write_env_empty_updates_no_file(tmp_path, monkeypatch):
    env_file = tmp_path / "env"
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
    out = configure.write_env({})
    assert not env_file.exists()  # nothing to persist -> no empty file created
    assert out == env_file


def test_write_env_sorted_lines(tmp_path, monkeypatch):
    env_file = tmp_path / "env"
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
    configure.write_env({"B_KEY": "2", "A_KEY": "1"})
    lines = [ln for ln in env_file.read_text().splitlines() if ln]
    assert lines == ["A_KEY=1", "B_KEY=2"]


# ---- #56: configure --test — prove the backend works AT SETUP ----


def test_configure_test_passes_with_working_backend(monkeypatch, capsys, tmp_path):
    from daimon_briefing import cli, llm
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "fake-cli")
    # #59: --test now requires extractable JSON, same bar as serialization.
    monkeypatch.setattr(llm, "_run_command", lambda *a, **k: (0, '{"ok": true}', ""))
    rc = cli.main(["configure", "--test"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "backend test: ok" in out


def test_configure_test_fails_loud_with_broken_backend(monkeypatch, capsys, tmp_path):
    from daimon_briefing import cli, llm
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "failing-cli")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(llm, "_run_command",
                        lambda *a, **k: (101, "", "panic: no prompt"))
    rc = cli.main(["configure", "--test"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "backend test: FAILED" in err
    assert "backend-stderr.log" in err


# ---- #59: --test proves JSON-extraction fitness, not just transport ----


def test_configure_test_fails_when_backend_cannot_produce_json(monkeypatch, capsys):
    # Field case: an agent-harness CLI chats politely but never emits JSON —
    # transport-only smoke test said ok while every serialize failed.
    from daimon_briefing import cli, llm
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "chatty-agent")
    monkeypatch.setattr(llm, "_run_command",
                        lambda *a, **k: (0, "Sure! Let me think about that...", ""))
    rc = cli.main(["configure", "--test"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "extractable JSON" in err


def test_configure_test_passes_with_json_capable_backend(monkeypatch, capsys):
    from daimon_briefing import cli, llm
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "good-cli")
    monkeypatch.setattr(llm, "_run_command",
                        lambda *a, **k: (0, 'Here you go: {"ok": true}', ""))
    rc = cli.main(["configure", "--test"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "backend test: ok" in out
