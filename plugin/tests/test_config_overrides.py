"""`config.log_dir_override` and `config.settings_override` (#1179 PR 5, D22).

Both are ContextVars like `checkpoint_dir_override`: they answer in the calling
context only, never reach a `threading.Thread`, and reset on exit. Under a
settings override `_get` answers DAIMON_* names from the mapping alone, so the
process env and the user's env file cannot leak into a sandboxed read."""

import threading

from daimon_briefing import config


def _in_thread(fn):
    out = []
    t = threading.Thread(target=lambda: out.append(fn()))
    t.start()
    t.join()
    return out[0]


def test_log_dir_override_answers_in_this_context_and_resets(tmp_path):
    before = config.log_dir()
    with config.log_dir_override(tmp_path / "logs"):
        assert config.log_dir() == tmp_path / "logs"
        assert config.recall_delivery_log() == (
            tmp_path / "logs" / "recall-delivery.jsonl")
        assert _in_thread(config.log_dir) == before
    assert config.log_dir() == before


def test_settings_override_answers_only_from_the_mapping(monkeypatch):
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "77")
    assert config.brief_max_bytes() == 77
    with config.settings_override({"DAIMON_LLM_MODEL": "m-1"}):
        assert config.llm_model() == "m-1"
        assert config.brief_max_bytes() != 77
    assert config.brief_max_bytes() == 77


def test_settings_override_ignores_the_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / "env"
    env_file.write_text("DAIMON_LLM_MODEL=from-file\n", encoding="utf-8")
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
    assert config.llm_model() == "from-file"
    with config.settings_override({}):
        assert config.llm_model() is None


def test_settings_override_does_not_reach_a_thread_and_resets(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_MODEL", "from-env")
    with config.settings_override({"DAIMON_LLM_MODEL": "mapped"}):
        assert config.llm_model() == "mapped"
        assert _in_thread(config.llm_model) == "from-env"
    assert config.llm_model() == "from-env"


def test_active_settings_is_the_override_or_none():
    assert config.active_settings() is None
    with config.settings_override({"DAIMON_X": "1"}):
        assert dict(config.active_settings()) == {"DAIMON_X": "1"}
    assert config.active_settings() is None


def test_the_mapping_is_copied_so_a_later_edit_cannot_change_the_override():
    mapping = {"DAIMON_LLM_MODEL": "a"}
    with config.settings_override(mapping):
        mapping["DAIMON_LLM_MODEL"] = "b"
        assert config.llm_model() == "a"


def test_non_daimon_names_come_from_the_mapping_then_the_process_env(monkeypatch):
    monkeypatch.setenv("LITELLM_MODEL", "lite-env")
    with config.settings_override({}):
        assert config.llm_model() == "lite-env"
    with config.settings_override({"LITELLM_MODEL": "lite-map"}):
        assert config.llm_model() == "lite-map"
