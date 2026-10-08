"""Every ledger appender goes through `jsonl.append_lines` (#1132 PR 3a).

The six appenders that never healed a torn tail now do, the bytes each one
writes are the shape it always wrote, and the lock sidecar lands beside a
bucket ledger and nowhere else.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from daimon_briefing import config, jsonl, recall_telemetry, store
from daimon_briefing.surfaces import Writer

PROJECT = "/p/append-callers"
KEY_A = "a" * 64
KEY_B = "b" * 64


@pytest.fixture
def team(monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")


@pytest.fixture
def logs(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "logs"))
    return tmp_path / "logs"


def _telemetry(item):
    recall_telemetry.record(
        [{"item_id": item, "match_score": 0.5}], query_terms=["gateway"],
        surface="recall-inject",
        now=datetime(2026, 9, 11, tzinfo=timezone.utc))
    return config.recall_delivery_log()


def _delivery(n):
    return _telemetry(f"o-{n}")


def _event(n):
    assert store.append_event(f"o-{n}", "resolved", project_dir=PROJECT, writer=Writer.HUMAN)
    return store._events_path(PROJECT)


def _verification(n):
    assert store.append_verification(f"o-{n}", "quote", "missed",
                                     project_dir=PROJECT)
    return store._ledger_path(PROJECT)


def _hits(n):
    assert store.record_forget_hits([{"text": f"text {n}"}],
                                    project_dir=PROJECT)
    return store._forget_hits_path(PROJECT)


def _tombstone(n):
    written = store.publish_tombstone((KEY_A, KEY_B)[n - 1], project_dir=PROJECT)
    assert written
    return Path(written[0])


HEALING = [
    pytest.param(_event, id="events"),
    pytest.param(_verification, id="verification"),
    pytest.param(_hits, id="forget-hits"),
    pytest.param(_tombstone, id="team-tombstones"),
    pytest.param(_delivery, id="recall-delivery"),
]


@pytest.mark.parametrize("appender", HEALING)
def test_a_torn_tail_is_terminated_before_the_next_row_lands(
        appender, tmp_checkpoint_dir, team, logs):
    path = appender(1)
    whole = path.read_bytes()
    torn = whole[:-6]
    assert not torn.endswith(b"\n")
    path.write_bytes(torn)
    appender(2)
    data = path.read_bytes()
    assert data.startswith(torn + b"\n")
    result = jsonl.read(path)
    assert (result.torn, result.split) == (1, 0)
    assert len(result.rows) == 1


@pytest.mark.parametrize("appender", HEALING)
def test_a_clean_tail_gets_no_blank_line(appender, tmp_checkpoint_dir, team,
                                         logs):
    path = appender(1)
    appender(2)
    assert b"\n\n" not in path.read_bytes()
    assert len(jsonl.read(path).rows) == 2


def test_events_bytes_are_the_ensure_ascii_false_dump_of_the_row(
        tmp_checkpoint_dir):
    assert store.append_event("o-1", "resolved", note="café   end",
                              project_dir=PROJECT, writer=Writer.HUMAN)
    raw = store._events_path(PROJECT).read_bytes()
    line = raw.decode("utf-8")
    assert line.endswith("\n") and line.count("\n") == 1
    assert "café   end" in line            # raw, not \u-escaped
    assert line == json.dumps(json.loads(line), ensure_ascii=False) + "\n"


def test_verification_bytes_are_the_ensure_ascii_false_dump_of_the_row(
        tmp_checkpoint_dir):
    assert store.append_verification("o-1", "quote", "café",
                                     project_dir=PROJECT)
    line = store._ledger_path(PROJECT).read_text(encoding="utf-8")
    assert "café" in line
    assert line == json.dumps(json.loads(line), ensure_ascii=False) + "\n"


def test_forget_hits_keep_the_default_ascii_escaping(tmp_checkpoint_dir):
    assert store.record_forget_hits([{"text": "a"}, {"text": "b"}],
                                    project_dir=PROJECT,
                                    reason="café")
    raw = store._forget_hits_path(PROJECT).read_bytes()
    assert b"caf\\u00e9" in raw and "é".encode() not in raw
    lines = raw.decode().splitlines()
    assert len(lines) == 2
    for line in lines:
        assert line == json.dumps(json.loads(line))


def test_forget_hits_write_every_row_of_a_call_together(
        tmp_checkpoint_dir, monkeypatch):
    calls = []
    real = jsonl.append_lines

    def spy(path, lines, **kw):
        lines = list(lines)
        calls.append(len(lines))
        return real(path, lines, **kw)

    monkeypatch.setattr(jsonl, "append_lines", spy)
    store.record_forget_hits([{"text": "a"}, {"text": "b"}, {"text": "c"}],
                             project_dir=PROJECT)
    assert calls == [3]


def test_telemetry_rows_stay_compact_and_unescaped(tmp_checkpoint_dir, logs):
    recall_telemetry.record(
        [{"item_id": "o-café", "match_score": 0.5},
         {"item_id": "o-two", "match_score": 0.4}],
        query_terms=["gateway"], surface="recall-inject",
        now=datetime(2026, 9, 11, tzinfo=timezone.utc))
    raw = config.recall_delivery_log().read_bytes().decode("utf-8")
    lines = raw.splitlines()
    assert len(lines) == 2 and raw.endswith("\n")
    assert "o-café" in lines[0]
    for line in lines:
        assert line == json.dumps(json.loads(line), ensure_ascii=False,
                                  separators=(",", ":"))


def test_tombstone_row_bytes_are_the_ensure_ascii_false_dump(
        tmp_checkpoint_dir, team):
    path = _tombstone(1)
    line = path.read_text(encoding="utf-8")
    assert line == json.dumps(json.loads(line), ensure_ascii=False) + "\n"
    assert set(json.loads(line)) == {"ts", "key", "author"}


# ---- where the lock sidecar lands --------------------------------------

def test_a_bucket_ledger_append_leaves_the_lock_beside_it(tmp_checkpoint_dir):
    path = _event(1)
    assert (path.parent / jsonl.LOCK_NAME).exists()


def test_a_team_tombstone_append_leaves_no_lock_in_the_team_dir(
        tmp_checkpoint_dir, team):
    _tombstone(1)
    assert list(config.team_dir().rglob(jsonl.LOCK_NAME)) == []


def test_a_log_append_leaves_no_lock_under_logs(tmp_checkpoint_dir, logs):
    _telemetry("o-1")
    assert list(logs.rglob(jsonl.LOCK_NAME)) == []
