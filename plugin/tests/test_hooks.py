import daimon_briefing as plugin
from daimon_briefing import hooks
from tests.conftest import make_messages


def _valid_json(session_id="S1"):
    import json

    return json.dumps(
        {
            "session_id": session_id,
            "working_context": {
                "active_topic": {"text": "t", "trust": "inferred"},
                "open_questions": [{"text": "q", "trust": "inferred"}],
                "recent_decisions": [{"text": "d", "trust": "inferred"}],
            },
            "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
        }
    )


# ---- pre_llm_call gating ----


def test_pre_llm_call_returns_none_when_not_first_turn(tmp_checkpoint_dir):
    out = hooks.pre_llm_call(
        session_id="S2", user_message="hi", conversation_history=[], is_first_turn=False,
        model="m", platform="cli",
    )
    assert out is None


def test_pre_llm_call_returns_none_when_no_checkpoint(tmp_checkpoint_dir):
    out = hooks.pre_llm_call(
        session_id="S2", user_message="hi", conversation_history=[], is_first_turn=True,
        model="m", platform="cli",
    )
    assert out is None


def test_pre_llm_call_injects_briefing(tmp_checkpoint_dir, sample_checkpoint):
    from daimon_briefing import store

    store.write_checkpoint("S-prev", sample_checkpoint)
    out = hooks.pre_llm_call(
        session_id="S2", user_message="hi", conversation_history=[], is_first_turn=True,
        model="m", platform="cli",
    )
    assert isinstance(out, dict)
    assert "context" in out
    assert "PR #6" in out["context"]


def test_pre_llm_call_disabled_returns_none(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    from daimon_briefing import store

    store.write_checkpoint("S-prev", sample_checkpoint)
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    out = hooks.pre_llm_call(
        session_id="S2", user_message="hi", conversation_history=[], is_first_turn=True,
        model="m", platform="cli",
    )
    assert out is None


def test_pre_llm_call_never_raises(tmp_checkpoint_dir, monkeypatch):
    # Make render explode; hook must swallow it.
    monkeypatch.setattr(hooks.briefing, "render", lambda *_: (_ for _ in ()).throw(ValueError("boom")))
    from daimon_briefing import store

    store.write_checkpoint("S-prev", {"session_id": "x", "working_context": {}, "epistemic_snapshot": {}})
    out = hooks.pre_llm_call(
        session_id="S2", user_message="hi", conversation_history=[], is_first_turn=True,
        model="m", platform="cli",
    )
    assert out is None


# ---- on_session_end gating + safety ----


def test_on_session_end_writes_checkpoint(tmp_checkpoint_dir, fake_chat_factory, monkeypatch):
    from daimon_briefing import store, transcript

    chat = fake_chat_factory(_valid_json("S-end"))
    monkeypatch.setattr(transcript, "from_session", lambda sid: make_messages(20))
    monkeypatch.setattr(hooks, "_chat", chat)

    hooks.on_session_end(
        session_id="S-end", completed=True, interrupted=False, model="m", platform="cli"
    )
    assert store.read_checkpoint("S-end") is not None
    assert store.read_latest()["session_id"] == "S-end"


def test_on_session_end_disabled_noop(tmp_checkpoint_dir, fake_chat_factory, monkeypatch):
    from daimon_briefing import store, transcript

    chat = fake_chat_factory(_valid_json("S-end"))
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    monkeypatch.setattr(transcript, "from_session", lambda sid: make_messages(20))
    monkeypatch.setattr(hooks, "_chat", chat)
    hooks.on_session_end(
        session_id="S-end", completed=True, interrupted=False, model="m", platform="cli"
    )
    assert store.read_checkpoint("S-end") is None
    assert chat.calls == []


def test_on_session_end_too_short_noop(tmp_checkpoint_dir, fake_chat_factory, monkeypatch):
    from daimon_briefing import store, transcript

    chat = fake_chat_factory(_valid_json("S-end"))
    monkeypatch.setattr(transcript, "from_session", lambda sid: make_messages(3))
    monkeypatch.setattr(hooks, "_chat", chat)
    hooks.on_session_end(
        session_id="S-end", completed=True, interrupted=False, model="m", platform="cli"
    )
    assert store.read_checkpoint("S-end") is None


def test_on_session_end_never_raises_when_serializer_explodes(tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import transcript

    def boom(sid):
        raise RuntimeError("transcript read blew up")

    monkeypatch.setattr(transcript, "from_session", boom)
    # Must not propagate.
    hooks.on_session_end(
        session_id="S-end", completed=True, interrupted=False, model="m", platform="cli"
    )


def test_on_session_end_never_raises_when_chat_explodes(tmp_checkpoint_dir, fake_chat_factory, monkeypatch):
    from daimon_briefing import store, transcript

    chat = fake_chat_factory(RuntimeError("kaboom"))
    monkeypatch.setattr(transcript, "from_session", lambda sid: make_messages(20))
    monkeypatch.setattr(hooks, "_chat", chat)
    hooks.on_session_end(
        session_id="S-end", completed=True, interrupted=False, model="m", platform="cli"
    )
    assert store.read_checkpoint("S-end") is None


# ---- never-raise: failure injection at every dependency seam ----


def _raiser(*_a, **_k):
    raise RuntimeError("injected failure")


def test_on_session_end_never_raises_when_config_raises(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(hooks.config, "is_disabled", _raiser)
    hooks.on_session_end(
        session_id="S-end", completed=True, interrupted=False, model="m", platform="cli"
    )  # must not raise


def test_pre_llm_call_never_raises_when_config_raises(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(hooks.config, "is_disabled", _raiser)
    out = hooks.pre_llm_call(
        session_id="S2", user_message="hi", conversation_history=[], is_first_turn=True,
        model="m", platform="cli",
    )
    assert out is None


def test_pre_llm_call_never_raises_when_read_latest_raises(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(hooks.store, "read_latest", _raiser)
    out = hooks.pre_llm_call(
        session_id="S2", user_message="hi", conversation_history=[], is_first_turn=True,
        model="m", platform="cli",
    )
    assert out is None


def test_pre_llm_call_never_raises_on_corrupt_latest_json(tmp_checkpoint_dir):
    # Real corrupt file on disk, not a mock: read_latest will raise JSONDecodeError.
    tmp_checkpoint_dir.mkdir(parents=True, exist_ok=True)
    (tmp_checkpoint_dir / "latest.json").write_text("{not json!", encoding="utf-8")
    out = hooks.pre_llm_call(
        session_id="S2", user_message="hi", conversation_history=[], is_first_turn=True,
        model="m", platform="cli",
    )
    assert out is None


def test_on_session_end_never_raises_when_store_write_raises(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch
):
    from daimon_briefing import transcript

    chat = fake_chat_factory(_valid_json("S-end"))
    monkeypatch.setattr(transcript, "from_session", lambda sid: make_messages(20))
    monkeypatch.setattr(hooks, "_chat", chat)
    monkeypatch.setattr(hooks.store, "write_checkpoint", _raiser)
    hooks.on_session_end(
        session_id="S-end", completed=True, interrupted=False, model="m", platform="cli"
    )  # must not raise


# ---- timeout budget: DAIMON_TIMEOUT is a TOTAL deadline for the serialize work ----


def test_on_session_end_passes_deadline_to_chat(tmp_checkpoint_dir, fake_chat_factory, monkeypatch):
    import time

    from daimon_briefing import transcript

    monkeypatch.setenv("DAIMON_TIMEOUT", "90")
    chat = fake_chat_factory(_valid_json("S-end"))
    monkeypatch.setattr(transcript, "from_session", lambda sid: make_messages(20))
    monkeypatch.setattr(hooks, "_chat", chat)

    before = time.monotonic()
    hooks.on_session_end(
        session_id="S-end", completed=True, interrupted=False, model="m", platform="cli"
    )
    after = time.monotonic()
    deadline = chat.calls[0]["kwargs"].get("deadline")
    assert deadline is not None
    # Deadline computed at hook start: now + DAIMON_TIMEOUT (total budget).
    assert before + 90 <= deadline <= after + 90


# ---- #74: both hook call sites normalize the project dir via resolve_project_root ----


def test_on_session_end_routes_project_through_resolve_project_root(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch
):
    from daimon_briefing import store, transcript

    chat = fake_chat_factory(_valid_json("S-end"))
    monkeypatch.setattr(transcript, "from_session", lambda sid: make_messages(20))
    monkeypatch.setattr(hooks, "_chat", chat)
    monkeypatch.setattr(hooks.config, "resolve_project_root", lambda raw: "/git/top")

    captured = {}

    def _spy(session_id, checkpoint, project_dir=None):
        captured["project_dir"] = project_dir
        return None

    monkeypatch.setattr(hooks.store, "write_checkpoint", _spy)
    hooks.on_session_end(
        session_id="S-end", completed=True, interrupted=False, model="m", platform="cli"
    )
    assert captured["project_dir"] == "/git/top"


def test_pre_llm_call_routes_project_through_resolve_project_root(
    tmp_checkpoint_dir, monkeypatch
):
    monkeypatch.setattr(hooks.config, "resolve_project_root", lambda raw: "/git/top")

    captured = {}

    def _spy(project_dir=None):
        captured["project_dir"] = project_dir
        return None

    monkeypatch.setattr(hooks.store, "read_latest", _spy)
    out = hooks.pre_llm_call(
        session_id="S2", user_message="hi", conversation_history=[], is_first_turn=True,
        model="m", platform="cli",
    )
    assert out is None  # read_latest returned None
    assert captured["project_dir"] == "/git/top"


# ---- register(ctx) ----


def test_register_wires_hooks_and_skill():
    calls = {"hooks": [], "skills": []}

    class Ctx:
        def register_hook(self, event, cb):
            calls["hooks"].append(event)

        def register_skill(self, name, path):
            calls["skills"].append((name, path))

    plugin.register(Ctx())
    assert "on_session_end" in calls["hooks"]
    assert "pre_llm_call" in calls["hooks"]
    assert len(calls["skills"]) == 2
    names = {n for n, _ in calls["skills"]}
    assert names == {"daimon-briefing", "daimon-end"}
    assert all(str(p).endswith("SKILL.md") for _, p in calls["skills"])


# ---- on_session_end scar harvest wiring (#76) ----


def test_on_session_end_runs_harvest_when_enabled(tmp_checkpoint_dir, fake_chat_factory, monkeypatch):
    from daimon_briefing import transcript

    chat = fake_chat_factory(_valid_json("S-h"))
    monkeypatch.setattr(transcript, "from_session", lambda sid: make_messages(20))
    monkeypatch.setattr(hooks, "_chat", chat)
    monkeypatch.setenv("DAIMON_SCAR_HARVEST", "1")
    monkeypatch.setattr(hooks.config, "resolve_project_root", lambda raw: "/git/top")

    seen = {}

    def _spy(messages, project_root=None, session_id=None):
        seen["root"] = project_root
        seen["sid"] = session_id
        return 0

    monkeypatch.setattr(hooks.harvest, "run", _spy)
    hooks.on_session_end(session_id="S-h", completed=True, interrupted=False, model="m", platform="cli")
    assert seen == {"root": "/git/top", "sid": "S-h"}


def test_on_session_end_skips_harvest_when_disabled(tmp_checkpoint_dir, fake_chat_factory, monkeypatch):
    from daimon_briefing import transcript

    chat = fake_chat_factory(_valid_json("S-h"))
    monkeypatch.setattr(transcript, "from_session", lambda sid: make_messages(20))
    monkeypatch.setattr(hooks, "_chat", chat)
    monkeypatch.delenv("DAIMON_SCAR_HARVEST", raising=False)

    called = {"n": 0}
    monkeypatch.setattr(hooks.harvest, "run", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    hooks.on_session_end(session_id="S-h", completed=True, interrupted=False, model="m", platform="cli")
    assert called["n"] == 0


def test_on_session_end_checkpoint_survives_harvest_explosion(tmp_checkpoint_dir, fake_chat_factory, monkeypatch):
    from daimon_briefing import store, transcript

    chat = fake_chat_factory(_valid_json("S-h"))
    monkeypatch.setattr(transcript, "from_session", lambda sid: make_messages(20))
    monkeypatch.setattr(hooks, "_chat", chat)
    monkeypatch.setenv("DAIMON_SCAR_HARVEST", "1")
    monkeypatch.setattr(hooks.harvest, "run", _raiser)  # harvest blows up

    hooks.on_session_end(session_id="S-h", completed=True, interrupted=False, model="m", platform="cli")
    assert store.read_checkpoint("S-h") is not None  # checkpoint written despite harvest crash
