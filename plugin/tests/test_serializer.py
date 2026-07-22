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
    # #314: chunked serializes scale the deadline by the wave plan, so every
    # call carries the SAME deadline, at or beyond the caller's single-wave one.
    seen = {c["kwargs"].get("deadline") for c in chat.calls}
    assert len(seen) == 1
    assert seen.pop() >= deadline


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


def test_serialize_retry_marker_differs_across_runs(fake_chat_factory):
    # #312: gateway response caches replay a pinned bad response for a
    # byte-identical request. The per-attempt marker de-dupes retries WITHIN a
    # run, but attempt numbers restart on every invocation — so a re-heal sent
    # byte-identical retries and got the same cached garbage back in 0s,
    # forever. A per-run nonce makes every retry gateway-fresh.
    chat1 = fake_chat_factory(["", _valid_checkpoint_json("S1")])
    assert serializer.serialize_strict("S1", make_messages(20), chat=chat1) is not None
    chat2 = fake_chat_factory(["", _valid_checkpoint_json("S1")])
    assert serializer.serialize_strict("S1", make_messages(20), chat=chat2) is not None
    retry1 = chat1.calls[1]["messages"][1]["content"]
    retry2 = chat2.calls[1]["messages"][1]["content"]
    assert "retry attempt 2" in retry1 and "retry attempt 2" in retry2
    assert retry1 != retry2


def test_serialize_first_attempt_stays_byte_identical_across_runs(fake_chat_factory):
    # Deliberate scope limit on #312: attempt 1 carries NO nonce. Replaying a
    # COMPLETED good response from a gateway cache is a feature, not a bug —
    # it is exactly what healed a deadline-killed session in the field (the
    # chunks and merge replayed in 0s on the next heal). Busting attempt 1
    # would re-buy completed work on every heal (#314's amplification).
    chat1 = fake_chat_factory(_valid_checkpoint_json("S1"))
    assert serializer.serialize_strict("S1", make_messages(20), chat=chat1) is not None
    chat2 = fake_chat_factory(_valid_checkpoint_json("S1"))
    assert serializer.serialize_strict("S1", make_messages(20), chat=chat2) is not None
    assert (chat1.calls[0]["messages"][1]["content"]
            == chat2.calls[0]["messages"][1]["content"])


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


def test_prompt_version_is_d016():
    # D-008 -> D-010 (#101: emotional_valence dropped from the schema).
    # D-009 is taken by the host-adapter decision. D-010 -> D-011 (#126:
    # per-item importance added to the emitted schema). D-011 -> D-012 (#5:
    # transcript-language preservation rule). D-012 -> D-013 (#208: verbatim
    # quote copy-paste discipline rule). D-013 -> D-014 (#287: external-
    # artifact identifier rule). D-014 -> D-015 (#358: verbatim items bind
    # to source transcript message ids). D-015 -> D-016 (#359: outcome
    # claims ground in tool-result signals). Pre-bump checkpoints firing the
    # format_version mismatch warning (#93) is DESIRED behavior. The bump
    # also rotates the #48 chunk-cache key, so pre-#359 cached extractions
    # (no tool-result rows in their chunks) can never satisfy a post-#359
    # request.
    assert serializer.PROMPT_VERSION == "D-016"


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


# ---- #292: code-owned provenance keys stripped at the serializer boundary ----
#
# format_version/created/author/etc. are facts the CODE asserts about a
# checkpoint's origin — never data the extraction model should get a vote on.
# The serialize prompt never asks for these keys, but a transcript that
# happens to discuss daimon's own schema (this bug's field report: the
# transcript quoted daimon's own format-drift warning banner) can make the
# model emit one anyway. Nothing else on the write path catches it —
# `_valid_item` only validates item fields, and store.write_checkpoint's
# setdefault stamps defer to whatever key is already present, which cannot
# tell a fresh serialize from a legitimate re-write. Stripping right after
# parse — before session_id is even assigned — means every serialize()
# caller (cli, hooks) hands store a clean dict, so its setdefault stamps
# land on the code's own values.


@pytest.mark.parametrize("key,spoofed", [
    ("format_version", "D-999"),
    ("created", "1999-01-01T00:00:00Z"),
    ("author", "not-the-real-author"),
    ("transcript_hash", "deadbeef"),
    ("project_slug", "some-other-project"),
    ("git_branch", "not-the-real-branch"),
    ("receipts", "not-a-real-receipt-marker"),
])
def test_serialize_strips_model_supplied_code_owned_key(fake_chat_factory, key, spoofed):
    spoofed_ckpt = json.loads(_valid_checkpoint_json("S1"))
    spoofed_ckpt[key] = spoofed
    chat = fake_chat_factory(json.dumps(spoofed_ckpt))
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat)
    assert ckpt is not None
    assert key not in ckpt  # stripped, not carried through as the model's value


def test_serialize_strip_survives_the_validation_retry_pass(fake_chat_factory):
    # A spoofed format_version alongside output that FAILS validation forces
    # the #118 resample retry — the strip must apply to the retried output
    # too, not just the first attempt.
    bad = json.loads(_valid_checkpoint_json("S1"))
    bad["working_context"]["recent_decisions"][0] = {"text": "d", "trust": "verbatim"}  # no quote -> invalid
    bad["format_version"] = "D-999"
    good = json.loads(_valid_checkpoint_json("S1"))
    good["format_version"] = "D-999"
    chat = fake_chat_factory([json.dumps(bad), json.dumps(good)])
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat)
    assert ckpt is not None
    assert "format_version" not in ckpt


def test_regression_model_supplied_format_version_does_not_stamp_checkpoint(
        tmp_checkpoint_dir, fake_chat_factory):
    # The exact field report behind #292: a transcript quoting daimon's own
    # format-drift banner (which cites a format_version string) made the
    # model emit format_version in its extracted JSON. The checkpoint that
    # reaches disk must carry the CODE's PROMPT_VERSION, never a model-
    # supplied one — regardless of how plausible the spoofed value looks.
    from daimon_briefing import store

    spoofed_version = serializer.PROMPT_VERSION + "-bogus"
    spoofed = json.loads(_valid_checkpoint_json("S1"))
    spoofed["format_version"] = spoofed_version
    chat = fake_chat_factory(json.dumps(spoofed))
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat)
    assert ckpt is not None
    assert "format_version" not in ckpt
    path = store.write_checkpoint("S1", ckpt)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["format_version"] == serializer.PROMPT_VERSION
    assert on_disk["format_version"] != spoofed_version


# ---- #314: wave-plan budget scaling + persisted chunk partials ----


def test_plan_waves_single_chunk():
    assert serializer._plan_waves(1, workers=4, k=3) == 1


def test_plan_waves_two_chunks_one_merge():
    # 1 chunk wave (2 concurrent) + 1 merge wave = 2
    assert serializer._plan_waves(2, workers=4, k=3) == 2


def test_plan_waves_deep_tree():
    # 8 chunks, workers=4, K=3: chunk batches ceil(8/4)=2; merge L1 = 3 groups
    # (3,3,2) in 1 batch; L2 = 1 group in 1 batch → 4 waves total.
    assert serializer._plan_waves(8, workers=4, k=3) == 4


def test_plan_waves_singleton_group_is_free():
    # 4 chunks, K=3: L1 groups are (3,1) — the singleton passes through with
    # no LLM call, so L1 costs 1 batch, then L2 merges 2 → 1+1+1 = 3.
    assert serializer._plan_waves(4, workers=4, k=3) == 3


def test_chunked_serialize_scales_deadline_by_wave_plan(fake_chat_factory, monkeypatch):
    # #314: N chunks + merge levels shared ONE single-call budget, so the merge
    # always started starved on slow gateways. The deadline must scale with the
    # wave plan: here 2 chunks (concurrent, 1 wave) + 1 merge wave = 2 waves.
    import time as _t
    monkeypatch.setenv("DAIMON_CHUNK_LINES", "6")
    monkeypatch.setenv("DAIMON_CHUNK_OVERLAP", "1")
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "100")
    monkeypatch.setenv("DAIMON_TIMEOUT", "100")
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "1")
    messages = make_messages(20)
    rendered = serializer._render_transcript(messages)
    n_chunks = len(serializer.chunk_transcript(rendered, 6, 1))
    assert n_chunks > 1
    responses = [_valid_checkpoint_json(f"c{i}") for i in range(n_chunks)]
    responses.append(_valid_checkpoint_json("merged"))
    chat = fake_chat_factory(responses)
    given = _t.monotonic() + 100

    assert serializer.serialize("S1", messages, chat=chat, deadline=given) is not None
    seen = {c["kwargs"].get("deadline") for c in chat.calls}
    assert len(seen) == 1  # every call shares ONE scaled deadline
    scaled = seen.pop()
    # 2 waves × 100s budget: extended by ~100s beyond the given single-wave deadline.
    assert scaled >= given + 50


def test_single_pass_deadline_is_not_scaled(fake_chat_factory, monkeypatch):
    import time as _t
    monkeypatch.setenv("DAIMON_TIMEOUT", "100")
    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    given = _t.monotonic() + 100
    assert serializer.serialize("S1", make_messages(20), chat=chat, deadline=given) is not None
    assert chat.calls[0]["kwargs"].get("deadline") == given


def _force_two_chunks(monkeypatch):
    monkeypatch.setenv("DAIMON_CHUNK_LINES", "6")
    monkeypatch.setenv("DAIMON_CHUNK_OVERLAP", "1")
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "100")
    # FakeChat consumes its script by call order; concurrent chunks would draw
    # entries non-deterministically (the ChatError poison must hit the merge).
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "1")
    messages = make_messages(20)
    rendered = serializer._render_transcript(messages)
    n = len(serializer.chunk_transcript(rendered, 6, 1))
    assert n > 1
    return messages, n


# ---- #48: content-addressed chunk cache (replaces the #314 partials store) ----


def test_chunk_cache_key_changes_with_config_dimensions(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_MODEL", "m1")
    monkeypatch.delenv("DAIMON_SCENE_TRACES", raising=False)
    base = serializer._chunk_cache_key("chunk text")
    assert base == serializer._chunk_cache_key("chunk text")  # stable
    assert base != serializer._chunk_cache_key("other text")
    monkeypatch.setenv("DAIMON_LLM_MODEL", "m2")
    assert serializer._chunk_cache_key("chunk text") != base
    monkeypatch.setenv("DAIMON_LLM_MODEL", "m1")
    monkeypatch.setenv("DAIMON_LLM_TEMPERATURE", "0.7")
    assert serializer._chunk_cache_key("chunk text") != base
    monkeypatch.delenv("DAIMON_LLM_TEMPERATURE", raising=False)
    # scene flag reshapes the serialize prompt WITHOUT a PROMPT_VERSION bump —
    # the key hashes the actual prompt text, so it must move (#319 trap class).
    monkeypatch.setenv("DAIMON_SCENE_TRACES", "1")
    assert serializer._chunk_cache_key("chunk text") != base


def test_chunk_cache_key_survives_backend_resolution_failure(monkeypatch):
    # A raising resolved_backend() must never break key computation — the
    # cache degrades to an "unknown"-backend namespace, not an exception.
    from daimon_briefing import configure as _configure
    def boom():
        raise RuntimeError("no backend configured")
    monkeypatch.setattr(_configure, "resolved_backend", boom)
    key = serializer._chunk_cache_key("chunk text")
    assert key == serializer._chunk_cache_key("chunk text")
    assert len(key) == 32


def test_chunk_transcript_prefix_chunks_byte_stable_under_growth():
    # The property the whole cache rests on: full windows re-materialize
    # byte-identically when the transcript grows; only the tail changes.
    lines = [f"line {i}" for i in range(40)]
    grown = lines + [f"line {i}" for i in range(40, 64)]
    old_chunks = serializer.chunk_transcript("\n".join(lines), 6, 1)
    new_chunks = serializer.chunk_transcript("\n".join(grown), 6, 1)
    # every FULL window of the old run reappears identically in the new run
    full = [c for c in old_chunks if len(c.splitlines()) == 6]
    assert full and all(c in new_chunks for c in full)
    # threshold crossing: single-chunk fast path returns the raw text, which
    # differs from the chunked rendering of the same region — crossing pays once
    small = "\n".join(lines[:5])
    assert serializer.chunk_transcript(small, 6, 1) == [small]


def test_failed_merge_persists_chunks_and_reheal_reuses_them(
        fake_chat_factory, monkeypatch, tmp_checkpoint_dir):
    # #314 guarantee, survived migration: a merge death must not re-buy chunks.
    from daimon_briefing import llm as _llm
    messages, n = _force_two_chunks(monkeypatch)
    responses = [_valid_checkpoint_json(f"c{i}") for i in range(n)]
    responses.append(_llm.ChatError("gateway died at merge"))
    chat1 = fake_chat_factory(responses)
    with pytest.raises(serializer.LLMCallError):
        serializer.serialize_strict("S1", messages, chat=chat1)
    saved = list((tmp_checkpoint_dir / ".chunk-cache").glob("*.json"))
    assert len(saved) == n  # every completed chunk survived the merge death

    chat2 = fake_chat_factory([_valid_checkpoint_json("merged")])
    ckpt = serializer.serialize_strict("S1", messages, chat=chat2)
    assert ckpt is not None and ckpt["session_id"] == "S1"
    assert len(chat2.calls) == 1  # merge only — no chunk was re-bought


def test_chunk_cache_survives_success_and_transfers_across_sessions(
        fake_chat_factory, monkeypatch, tmp_checkpoint_dir):
    # #48 core: the cache persists after a fully successful serialize, and the
    # key binds chunk TEXT + config, not session_id — a resume fork or a
    # periodic re-serialize of the same content reuses every paid-for chunk.
    messages, n = _force_two_chunks(monkeypatch)
    chat1 = fake_chat_factory(
        [_valid_checkpoint_json(f"c{i}") for i in range(n)]
        + [_valid_checkpoint_json("merged")])
    assert serializer.serialize_strict("S1", messages, chat=chat1) is not None
    assert len(list((tmp_checkpoint_dir / ".chunk-cache").glob("*.json"))) == n

    chat2 = fake_chat_factory([_valid_checkpoint_json("merged2")])
    assert serializer.serialize_strict("S2", messages, chat=chat2) is not None
    assert len(chat2.calls) == 1  # merge only — S2 reused S1's chunks


def test_grown_transcript_pays_only_tail_chunks(
        fake_chat_factory, monkeypatch, tmp_checkpoint_dir):
    from daimon_briefing import llm as _llm  # noqa: F401 (parity with siblings)
    messages, n = _force_two_chunks(monkeypatch)
    chat1 = fake_chat_factory(
        [_valid_checkpoint_json(f"c{i}") for i in range(n)]
        + [_valid_checkpoint_json("merged")])
    assert serializer.serialize_strict("S1", messages, chat=chat1) is not None

    grown = messages + make_messages(20)
    rendered = serializer._render_transcript(grown)
    n2 = len(serializer.chunk_transcript(rendered, 6, 1))
    assert n2 > n
    chat2 = fake_chat_factory(
        [_valid_checkpoint_json(f"g{i}") for i in range(n2)]
        + [_valid_checkpoint_json("merged2")])
    assert serializer.serialize_strict("S1", grown, chat=chat2) is not None
    # full prefix windows hit the cache; only new/changed tail windows + merge pay
    paid = len(chat2.calls) - 1
    assert 0 < paid < n2


def test_fallback_poisoned_run_skips_cache_writes(
        fake_chat_factory, monkeypatch, tmp_checkpoint_dir):
    # #343 lesson product-side: once the weaker fallback backend fired, this
    # run's outputs must not be cached under the primary backend's key.
    from daimon_briefing import llm as _llm
    monkeypatch.setattr(_llm, "fallback_used", lambda: True)
    messages, n = _force_two_chunks(monkeypatch)
    chat = fake_chat_factory(
        [_valid_checkpoint_json(f"c{i}") for i in range(n)]
        + [_valid_checkpoint_json("merged")])
    assert serializer.serialize_strict("S1", messages, chat=chat) is not None
    assert not list((tmp_checkpoint_dir / ".chunk-cache").glob("*.json"))


def test_chunk_cache_kill_switch_no_reads_no_writes(
        fake_chat_factory, monkeypatch, tmp_checkpoint_dir):
    monkeypatch.setenv("DAIMON_CHUNK_CACHE", "0")
    messages, n = _force_two_chunks(monkeypatch)
    chat1 = fake_chat_factory(
        [_valid_checkpoint_json(f"c{i}") for i in range(n)]
        + [_valid_checkpoint_json("merged")])
    assert serializer.serialize_strict("S1", messages, chat=chat1) is not None
    assert not (tmp_checkpoint_dir / ".chunk-cache").exists()
    chat2 = fake_chat_factory(
        [_valid_checkpoint_json(f"c{i}") for i in range(n)]
        + [_valid_checkpoint_json("merged")])
    assert serializer.serialize_strict("S1", messages, chat=chat2) is not None
    assert len(chat2.calls) == n + 1  # no reads either


def test_llm_no_cache_skips_reads_but_still_writes(
        fake_chat_factory, monkeypatch, tmp_checkpoint_dir):
    # DAIMON_LLM_NO_CACHE means "no replayed LLM output" — honor the intent:
    # never serve a cached chunk, but caching THIS run's fresh output is fine.
    messages, n = _force_two_chunks(monkeypatch)
    chat1 = fake_chat_factory(
        [_valid_checkpoint_json(f"c{i}") for i in range(n)]
        + [_valid_checkpoint_json("merged")])
    assert serializer.serialize_strict("S1", messages, chat=chat1) is not None
    monkeypatch.setenv("DAIMON_LLM_NO_CACHE", "1")
    chat2 = fake_chat_factory(
        [_valid_checkpoint_json(f"c{i}") for i in range(n)]
        + [_valid_checkpoint_json("merged")])
    assert serializer.serialize_strict("S1", messages, chat=chat2) is not None
    assert len(chat2.calls) == n + 1  # cache reads bypassed


def test_corrupt_chunk_cache_entry_recomputed(
        fake_chat_factory, monkeypatch, tmp_checkpoint_dir):
    from daimon_briefing import llm as _llm
    messages, n = _force_two_chunks(monkeypatch)
    chat1 = fake_chat_factory(
        [_valid_checkpoint_json(f"c{i}") for i in range(n)]
        + [_llm.ChatError("merge died")])
    with pytest.raises(serializer.LLMCallError):
        serializer.serialize_strict("S1", messages, chat=chat1)
    for p in (tmp_checkpoint_dir / ".chunk-cache").glob("*.json"):
        p.write_text("not json{", encoding="utf-8")
    chat2 = fake_chat_factory(
        [_valid_checkpoint_json(f"c{i}") for i in range(n)]
        + [_valid_checkpoint_json("merged")])
    assert serializer.serialize_strict("S1", messages, chat=chat2) is not None
    assert len(chat2.calls) == n + 1  # corrupt entries recomputed, never fatal


def test_chunk_cache_reap_removes_aged_entries(
        fake_chat_factory, monkeypatch, tmp_checkpoint_dir):
    import os as _os
    import time as _t
    from daimon_briefing import config as _config
    from daimon_briefing import llm as _llm
    cdir = tmp_checkpoint_dir / ".chunk-cache"
    cdir.mkdir(parents=True)
    old = cdir / "deadbeef00.json"
    old.write_text("{}", encoding="utf-8")
    aged = _t.time() - _config.chunk_cache_days() * 24 * 3600 - 3600
    _os.utime(old, (aged, aged))
    messages, n = _force_two_chunks(monkeypatch)
    chat = fake_chat_factory(
        [_valid_checkpoint_json(f"c{i}") for i in range(n)]
        + [_llm.ChatError("merge died")])
    with pytest.raises(serializer.LLMCallError):
        serializer.serialize_strict("S1", messages, chat=chat)
    assert not old.exists()  # reaped by the first save


def test_chunk_cache_reap_survives_unremovable_entry(
        fake_chat_factory, monkeypatch, tmp_checkpoint_dir):
    import os as _os
    import time as _t
    from daimon_briefing import config as _config
    from daimon_briefing import llm as _llm
    cdir = tmp_checkpoint_dir / ".chunk-cache"
    (cdir / "stuck.json").mkdir(parents=True)
    aged = _t.time() - _config.chunk_cache_days() * 24 * 3600 - 3600
    _os.utime(cdir / "stuck.json", (aged, aged))
    messages, n = _force_two_chunks(monkeypatch)
    chat = fake_chat_factory(
        [_valid_checkpoint_json(f"c{i}") for i in range(n)]
        + [_llm.ChatError("merge died")])
    with pytest.raises(serializer.LLMCallError):
        serializer.serialize_strict("S1", messages, chat=chat)
    assert len(list(cdir.glob("*.json"))) == n + 1  # n saved + the stuck dir


def test_save_chunk_cache_survives_dir_being_a_file(
        fake_chat_factory, monkeypatch, tmp_checkpoint_dir):
    (tmp_checkpoint_dir / ".chunk-cache").parent.mkdir(parents=True, exist_ok=True)
    (tmp_checkpoint_dir / ".chunk-cache").write_text("not a dir", encoding="utf-8")
    messages, n = _force_two_chunks(monkeypatch)
    chat = fake_chat_factory(
        [_valid_checkpoint_json(f"c{i}") for i in range(n)]
        + [_valid_checkpoint_json("merged")])
    assert serializer.serialize_strict("S1", messages, chat=chat) is not None


def test_chunk_cache_files_written_0600(
        fake_chat_factory, monkeypatch, tmp_checkpoint_dir):
    # Pre-redaction content on disk gets key-file permissions (env-file
    # precedent) — the privacy half of the #48 design.
    import stat as _stat
    messages, n = _force_two_chunks(monkeypatch)
    chat = fake_chat_factory(
        [_valid_checkpoint_json(f"c{i}") for i in range(n)]
        + [_valid_checkpoint_json("merged")])
    assert serializer.serialize_strict("S1", messages, chat=chat) is not None
    for p in (tmp_checkpoint_dir / ".chunk-cache").glob("*.json"):
        assert _stat.S_IMODE(p.stat().st_mode) == 0o600


# ---- #317: scene traces (encode-time episodic context, bench-gated experiment) ----


def test_prompts_unchanged_when_scene_flag_off(monkeypatch):
    monkeypatch.delenv("DAIMON_SCENE_TRACES", raising=False)
    assert serializer._serialize_sys() == serializer.SERIALIZE_SYS
    assert serializer._merge_sys() == serializer.MERGE_SYS
    # the constants themselves must stay scene-free — flag off is byte-identical
    assert "scene" not in serializer.SERIALIZE_SYS
    assert "scene" not in serializer.MERGE_SYS


def test_prompts_mention_scene_when_flag_on(monkeypatch):
    monkeypatch.setenv("DAIMON_SCENE_TRACES", "1")
    assert '"scene"' in serializer._serialize_sys()
    assert '"scene"' in serializer._merge_sys()


def test_serialize_sends_scene_prompt_when_flag_on(fake_chat_factory, monkeypatch):
    monkeypatch.setenv("DAIMON_SCENE_TRACES", "1")
    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    serializer.serialize("S1", make_messages(20), chat=chat)
    assert '"scene"' in chat.calls[0]["messages"][0]["content"]


def test_scene_preserved_and_trimmed_through_serialize(fake_chat_factory, monkeypatch):
    monkeypatch.setenv("DAIMON_SCENE_TRACES", "1")
    cp = json.loads(_valid_checkpoint_json("S1"))
    cp["working_context"]["open_questions"][0]["scene"] = (
        "  came up while debugging the retry path  ")
    chat = fake_chat_factory(json.dumps(cp))
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat)
    assert (ckpt["working_context"]["open_questions"][0]["scene"]
            == "came up while debugging the retry path")


def test_sanitize_scene_drops_non_str_and_empty():
    cp = json.loads(_valid_checkpoint_json("S1"))
    cp["working_context"]["open_questions"][0]["scene"] = 42
    cp["working_context"]["recent_decisions"][0]["scene"] = "   "
    serializer.sanitize_scene(cp)
    assert "scene" not in cp["working_context"]["open_questions"][0]
    assert "scene" not in cp["working_context"]["recent_decisions"][0]


def test_sanitize_scene_caps_length():
    cp = json.loads(_valid_checkpoint_json("S1"))
    cp["working_context"]["open_questions"][0]["scene"] = "x" * 2000
    serializer.sanitize_scene(cp)
    scene = cp["working_context"]["open_questions"][0]["scene"]
    assert len(scene) == serializer._SCENE_MAX_CHARS


def test_sanitize_scene_runs_even_with_flag_off(fake_chat_factory, monkeypatch):
    # a model that hallucinates a malformed scene with the flag off must not
    # write garbage to disk — sanitize always runs
    monkeypatch.delenv("DAIMON_SCENE_TRACES", raising=False)
    cp = json.loads(_valid_checkpoint_json("S1"))
    cp["working_context"]["open_questions"][0]["scene"] = ["not", "a", "string"]
    chat = fake_chat_factory(json.dumps(cp))
    ckpt = serializer.serialize("S1", make_messages(20), chat=chat)
    assert "scene" not in ckpt["working_context"]["open_questions"][0]


# ---- #358: verbatim items bind to transcript message ids ----
#
# Capture-time binding: hosts whose transcripts carry a stable per-message id
# (Claude Code JSONL `uuid`) render each identified message with a bracketed
# [mN] marker; the extractor cites the marker per verbatim item; the parse
# boundary translates markers to host ids and drops anything that does not
# resolve against the actual transcript (same code-owned-key discipline as
# #292/#295, one level down). Hosts without ids render byte-identical to the
# pre-#358 format and keep whole-transcript scanning.


def _msgs_with_ids(n=6):
    out = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        out.append({"role": role, "content": f"line {i} from {role}",
                    "id": f"uuid-{i}"})
    return out


def test_render_transcript_without_ids_is_byte_identical_to_today():
    msgs = [{"role": "user", "content": "hola"},
            {"role": "assistant", "content": "todo bien"}]
    assert serializer._render_transcript(msgs) == (
        "user: hola\n\nassistant: todo bien")


def test_render_transcript_prefixes_markers_only_for_id_messages():
    msgs = [{"role": "user", "content": "first", "id": "uuid-a"},
            {"role": "assistant", "content": "second"},
            {"role": "user", "content": "third", "id": "uuid-c"}]
    assert serializer._render_transcript(msgs) == (
        "[m1] user: first\n\nassistant: second\n\n[m3] user: third")


def test_message_id_map_maps_markers_to_host_ids():
    msgs = [{"role": "user", "content": "first", "id": "uuid-a"},
            {"role": "assistant", "content": "second"},
            {"role": "user", "content": "third", "id": " uuid-c "}]
    assert serializer.message_id_map(msgs) == {"m1": "uuid-a", "m3": "uuid-c"}
    assert serializer.message_id_map([]) == {}
    assert serializer.message_id_map(None) == {}


def test_prompts_carry_source_message_id_rule():
    # Always-on (no flag): the prompts are content-pinned, not byte-pinned, so
    # the rule ships unconditionally. Both prompts must know the key — merge
    # re-emits items and would silently drop the binding otherwise.
    assert "SOURCE MESSAGE IDS" in serializer.SERIALIZE_SYS
    assert '"source_message_ids"' in serializer.SERIALIZE_SYS
    assert "SOURCE MESSAGE IDS" in serializer.MERGE_SYS
    assert '"source_message_ids"' in serializer.MERGE_SYS


def _cp_one_decision(item):
    return {
        "session_id": "S1",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [item],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [], "uncertainties": [],
            "contradictions_flagged": [],
        },
    }


def test_sanitize_source_ids_translates_marker_to_host_id():
    cp = _cp_one_decision({"text": "d", "trust": "verbatim",
                           "quote": "line 3 from assistant",
                           "source_message_ids": ["m4"]})
    serializer.sanitize_source_ids(cp, {"m4": "uuid-3"})
    item = cp["working_context"]["recent_decisions"][0]
    assert item["source_message_ids"] == ["uuid-3"]


def test_sanitize_source_ids_accepts_bare_string_and_bracketed_marker():
    # Models reflow "[m4]" or emit a bare string instead of a list — both
    # normalize instead of losing the binding.
    for raw in ("m4", "[m4]", ["[m4]"]):
        cp = _cp_one_decision({"text": "d", "trust": "verbatim",
                               "quote": "line 3 from assistant",
                               "source_message_ids": raw})
        serializer.sanitize_source_ids(cp, {"m4": "uuid-3"})
        item = cp["working_context"]["recent_decisions"][0]
        assert item["source_message_ids"] == ["uuid-3"], raw


def test_sanitize_source_ids_drops_unknown_ids_and_garbage():
    # An id the transcript cannot vouch for is an invented id — parse boundary
    # drops it; nothing valid left removes the key entirely.
    cp = _cp_one_decision({"text": "d", "trust": "verbatim",
                           "quote": "line 3 from assistant",
                           "source_message_ids": ["m99", 7, None, {"m": 1}]})
    serializer.sanitize_source_ids(cp, {"m4": "uuid-3"})
    assert "source_message_ids" not in cp["working_context"]["recent_decisions"][0]


def test_sanitize_source_ids_passes_through_known_host_ids():
    # A merged/cached partial can already carry the translated host id — it is
    # still validated against the transcript's actual ids, then kept.
    cp = _cp_one_decision({"text": "d", "trust": "verbatim",
                           "quote": "line 3 from assistant",
                           "source_message_ids": ["uuid-3", "uuid-3"]})
    serializer.sanitize_source_ids(cp, {"m4": "uuid-3"})
    item = cp["working_context"]["recent_decisions"][0]
    assert item["source_message_ids"] == ["uuid-3"]  # deduped, validated


def test_sanitize_source_ids_drops_key_on_inferred_items():
    # Binding rides the verbatim quote; an inferred item has no quote to bind.
    cp = _cp_one_decision({"text": "d", "trust": "inferred",
                           "source_message_ids": ["m4"]})
    serializer.sanitize_source_ids(cp, {"m4": "uuid-3"})
    assert "source_message_ids" not in cp["working_context"]["recent_decisions"][0]


def test_sanitize_source_ids_with_empty_map_drops_everything():
    # The #23 introspection path (cli write-checkpoint) has no transcript: no
    # model-claimed binding is validatable, so all of them go.
    cp = _cp_one_decision({"text": "d", "trust": "verbatim",
                           "quote": "line 3 from assistant",
                           "source_message_ids": ["m4", "uuid-3"]})
    serializer.sanitize_source_ids(cp, {})
    assert "source_message_ids" not in cp["working_context"]["recent_decisions"][0]


def _decision_checkpoint_json(item):
    return json.dumps(_cp_one_decision(item))


def test_serialize_strict_stores_host_ids_for_cited_marker(
        fake_chat_factory, monkeypatch):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    chat = fake_chat_factory(_decision_checkpoint_json(
        {"text": "d", "trust": "verbatim", "quote": "line 3 from assistant",
         "source_message_ids": ["m4"]}))
    out = serializer.serialize_strict("S1", _msgs_with_ids(6), chat=chat)
    item = out["working_context"]["recent_decisions"][0]
    assert item["source_message_ids"] == ["uuid-3"]  # marker -> host id
    assert item["trust"] == "verbatim"
    assert item["quote_verified"] is True
    # The extractor actually saw the marker it was asked to cite.
    sent = chat.calls[0]["messages"][1]["content"]
    assert "[m4] assistant: line 3 from assistant" in sent


def test_serialize_strict_drops_invented_marker_and_falls_back(
        fake_chat_factory, monkeypatch):
    # Model cites a marker that does not exist: the binding dies at the parse
    # boundary and verification falls back to the whole-transcript scan —
    # exactly today's behavior, verdict included.
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    chat = fake_chat_factory(_decision_checkpoint_json(
        {"text": "d", "trust": "verbatim", "quote": "line 3 from assistant",
         "source_message_ids": ["m99"]}))
    out = serializer.serialize_strict("S1", _msgs_with_ids(6), chat=chat)
    item = out["working_context"]["recent_decisions"][0]
    assert "source_message_ids" not in item
    assert item["trust"] == "verbatim"
    assert item["quote_verified"] is True


def test_serialize_strict_idless_host_stays_on_todays_path(
        fake_chat_factory, monkeypatch):
    # Hosts without per-message ids (Windsurf, Codex, markdown): no markers in
    # the rendered prompt, and a hallucinated citation cannot survive.
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    chat = fake_chat_factory(_decision_checkpoint_json(
        {"text": "d", "trust": "verbatim", "quote": "line 3 from assistant",
         "source_message_ids": ["m2"]}))
    out = serializer.serialize_strict("S1", make_messages(6), chat=chat)
    sent = chat.calls[0]["messages"][1]["content"]
    assert "[m" not in sent  # rendered transcript is marker-free
    item = out["working_context"]["recent_decisions"][0]
    assert "source_message_ids" not in item
    assert item["quote_verified"] is True


# ---- #359: outcome claims ground in tool-result signals ----
#
# Capture-time grounding: transcript.py surfaces tool-result rows as "tool"
# messages (Claude Code only — the one host with stable ids AND parseable
# tool results). The extractor cites the signal's [mN] marker in
# source_message_ids; the parse boundary validates it; ground_outcomes()
# derives the code-owned advisory `grounded` field and narrowly downgrades
# verbatim OUTCOME claims that cite no signal in a signal-bearing session.
# Hosts without tool rows degrade to a no-op: no downgrade, no `grounded`.


def _msgs_with_tool(n_conv=4):
    out = []
    for i in range(n_conv):
        role = "user" if i % 2 == 0 else "assistant"
        out.append({"role": role, "content": f"line {i} from {role}",
                    "id": f"uuid-{i}"})
    out.append({"role": "tool", "content": "2 passed, 0 failed - exit code 0",
                "id": "uuid-tool", "tool_result": True})
    return out


def test_render_transcript_labels_tool_rows():
    msgs = [{"role": "user", "content": "run it", "id": "u-1"},
            {"role": "tool", "content": "ok", "id": "t-2", "tool_result": True},
            {"role": "tool", "content": "boom", "id": "t-3",
             "tool_result": True, "tool_error": True}]
    assert serializer._render_transcript(msgs) == (
        "[m1] user: run it\n\n[m2] tool: ok\n\n[m3] tool (error): boom")


def test_signal_message_ids_collects_tool_result_ids():
    msgs = _msgs_with_tool(4)
    assert serializer.signal_message_ids(msgs) == {"uuid-tool"}
    assert serializer.signal_message_ids([]) == set()
    assert serializer.signal_message_ids(None) == set()
    # role alone is not a signal — the flag is (markdown transcripts can have
    # "tool:" role rows with no tool_result payload behind them).
    assert serializer.signal_message_ids(
        [{"role": "tool", "content": "x", "id": "t-1"}]) == set()


def test_prompts_carry_outcome_grounding_rule():
    assert "OUTCOME GROUNDING" in serializer.SERIALIZE_SYS
    assert "tool" in serializer.SERIALIZE_SYS
    # merge re-emits items: it must know signal ids can ride inferred items
    # too, or it would silently drop them (rule 14 said "never on inferred").
    assert "tool-result" in serializer.MERGE_SYS


def test_sanitize_source_ids_keeps_signal_ids_on_inferred_items():
    # The #358 rule was "binding rides the verbatim quote" — #359 adds the one
    # exception: a pointer to a tool-result signal is valid on ANY item.
    cp = _cp_one_decision({"text": "deploy succeeded", "trust": "inferred",
                           "source_message_ids": ["m5"]})
    serializer.sanitize_source_ids(cp, {"m4": "uuid-3", "m5": "uuid-tool"},
                                   {"uuid-tool"})
    item = cp["working_context"]["recent_decisions"][0]
    assert item["source_message_ids"] == ["uuid-tool"]


def test_sanitize_source_ids_still_drops_non_signal_ids_on_inferred_items():
    cp = _cp_one_decision({"text": "d", "trust": "inferred",
                           "source_message_ids": ["m4", "m5"]})
    serializer.sanitize_source_ids(cp, {"m4": "uuid-3", "m5": "uuid-tool"},
                                   {"uuid-tool"})
    item = cp["working_context"]["recent_decisions"][0]
    assert item["source_message_ids"] == ["uuid-tool"]  # m4 dropped, m5 kept


def test_sanitize_source_ids_verbatim_item_keeps_quote_and_signal_ids():
    cp = _cp_one_decision({"text": "deploy succeeded", "trust": "verbatim",
                           "quote": "line 3 from assistant",
                           "source_message_ids": ["m4", "m5"]})
    serializer.sanitize_source_ids(cp, {"m4": "uuid-3", "m5": "uuid-tool"},
                                   {"uuid-tool"})
    item = cp["working_context"]["recent_decisions"][0]
    assert item["source_message_ids"] == ["uuid-3", "uuid-tool"]


def test_asserts_outcome_lexicon_is_conservative():
    yes = ["deploy succeeded", "PR #12 merged", "tests pass now",
           "the build failed", "released 0.19.0 to PyPI",
           "all 42 tests passed", "serialization completed successfully"]
    no = ["will be merged tomorrow", "should be deployed after review",
          "plan to release on Friday", "whether the deploy succeeded",
          "use the passed argument", "decide the merge strategy",
          "el despliegue funciono bien"]  # non-English: honest no-op
    for text in yes:
        assert serializer._asserts_outcome(text), text
    for text in no:
        assert not serializer._asserts_outcome(text), text


def test_ground_outcomes_marks_signal_backed_items_grounded():
    cp = _cp_one_decision({"text": "deploy succeeded", "trust": "verbatim",
                           "quote": "q", "source_message_ids":
                           ["uuid-3", "uuid-tool"]})
    cp["working_context"]["open_questions"] = [
        {"text": "tests pass on CI too?", "trust": "inferred",
         "source_message_ids": ["uuid-tool"]}]
    serializer.ground_outcomes(cp, {"uuid-tool"})
    assert cp["working_context"]["recent_decisions"][0]["grounded"] is True
    assert cp["working_context"]["open_questions"][0]["grounded"] is True


def test_ground_outcomes_downgrades_ungrounded_verbatim_outcome_claim():
    cp = _cp_one_decision({"text": "deploy succeeded", "trust": "verbatim",
                           "quote": "deploy succeeded"})
    n = serializer.ground_outcomes(cp, {"uuid-tool"})
    item = cp["working_context"]["recent_decisions"][0]
    assert n == 1
    assert item["trust"] == "inferred"
    assert item["grounded"] is False
    assert item["quote"] == "deploy succeeded"  # transcription stays honest


def test_ground_outcomes_leaves_non_outcome_and_hedged_items_alone():
    cp = _cp_one_decision({"text": "rename the module to carry.py",
                           "trust": "verbatim", "quote": "q"})
    cp["working_context"]["open_questions"] = [
        {"text": "will be merged tomorrow", "trust": "verbatim", "quote": "q2"}]
    n = serializer.ground_outcomes(cp, {"uuid-tool"})
    assert n == 0
    assert cp["working_context"]["recent_decisions"][0]["trust"] == "verbatim"
    assert cp["working_context"]["open_questions"][0]["trust"] == "verbatim"
    for item in serializer.iter_items(cp):
        assert "grounded" not in item


def test_ground_outcomes_is_a_noop_when_session_has_no_signals():
    # Windsurf/Codex/hermes/markdown surface no tool rows: grounding is
    # IMPOSSIBLE there, so an outcome claim keeps today's exact treatment —
    # absence of evidence about the host is not evidence against the claim.
    cp = _cp_one_decision({"text": "deploy succeeded", "trust": "verbatim",
                           "quote": "q"})
    n = serializer.ground_outcomes(cp, set())
    item = cp["working_context"]["recent_decisions"][0]
    assert n == 0
    assert item["trust"] == "verbatim"
    assert "grounded" not in item


def test_ground_outcomes_tolerates_malformed_item_text():
    # A torn or hand-edited checkpoint can hold a non-string `text` (the #134
    # lesson lives at the validate boundary, but ground_outcomes also runs on
    # the cli write-checkpoint path where shapes vary). Grounding is advisory:
    # garbage must neither raise nor downgrade — never-fatal, same philosophy
    # as sanitize_importance.
    cp = _cp_one_decision({"text": ["deploy", "succeeded"], "trust": "verbatim",
                           "quote": "q"})
    n = serializer.ground_outcomes(cp, {"uuid-tool"})
    item = cp["working_context"]["recent_decisions"][0]
    assert n == 0
    assert item["trust"] == "verbatim"
    assert "grounded" not in item


def test_ground_outcomes_strips_model_claimed_grounded():
    # `grounded` is code-owned (#292 discipline): a model that emits it gets
    # stomped — re-derived from validated pointers or removed, never trusted.
    cp = _cp_one_decision({"text": "d", "trust": "inferred", "grounded": True})
    cp["working_context"]["open_questions"] = [
        {"text": "q", "trust": "inferred", "grounded": "yes"}]
    serializer.ground_outcomes(cp, set())
    for item in serializer.iter_items(cp):
        assert "grounded" not in item


def test_serialize_strict_grounds_cited_outcome_claim(
        fake_chat_factory, monkeypatch):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    chat = fake_chat_factory(_decision_checkpoint_json(
        {"text": "deploy succeeded", "trust": "verbatim",
         "quote": "line 3 from assistant",
         "source_message_ids": ["m4", "m5"]}))
    out = serializer.serialize_strict("S1", _msgs_with_tool(4), chat=chat)
    item = out["working_context"]["recent_decisions"][0]
    assert item["source_message_ids"] == ["uuid-3", "uuid-tool"]
    assert item["trust"] == "verbatim"
    assert item["quote_verified"] is True
    assert item["grounded"] is True
    # the extractor saw the tool row, marked as such
    sent = chat.calls[0]["messages"][1]["content"]
    assert "[m5] tool: 2 passed, 0 failed - exit code 0" in sent


def test_serialize_strict_downgrades_uncited_outcome_claim(
        fake_chat_factory, monkeypatch):
    # Signals existed in this session; the claim cites none of them. The
    # quote is a faithful transcription (quote_verified stays True) but the
    # OUTCOME is unwitnessed: stored as inferred, marked grounded: false.
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    chat = fake_chat_factory(_decision_checkpoint_json(
        {"text": "deploy succeeded", "trust": "verbatim",
         "quote": "line 3 from assistant"}))
    out = serializer.serialize_strict("S1", _msgs_with_tool(4), chat=chat)
    item = out["working_context"]["recent_decisions"][0]
    assert item["trust"] == "inferred"
    assert item["grounded"] is False
    assert item["quote_verified"] is True
    assert item["quote"] == "line 3 from assistant"


def test_serialize_strict_signal_free_host_keeps_todays_behavior(
        fake_chat_factory, monkeypatch):
    # No tool rows (Windsurf/Codex/markdown): outcome claims are untouched
    # and unmarked — grounding degrades to a no-op, not a mass downgrade.
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    chat = fake_chat_factory(_decision_checkpoint_json(
        {"text": "deploy succeeded", "trust": "verbatim",
         "quote": "line 3 from assistant"}))
    out = serializer.serialize_strict("S1", make_messages(6), chat=chat)
    item = out["working_context"]["recent_decisions"][0]
    assert item["trust"] == "verbatim"
    assert "grounded" not in item


def test_serialize_strict_min_messages_ignores_tool_rows(
        fake_chat_factory, monkeypatch):
    # Tool rows are evidence, not conversation — surfacing them must not let
    # a 2-turn session sneak past the too-short gate.
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    msgs = _msgs_with_tool(2)  # 2 conversation turns + 1 tool row
    msgs.append({"role": "tool", "content": "out", "id": "t-x",
                 "tool_result": True})
    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    assert serializer.serialize("S1", msgs, chat=chat) is None
    assert chat.calls == []


# ---- #360: perspective-diverse escalation (heal-path only) ----
#
# Escalation is the heal tier for failed serializes: N extraction passes over
# the same transcript from distinct perspectives, one ordinary merge, then the
# UNCHANGED deterministic gates (sanitize_source_ids, verify_quotes,
# ground_outcomes, redaction downstream). The merge model is a producer, never
# a verifier (scar #10). Only serialize_strict(escalate=True) triggers it —
# the session-end default path never escalates.


def test_escalation_perspectives_are_three_distinct_lanes():
    names = [name for name, _ in serializer.ESCALATION_PERSPECTIVES]
    assert names == ["decisions-and-outcomes", "open-loops-and-questions",
                     "artifacts-and-identifiers"]
    systems = [system for _, system in serializer.escalation_systems()]
    assert len(set(systems)) == 3
    for system in systems:
        # each pass keeps the FULL base prompt (all rules stay in force) and
        # appends its perspective — never replaces the extraction contract
        assert system.startswith(serializer.SERIALIZE_SYS)
        assert "EXTRACTION PERSPECTIVE" in system


def test_base_prompt_untouched_by_escalation():
    # flag-off / non-escalated path must stay byte-identical: the perspective
    # text lives only in the escalation addenda, never in the base constants.
    assert "EXTRACTION PERSPECTIVE" not in serializer.SERIALIZE_SYS
    assert "EXTRACTION PERSPECTIVE" not in serializer.MERGE_SYS


def test_escalated_single_chunk_runs_one_pass_per_perspective_plus_merge(
        fake_chat_factory, monkeypatch):
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "1")
    # scar #8: merge-call count is hierarchical — pin K so 3 partials = 1 merge
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "100")
    chat = fake_chat_factory(
        [_valid_checkpoint_json(f"p{i}") for i in range(3)]
        + [_valid_checkpoint_json("merged")])
    ckpt = serializer.serialize_strict(
        "S1", make_messages(20), chat=chat, escalate=True)
    assert ckpt is not None and ckpt["session_id"] == "S1"
    assert len(chat.calls) == 4
    extraction_systems = [c["messages"][0]["content"] for c in chat.calls[:3]]
    assert len(set(extraction_systems)) == 3  # three DISTINCT perspectives
    for system in extraction_systems:
        assert "EXTRACTION PERSPECTIVE" in system
    # stage 2 is the ordinary merge — same MERGE_SYS producer, no new verifier
    assert chat.calls[3]["messages"][0]["content"] == serializer.MERGE_SYS


def test_serialize_strict_default_never_escalates(fake_chat_factory, monkeypatch):
    # The session-end default path (no escalate arg) is byte-identical to
    # today: one single-pass call under the plain serialize prompt.
    monkeypatch.delenv("DAIMON_SCENE_TRACES", raising=False)
    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    assert serializer.serialize_strict("S1", make_messages(20), chat=chat) is not None
    assert len(chat.calls) == 1
    assert chat.calls[0]["messages"][0]["content"] == serializer.SERIALIZE_SYS


def test_escalated_result_still_crosses_deterministic_gates(
        fake_chat_factory, monkeypatch):
    # The merge model is a PRODUCER, never a verifier: a quote the transcript
    # does not contain must still be downgraded by verify_quotes, exactly as
    # on the default path.
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "1")
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "100")
    merged = json.loads(_valid_checkpoint_json("S1"))
    merged["working_context"]["recent_decisions"][0] = {
        "text": "d", "trust": "verbatim",
        "quote": "this sentence appears nowhere in the transcript"}
    chat = fake_chat_factory(
        [_valid_checkpoint_json(f"p{i}") for i in range(3)]
        + [json.dumps(merged)])
    ckpt = serializer.serialize_strict(
        "S1", make_messages(20), chat=chat, escalate=True)
    item = ckpt["working_context"]["recent_decisions"][0]
    assert item["trust"] == "inferred"
    assert item["quote_verified"] is False


def test_escalated_strips_model_supplied_code_owned_keys(
        fake_chat_factory, monkeypatch):
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "1")
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "100")
    merged = json.loads(_valid_checkpoint_json("S1"))
    merged["format_version"] = "D-999-spoofed"
    chat = fake_chat_factory(
        [_valid_checkpoint_json(f"p{i}") for i in range(3)]
        + [json.dumps(merged)])
    ckpt = serializer.serialize_strict(
        "S1", make_messages(20), chat=chat, escalate=True)
    assert "format_version" not in ckpt


def test_escalated_too_short_still_skips(fake_chat_factory):
    chat = fake_chat_factory(_valid_checkpoint_json("S1"))
    with pytest.raises(serializer.TooShortError):
        serializer.serialize_strict("S1", make_messages(4), chat=chat,
                                    escalate=True)
    assert chat.calls == []


def test_chunk_cache_key_gives_each_perspective_its_own_lane():
    # #48 keying already hashes the system prompt; passing each perspective's
    # actual prompt must yield distinct, stable lanes — and the default lane
    # (no system arg) must be byte-identical to hashing the plain prompt.
    base = serializer._chunk_cache_key("chunk text")
    assert base == serializer._chunk_cache_key(
        "chunk text", system=serializer._serialize_sys())
    keys = [serializer._chunk_cache_key("chunk text", system=system)
            for _, system in serializer.escalation_systems()]
    assert len(set(keys)) == 3          # perspectives never cross-contaminate
    assert base not in keys             # nor bleed into the default lane
    assert keys == [serializer._chunk_cache_key("chunk text", system=system)
                    for _, system in serializer.escalation_systems()]  # stable


def test_escalated_reheal_reuses_own_cached_partials(
        fake_chat_factory, monkeypatch, tmp_checkpoint_dir):
    # A re-escalation must reuse its OWN prior perspective partials (#48):
    # second run pays only the merge.
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "1")
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "100")
    messages = make_messages(20)
    chat1 = fake_chat_factory(
        [_valid_checkpoint_json(f"p{i}") for i in range(3)]
        + [_valid_checkpoint_json("merged")])
    assert serializer.serialize_strict(
        "S1", messages, chat=chat1, escalate=True) is not None
    assert len(list((tmp_checkpoint_dir / ".chunk-cache").glob("*.json"))) == 3

    chat2 = fake_chat_factory([_valid_checkpoint_json("merged2")])
    assert serializer.serialize_strict(
        "S1", messages, chat=chat2, escalate=True) is not None
    assert len(chat2.calls) == 1  # merge only — every perspective pass reused


def test_escalated_run_never_reuses_default_lane_chunks(
        fake_chat_factory, monkeypatch, tmp_checkpoint_dir):
    # A prior DEFAULT chunked run's cached extractions were produced under a
    # different prompt — the escalated run must not consume them (and vice
    # versa): perspective passes pay their own calls.
    messages, n = _force_two_chunks(monkeypatch)
    chat1 = fake_chat_factory(
        [_valid_checkpoint_json(f"c{i}") for i in range(n)]
        + [_valid_checkpoint_json("merged")])
    assert serializer.serialize_strict("S1", messages, chat=chat1) is not None
    assert len(list((tmp_checkpoint_dir / ".chunk-cache").glob("*.json"))) == n

    chat2 = fake_chat_factory(
        [_valid_checkpoint_json(f"e{i}") for i in range(3 * n)]
        + [_valid_checkpoint_json("merged2")])
    assert serializer.serialize_strict(
        "S1", messages, chat=chat2, escalate=True) is not None
    assert len(chat2.calls) == 3 * n + 1  # no cross-lane reuse


def test_escalated_deadline_scales_by_perspective_wave_plan(
        fake_chat_factory, monkeypatch):
    # An escalated run makes MORE calls — the wave plan must count every
    # perspective pass (#314 machinery, no new budget): 1 chunk x 3
    # perspectives at concurrency 1 = 3 chunk waves + 1 merge wave = 4 waves,
    # so the deadline extends by (4-1) x DAIMON_TIMEOUT.
    import time as _t
    monkeypatch.setenv("DAIMON_TIMEOUT", "100")
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "1")
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "100")
    chat = fake_chat_factory(
        [_valid_checkpoint_json(f"p{i}") for i in range(3)]
        + [_valid_checkpoint_json("merged")])
    given = _t.monotonic() + 100
    assert serializer.serialize_strict(
        "S1", make_messages(20), chat=chat, escalate=True,
        deadline=given) is not None
    seen = {c["kwargs"].get("deadline") for c in chat.calls}
    assert len(seen) == 1  # every call shares ONE scaled deadline
    scaled = seen.pop()
    assert scaled >= given + 250  # ~300s extension for 4 waves
