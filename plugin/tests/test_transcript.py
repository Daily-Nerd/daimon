import json as _json
from pathlib import Path

from daimon_briefing import transcript

FIXTURES = Path(__file__).parent / "fixtures"


def test_from_file_markdown_parses_roles():
    msgs = transcript.from_file(FIXTURES / "sample_transcript.md")
    assert len(msgs) >= 4
    assert all("role" in m and "content" in m for m in msgs)
    roles = {m["role"] for m in msgs}
    assert "user" in roles
    assert "assistant" in roles
    # The PR-merge gap line must be preserved verbatim somewhere.
    joined = "\n".join(m["content"] for m in msgs)
    assert "merge it myself" in joined


def test_from_file_plaintext_fallback(tmp_path):
    p = tmp_path / "plain.txt"
    p.write_text("just some notes with no role markers at all\nsecond line")
    msgs = transcript.from_file(p)
    # Plain text with no markers -> a single user message carrying the whole blob.
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert "just some notes" in msgs[0]["content"]


def test_from_file_missing_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        transcript.from_file(tmp_path / "does-not-exist.md")


def _write_jsonl(path, objs):
    import json

    path.write_text("\n".join(json.dumps(o) for o in objs) + "\n", encoding="utf-8")
    return path


def test_from_file_claude_jsonl_parses_user_and_assistant(tmp_path):
    p = _write_jsonl(tmp_path / "session.jsonl", [
        {"type": "last-prompt", "sessionId": "S"},
        {"type": "mode", "mode": "normal"},
        {"type": "user", "message": {"role": "user", "content": "where did we leave off?"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "private reasoning"},
            {"type": "text", "text": "PR #10 merged."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "git log"}},
        ]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "198191d Merge pull request #11"},
            {"type": "text", "text": "build the SessionEnd hook"},
        ]}},
        {"type": "system", "content": "hook output noise"},
    ])
    msgs = transcript.from_file(p)
    assert msgs == [
        {"role": "user", "content": "where did we leave off?"},
        {"role": "assistant", "content": "PR #10 merged."},
        {"role": "user", "content": "build the SessionEnd hook"},
    ]


def test_from_file_claude_jsonl_skips_sidechain_meta_and_bad_lines(tmp_path):
    p = tmp_path / "session.jsonl"
    import json

    lines = [
        json.dumps({"type": "user", "isSidechain": True,
                    "message": {"role": "user", "content": "subagent chatter"}}),
        "{not valid json at all",
        json.dumps({"type": "user", "isMeta": True,
                    "message": {"role": "user", "content": "meta noise"}}),
        json.dumps({"type": "user", "message": {"role": "user", "content": "real question"}}),
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    msgs = transcript.from_file(p)
    assert msgs == [{"role": "user", "content": "real question"}]


def test_from_file_claude_jsonl_no_conversation_returns_empty(tmp_path):
    # A .jsonl with only noise must return [] — NOT fall back to the
    # plain-text single-blob path (raw JSON fed to the serializer is garbage).
    p = _write_jsonl(tmp_path / "session.jsonl", [
        {"type": "mode", "mode": "normal"},
        {"type": "file-history-snapshot", "snapshot": "x"},
    ])
    assert transcript.from_file(p) == []


def test_from_file_generic_jsonl_parses_codex_like_shapes(tmp_path):
    p = _write_jsonl(tmp_path / "codex.jsonl", [
        {"type": "session_meta", "cwd": "/p"},
        {"role": "user", "content": "please fix the hook"},
        {"type": "assistant_message", "content": [
            {"type": "text", "text": "I changed the Stop adapter."},
            {"type": "tool_use", "name": "apply_patch"},
        ]},
        {"type": "user_input", "user_input": {"user_response": "ship it"}},
    ])

    msgs = transcript.from_file(p)

    assert msgs == [
        {"role": "user", "content": "please fix the hook"},
        {"role": "assistant", "content": "I changed the Stop adapter."},
        {"role": "user", "content": "ship it"},
    ]


def test_from_file_parses_current_codex_event_stream_without_duplicates(tmp_path):
    p = _write_jsonl(tmp_path / "codex.jsonl", [
        {"type": "response_item", "payload": {
            "type": "message", "role": "developer", "content": [
                {"type": "input_text", "text": "injected instructions"},
            ],
        }},
        {"type": "event_msg", "payload": {
            "type": "user_message", "message": "does capture work?",
        }},
        {"type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "does capture work?"},
            ],
        }},
        {"type": "event_msg", "payload": {
            "type": "agent_message", "message": "I will verify it.",
        }},
        {"type": "response_item", "payload": {
            "type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "I will verify it."},
            ],
        }},
    ])

    msgs = transcript.from_file(p)

    assert msgs == [
        {"role": "user", "content": "does capture work?"},
        {"role": "assistant", "content": "I will verify it."},
    ]


def test_from_file_windsurf_cascade_jsonl_parses_user_and_assistant(tmp_path):
    # Field-confirmed (#70): native Cascade transcript rows are exactly
    # {type, status, <payload-key>}. canceled is dropped, error is kept
    # (a failed-but-emitted response is still context), tool lines skipped.
    p = _write_jsonl(tmp_path / "cascade.jsonl", [
        {"type": "user_input", "status": "done",
         "user_input": {"user_response": "arreglá el bug de auth"}},
        {"type": "run_command", "status": "done",
         "run_command": {"command": "pytest"}},
        {"type": "planner_response", "status": "canceled",
         "planner_response": {"response": "should not appear"}},
        {"type": "planner_response", "status": "done",
         "planner_response": {"response": "Ya lo arreglé."}},
        {"type": "user_input", "status": "error",
         "user_input": {"user_response": "seguí, dale"}},
        {"type": "planner_response", "status": "error",
         "planner_response": {"response": "falló a mitad, pero esto salió"}},
    ])
    msgs = transcript.from_file(p)
    assert msgs == [
        {"role": "user", "content": "arreglá el bug de auth"},
        {"role": "assistant", "content": "Ya lo arreglé."},
        {"role": "user", "content": "seguí, dale"},
        {"role": "assistant", "content": "falló a mitad, pero esto salió"},
    ]


def test_from_file_windsurf_cascade_tolerates_schema_widened_rows(tmp_path):
    # Forward-compat: a Cascade row that grows a 4th key (e.g. timestamp)
    # must still parse as a turn — a strict key-count gate would silently
    # disable the whole branch and the best-effort fallback would drop every
    # planner_response (no role mapping) → user-only transcripts. The
    # `status` key is the discriminator, not the key count.
    p = _write_jsonl(tmp_path / "cascade.jsonl", [
        {"type": "user_input", "status": "done",
         "timestamp": "2026-07-04T10:00:00Z",
         "user_input": {"user_response": "hola"}},
        {"type": "planner_response", "status": "done",
         "timestamp": "2026-07-04T10:00:05Z",
         "planner_response": {"response": "hola, ¿qué hacemos?"}},
    ])
    msgs = transcript.from_file(p)
    assert msgs == [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "hola, ¿qué hacemos?"},
    ]


def test_from_file_windsurf_cascade_noise_only_returns_empty(tmp_path):
    p = _write_jsonl(tmp_path / "cascade.jsonl", [
        {"type": "grep_search_v2", "status": "done", "grep_search": {"query": "foo"}},
        {"type": "list_directory", "status": "done", "list_directory": {"path": "/x"}},
    ])
    assert transcript.from_file(p) == []


# ---- #358: per-message identity capture ----


def test_from_file_claude_jsonl_binds_message_uuid_as_id(tmp_path):
    # Claude Code rows carry a stable per-message `uuid` (field-verified on
    # live transcripts) — bind it to the normalized message as `id` so
    # verbatim items can anchor to the exact transcript entry their quote
    # came from (#358).
    p = _write_jsonl(tmp_path / "session.jsonl", [
        {"type": "user", "uuid": "aaa-111",
         "message": {"role": "user", "content": "where did we leave off?"}},
        {"type": "assistant", "uuid": "bbb-222",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "PR #10 merged."}]}},
    ])
    assert transcript.from_file(p) == [
        {"role": "user", "content": "where did we leave off?", "id": "aaa-111"},
        {"role": "assistant", "content": "PR #10 merged.", "id": "bbb-222"},
    ]


def test_from_file_rows_without_usable_uuid_get_no_id_key(tmp_path):
    # Hosts without a stable per-message id — and malformed uuid values — keep
    # today's exact {"role", "content"} shape: no id key, so downstream falls
    # back to whole-transcript quote scanning (#358). Windsurf Cascade and the
    # Codex event stream are covered by their own strict-equality tests above.
    p = _write_jsonl(tmp_path / "session.jsonl", [
        {"type": "user", "uuid": 42,
         "message": {"role": "user", "content": "malformed uuid row"}},
        {"type": "user", "uuid": "   ",
         "message": {"role": "user", "content": "blank uuid row"}},
        {"type": "user", "message": {"role": "user", "content": "no uuid row"}},
    ])
    assert transcript.from_file(p) == [
        {"role": "user", "content": "malformed uuid row"},
        {"role": "user", "content": "blank uuid row"},
        {"role": "user", "content": "no uuid row"},
    ]


# ---- #359: tool-result rows surface as signal-bearing "tool" messages ----


def test_from_file_claude_jsonl_surfaces_tool_result_rows(tmp_path):
    # Pre-#359 the parser dropped tool_result rows as noise; a row carrying a
    # stable uuid now surfaces as a "tool" message so outcome claims can
    # ground in the concrete signal it holds (exit status, test summary).
    p = _write_jsonl(tmp_path / "session.jsonl", [
        {"type": "user", "uuid": "u-1",
         "message": {"role": "user", "content": "run the tests"}},
        {"type": "assistant", "uuid": "a-2",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "running the suite"},
             {"type": "tool_use", "name": "Bash", "input": {"command": "pytest"}}]}},
        {"type": "user", "uuid": "t-3",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "content": "42 passed in 1.2s"}]},
         "toolUseResult": {"stdout": "42 passed in 1.2s", "stderr": ""}},
    ])
    assert transcript.from_file(p) == [
        {"role": "user", "content": "run the tests", "id": "u-1"},
        {"role": "assistant", "content": "running the suite", "id": "a-2"},
        {"role": "tool", "content": "42 passed in 1.2s", "id": "t-3",
         "tool_result": True},
    ]


def test_from_file_tool_result_error_rows_carry_the_error_flag(tmp_path):
    # is_error is the closest thing Claude Code exposes to an exit status on
    # every tool result — preserve it so the failure signal survives.
    p = _write_jsonl(tmp_path / "session.jsonl", [
        {"type": "user", "uuid": "u-1",
         "message": {"role": "user", "content": "deploy it"}},
        {"type": "user", "uuid": "t-2",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "is_error": True,
              "content": "Exit code 1\nfatal: deploy failed"}]}},
    ])
    assert transcript.from_file(p) == [
        {"role": "user", "content": "deploy it", "id": "u-1"},
        {"role": "tool", "content": "Exit code 1\nfatal: deploy failed",
         "id": "t-2", "tool_result": True, "tool_error": True},
    ]


def test_from_file_tool_result_rows_without_uuid_stay_dropped(tmp_path):
    # No uuid means no [mN] marker and no way to cite the row — grounding is
    # pointer-based, so an id-less tool result stays dropped: byte-identical
    # pre-#359 behavior for hosts without stable ids.
    p = _write_jsonl(tmp_path / "session.jsonl", [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "198191d some output"}]}},
    ])
    assert transcript.from_file(p) == [{"role": "user", "content": "hi"}]


def test_from_file_tool_result_flattens_block_lists_and_truncates(tmp_path):
    # tool_result `content` can itself be a block list; flatten text blocks.
    # Output is truncated to the cap — the signal (exit status, summary line)
    # lives at the head, and full payloads would bloat the serialize prompt.
    long_tail = "x" * 5000
    p = _write_jsonl(tmp_path / "session.jsonl", [
        {"type": "user", "uuid": "t-1",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "content": [
                 {"type": "text", "text": "head line"},
                 {"type": "text", "text": long_tail}]}]}},
    ])
    msgs = transcript.from_file(p)
    assert len(msgs) == 1
    content = msgs[0]["content"]
    assert content.startswith("head line")
    assert len(content) <= transcript._TOOL_RESULT_MAX_CHARS
    assert msgs[0]["tool_result"] is True


def test_from_file_tool_result_empty_output_gets_placeholder(tmp_path):
    # An empty stdout on success is still a signal ("ran, said nothing") —
    # a placeholder keeps the row alive instead of dropping the signal.
    p = _write_jsonl(tmp_path / "session.jsonl", [
        {"type": "user", "uuid": "t-1",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "content": ""}]}},
    ])
    assert transcript.from_file(p) == [
        {"role": "tool", "content": "(no output)", "id": "t-1",
         "tool_result": True},
    ]


def test_from_file_mixed_text_and_tool_result_row_keeps_text_only(tmp_path):
    # A row carrying BOTH text and tool_result blocks keeps its pre-#359
    # shape: the text wins, the tool payload is not split into a second
    # message (live Claude Code rows are pure tool_result rows).
    p = _write_jsonl(tmp_path / "session.jsonl", [
        {"type": "user", "uuid": "u-1",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "content": "198191d output"},
             {"type": "text", "text": "build the SessionEnd hook"}]}},
    ])
    assert transcript.from_file(p) == [
        {"role": "user", "content": "build the SessionEnd hook", "id": "u-1"},
    ]


def test_from_session_returns_empty_when_hermes_unavailable(monkeypatch):
    # Force the hermes import seam to fail; must degrade to [] not raise.
    monkeypatch.setattr(transcript, "_load_session_db", lambda: None)
    msgs = transcript.from_session("any-id")
    assert msgs == []


def test_from_session_uses_db_when_available(monkeypatch):
    class FakeDB:
        def get_messages_as_conversation(self, session_id):
            assert session_id == "S-X"
            return [{"role": "user", "content": "hi"}]

    monkeypatch.setattr(transcript, "_load_session_db", lambda: FakeDB())
    msgs = transcript.from_session("S-X")
    assert msgs == [{"role": "user", "content": "hi"}]


# ---- last_timestamp: session-end stamp for heal/serialize routing (#123) ----


def _jsonl(tmp_path, rows, name="s.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(_json.dumps(r) for r in rows), encoding="utf-8")
    return p


def test_last_timestamp_returns_max_normalized_to_seconds(tmp_path):
    p = _jsonl(tmp_path, [
        {"type": "user", "message": {"role": "user", "content": "hi"},
         "timestamp": "2026-07-01T10:00:00.123Z"},
        {"type": "assistant", "message": {"role": "assistant", "content": "yo"},
         "timestamp": "2026-07-01T10:05:30.500Z"},
    ])
    assert transcript.last_timestamp(p) == "2026-07-01T10:05:30Z"


def test_last_timestamp_tolerates_out_of_order_rows(tmp_path):
    p = _jsonl(tmp_path, [
        {"role": "user", "content": "a", "timestamp": "2026-07-01T12:00:00Z"},
        {"role": "assistant", "content": "b", "timestamp": "2026-07-01T11:00:00Z"},
    ])
    assert transcript.last_timestamp(p) == "2026-07-01T12:00:00Z"


def test_last_timestamp_skips_malformed_stamps(tmp_path):
    p = _jsonl(tmp_path, [
        {"role": "user", "content": "a", "timestamp": "not-a-date"},
        {"role": "assistant", "content": "b", "timestamp": "2026-07-01T09:30:00Z"},
    ])
    assert transcript.last_timestamp(p) == "2026-07-01T09:30:00Z"


def test_last_timestamp_none_when_jsonl_has_no_stamps(tmp_path):
    p = _jsonl(tmp_path, [{"role": "user", "content": "a"}])
    assert transcript.last_timestamp(p) is None


def test_last_timestamp_none_for_non_jsonl(tmp_path):
    p = tmp_path / "t.md"
    p.write_text("**user**: hello", encoding="utf-8")
    assert transcript.last_timestamp(p) is None


def test_last_timestamp_none_for_missing_file(tmp_path):
    assert transcript.last_timestamp(tmp_path / "nope.jsonl") is None
