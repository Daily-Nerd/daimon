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
    from daimon_briefing import transcript

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


# ---- #103 I1: pre_llm_call must withhold resolved items before injecting ----


def test_pre_llm_call_withholds_resolved_item(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    # hermes' pre_llm_call rendered the RAW checkpoint — a resolved item still
    # auto-injected into every new session's context. Fix mirrors _cmd_brief:
    # read this project's resolutions, apply briefing.withhold, then render.
    # A project_dir is required: resolutions() no-ops (empty dict) for the
    # unrouted/global bucket (project_slug(None) is None), so the fixture
    # must route through a real project like the CLI-level #103 tests do.
    from daimon_briefing import store

    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/repo/x")
    store.write_checkpoint("S-prev", sample_checkpoint, project_dir="/repo/x")
    written = store.read_latest(project_dir="/repo/x")
    item_id = written["working_context"]["open_questions"][1]["id"]
    store.append_event(item_id, "resolved", project_dir="/repo/x")
    out = hooks.pre_llm_call(
        session_id="S2", user_message="hi", conversation_history=[], is_first_turn=True,
        model="m", platform="cli",
    )
    assert isinstance(out, dict)
    assert "Chunk threshold for the serializer" not in out["context"]
    # PR #6 item is untouched — only the resolved item is withheld.
    assert "PR #6" in out["context"]


def test_pre_llm_call_fails_open_when_resolutions_raises(
    tmp_checkpoint_dir, sample_checkpoint, monkeypatch
):
    # Withhold machinery must never suppress the injection path either — a
    # broken events.jsonl still injects the full, unfiltered briefing.
    from daimon_briefing import store

    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/repo/x")
    store.write_checkpoint("S-prev", sample_checkpoint, project_dir="/repo/x")

    def _boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(hooks.store, "resolutions", _boom)
    out = hooks.pre_llm_call(
        session_id="S2", user_message="hi", conversation_history=[], is_first_turn=True,
        model="m", platform="cli",
    )
    assert isinstance(out, dict)
    assert "Chunk threshold for the serializer" in out["context"]


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


# ---- #142: a failed in-process capture must land in serialize.log (the ledger) ----


def _ledger_text():
    from daimon_briefing import config

    return (config.log_dir() / "serialize.log").read_text(encoding="utf-8")


def _fail_session(fake_chat_factory, monkeypatch, session_id="S-fail", **kwargs):
    """Drive on_session_end into a real capture failure: the LLM explodes."""
    from daimon_briefing import transcript

    chat = fake_chat_factory(RuntimeError("kaboom"))
    monkeypatch.setattr(transcript, "from_session", lambda sid: make_messages(20))
    monkeypatch.setattr(hooks, "_chat", chat)
    hooks.on_session_end(
        session_id=session_id, completed=True, interrupted=False, model="m",
        platform="cli", **kwargs,
    )


def test_on_session_end_failure_writes_attributed_ledger_entry(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch
):
    # log.exception alone reaches nothing `status`/`heal` parse — the failure
    # must land in serialize.log in the CLI writer's exact shape, attributed
    # to its session by the per-session ledger fold.
    import time

    from daimon_briefing import cli

    _fail_session(fake_chat_factory, monkeypatch, session_id="S-fail")

    text = _ledger_text()
    entry = cli._session_ledger(text, time.time()).get("S-fail")
    assert entry is not None
    assert entry["result_kind"] == "error"
    assert entry["spawned"] is True  # classified by the normal heal rules
    assert "kaboom" in (entry["result_line"] or "")


def test_status_counts_failed_in_process_capture(
    tmp_checkpoint_dir, fake_chat_factory, capsys, monkeypatch
):
    import json

    from daimon_briefing import cli

    _fail_session(fake_chat_factory, monkeypatch, session_id="S-fail")

    cli.main(["status", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert "S-fail" in [f["sid"] for f in data["outstanding"]]


def test_heal_plan_includes_failed_in_process_capture(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch
):
    # No transcript file on disk -> not auto-repairable, but the plan must
    # still surface the failed session instead of pretending nothing happened.
    import time

    from daimon_briefing import cli

    _fail_session(fake_chat_factory, monkeypatch, session_id="S-fail")

    plan = cli._heal_plan(_ledger_text(), time.time())
    listed = [s["sid"] for s in plan["skipped"]]
    if plan["target"] is not None:
        listed.append(plan["target"]["sid"])
    assert "S-fail" in listed


def test_heal_retries_failed_in_process_capture_with_transcript(
    tmp_checkpoint_dir, fake_chat_factory, capsys, monkeypatch
):
    # When the host hands the hook a real transcript file, the failure is
    # healable like any spawned-CLI failure: heal targets it, marks the ONE
    # retry (#26), and the retry writes the checkpoint.
    import time
    from pathlib import Path

    from daimon_briefing import cli, store

    tpath = Path(__file__).parent / "fixtures" / "sample_transcript.md"
    sid = tpath.stem  # ledger attribution: session id == transcript stem

    _fail_session(fake_chat_factory, monkeypatch, session_id=sid,
                  transcript_path=str(tpath))

    plan = cli._heal_plan(_ledger_text(), time.time())
    assert plan["target"] is not None
    assert plan["target"]["sid"] == sid

    good_chat = fake_chat_factory(_valid_json(sid))
    monkeypatch.setattr(cli, "_chat", good_chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    assert cli.main(["heal"]) == 0
    assert store.read_checkpoint(sid) is not None
    assert f"session-start: retry serialize for {sid}" in _ledger_text()


def test_on_session_end_failure_ignores_mismatched_transcript_path(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch
):
    # A host transcript path whose stem is NOT the session id would split the
    # failure across two ledger entries (spawn under one id, error under
    # another) — fall back to the session id token instead.
    import time

    from daimon_briefing import cli

    _fail_session(fake_chat_factory, monkeypatch, session_id="S-fail",
                  transcript_path="/somewhere/else.jsonl")

    entry = cli._session_ledger(_ledger_text(), time.time()).get("S-fail")
    assert entry is not None
    assert entry["result_kind"] == "error"
    assert entry["spawned"] is True


def test_on_session_end_success_leaves_no_ledger_entry(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch
):
    from daimon_briefing import config, transcript

    chat = fake_chat_factory(_valid_json("S-ok"))
    monkeypatch.setattr(transcript, "from_session", lambda sid: make_messages(20))
    monkeypatch.setattr(hooks, "_chat", chat)
    hooks.on_session_end(
        session_id="S-ok", completed=True, interrupted=False, model="m", platform="cli"
    )
    assert not (config.log_dir() / "serialize.log").exists()


def test_on_session_end_too_short_serialize_leaves_no_failure_entry(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch
):
    # A too-short session is a legitimate skip, not a loss — it must never be
    # ledgered as a failure heal would try to repair.
    from daimon_briefing import config, serializer, transcript

    monkeypatch.setattr(transcript, "from_session", lambda sid: make_messages(20))
    monkeypatch.setattr(hooks, "_chat", fake_chat_factory(_valid_json("S-tiny")))

    def _too_short(*_a, **_k):
        raise serializer.TooShortError("too short")

    monkeypatch.setattr(hooks.serializer, "serialize_strict", _too_short, raising=False)
    monkeypatch.setattr(hooks.serializer, "serialize", lambda *a, **k: None)
    hooks.on_session_end(
        session_id="S-tiny", completed=True, interrupted=False, model="m", platform="cli"
    )
    log_file = config.log_dir() / "serialize.log"
    assert not log_file.exists() or "error:" not in log_file.read_text(encoding="utf-8")


def test_on_session_end_never_raises_when_ledger_write_fails(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch
):
    monkeypatch.setattr(hooks.config, "log_dir", _raiser)
    _fail_session(fake_chat_factory, monkeypatch, session_id="S-fail")  # must not raise


def test_hooks_docstring_no_stale_project_name():
    assert "hermes" not in (hooks.__doc__ or "").lower()
