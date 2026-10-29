"""#1255: what one serialize run knows about itself leaves only through its
return value (the checkpoint) or its raised SerializeError. A collector write
with no open scope raises instead of landing in a hidden default."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Thread

import pytest

from daimon_briefing import llm, schema, serializer
from tests.conftest import FIXTURES, make_messages


def _checkpoint_json(session_id, bad_quotes=0):
    """A valid checkpoint naming `bad_quotes` verbatim quotes that are nowhere
    in the transcript, so verify_quotes downgrades exactly that many."""
    open_questions = [
        {"text": f"q{i}", "trust": "verbatim",
         "quote": f"never said anywhere {i}"} for i in range(bad_quotes)]
    return json.dumps({
        "session_id": session_id,
        "working_context": {
            "active_topic": {"text": "topic", "trust": "inferred"},
            "open_questions": open_questions,
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
        "worker_queue": [],
    })


# ---- a write with no scope open raises ---------------------------------------


@pytest.fixture
def no_scope():
    token = llm._CURRENT_CALL_STATE.set(None)
    yield
    llm._CURRENT_CALL_STATE.reset(token)


def test_note_fallback_used_without_a_scope_raises(no_scope):
    with pytest.raises(RuntimeError, match="scope"):
        llm.note_fallback_used()


def test_note_served_without_a_scope_raises(no_scope):
    with pytest.raises(RuntimeError, match="scope"):
        llm.note_served("some-model")


def test_usage_write_without_a_scope_raises(no_scope):
    with pytest.raises(RuntimeError, match="scope"):
        llm._note_call_usage("litellm", "m", None, None)


def test_a_finished_scope_leaves_nothing_behind(no_scope):
    with llm.call_scope():
        llm.note_fallback_used()
        assert llm.fallback_used() is True
    with pytest.raises(RuntimeError, match="scope"):
        llm.fallback_used()


# ---- the facts ride on the returned checkpoint --------------------------------


def _fallback_chat(session_id):
    def chat(_messages, **_kwargs):
        llm.note_fallback_used()
        return _checkpoint_json(session_id)
    return chat


def test_fallback_marker_is_stamped_on_the_checkpoint():
    ckpt = serializer.serialize_strict(
        "S-fb", make_messages(20), chat=_fallback_chat("S-fb"))
    assert ckpt[schema.CAPTURE_FALLBACK] is True
    clean = serializer.serialize_strict(
        "S-ok", make_messages(20), chat=lambda *a, **k: _checkpoint_json("S-ok"))
    assert clean[schema.CAPTURE_FALLBACK] is False


def test_marker_survives_a_worker_thread():
    box = {}

    def run():
        box["ckpt"] = serializer.serialize_strict(
            "S-fb", make_messages(20), chat=_fallback_chat("S-fb"))

    worker = Thread(target=run)
    worker.start()
    worker.join(timeout=30)
    assert box["ckpt"][schema.CAPTURE_FALLBACK] is True


def test_concurrent_serializes_carry_their_own_downgrade_count():
    barrier = Barrier(2)

    def capture(name, bad_quotes):
        def chat(_messages, **_kwargs):
            barrier.wait(timeout=10)
            return _checkpoint_json(name, bad_quotes)
        return serializer.serialize_strict(name, make_messages(20), chat=chat)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(capture, "S-one", 1)
        second = pool.submit(capture, "S-three", 3)
        one, three = first.result(timeout=30), second.result(timeout=30)
    assert one[schema.CAPTURE_QUOTES_DOWNGRADED] == 1
    assert three[schema.CAPTURE_QUOTES_DOWNGRADED] == 3


@pytest.mark.parametrize("key", [schema.CAPTURE_FALLBACK,
                                 schema.CAPTURE_QUOTES_DOWNGRADED])
def test_a_model_authored_run_fact_is_stripped(key):
    spoofed = json.loads(_checkpoint_json("S1"))
    spoofed[key] = True if key == schema.CAPTURE_FALLBACK else 99
    ckpt = serializer.serialize_strict(
        "S1", make_messages(20), chat=lambda *a, **k: json.dumps(spoofed))
    assert key in serializer._CODE_OWNED_KEYS
    assert ckpt[key] in (False, 0)


# ---- a failed run reports through the exception -------------------------------


def test_serialize_error_carries_the_models_served_before_it():
    def failing_chat(_messages, **_kwargs):
        llm.note_served("failed-model")
        raise RuntimeError("gateway failed")

    with pytest.raises(serializer.LLMCallError) as raised:
        serializer.serialize_strict("S-failed", make_messages(20), chat=failing_chat)
    assert raised.value.served_models == ["failed-model"]


def test_too_short_error_carries_an_empty_list():
    with pytest.raises(serializer.TooShortError) as raised:
        serializer.serialize_strict("S-short", make_messages(1), chat=lambda *a, **k: "")
    assert raised.value.served_models == []


# ---- scopeless callers open their own scope -----------------------------------


def _recording_chat(reply):
    def chat(*_args, **_kwargs):
        llm.note_served("recorded-model")  # raises when no scope is open
        return reply
    return chat


def test_the_llm_briefing_render_opens_its_own_scope(monkeypatch, no_scope):
    from daimon_briefing import briefing

    monkeypatch.setattr(llm, "chat", _recording_chat("narrative"))
    assert briefing._render_llm({"session_id": "S1"}) == "narrative"


def test_configure_test_opens_its_own_scope(monkeypatch, no_scope):
    from daimon_briefing.cli import configure_cmd

    monkeypatch.setattr(llm, "chat", _recording_chat('{"ok": true}'))
    assert configure_cmd._run_backend_test() == 0


# ---- the CLI reads the facts off the checkpoint -------------------------------


def test_cli_serialize_marks_the_fallback_when_run_in_a_worker_thread(
        tmp_checkpoint_dir, capsys, monkeypatch):
    from daimon_briefing import cli

    monkeypatch.setattr(cli, "_chat", _fallback_chat("S-fb"))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    box = {}
    worker = Thread(target=lambda: box.update(
        rc=cli.main(["serialize", str(FIXTURES / "sample_transcript.md")])))
    worker.start()
    worker.join(timeout=60)
    assert box["rc"] == 0
    assert "[fallback backend]" in capsys.readouterr().out
