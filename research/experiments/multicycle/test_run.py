import json

import seed
import run_multicycle as rm


def _fake_chat_factory():
    """Deterministic stand-in for the serializer LLM: always returns the seed
    checkpoint body as JSON (valid per serializer.validate), so the pipeline
    round-trips without a gateway."""
    body = seed.make_seed()

    def fake_chat(messages, **kwargs):
        return json.dumps(body)
    return fake_chat


def test_run_arm_produces_cached_cycles(tmp_path):
    rows = rm.run_arm("control", cycles=3, chat=_fake_chat_factory(),
                      run_dir=tmp_path)
    assert (tmp_path / "control" / "cycle-01.json").exists()
    assert (tmp_path / "control" / "cycle-03.json").exists()
    assert {r["cycle"] for r in rows} == {0, 1, 2, 3}


def test_resume_skips_existing_cycles(tmp_path):
    rm.run_arm("control", cycles=2, chat=_fake_chat_factory(), run_dir=tmp_path)
    calls = []

    def counting_chat(messages, **kwargs):
        calls.append(1)
        return json.dumps(seed.make_seed())
    rm.run_arm("control", cycles=3, chat=counting_chat, run_dir=tmp_path)
    # cycles 1-2 cached; only cycle 3 needed LLM calls
    assert len(calls) >= 1
    data = json.loads((tmp_path / "control" / "cycle-01.json").read_text())
    assert data["session_id"] == "control-cycle-001"


def test_created_advances_one_day_per_cycle(tmp_path):
    rm.run_arm("control", cycles=2, chat=_fake_chat_factory(), run_dir=tmp_path)
    c1 = json.loads((tmp_path / "control" / "cycle-01.json").read_text())
    c2 = json.loads((tmp_path / "control" / "cycle-02.json").read_text())
    assert c1["created"] == "2026-06-02T00:00:00Z"
    assert c2["created"] == "2026-06-03T00:00:00Z"


def test_counting_chat_aborts_over_budget():
    def chat(messages, **kwargs):
        return "x" * 4000  # ~1000 tokens per call
    wrapped = rm.CountingChat(chat, budget=1500)
    wrapped([{"role": "user", "content": "y" * 4000}])  # ~2000 total
    try:
        wrapped([{"role": "user", "content": "z"}])
        assert False, "expected BudgetExceeded"
    except rm.BudgetExceeded:
        pass


def test_carry_arm_injects_raw_checkpoint_json(tmp_path, monkeypatch):
    seen_contexts = []
    real = rm._context_for

    def spy(arm, cp, now_epoch):
        ctx = real(arm, cp, now_epoch)
        seen_contexts.append((arm, ctx))
        return ctx
    monkeypatch.setattr(rm, "_context_for", spy)
    rm.run_arm("carry", cycles=1, chat=_fake_chat_factory(), run_dir=tmp_path)
    arm, ctx = seen_contexts[0]
    assert arm == "carry"
    assert json.loads(ctx)["session_id"] == "cycle-000"  # raw JSON, not prose


def test_briefing_receives_simulated_clock(tmp_path, monkeypatch):
    from daimon_briefing import briefing as daimon_briefing_briefing
    from daimon_briefing import store

    captured_now = []
    real_build = daimon_briefing_briefing.build

    def spy(cp, now=None, **kwargs):
        captured_now.append(now)
        return real_build(cp, now=now, **kwargs)
    monkeypatch.setattr(daimon_briefing_briefing, "build", spy)

    def chat(messages, **kwargs):
        return json.dumps(seed.make_seed())

    rm.run_arm("control", cycles=2, chat=chat, run_dir=tmp_path)
    assert captured_now == [
        store._created_epoch("2026-06-02T00:00:00Z"),
        store._created_epoch("2026-06-03T00:00:00Z"),
    ]


def test_driver_carries_when_enabled(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("DAIMON_CARRY", "1")
    # fake chat DROPS the seed open question: returns seed minus open_questions
    body = seed.make_seed()
    body["working_context"]["open_questions"] = []

    def dropping_chat(messages, **kwargs):
        return json.dumps(body)
    rm.run_arm("control", cycles=1, chat=dropping_chat, run_dir=tmp_path)
    cp = json.loads((tmp_path / "control" / "cycle-01.json").read_text())
    qs = cp["working_context"]["open_questions"]
    assert any("quorint-ledger" in q["text"] for q in qs)  # carried back in
    assert qs and qs[0].get("carried_from") == "cycle-000"


def test_driver_kill_switch_reproduces_run01_behavior(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("DAIMON_CARRY", "0")
    body = seed.make_seed()
    body["working_context"]["open_questions"] = []

    def dropping_chat(messages, **kwargs):
        return json.dumps(body)
    rm.run_arm("control", cycles=1, chat=dropping_chat, run_dir=tmp_path)
    cp = json.loads((tmp_path / "control" / "cycle-01.json").read_text())
    assert cp["working_context"]["open_questions"] == []
