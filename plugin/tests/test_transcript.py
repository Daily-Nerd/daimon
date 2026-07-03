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

import json as _json


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
