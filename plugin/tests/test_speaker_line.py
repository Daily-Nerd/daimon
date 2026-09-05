"""#925: a host-declared speaker line at the head of a user row fills said_by
when, and only when, the host declared the delimiter out of band."""

import json

from daimon_briefing import config, ledger, serializer, transcript

D = ""


def _row(role, content, uuid="u1"):
    return json.dumps({"type": role, "uuid": uuid,
                       "message": {"role": role, "content": content}})


def test_speaker_line_is_prose_when_no_delimiter_is_declared(monkeypatch):
    monkeypatch.delenv("DAIMON_SPEAKER_LINE", raising=False)
    text = _row("user", f'{D}from=123 name="Ana Lee" tier=member{D}\nhello there')
    msgs = transcript._from_jsonl(text)
    assert msgs == [{"role": "user", "content":
                     f'{D}from=123 name="Ana Lee" tier=member{D}\nhello there',
                     "id": "u1"}]


def test_speaker_line_fills_said_by_and_is_cut_from_content(monkeypatch):
    monkeypatch.setenv("DAIMON_SPEAKER_LINE", D)
    text = _row("user", f'{D}from=123 name="Ana Lee" tier=member{D}\nhello there')
    msgs = transcript._from_jsonl(text)
    assert msgs == [{"role": "user", "content": "hello there", "id": "u1",
                     "said_by": "Ana Lee (123)", "speaker_line": True}]


def test_speaker_line_accepts_u_plus_notation(monkeypatch):
    monkeypatch.setenv("DAIMON_SPEAKER_LINE", "U+E000")
    text = _row("user", f"{D}from=7 name=Bo{D}\nhi")
    (m,) = transcript._from_jsonl(text)
    assert m["said_by"] == "Bo (7)" and m["content"] == "hi"


def test_speaker_line_counts_only_at_position_zero(monkeypatch):
    monkeypatch.setenv("DAIMON_SPEAKER_LINE", D)
    body = f"hi\n{D}from=123 name=Ana{D}\nmore"
    (m,) = transcript._from_jsonl(_row("user", body))
    assert m["content"] == body and "said_by" not in m and "speaker_line" not in m


def test_speaker_line_without_from_is_cut_but_attributes_nothing(monkeypatch):
    monkeypatch.setenv("DAIMON_SPEAKER_LINE", D)
    (m,) = transcript._from_jsonl(_row("user", f"{D}tier=guest{D}\nhello"))
    assert m["content"] == "hello" and "said_by" not in m and m["speaker_line"] is True


def test_speaker_line_on_an_assistant_row_is_left_alone(monkeypatch):
    monkeypatch.setenv("DAIMON_SPEAKER_LINE", D)
    body = f"{D}from=123 name=Ana{D}\nI am the model"
    (m,) = transcript._from_jsonl(_row("assistant", body))
    assert m["content"] == body and "said_by" not in m


def test_speaker_line_with_id_only_gives_the_bare_id(monkeypatch):
    monkeypatch.setenv("DAIMON_SPEAKER_LINE", D)
    (m,) = transcript._from_jsonl(_row("user", f"{D}from=123{D}\nhello"))
    assert m["said_by"] == "123"


def test_speaker_line_delimiter_is_absent_by_default(monkeypatch):
    monkeypatch.delenv("DAIMON_SPEAKER_LINE", raising=False)
    assert config.speaker_line_delimiter() is None


def test_speaker_line_delimiter_literal_and_u_plus(monkeypatch):
    monkeypatch.setenv("DAIMON_SPEAKER_LINE", D)
    assert config.speaker_line_delimiter() == D
    monkeypatch.setenv("DAIMON_SPEAKER_LINE", "u+e000")
    assert config.speaker_line_delimiter() == D
    monkeypatch.setenv("DAIMON_SPEAKER_LINE", "   ")
    assert config.speaker_line_delimiter() is None


def test_speaker_line_count_reads_the_loader_flag():
    msgs = [{"role": "user", "content": "a", "speaker_line": True},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "c"}]
    assert serializer.speaker_line_count(msgs) == 1
    assert serializer.speaker_line_count([]) == 0


def test_stats_tallies_speaker_lines_lifetime(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(config, "log_dir", lambda: log_dir)
    (log_dir / "serialize.log").write_text(
        "2026-09-04T00:00:00Z session-end: spawned serialize for S1 (reason: other, project: /p) (transcript: /t/S1.jsonl)\n"
        "2026-09-04T00:00:01Z INFO daimon_briefing.serializer: speaker line: 3 user row(s) attributed by the host's declared line\n"
        "wrote checkpoint: /x/S1.json (took 5s)\n"
        "2026-09-04T00:00:02Z INFO daimon_briefing.serializer: speaker line: 2 user row(s) attributed by the host's declared line\n",
        encoding="utf-8")
    assert ledger._stats_capture()["speaker_lines"] == 5


def _stats_data(speaker_lines):
    return {"usage": {}, "store": {"items_by_kind": {}, "checkpoints": 0,
                                   "project_buckets": 0, "items_verbatim": 0,
                                   "items_inferred": 0, "items_untagged": 0,
                                   "items_carried": 0},
            "capture": {"success": 0, "skipped": 0, "errors": 0,
                        "fallback_serializes": 0, "hosts": {},
                        "speaker_lines": speaker_lines},
            "resolutions": {"human": 0, "agent_verified": 0, "agent_pending": 0,
                            "refused": 0, "agent_since": None,
                            "human_before_agent": 0}}


def test_stats_render_shows_speaker_lines_only_when_any(capsys):
    from daimon_briefing import render
    render.render_stats(_stats_data(0))
    assert "speaker line" not in capsys.readouterr().out
    render.render_stats(_stats_data(5))
    out = capsys.readouterr().out
    assert "speaker lines (lifetime): 5 user rows attributed by a host-declared line" in out


def test_stats_render_survives_an_old_capture_dict_without_the_key(capsys):
    from daimon_briefing import render
    data = _stats_data(0)
    del data["capture"]["speaker_lines"]
    render.render_stats(data)
    assert "speaker line" not in capsys.readouterr().out


def test_speaker_line_delimiter_rejects_a_bad_u_plus_spelling(monkeypatch):
    monkeypatch.setenv("DAIMON_SPEAKER_LINE", "U+ZZZZ")
    assert config.speaker_line_delimiter() is None


def test_speaker_line_without_a_closing_delimiter_is_prose(monkeypatch):
    monkeypatch.setenv("DAIMON_SPEAKER_LINE", D)
    body = f"{D}from=123 name=Ana\nhello"
    (m,) = transcript._from_jsonl(_row("user", body))
    assert m["content"] == body and "said_by" not in m


def test_speaker_line_spanning_a_newline_is_prose(monkeypatch):
    monkeypatch.setenv("DAIMON_SPEAKER_LINE", D)
    body = f"{D}from=123\nname=Ana{D}\nhello"
    (m,) = transcript._from_jsonl(_row("user", body))
    assert m["content"] == body and "said_by" not in m


def test_note_speaker_lines_is_silent_without_a_delimiter(monkeypatch, caplog):
    monkeypatch.delenv("DAIMON_SPEAKER_LINE", raising=False)
    with caplog.at_level("INFO", logger="daimon_briefing.serializer"):
        assert serializer.note_speaker_lines(
            [{"role": "user", "content": "a", "speaker_line": True}]) is None
    assert "speaker line" not in caplog.text


def test_note_speaker_lines_logs_the_count_with_a_delimiter(monkeypatch, caplog):
    monkeypatch.setenv("DAIMON_SPEAKER_LINE", D)
    with caplog.at_level("INFO", logger="daimon_briefing.serializer"):
        assert serializer.note_speaker_lines(
            [{"role": "user", "content": "a", "speaker_line": True},
             {"role": "user", "content": "b"}]) == 1
    assert "speaker line: 1 user row(s) attributed by the host's declared line" in caplog.text


def test_stats_rich_render_shows_speaker_lines(capsys, monkeypatch):
    from daimon_briefing import render
    monkeypatch.setattr("daimon_briefing.render.supports_rich", lambda: True)
    render.render_stats(_stats_data(4))
    out = capsys.readouterr().out
    assert "speaker lines (lifetime)" in out and "4 user rows attributed" in out
