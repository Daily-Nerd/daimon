import json
from pathlib import Path

import pytest

from daimon_briefing import serializer
from tests.conftest import make_messages

_REPO = Path(__file__).resolve().parents[2]
_DOC_01B = _REPO / "research/experiments/track-a/prompts/01b-serialize-d007.md"


def _valid_checkpoint_json(session_id="S1"):
    return json.dumps(
        {
            "session_id": session_id,
            "working_context": {
                "active_topic": {"text": "topic", "trust": "inferred"},
                "open_questions": [{"text": "q", "trust": "inferred"}],
                "recent_decisions": [
                    # Quote is present in the make_messages() transcript these
                    # tests render, so #125 verify_quotes keeps it verbatim
                    # rather than downgrading an unpinned quote to inferred.
                    {"text": "d", "trust": "verbatim", "quote": "line 3 from assistant"}
                ],
            },
            "epistemic_snapshot": {
                "strong_beliefs": [],
                "uncertainties": [],
                "contradictions_flagged": [],
            },
            "worker_queue": [],
        }
    )


def test_serialize_happy_path(fake_chat_factory):
    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    messages = make_messages(20)
    ckpt = serializer.serialize("S1", messages, chat=chat)
    assert ckpt is not None
    assert ckpt["session_id"] == "S1"
    assert ckpt["working_context"]["recent_decisions"][0]["trust"] == "verbatim"
    # session_id is forced to the real one regardless of what the model emitted.
    chat2 = fake_chat_factory(_valid_checkpoint_json("WRONG"))
    ckpt2 = serializer.serialize("S-real", make_messages(20), chat=chat2)
    assert ckpt2["session_id"] == "S-real"


def test_serialize_handles_json_fences(fake_chat_factory):
    fenced = "```json\n" + _valid_checkpoint_json("S1") + "\n```"
    chat = fake_chat_factory(fenced)
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat)
    assert ckpt["session_id"] == "S1"


def test_serialize_garbage_json_returns_none(fake_chat_factory):
    chat = fake_chat_factory("I'm sorry, I cannot do that. Here is some prose instead.")
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat)
    assert ckpt is None


def test_serialize_missing_required_keys_returns_none(fake_chat_factory):
    chat = fake_chat_factory(json.dumps({"session_id": "S1"}))  # no working_context
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat)
    assert ckpt is None


def test_serialize_bad_trust_class_returns_none(fake_chat_factory):
    bad = json.loads(_valid_checkpoint_json("S1"))
    bad["working_context"]["recent_decisions"][0]["trust"] = "totally-made-up"
    chat = fake_chat_factory(json.dumps(bad))
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat)
    assert ckpt is None


def test_serialize_verbatim_without_quote_returns_none(fake_chat_factory):
    bad = json.loads(_valid_checkpoint_json("S1"))
    bad["working_context"]["recent_decisions"][0] = {"text": "d", "trust": "verbatim"}
    chat = fake_chat_factory(json.dumps(bad))
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat)
    assert ckpt is None


def test_serialize_too_short_skips(fake_chat_factory):
    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    ckpt = serializer.serialize("S1", make_messages(4), chat=chat)
    assert ckpt is None
    assert chat.calls == []  # never called the LLM


def test_serialize_min_messages_configurable(fake_chat_factory, monkeypatch):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    ckpt = serializer.serialize("S1", make_messages(3), chat=chat)
    assert ckpt is not None
    assert len(chat.calls) == 1


def test_serialize_empty_messages_skips(fake_chat_factory):
    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    assert serializer.serialize("S1", [], chat=chat) is None
    assert chat.calls == []


def test_serialize_chat_raises_returns_none(fake_chat_factory):
    chat = fake_chat_factory(RuntimeError("LLM exploded"))
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat)
    assert ckpt is None


def test_serialize_verbatim_whitespace_quote_returns_none(fake_chat_factory):
    bad = json.loads(_valid_checkpoint_json("S1"))
    bad["working_context"]["recent_decisions"][0] = {
        "text": "d", "trust": "verbatim", "quote": "   \n  "
    }
    chat = fake_chat_factory(json.dumps(bad))
    assert serializer.serialize("S1", make_messages(20), chat=chat) is None


def test_validate_allows_empty_active_topic_text():
    # Contract: active_topic MAY have empty text (a session without a clear topic).
    # validate() accepts it; briefing.render() skips it (see test_briefing).
    ckpt = json.loads(_valid_checkpoint_json("S1"))
    ckpt["working_context"]["active_topic"] = {"text": "", "trust": "inferred"}
    assert serializer.validate(ckpt) is True


def test_serialize_deadline_already_past_skips(fake_chat_factory):
    import time

    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    ckpt = serializer.serialize(
        "S1", make_messages(20), chat=chat, deadline=time.monotonic() - 1
    )
    assert ckpt is None
    assert chat.calls == []  # never called the LLM


def test_serialize_forwards_deadline_to_chat(fake_chat_factory):
    import time

    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    deadline = time.monotonic() + 60
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat, deadline=deadline)
    assert ckpt is not None
    assert chat.calls[0]["kwargs"].get("deadline") == deadline


# --- Slice 2: chunked multi-pass extraction (armC) -------------------------


def test_chunk_transcript_splits_with_overlap():
    text = "\n".join(f"line {i}" for i in range(25))
    chunks = serializer.chunk_transcript(text, chunk_lines=10, overlap_lines=2)
    assert len(chunks) > 1
    assert all(len(c.splitlines()) <= 10 for c in chunks)
    # consecutive chunks share the overlap region
    assert chunks[0].splitlines()[-2:] == chunks[1].splitlines()[:2]
    # every original line appears somewhere
    joined = "\n".join(chunks)
    assert all(f"line {i}" in joined for i in range(25))


def test_chunk_transcript_short_text_single_chunk():
    text = "\n".join(f"line {i}" for i in range(5))
    assert serializer.chunk_transcript(text, chunk_lines=10, overlap_lines=2) == [text]


def test_serialize_short_session_stays_single_pass(fake_chat_factory, monkeypatch):
    monkeypatch.setenv("DAIMON_CHUNK_LINES", "500")
    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat)
    assert ckpt is not None
    assert len(chat.calls) == 1  # no chunk fan-out, no merge call


def test_serialize_long_session_chunks_then_merges(fake_chat_factory, monkeypatch):
    monkeypatch.setenv("DAIMON_CHUNK_LINES", "6")
    monkeypatch.setenv("DAIMON_CHUNK_OVERLAP", "1")
    # Pin K above n_chunks so this test stays single-level — it verifies the merge
    # call format, not the hierarchical fan-out depth (covered by the #28 tests).
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "100")
    messages = make_messages(20)
    rendered = serializer._render_transcript(messages)
    n_chunks = len(serializer.chunk_transcript(rendered, 6, 1))
    assert n_chunks > 1  # precondition: this input really fans out

    merged = json.loads(_valid_checkpoint_json("S1"))
    merged["working_context"]["active_topic"]["text"] = "MERGED"
    responses = [_valid_checkpoint_json(f"chunk{i}") for i in range(n_chunks)]
    responses.append(json.dumps(merged))
    chat = fake_chat_factory(responses)

    ckpt = serializer.serialize("S-long", messages, chat=chat)
    assert ckpt is not None
    assert len(chat.calls) == n_chunks + 1
    # final result comes from the merge pass, session_id forced
    assert ckpt["working_context"]["active_topic"]["text"] == "MERGED"
    assert ckpt["session_id"] == "S-long"
    # last call is the merge: merge system prompt + partials as a JSON array
    merge_call = chat.calls[-1]
    assert merge_call["messages"][0]["content"] == serializer.MERGE_SYS
    assert "PARTIAL CHECKPOINTS" in merge_call["messages"][1]["content"]
    # chunk calls use the D-007 serialize prompt
    assert all(c["messages"][0]["content"] == serializer.SERIALIZE_SYS
               for c in chat.calls[:-1])


def test_serialize_chunked_forwards_deadline_to_every_call(fake_chat_factory, monkeypatch):
    import time

    monkeypatch.setenv("DAIMON_CHUNK_LINES", "6")
    monkeypatch.setenv("DAIMON_CHUNK_OVERLAP", "1")
    # Pin K above n_chunks — deadline forwarding is verified per-call regardless of
    # hierarchy depth; this keeps the response script predictable (n_chunks + 1).
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "100")
    messages = make_messages(20)
    rendered = serializer._render_transcript(messages)
    n_chunks = len(serializer.chunk_transcript(rendered, 6, 1))
    responses = [_valid_checkpoint_json(f"c{i}") for i in range(n_chunks)]
    responses.append(_valid_checkpoint_json("merged"))
    chat = fake_chat_factory(responses)
    deadline = time.monotonic() + 600

    ckpt = serializer.serialize("S1", messages, chat=chat, deadline=deadline)
    assert ckpt is not None
    assert all(c["kwargs"].get("deadline") == deadline for c in chat.calls)


def test_merge_prompt_carries_qstale_and_external_state():
    # Q-STALE (findings/03): merge must prefer the LATEST state of an evolving
    # fact; external_state flags must survive the merge.
    sys_lower = serializer.MERGE_SYS.lower()
    assert "latest" in sys_lower
    assert "external_state" in serializer.MERGE_SYS


def test_serialize_chunked_runs_chunks_concurrently(monkeypatch):
    # Gateway calls are ~minutes each and generation-bound; sequential chunking
    # makes long-session serialize unusable. Chunks are independent -> concurrent.
    import threading
    import time as _time

    monkeypatch.setenv("DAIMON_CHUNK_LINES", "6")
    monkeypatch.setenv("DAIMON_CHUNK_OVERLAP", "1")
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "8")
    # Pin K above n_chunks — concurrency is exercised by the chunk fan-out; the
    # merge-call count assertion (n_chunks + 1) requires single-level merge here.
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "100")
    messages = make_messages(20)
    rendered = serializer._render_transcript(messages)
    n_chunks = len(serializer.chunk_transcript(rendered, 6, 1))
    assert n_chunks >= 3

    lock = threading.Lock()
    calls = []

    def slow_chat(chat_messages, **kwargs):
        with lock:
            calls.append(chat_messages)
        _time.sleep(0.25)
        return _valid_checkpoint_json("S1")

    t0 = _time.monotonic()
    ckpt = serializer.serialize_strict("S1", messages, chat=slow_chat)
    elapsed = _time.monotonic() - t0
    assert ckpt is not None
    assert len(calls) == n_chunks + 1
    # sequential would be (n_chunks + 1) * 0.25; concurrent ~= 2 * 0.25
    assert elapsed < (n_chunks + 1) * 0.25 - 0.1


def test_serialize_chunked_partials_keep_chunk_order(monkeypatch):
    # Merge input must stay in chronological chunk order even with concurrency.
    # The fake derives its response from the request (chunk number in the user
    # content), so the assertion is independent of thread scheduling.
    import re
    import threading

    monkeypatch.setenv("DAIMON_CHUNK_LINES", "6")
    monkeypatch.setenv("DAIMON_CHUNK_OVERLAP", "1")
    # Pin K above n_chunks — order is asserted at the single flat merge call; the
    # hierarchical fan-out order is covered by the #28 tests.
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "100")
    messages = make_messages(20)
    rendered = serializer._render_transcript(messages)
    n_chunks = len(serializer.chunk_transcript(rendered, 6, 1))

    lock = threading.Lock()
    merge_users = []

    def chat(chat_messages, **kwargs):
        user = chat_messages[1]["content"]
        m = re.search(r"chunk (\d+) of", user)
        if m:
            return _valid_checkpoint_json(f"part-{m.group(1)}")
        with lock:
            merge_users.append(user)
        return _valid_checkpoint_json("merged")

    serializer.serialize_strict("S1", messages, chat=chat)
    assert len(merge_users) == 1
    merge_user = merge_users[0]
    positions = [merge_user.index(f'part-{i + 1}"') for i in range(n_chunks)]
    assert positions == sorted(positions)


# --- Slice 2: named failure reasons (serialize_strict) ----------------------


def test_strict_too_short_raises_named_error(fake_chat_factory):
    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    with pytest.raises(serializer.TooShortError):
        serializer.serialize_strict("S1", make_messages(4), chat=chat)
    assert chat.calls == []


def test_strict_llm_failure_raises_named_error(fake_chat_factory):
    chat = fake_chat_factory(RuntimeError("gateway timeout"))
    with pytest.raises(serializer.LLMCallError):
        serializer.serialize_strict("S1", make_messages(20), chat=chat)


def test_strict_garbage_output_raises_named_error(fake_chat_factory):
    chat = fake_chat_factory("prose, not JSON")
    with pytest.raises(serializer.OutputParseError):
        serializer.serialize_strict("S1", make_messages(20), chat=chat)


def test_strict_schema_violation_raises_named_error(fake_chat_factory):
    bad = json.loads(_valid_checkpoint_json("S1"))
    bad["working_context"]["recent_decisions"][0] = {"text": "d", "trust": "verbatim"}
    chat = fake_chat_factory(json.dumps(bad))
    with pytest.raises(serializer.SchemaValidationError):
        serializer.serialize_strict("S1", make_messages(20), chat=chat)


def test_strict_chunked_bad_chunk_names_the_chunk(fake_chat_factory, monkeypatch):
    monkeypatch.setenv("DAIMON_CHUNK_LINES", "6")
    monkeypatch.setenv("DAIMON_CHUNK_OVERLAP", "1")
    # Sequential: with concurrency >1, workers past chunk 2 race ahead and
    # exhaust FakeChat's scripted responses before the parse error lands (flaky).
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "1")
    messages = make_messages(20)
    # Two garbage responses: a single bad parse is retried once (parse_retries=1),
    # so the failure must persist through the retry to surface as OutputParseError.
    responses = [_valid_checkpoint_json("c0"), "garbage not json", "garbage not json"]
    chat = fake_chat_factory(responses)
    with pytest.raises(serializer.OutputParseError, match="chunk 2"):
        serializer.serialize_strict("S1", messages, chat=chat)


def test_lenient_serialize_still_returns_none_on_strict_errors(fake_chat_factory):
    # The hermes hook contract: serialize() never raises.
    chat = fake_chat_factory("prose, not JSON")
    assert serializer.serialize("S1", make_messages(20), chat=chat) is None


def test_serialize_sends_configured_temperature_through_real_client(monkeypatch):
    # End-to-end through llm.chat: no temperature pinned at the call site, so
    # DAIMON_LLM_TEMPERATURE must flow into the request body.
    import io
    import urllib.request

    monkeypatch.setenv("DAIMON_LLM_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("DAIMON_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("DAIMON_LLM_MODEL", "test-model")
    monkeypatch.setenv("DAIMON_LLM_TEMPERATURE", "1")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        payload = {"choices": [{"message": {"content": _valid_checkpoint_json("S1")}}]}
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    ckpt = serializer.serialize("S1", make_messages(20))
    assert ckpt is not None
    assert captured["body"]["temperature"] == 1.0


# --- Issue #28: hierarchical merge ----------------------------------------


def _partial(tag):
    """Valid checkpoint JSON whose active_topic.text is distinguishable by tag."""
    return json.dumps(
        {
            "session_id": "S1",
            "working_context": {
                "active_topic": {"text": f"topic-{tag}", "trust": "inferred"},
                "open_questions": [{"text": f"q-{tag}", "trust": "inferred"}],
                "recent_decisions": [
                    {"text": f"d-{tag}", "trust": "verbatim", "quote": f"quote-{tag}"}
                ],
            },
            "epistemic_snapshot": {
                "strong_beliefs": [],
                "uncertainties": [],
                "contradictions_flagged": [],
            },
            "worker_queue": [],
        }
    )


def test_hierarchical_merge_9_chunks_k3(fake_chat_factory, monkeypatch):
    # 9 chunks, K=3: 9 chunk calls + 3 level-1 merges + 1 level-2 merge = 13 total.
    # chunk_lines=7, overlap=3 yields exactly 9 chunks from make_messages(20).
    # Forced sequential (DAIMON_CHUNK_CONCURRENCY=1) so FakeChat call indices are
    # deterministic: calls 0..8 = chunks 1..9; calls 9..11 = level-1 merges;
    # call 12 = level-2 merge.
    monkeypatch.setenv("DAIMON_CHUNK_LINES", "7")
    monkeypatch.setenv("DAIMON_CHUNK_OVERLAP", "3")
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "1")
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "3")

    messages = make_messages(20)
    rendered = serializer._render_transcript(messages)
    chunks = serializer.chunk_transcript(rendered, 7, 3)
    assert len(chunks) == 9, f"precondition: expected 9 chunks, got {len(chunks)}"

    # Scripted responses: 9 chunk partials + 3 level-1 merge results + 1 level-2 result.
    chunk_responses = [_partial(f"chunk{i + 1}") for i in range(9)]
    level1_responses = [_partial(f"merge1-{g + 1}") for g in range(3)]
    level2_response = _partial("final")
    responses = chunk_responses + level1_responses + [level2_response]
    chat = fake_chat_factory(responses)

    ckpt = serializer.serialize_strict("S1", messages, chat=chat)
    assert ckpt is not None
    assert len(chat.calls) == 13  # 9 chunk + 3 level-1 + 1 level-2

    # All 4 merge calls (indices 9..12) must use MERGE_SYS, not SERIALIZE_SYS.
    for idx in range(9, 13):
        assert chat.calls[idx]["messages"][0]["content"] == serializer.MERGE_SYS, (
            f"call {idx} should use MERGE_SYS"
        )

    # Level-1 merge call 0 (index 9): payload must contain partials 1-3 in order.
    l1g1_user = chat.calls[9]["messages"][1]["content"]
    assert "PARTIAL CHECKPOINTS" in l1g1_user
    payload1 = json.loads(l1g1_user.split("PARTIAL CHECKPOINTS (JSON array, one per chunk, in chronological order):\n", 1)[1])
    assert [p["working_context"]["active_topic"]["text"] for p in payload1] == [
        "topic-chunk1", "topic-chunk2", "topic-chunk3"
    ]

    # Level-1 merge call 1 (index 10): partials 4-6.
    l1g2_user = chat.calls[10]["messages"][1]["content"]
    payload2 = json.loads(l1g2_user.split("PARTIAL CHECKPOINTS (JSON array, one per chunk, in chronological order):\n", 1)[1])
    assert [p["working_context"]["active_topic"]["text"] for p in payload2] == [
        "topic-chunk4", "topic-chunk5", "topic-chunk6"
    ]

    # Level-1 merge call 2 (index 11): partials 7-9.
    l1g3_user = chat.calls[11]["messages"][1]["content"]
    payload3 = json.loads(l1g3_user.split("PARTIAL CHECKPOINTS (JSON array, one per chunk, in chronological order):\n", 1)[1])
    assert [p["working_context"]["active_topic"]["text"] for p in payload3] == [
        "topic-chunk7", "topic-chunk8", "topic-chunk9"
    ]

    # Final checkpoint comes from the level-2 merge response.
    assert ckpt["working_context"]["active_topic"]["text"] == "topic-final"


def test_hierarchical_merge_7_chunks_singleton_passthrough(fake_chat_factory, monkeypatch):
    # 7 chunks, K=3: groups [3,3,1] — singleton passes through WITHOUT an LLM call.
    # chunk_lines=7, overlap=1 yields exactly 7 chunks from make_messages(20).
    # Total calls: 7 chunk + 2 level-1 merges + 1 level-2 merge = 10.
    monkeypatch.setenv("DAIMON_CHUNK_LINES", "7")
    monkeypatch.setenv("DAIMON_CHUNK_OVERLAP", "1")
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "1")
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "3")

    messages = make_messages(20)
    rendered = serializer._render_transcript(messages)
    chunks = serializer.chunk_transcript(rendered, 7, 1)
    assert len(chunks) == 7, f"precondition: expected 7 chunks, got {len(chunks)}"

    # 7 chunk partials + 2 level-1 merges + 1 level-2 merge = 10 scripted responses.
    chunk_responses = [_partial(f"chunk{i + 1}") for i in range(7)]
    level1_responses = [_partial(f"merge1-{g + 1}") for g in range(2)]
    level2_response = _partial("final")
    responses = chunk_responses + level1_responses + [level2_response]
    chat = fake_chat_factory(responses)

    ckpt = serializer.serialize_strict("S1", messages, chat=chat)
    assert ckpt is not None
    assert len(chat.calls) == 10  # singleton group never triggers an LLM call

    # Level-2 merge (index 9): its payload must contain 3 elements:
    # merge1-1 result, merge1-2 result, and chunk7 verbatim (never re-merged).
    l2_user = chat.calls[9]["messages"][1]["content"]
    payload = json.loads(l2_user.split("PARTIAL CHECKPOINTS (JSON array, one per chunk, in chronological order):\n", 1)[1])
    assert len(payload) == 3
    assert payload[0]["working_context"]["active_topic"]["text"] == "topic-merge1-1"
    assert payload[1]["working_context"]["active_topic"]["text"] == "topic-merge1-2"
    # Third element is chunk7's partial verbatim — the singleton was passed through.
    assert payload[2]["working_context"]["active_topic"]["text"] == "topic-chunk7"


def test_hierarchical_merge_failure_names_level_and_group(fake_chat_factory, monkeypatch):
    # A level-1 group merge failure must name level and group in LLMCallError.
    monkeypatch.setenv("DAIMON_CHUNK_LINES", "7")
    monkeypatch.setenv("DAIMON_CHUNK_OVERLAP", "3")
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "1")
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "3")

    messages = make_messages(20)
    rendered = serializer._render_transcript(messages)
    chunks = serializer.chunk_transcript(rendered, 7, 3)
    assert len(chunks) == 9, f"precondition: expected 9 chunks, got {len(chunks)}"

    # 9 chunk partials succeed; level-1 group 2 (index 10) raises.
    chunk_responses = [_partial(f"chunk{i + 1}") for i in range(9)]
    level1_g1_response = _partial("merge1-1")
    responses = chunk_responses + [level1_g1_response, RuntimeError("gateway timeout")]
    chat = fake_chat_factory(responses)

    with pytest.raises(serializer.LLMCallError, match=r"merge level 1, group 2"):
        serializer.serialize_strict("S1", messages, chat=chat)


# --- Parse-failure retry (empty 200s from reasoning gateways) ---------------


def test_serialize_retries_unparseable_output_once(fake_chat_factory):
    # Gateways intermittently return HTTP 200 with empty content (observed:
    # kimi-k2.6 at temperature=1); one re-call clears it. The retry MUST NOT be
    # byte-identical: gateway response caches replay the same garbage for an
    # identical request (H1 attempt 5 — the retry got the cached empty body
    # back in <1s). A per-attempt marker makes each retry a distinct request.
    chat = fake_chat_factory(["", _valid_checkpoint_json("S1")])
    ckpt = serializer.serialize_strict("S1", make_messages(20), chat=chat)
    assert ckpt is not None
    assert len(chat.calls) == 2  # first parse failed, single retry succeeded
    first_user = chat.calls[0]["messages"][1]["content"]
    retry_user = chat.calls[1]["messages"][1]["content"]
    assert retry_user != first_user  # cache-buster: never byte-identical
    assert "retry attempt" not in first_user  # first attempt is pristine
    assert "retry attempt 2" in retry_user
    # Original content fully preserved — the marker is appended, not edited in.
    assert retry_user.startswith(first_user)


def test_serialize_first_try_success_has_no_retry_marker(fake_chat_factory):
    # Regression guard: the cache-buster must never leak into a healthy call.
    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    ckpt = serializer.serialize_strict("S1", make_messages(20), chat=chat)
    assert ckpt is not None
    assert len(chat.calls) == 1
    assert "retry attempt" not in chat.calls[0]["messages"][1]["content"]


def test_serialize_persistent_prose_raises_after_two_attempts(fake_chat_factory):
    # Non-list FakeChat response: every call (initial + retry) gets the same prose.
    chat = fake_chat_factory("still prose, not JSON")
    with pytest.raises(
        serializer.OutputParseError, match=r"transcript after 2 attempts"
    ):
        serializer.serialize_strict("S1", make_messages(20), chat=chat)
    assert len(chat.calls) == 2  # 1 + parse_retries(=1), then give up
    # Cache-buster applies on the failure path too — the two requests differ.
    assert (chat.calls[0]["messages"][1]["content"]
            != chat.calls[1]["messages"][1]["content"])


def test_serialize_parse_retry_skipped_when_deadline_exhausted():
    # The retry must not burn a dead deadline: if the budget ran out during the
    # first call, raise immediately instead of re-calling.
    import time

    calls = []

    def slow_prose_chat(chat_messages, **kwargs):
        calls.append(chat_messages)
        time.sleep(0.15)
        return "prose, not JSON"

    deadline = time.monotonic() + 0.05  # alive at entry, dead when parse fails
    with pytest.raises(serializer.OutputParseError):
        serializer.serialize_strict(
            "S1", make_messages(20), chat=slow_prose_chat, deadline=deadline
        )
    assert len(calls) == 1  # no second call once the deadline is gone


# --- #225: command-backend empty output retries like an empty 200 body ------


def test_call_and_parse_retries_empty_output_error_once(fake_chat_factory, caplog):
    # rc=0 + empty stdout raises llm.EmptyOutputError instead of parsing to
    # nothing — it must get the SAME cache-buster retry treatment as an
    # unparseable HTTP response, not an immediate LLMCallError.
    import logging

    from daimon_briefing import llm

    chat = fake_chat_factory([llm.EmptyOutputError("command backend returned empty "
                                                     "output (stderr: /tmp/x.log)"),
                              _valid_checkpoint_json("S1")])
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.serializer"):
        ckpt = serializer.serialize_strict("S1", make_messages(20), chat=chat)
    assert ckpt is not None
    assert len(chat.calls) == 2
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "empty output" in msg
    assert "retrying with cache-buster" in msg
    first_user = chat.calls[0]["messages"][1]["content"]
    retry_user = chat.calls[1]["messages"][1]["content"]
    assert "retry attempt 2" in retry_user
    assert retry_user.startswith(first_user)


def test_call_and_parse_empty_output_error_every_attempt_raises_llm_call_error(fake_chat_factory):
    from daimon_briefing import llm

    err = llm.EmptyOutputError("command backend returned empty output (stderr: "
                               "/tmp/x.log)")
    chat = fake_chat_factory([err, err])
    with pytest.raises(serializer.LLMCallError,
                       match="command backend returned empty output"):
        serializer.serialize_strict("S1", make_messages(20), chat=chat)
    assert len(chat.calls) == 2  # 1 + parse_retries(=1), then give up


def test_call_and_parse_plain_chat_error_still_fails_immediately(fake_chat_factory):
    # Regression guard: non-empty-output ChatErrors (transport failures) are
    # chat()'s own retry domain — _call_and_parse must NOT retry them.
    from daimon_briefing import llm

    chat = fake_chat_factory(llm.ChatError("command backend exited 1 (stderr: /tmp/x.log)"))
    with pytest.raises(serializer.LLMCallError):
        serializer.serialize_strict("S1", make_messages(20), chat=chat)
    assert len(chat.calls) == 1  # no retry regression


def test_call_and_parse_empty_output_error_skipped_when_deadline_exhausted():
    # Same guard as the parse-retry path: a dead deadline must not burn a
    # second call even though EmptyOutputError is otherwise retryable.
    import time

    from daimon_briefing import llm

    calls = []

    def slow_empty_chat(chat_messages, **kwargs):
        calls.append(chat_messages)
        time.sleep(0.15)
        raise llm.EmptyOutputError("command backend returned empty output "
                                   "(stderr: /tmp/x.log)")

    deadline = time.monotonic() + 0.05  # alive at entry, dead when the call returns
    with pytest.raises(serializer.LLMCallError,
                       match="command backend returned empty output"):
        serializer.serialize_strict(
            "S1", make_messages(20), chat=slow_empty_chat, deadline=deadline
        )
    assert len(calls) == 1  # no second call once the deadline is gone


# --- Live progress logging (40-min runs need a heartbeat) --------------------


def test_parse_retry_logs_warning_without_body(fake_chat_factory, caplog):
    # A retried parse must be visible (a doomed run looks healthy otherwise),
    # but model output must NEVER reach the log (it can echo request contents).
    import logging

    chat = fake_chat_factory(
        ["SENTINEL-BODY prose, not JSON", _valid_checkpoint_json("S1")]
    )
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.serializer"):
        ckpt = serializer.serialize_strict("S1", make_messages(20), chat=chat)
    assert ckpt is not None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "retrying" in msg
    assert "transcript" in msg  # the what-label for failure attribution
    assert all("SENTINEL-BODY" not in r.getMessage() for r in caplog.records)


def test_serialize_prompt_has_d008_fidelity_rules():
    sys = serializer.SERIALIZE_SYS
    assert "FINAL-STATE RESOLUTION" in sys
    assert "DISTINCT ITEMS" in sys
    assert "EXACT QUANTITIES & IDENTIFIERS" in sys
    # anti-confab guard must survive the fidelity push
    assert "Do NOT invent a resolution" in sys
    assert "Omission is safer than fabrication" in sys


def test_serialize_prompt_has_quote_discipline():
    # #208: over half of real verbatim downgrades were light edits to the quote
    # (substituted quote glyphs, an added/dropped word, lists reflowed to prose)
    # or mid-quote elisions left unmarked. The copy-paste contract must be
    # explicit in the prompt.
    sys = serializer.SERIALIZE_SYS
    assert "QUOTE DISCIPLINE" in sys
    assert "COPY-PASTE" in sys
    assert "contiguous" in sys
    assert "mark the gap with `...`" in sys
    assert "never add or drop a word" in sys
    assert "Never stitch" in sys
    assert "a correct inferred beats a downgraded verbatim" in sys


def test_validation_retry_note_restates_copy_paste_contract(
    fake_chat_factory, monkeypatch
):
    # #208: the schema-validation retry is a second chance at quote fidelity
    # too — the nudge must restate exact copy-paste and `...`-marked elisions.
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    chat = fake_chat_factory([_invalid_checkpoint_json(), _valid_checkpoint_json("S1")])
    serializer.serialize_strict("S1", make_messages(6), chat=chat)
    second = chat.calls[1]["messages"][-1]["content"]
    assert "copy-pasted exactly" in second
    assert "elisions marked with `...`" in second


def test_prompt_version_is_d014():
    # D-008 -> D-010 (#101: emotional_valence dropped from the schema).
    # D-009 is taken by the host-adapter decision. D-010 -> D-011 (#126:
    # per-item importance added to the emitted schema). D-011 -> D-012 (#5:
    # transcript-language preservation rule). D-012 -> D-013 (#208: verbatim
    # quote copy-paste discipline rule). D-013 -> D-014 (#287: external-
    # artifact identifier rule). Pre-bump checkpoints firing the
    # format_version mismatch warning (#93) is DESIRED behavior.
    assert serializer.PROMPT_VERSION == "D-014"


def test_prompts_preserve_transcript_language():
    # #5: item text must stay in the transcript's language (Spanish sessions
    # produced English paraphrases while quotes stayed Spanish -> mixed-language
    # checkpoints break recall term-overlap and carry twin-dedup). Rule must be
    # in BOTH prompts — merge re-emits items and can translate-drift too.
    for sys in (serializer.SERIALIZE_SYS, serializer.MERGE_SYS):
        assert "same language as the transcript" in sys
        assert "Never translate quotes" in sys


def test_emotional_valence_absent_from_prompts():
    # #101: emotional_valence is killed — no rule, no schema example, in either
    # the serialize prompt or the merge prompt.
    assert "emotional_valence" not in serializer.SERIALIZE_SYS
    assert "emotional_valence" not in serializer.MERGE_SYS


def test_checkpoint_without_emotional_valence_validates(fake_chat_factory):
    # The schema no longer asks for emotional_valence; a checkpoint without it
    # must serialize/validate cleanly (it already did — pin it).
    ckpt = json.loads(_valid_checkpoint_json("S1"))
    assert "emotional_valence" not in ckpt["working_context"]
    assert serializer.validate(ckpt) is True


def test_chunked_serialize_logs_progress(fake_chat_factory, monkeypatch, caplog):
    # Same 9-chunk/K=3 setup as the hierarchical tests: 9 chunk calls, then
    # level 1 (3 groups) and level 2 (1 group). Each step must leave an INFO
    # heartbeat so a human can kill a bad multi-hour run early.
    import logging

    monkeypatch.setenv("DAIMON_CHUNK_LINES", "7")
    monkeypatch.setenv("DAIMON_CHUNK_OVERLAP", "3")
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "1")
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "3")
    messages = make_messages(20)

    responses = [_partial(f"chunk{i + 1}") for i in range(9)]
    responses += [_partial(f"merge1-{g + 1}") for g in range(3)]
    responses += [_partial("final")]
    chat = fake_chat_factory(responses)

    with caplog.at_level(logging.INFO, logger="daimon_briefing.serializer"):
        ckpt = serializer.serialize_strict("S1", messages, chat=chat)
    assert ckpt is not None

    msgs = [r.getMessage() for r in caplog.records]
    assert any("9 chunks" in m for m in msgs)  # chunked-path start
    chunk_done = [m for m in msgs if "chunk" in m and "done" in m]
    assert len(chunk_done) == 9
    assert any("merge level 1" in m and "3 group" in m for m in msgs)
    assert any("merge level 2" in m and "1 group" in m for m in msgs)
    merge_done = [m for m in msgs if "merge level" in m and "done" in m]
    assert len(merge_done) == 4  # 3 level-1 groups + 1 level-2 group


def test_01b_doc_in_sync_with_embedded_prompt():
    doc = _DOC_01B.read_text()
    for marker in ("EXTERNAL-STATE FLAG", "FINAL-STATE RESOLUTION", "DISTINCT ITEMS",
                   "EXACT QUANTITIES & IDENTIFIERS"):
        assert marker in doc, f"01b doc missing D-008 rule: {marker}"


_DOC_01C = _REPO / "research/experiments/track-a/prompts/01c-merge-checkpoints.md"

def test_merge_prompt_has_reconciliation_and_distinct_guard():
    sys = serializer.MERGE_SYS
    assert "FINAL-STATE RECONCILIATION ACROSS CHUNKS" in sys
    assert "does NOT un-settle an earlier decision" in sys
    assert "differ in SUBSTANCE" in sys  # dedup distinct-guard

def test_01c_doc_in_sync_with_embedded_merge():
    doc = _DOC_01C.read_text()
    for marker in ("SUPERSESSION", "FINAL-STATE RECONCILIATION ACROSS CHUNKS",
                   "differ in SUBSTANCE"):
        assert marker in doc, f"01c doc missing merge rule: {marker}"


def test_valid_item_accepts_wellformed_anchor():
    item = {"text": "t", "trust": "inferred",
            "anchored_to": {"qualified_name": "m.py::foo", "file": "m.py",
                            "symbol": "foo", "body_hash": "abc"}}
    assert serializer._valid_item(item) is True


def test_valid_item_accepts_absent_anchor():
    assert serializer._valid_item({"text": "t", "trust": "inferred"}) is True


def test_valid_item_rejects_malformed_anchor():
    item = {"text": "t", "trust": "inferred", "anchored_to": {"file": 123}}
    assert serializer._valid_item(item) is False


# ---- #134: a present-but-null (or non-str) text passed validation, reached
# disk, then crashed the briefing render on the next session ----


def test_valid_item_rejects_null_text():
    assert serializer._valid_item({"text": None, "trust": "inferred"}) is False


def test_valid_item_rejects_nonstr_text():
    assert serializer._valid_item({"text": 123, "trust": "inferred"}) is False


def test_valid_item_still_accepts_empty_text():
    # active_topic MAY have empty text (test_validate_allows_empty_active_topic_text)
    # — a str-but-empty text stays valid; only a non-str text is rejected.
    assert serializer._valid_item({"text": "", "trust": "inferred"}) is True


# ---- #118: validation-failure retry with attempt nonce ----


def _invalid_checkpoint_json(session_id="S1"):
    # The live failure shape: verbatim item with the quote inlined into text
    # and NO quote field — validate() must reject it.
    return json.dumps(
        {
            "session_id": session_id,
            "working_context": {
                "active_topic": {"text": "topic", "trust": "inferred"},
                "open_questions": [],
                "recent_decisions": [
                    {"text": "d ('exact words')", "trust": "verbatim"}
                ],
            },
            "epistemic_snapshot": {
                "strong_beliefs": [],
                "uncertainties": [],
                "contradictions_flagged": [],
            },
            "worker_queue": [],
        }
    )


def test_validation_failure_retries_once_with_nonce(fake_chat_factory, monkeypatch):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    chat = fake_chat_factory([_invalid_checkpoint_json(), _valid_checkpoint_json("S1")])
    out = serializer.serialize_strict("S1", make_messages(6), chat=chat)
    assert out["session_id"] == "S1"
    assert len(chat.calls) == 2
    first = chat.calls[0]["messages"][-1]["content"]
    second = chat.calls[1]["messages"][-1]["content"]
    assert first != second  # never byte-identical — gateway caches replay garbage
    assert "attempt 2" in second
    assert "quote" in second  # the reminder names the observed failure mode


def test_validation_failure_twice_raises(fake_chat_factory, monkeypatch):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    chat = fake_chat_factory([_invalid_checkpoint_json(), _invalid_checkpoint_json()])
    with pytest.raises(serializer.SchemaValidationError):
        serializer.serialize_strict("S1", make_messages(6), chat=chat)
    assert len(chat.calls) == 2  # bounded: exactly one retry


def test_validation_retry_chunked_redoes_merge_not_chunks(fake_chat_factory, monkeypatch):
    # Chunked path: chunk partials are fine; the MERGE produced the invalid
    # output. The retry must re-run the merge with the nonce, not re-serialize
    # every chunk (chunks are the expensive calls).
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_CHUNK_LINES", "4")
    monkeypatch.setenv("DAIMON_CHUNK_OVERLAP", "0")
    # Large K keeps the merge to ONE level (scar: merge call count is
    # hierarchical, chunks×levels — a second level would break the scripted
    # response count below).
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "100")
    msgs = make_messages(24)  # forces >1 chunk at 4 lines/chunk
    partial = _valid_checkpoint_json("S1")
    # Script: N chunk responses (valid), then invalid merge, then valid merge.
    from daimon_briefing import serializer as s
    text = s._render_transcript(msgs)
    n_chunks = len(s.chunk_transcript(text, 4, 0))
    assert n_chunks > 1
    chat = fake_chat_factory(
        [partial] * n_chunks + [_invalid_checkpoint_json(), _valid_checkpoint_json("S1")]
    )
    out = s.serialize_strict("S1", msgs, chat=chat)
    assert out["session_id"] == "S1"
    assert len(chat.calls) == n_chunks + 2  # chunks once, merge twice
    assert "attempt 2" in chat.calls[-1]["messages"][-1]["content"]


# ---- importance: LLM-scored 1-10 per item, sanitized never fatal (#126) ----


def test_prompts_request_importance():
    assert '"importance"' in serializer.SERIALIZE_SYS
    assert '"importance"' in serializer.MERGE_SYS


def _ckpt_with_importances(values):
    items = [
        {"text": f"q{i}", "trust": "inferred", "importance": v}
        for i, v in enumerate(values)
    ]
    return {
        "session_id": "S1",
        "working_context": {
            "active_topic": {"text": "topic", "trust": "inferred"},
            "open_questions": items,
            "recent_decisions": [],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [],
            "uncertainties": [],
            "contradictions_flagged": [],
        },
        "worker_queue": [],
    }


def test_sanitize_importance_keeps_valid_clamps_out_of_range():
    ckpt = _ckpt_with_importances([7, 0, 99])
    serializer.sanitize_importance(ckpt)
    got = [i.get("importance") for i in ckpt["working_context"]["open_questions"]]
    assert got == [7, 1, 10]


def test_sanitize_importance_drops_non_int_junk():
    ckpt = _ckpt_with_importances(["high", None, 7.5, True])
    serializer.sanitize_importance(ckpt)
    for item in ckpt["working_context"]["open_questions"]:
        assert "importance" not in item


def test_sanitize_importance_covers_active_topic_and_epistemic_lists():
    ckpt = _ckpt_with_importances([5])
    ckpt["working_context"]["active_topic"]["importance"] = 42
    ckpt["epistemic_snapshot"]["strong_beliefs"] = [
        {"text": "b", "trust": "inferred", "importance": "junk"}
    ]
    ckpt["epistemic_snapshot"]["uncertainties"] = [
        {"text": "u", "trust": "inferred", "importance": -3}
    ]
    serializer.sanitize_importance(ckpt)
    assert ckpt["working_context"]["active_topic"]["importance"] == 10
    assert "importance" not in ckpt["epistemic_snapshot"]["strong_beliefs"][0]
    assert ckpt["epistemic_snapshot"]["uncertainties"][0]["importance"] == 1


def test_serialize_strict_sanitizes_importance_never_fails_on_junk(fake_chat_factory):
    raw = json.loads(_valid_checkpoint_json("S1"))
    raw["working_context"]["open_questions"][0]["importance"] = "critical"
    raw["working_context"]["recent_decisions"][0]["importance"] = 9
    chat = fake_chat_factory(json.dumps(raw))
    ckpt = serializer.serialize_strict("S1", make_messages(20), chat=chat)
    wc = ckpt["working_context"]
    assert "importance" not in wc["open_questions"][0]
    assert wc["recent_decisions"][0]["importance"] == 9


def test_validate_tolerates_importance_field():
    ckpt = _ckpt_with_importances([3])
    assert serializer.validate(ckpt)


def test_recent_decisions_schema_carries_links_shape():
    # #14: recent_decisions items gain an optional links shape for typed
    # cross-references (v1 scope: supersedes only). Both prompts render the
    # same schema block, so both must show the new item shape.
    links_shape = '"links": [{"type": "", "target": ""}]'
    assert links_shape in serializer.SERIALIZE_SYS
    assert links_shape in serializer.MERGE_SYS


def test_extraction_rule_mentions_supersedes_conservatively():
    # Conservative extraction: only explicit replacement language earns a
    # supersedes link — never mere topic overlap between two decisions.
    sys_prompt = serializer.SERIALIZE_SYS
    assert "supersedes" in sys_prompt
    assert "instead of" in sys_prompt  # explicit-replacement language required
    assert "topic overlap" in sys_prompt  # anti-overreach guard


def test_merge_sys_preserves_links():
    # Merge must carry links through untouched, and union them (never drop
    # either side's) when two items collapse into one canonical item.
    sys_prompt = serializer.MERGE_SYS
    assert "LINKS PRESERVATION" in sys_prompt
    assert "verbatim" in sys_prompt
    assert "union" in sys_prompt.lower()


# ---- #230: llm_backend / llm_model provenance stamp ----


def test_serialize_stamps_backend_and_model_for_http_style_config(fake_chat_factory, monkeypatch):
    # auto backend + an API key resolves to "litellm" — same resolution
    # configure.resolved_backend() promises to mirror from llm.chat().
    monkeypatch.setenv("DAIMON_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("DAIMON_LLM_MODEL", "test-model")
    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat)
    assert ckpt is not None
    assert ckpt["llm_backend"] == "litellm"
    assert ckpt["llm_model"] == "test-model"


def test_serialize_leaves_model_absent_when_config_has_no_model_string(fake_chat_factory, monkeypatch):
    # No API key, no DAIMON_LLM_MODEL, but an explicit command resolves —
    # auto picks "command". The command backend's model (if any) lives in
    # the command string itself, not a config key the stamp can cheaply
    # read, so llm_model must be ABSENT — never guessed.
    monkeypatch.delenv("DAIMON_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DAIMON_LLM_MODEL", raising=False)
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "echo hi")
    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat)
    assert ckpt is not None
    assert ckpt["llm_backend"] == "command"
    assert "llm_model" not in ckpt


def test_serialize_overwrites_model_authored_llm_backend_field(fake_chat_factory, monkeypatch):
    # A checkpoint whose extracted JSON already carries an `llm_backend` key
    # (model-authored, whether hallucinated or adversarially spoofed) must
    # have it stomped by the resolved truth — direct assignment, not
    # setdefault (deliberate contrast with git_branch, #222).
    monkeypatch.setenv("DAIMON_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("DAIMON_LLM_MODEL", "test-model")
    spoofed = json.loads(_valid_checkpoint_json("S1"))
    spoofed["llm_backend"] = "totally-fake-backend-the-model-made-up"
    chat = fake_chat_factory(json.dumps(spoofed))
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat)
    assert ckpt is not None
    assert ckpt["llm_backend"] == "litellm"


def test_serialize_reserialize_after_backend_switch_stamps_fresh_values(fake_chat_factory, monkeypatch):
    # Heal-style re-serialize: a fresh LLM run recorded with whatever backend
    # resolves THIS TIME, not whatever an earlier attempt saw.
    monkeypatch.delenv("DAIMON_LLM_API_KEY", raising=False)
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "echo hi")
    chat1 = fake_chat_factory(_valid_checkpoint_json("S1"))
    first = serializer.serialize("S1", make_messages(20), chat=chat1)
    assert first["llm_backend"] == "command"

    monkeypatch.setenv("DAIMON_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("DAIMON_LLM_MODEL", "test-model")
    chat2 = fake_chat_factory(_valid_checkpoint_json("S1"))
    second = serializer.serialize("S1", make_messages(20), chat=chat2)
    assert second["llm_backend"] == "litellm"
    assert second["llm_model"] == "test-model"


def test_serialize_completes_when_backend_resolver_raises(fake_chat_factory, monkeypatch):
    # Fail-open: a broken resolver must never sink an otherwise-successful
    # serialize. Both provenance fields are simply left absent.
    from daimon_briefing import configure

    def _boom():
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(configure, "resolved_backend", _boom)
    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat)
    assert ckpt is not None
    assert "llm_backend" not in ckpt
    assert "llm_model" not in ckpt


def test_serialize_stamps_backend_when_model_lookup_raises(
        fake_chat_factory, monkeypatch):
    # Fail-open, per-field: config.llm_model() blowing up costs only the
    # model stamp — the resolved backend (already in hand) still lands.
    from daimon_briefing import config, configure

    monkeypatch.setattr(configure, "resolved_backend", lambda: "litellm")

    def _boom():
        raise RuntimeError("model lookup exploded")

    monkeypatch.setattr(config, "llm_model", _boom)
    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat)
    assert ckpt is not None
    assert ckpt["llm_backend"] == "litellm"
    assert "llm_model" not in ckpt


def test_serialize_prompt_has_external_artifact_identifier_rule():
    # #287: a briefing carried a fix perfectly but not the upstream repo's
    # name — "issue #5" without a repo is half a pointer a future session
    # cannot resolve. The serializer must pull an artifact's most specific
    # identifier from anywhere in the transcript into the item text, and
    # never invent one the transcript does not contain.
    sys = serializer.SERIALIZE_SYS
    assert "EXTERNAL ARTIFACT IDENTIFIERS" in sys
    assert "MOST SPECIFIC identifier" in sys
    assert "half a pointer" in sys
    assert "Never invent an identifier" in sys
