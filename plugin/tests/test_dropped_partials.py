"""q-741c17a4d3be: an extraction call dropped from a run (one chunk or
perspective pass whose output stayed unusable) is recorded on the checkpoint
in a code-owned field, so the gap is on the record and the briefing says so.

Isolation (HOME aside) comes from tests/conftest.py's autouse fixture; the LLM
is always a scripted stub."""
import json
import time

import pytest

from daimon_briefing import briefing, llm, schema, serializer, store
from daimon_briefing.surfaces import Writer
from tests.conftest import make_messages
from tests.test_partial_shape import Script, _chunked, flat, good

KEY = schema.CAPTURE_DROPPED_PARTIALS


@pytest.fixture(autouse=True)
def _no_real_rescue(monkeypatch):
    llm.reset_fallback()

    def none(messages, deadline):
        raise llm.NoRescueAvailable("no rescue configured")

    monkeypatch.setattr(llm, "rescue_unparseable", none)
    yield
    llm.reset_fallback()


def test_a_dropped_chunk_is_recorded_on_the_checkpoint(monkeypatch):
    _chunked(monkeypatch)
    ms = make_messages(24)
    n = len(serializer.chunk_transcript(serializer._render_transcript(ms), 4, 0))
    chat = Script(lambda i, c: flat() if "TRANSCRIPT (chunk 2 of" in c else good())
    ckpt = serializer.serialize_strict("S1", ms, chat=chat)
    assert ckpt[KEY] == [{"kind": "chunk", "label": f"chunk 2 of {n}"}]


def test_a_dropped_perspective_pass_is_recorded(monkeypatch):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    chat = Script(lambda i, c: flat() if "ARTIFACTS AND IDENTIFIERS" in c else good())
    ckpt = serializer.serialize_strict("S1", make_messages(20), chat=chat,
                                       escalate=True)
    assert ckpt[KEY] == [{"kind": "perspective",
                          "label": "perspective artifacts-and-identifiers, chunk 1 of 1"}]


def test_a_clean_run_carries_no_field(monkeypatch):
    _chunked(monkeypatch)
    ckpt = serializer.serialize_strict(
        "S1", make_messages(24), chat=Script(lambda i, c: good()))
    assert KEY not in ckpt


def test_a_model_authored_copy_is_stripped():
    forged = {**good(), KEY: [{"kind": "chunk", "label": "forged"}]}
    ckpt = serializer.serialize_strict(
        "S1", make_messages(20), chat=lambda *a, **k: json.dumps(forged))
    assert KEY in serializer._CODE_OWNED_KEYS
    assert KEY not in ckpt


def _written(dropped):
    cp = {"session_id": "S-1", "created": "2026-08-01T00:00:00Z",
          "working_context": {
              "active_topic": {"text": "topic", "trust": "inferred"},
              "open_questions": [], "recent_decisions": [
                  {"text": "a decision", "trust": "inferred"}]},
          "epistemic_snapshot": {}}
    if dropped is not None:
        cp[KEY] = dropped
    store.write_checkpoint("S-1", cp, project_dir="/p/drops", writer=Writer.HUMAN)


def test_the_briefing_says_what_is_missing_in_one_line():
    _written([{"kind": "chunk", "label": "chunk 7 of 20"}])
    got = briefing.prepare("/p/drops", time.time())
    lines = [n for n in got.notes if "could not be read" in n]
    assert lines == ["⚠ 1 transcript chunk could not be read (chunk 7 of 20); "
                     "that stretch of the session is missing."]


def test_the_briefing_counts_several_chunks_and_names_thin_perspectives():
    _written([{"kind": "chunk", "label": "chunk 7 of 20"},
              {"kind": "chunk", "label": "chunk 9 of 20"},
              {"kind": "perspective", "label": "perspective x, chunk 1 of 20"}])
    got = briefing.prepare("/p/drops", time.time())
    lines = [n for n in got.notes if "could not be read" in n]
    assert len(lines) == 1 and "\n" not in lines[0]
    assert "2 transcript chunks could not be read (chunk 7 of 20, chunk 9 of 20)" in lines[0]
    assert "1 extra reading pass failed" in lines[0]


def test_a_clean_checkpoint_adds_no_coverage_note():
    _written(None)
    got = briefing.prepare("/p/drops", time.time())
    assert not [n for n in got.notes if "could not be read" in n]
