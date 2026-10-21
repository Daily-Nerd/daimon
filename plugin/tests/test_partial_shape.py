"""#1218: a model-authored dict with the wrong envelope shape is rejected at the
parse boundary (retry, rescue, named error) and never cached or replayed.

Isolation (HOME aside) comes from tests/conftest.py's autouse fixture; the LLM
is always a scripted stub."""
import json

import pytest

from daimon_briefing import field_table, llm, serializer
from tests.conftest import make_messages


def _item(text):
    return {"text": text, "trust": "inferred", "importance": 5}


def good(topic="t"):
    return {
        "working_context": {"active_topic": _item(topic), "open_questions": [],
                            "recent_decisions": []},
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
        "worker_queue": [],
    }


def flat():
    g = good()
    return {**g["working_context"], "epistemic_snapshot": g["epistemic_snapshot"],
            "worker_queue": []}


class Script:
    """chat stub: fn(call_number, user_content) -> dict, served as JSON text."""

    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def __call__(self, messages, **kw):
        self.calls.append(messages)
        return json.dumps(self.fn(len(self.calls), messages[-1]["content"]))


@pytest.fixture(autouse=True)
def _no_real_rescue(monkeypatch):
    llm.reset_fallback()

    def none(messages, deadline):
        raise llm.NoRescueAvailable("no rescue configured")

    monkeypatch.setattr(llm, "rescue_unparseable", none)
    yield
    llm.reset_fallback()


def _chunked(monkeypatch):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_CHUNK_LINES", "4")
    monkeypatch.setenv("DAIMON_CHUNK_OVERLAP", "0")
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "100")
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "1")


def _cached_partials():
    return [json.loads(f.read_text())["partial"]
            for f in serializer._chunk_cache_dir().glob("*.json")]


def test_1a_flat_partial_on_disk_is_a_cache_miss():
    key = serializer._chunk_cache_key("chunk text")
    serializer._save_chunk_cache(key, flat())
    assert serializer._load_chunk_cache(key) is None


def test_1a_well_shaped_partial_still_replays():
    key = serializer._chunk_cache_key("chunk text")
    serializer._save_chunk_cache(key, good())
    assert serializer._load_chunk_cache(key) == good()


def test_1b_flat_chunk_is_retried_and_never_cached(monkeypatch):
    _chunked(monkeypatch)
    ms = make_messages(24)

    def fn(n, content):
        # The first attempt at chunk 1 comes back flat; every other call is good.
        return flat() if n == 1 else good()

    chat = Script(fn)
    serializer.serialize_strict("S1", ms, chat=chat)
    assert "retry attempt 2" in chat.calls[1][-1]["content"]   # nonce'd re-ask
    cached = _cached_partials()
    assert cached and all("working_context" in p for p in cached)


def test_1b_flat_chunk_that_stays_flat_names_the_chunk(monkeypatch):
    _chunked(monkeypatch)
    chat = Script(lambda n, content: flat() if "chunk 1 of" in content else good())
    with pytest.raises(serializer.OutputParseError) as ei:
        serializer.serialize_strict("S1", make_messages(24), chat=chat)
    assert "chunk 1 of" in str(ei.value)
    assert "open_questions is outside working_context" in str(ei.value)
    assert not [p for p in _cached_partials() if "working_context" not in p]


def test_1c_rescue_output_is_never_cached_scar_0040():
    llm._fallback_used = True
    serializer._save_chunk_cache(serializer._chunk_cache_key("x"), good())
    assert not list(serializer._chunk_cache_dir().glob("*.json"))


def test_1d_flat_merge_output_names_the_merge_group(monkeypatch):
    _chunked(monkeypatch)
    ms = make_messages(24)
    n_chunks = len(serializer.chunk_transcript(serializer._render_transcript(ms), 4, 0))
    chat = Script(lambda n, content: good() if n <= n_chunks else flat())
    with pytest.raises(serializer.OutputParseError) as ei:
        serializer.serialize_strict("S1", ms, chat=chat)
    assert "merge level 1, group 1 of 1" in str(ei.value)
    assert "open_questions is outside working_context" in str(ei.value)


def test_flat_primary_is_rescued_by_a_well_shaped_fallback(monkeypatch):
    rescues = []

    def rescue(messages, deadline):
        rescues.append(messages)
        return json.dumps(good("rescued"))

    monkeypatch.setattr(llm, "rescue_unparseable", rescue)
    chat = Script(lambda n, content: flat())
    out = serializer._call_and_parse(chat, "sys", "user", None, "transcript")
    assert out["working_context"]["active_topic"]["text"] == "rescued"
    assert len(chat.calls) == 2 and len(rescues) == 1


def test_flat_rescue_is_rejected_not_wrapped(monkeypatch):
    monkeypatch.setattr(llm, "rescue_unparseable",
                        lambda messages, deadline: json.dumps(flat()))
    chat = Script(lambda n, content: flat())
    with pytest.raises(serializer.OutputParseError) as ei:
        serializer._call_and_parse(chat, "sys", "user", None, "perspective p, chunk 2 of 3")
    assert "perspective p, chunk 2 of 3" in str(ei.value)
    assert "open_questions is outside working_context" in str(ei.value)


def test_epistemic_snapshot_shape_is_checked_too():
    bad = good()
    bad["epistemic_snapshot"] = []
    assert field_table.partial_reason(bad) == "epistemic_snapshot is not a dict"


def test_partial_reason_is_envelope_only():
    # A partial may lack what the final checkpoint requires (session_id is
    # code-assigned, items are checked on the merged result).
    assert field_table.partial_reason(good()) is None
    assert field_table.partial_reason({}) is None
    assert field_table.partial_reason([]) == "partial is not a dict"


def test_absent_block_is_accepted_but_the_final_checkpoint_still_requires_it():
    no_snapshot = {"working_context": good()["working_context"], "worker_queue": []}
    assert field_table.partial_reason(no_snapshot) is None
    assert "epistemic_snapshot" not in no_snapshot   # nothing inserted
    full = {**good(), "session_id": "S1"}
    assert field_table.checkpoint_reason(full) is None
    del full["epistemic_snapshot"]
    assert field_table.checkpoint_reason(full) == "epistemic_snapshot is not a dict"


@pytest.mark.parametrize("key,block", [
    ("active_topic", "working_context"), ("open_questions", "working_context"),
    ("recent_decisions", "working_context"), ("strong_beliefs", "epistemic_snapshot"),
    ("uncertainties", "epistemic_snapshot"),
    ("contradictions_flagged", "epistemic_snapshot"),
])
def test_misplaced_child_is_rejected_naming_it(key, block):
    p = good()
    p[key] = []
    assert field_table.partial_reason(p) == f"{key} is outside {block}"


def test_chunk_partial_missing_epistemic_snapshot_merges_end_to_end(monkeypatch):
    _chunked(monkeypatch)
    ms = make_messages(24)
    n_chunks = len(serializer.chunk_transcript(serializer._render_transcript(ms), 4, 0))

    def fn(n, content):
        if n == 1:   # chunk 1 omits the block entirely
            return {"working_context": good()["working_context"], "worker_queue": []}
        return good()

    chat = Script(fn)
    ckpt = serializer.serialize_strict("S1", ms, chat=chat)
    assert serializer.validation_reason(ckpt) is None
    assert len(chat.calls) == n_chunks + 1    # no re-ask, one merge
