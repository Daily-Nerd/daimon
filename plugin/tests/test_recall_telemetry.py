import json
from datetime import datetime, timedelta, timezone

from daimon_briefing import recall_telemetry


def test_record_writes_one_structured_row_per_delivery(tmp_path, monkeypatch):
    log = tmp_path / "logs"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(log))
    rows = [{"item_id": "o-abc123", "session_id": "S-1",
             "project_slug": "-repo", "match_score": 0.42,
             "term_hits": 3}]
    recall_telemetry.record(
        rows, query_terms=["gateway", "api_key=secretvalue"],
        surface="recall-inject",
        now=datetime(2026, 9, 11, tzinfo=timezone.utc),
    )
    payload = json.loads((log / "recall-delivery.jsonl").read_text())
    assert payload["item_id"] == "o-abc123"
    assert payload["match_score"] == 0.42
    assert payload["term_hits"] == 3
    assert payload["query_term_count"] == 2


def test_record_normalizes_invalid_values_and_accepts_naive_time(
        tmp_path, monkeypatch):
    log = tmp_path / "logs"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(log))
    recall_telemetry.record(
        [{"item_id": "o-invalid", "match_score": "not-a-score",
          "term_hits": True}],
        query_terms=["  ", "gateway"],
        surface="recall-search",
        now=datetime(2026, 9, 11),
    )
    payload = json.loads((log / "recall-delivery.jsonl").read_text())
    assert payload["at"] == "2026-09-11T00:00:00Z"
    assert payload["match_score"] is None
    assert payload["term_hits"] is None
    assert payload["query_term_count"] == 1


def test_record_skips_empty_delivery_and_ignores_write_errors(
        tmp_path, monkeypatch):
    recall_telemetry.record([], query_terms=[], surface="recall-search")

    blocked_parent = tmp_path / "blocked"
    blocked_parent.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(
        recall_telemetry.config,
        "recall_delivery_log",
        lambda: blocked_parent / "recall-delivery.jsonl",
    )
    recall_telemetry.record(
        [{"item_id": "o-write-error"}],
        query_terms=[],
        surface="recall-search",
    )


def test_stats_ignores_malformed_rows_and_splits_recent_window(tmp_path, monkeypatch):
    log = tmp_path / "logs"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(log))
    log.mkdir()
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    old = (now - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    (log / "recall-delivery.jsonl").write_text(
        "not json\n"
        "[]\n"
        + json.dumps({"at": old, "surface": "recall-inject",
                      "match_score": 0.1, "term_hits": 2}) + "\n"
        + json.dumps({"at": fresh, "surface": "recall-search",
                      "match_score": 0.8, "term_hits": None}) + "\n",
        encoding="utf-8",
    )
    out = recall_telemetry.stats(now=now)
    assert out["lifetime"]["deliveries"] == 2
    assert out["window"]["deliveries"] == 1
    assert out["window"]["by_surface"] == {"recall-search": 1}
    assert out["window"]["match_score"]["median"] == 0.8


def test_stats_handles_invalid_timestamp_and_missing_log(tmp_path, monkeypatch):
    log = tmp_path / "logs"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(log))
    log.mkdir()
    (log / "recall-delivery.jsonl").write_text(
        json.dumps({"at": "not-a-timestamp", "surface": "recall-search"})
        + "\n",
        encoding="utf-8",
    )
    out = recall_telemetry.stats(now=datetime(2026, 9, 11, tzinfo=timezone.utc))
    assert out["lifetime"]["deliveries"] == 1
    assert out["window"]["deliveries"] == 0

    (log / "recall-delivery.jsonl").unlink()
    out = recall_telemetry.stats()
    assert out["lifetime"]["deliveries"] == 0
