"""Migration receipts, the recall-delivery log and `stats` read per line
(#1132 PR 3b). Fixtures are raw bytes; one undecodable byte costs its own
line, and a row holding a raw U+2028 stays one row."""

import json
from datetime import datetime, timezone

from daimon_briefing import buckets, cli, config, recall_telemetry, store

SEP = " "
BAD = b"\xff\xfe not utf-8\n"


def _line(row):
    return json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\n"


def _receipt(src, **extra):
    return {"ts": "2026-10-01T00:00:00Z", "from_slug": src, "to_slug": "-new",
            **extra}


def test_migration_records_read_around_bad_bytes_and_a_line_separator(
        tmp_checkpoint_dir):
    path = buckets.migrations_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_line(_receipt("-a", note=f"x{SEP}y")) + BAD
                     + _line(_receipt("-b")))
    got = buckets.records()
    assert [r["from_slug"] for r in got] == ["-a", "-b"]
    assert got[0]["note"] == f"x{SEP}y"


def _delivery(item):
    return {"at": "2026-09-11T00:00:00Z", "surface": "recall-inject",
            "item_id": item, "match_score": 0.5, "term_hits": 1}


def _stats_with(tmp_path, monkeypatch, *chunks):
    log = tmp_path / "logs"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(log))
    path = config.recall_delivery_log()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(chunks))
    return recall_telemetry.stats(
        now=datetime(2026, 9, 12, tzinfo=timezone.utc))


def test_recall_stats_do_not_raise_on_an_undecodable_byte(
        tmp_path, monkeypatch):
    got = _stats_with(tmp_path, monkeypatch, _line(_delivery("o-1")), BAD,
                      _line(_delivery("o-2")))
    assert got["lifetime"]["deliveries"] == 2


def test_recall_stats_keep_a_row_holding_a_raw_line_separator(
        tmp_path, monkeypatch):
    row = dict(_delivery("o-1"), session_id=f"a{SEP}b")
    got = _stats_with(tmp_path, monkeypatch, _line(row), _line(_delivery("o-2")))
    assert got["lifetime"]["deliveries"] == 2


def _event(ref, ts, **extra):
    return {"ts": ts, "kind": "resolution", "item_ref": ref,
            "status": "resolved", "source": "cli", **extra}


def test_stats_resolutions_count_around_bad_bytes_and_a_line_separator(
        tmp_checkpoint_dir):
    project = "/p/stats-readers"
    path = store._events_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        _line(_event("o-a", "2026-01-01T00:00:00Z", note=f"x{SEP}y")) + BAD
        + _line(_event("o-b", "2026-01-02T00:00:00Z")))
    got = cli._stats_resolutions(project, {})
    assert got["human"] == 2
