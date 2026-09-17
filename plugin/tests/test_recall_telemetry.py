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


def test_record_carries_the_rendered_width_and_normalizes_the_rest(
        tmp_path, monkeypatch):
    # #1030: width now varies by slot, so the ledger records what each delivery
    # actually rendered. Surfaces that render no cut (recall-search) send no
    # such fields and must not acquire invented ones, and a wrong-typed value
    # is dropped the same way a wrong-typed score is.
    log = tmp_path / "logs"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(log))
    recall_telemetry.record(
        [{"item_id": "o-wide", "rendered_chars": 320, "truncated": True},
         {"item_id": "o-short", "rendered_chars": 41, "truncated": False},
         {"item_id": "o-plain"},
         {"item_id": "o-bad", "rendered_chars": "320", "truncated": 1}],
        query_terms=["ledger"],
        surface="recall-inject",
        now=datetime(2026, 9, 14, tzinfo=timezone.utc),
    )
    rows = [json.loads(line) for line in
            (log / "recall-delivery.jsonl").read_text().splitlines()]
    assert [r["rendered_chars"] for r in rows] == [320, 41, None, None]
    assert [r["truncated"] for r in rows] == [True, False, None, None]


def test_record_carries_the_hint_form_and_defaults_to_none(tmp_path, monkeypatch):
    # #1036: which hint rendering (shell command vs named MCP tool) a delivery
    # actually carried, so the two can be compared later. Omitting the
    # argument (every call site that predates #1036) must record None rather
    # than inventing a value.
    log = tmp_path / "logs"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(log))
    recall_telemetry.record(
        [{"item_id": "o-tool"}], query_terms=["ledger"],
        surface="recall-inject", hint_form="tool",
        now=datetime(2026, 9, 15, tzinfo=timezone.utc),
    )
    recall_telemetry.record(
        [{"item_id": "o-shell"}], query_terms=["ledger"],
        surface="recall-inject", hint_form="shell",
        now=datetime(2026, 9, 15, tzinfo=timezone.utc),
    )
    recall_telemetry.record(
        [{"item_id": "o-legacy-call"}], query_terms=["ledger"],
        surface="recall-inject",
        now=datetime(2026, 9, 15, tzinfo=timezone.utc),
    )
    rows = [json.loads(line) for line in
            (log / "recall-delivery.jsonl").read_text().splitlines()]
    assert [r["hint_form"] for r in rows] == ["tool", "shell", None]


def test_record_carries_the_injecting_session_and_defaults_to_unknown(
        tmp_path, monkeypatch):
    # #1043: `session_id` stays the CAPTURING session (provenance); a
    # separate field carries which live session the row was injected into.
    # Omitting the argument (every call site that predates #1043) must
    # record None rather than inventing a value or reusing `session_id`.
    log = tmp_path / "logs"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(log))
    recall_telemetry.record(
        [{"item_id": "o-live", "session_id": "S-captured"}],
        query_terms=["ledger"], surface="recall-inject",
        injected_into="S-live-session",
        now=datetime(2026, 9, 15, tzinfo=timezone.utc),
    )
    recall_telemetry.record(
        [{"item_id": "o-legacy-call", "session_id": "S-captured"}],
        query_terms=["ledger"], surface="recall-inject",
        now=datetime(2026, 9, 15, tzinfo=timezone.utc),
    )
    rows = [json.loads(line) for line in
            (log / "recall-delivery.jsonl").read_text().splitlines()]
    assert rows[0]["session_id"] == "S-captured"
    assert rows[0]["injected_into"] == "S-live-session"
    assert rows[1]["session_id"] == "S-captured"
    assert rows[1]["injected_into"] is None


def test_stats_breaks_down_deliveries_by_injected_into_and_hint_form(
        tmp_path, monkeypatch):
    # #1043: the follow-through comparison (hint_form tool vs shell) needs a
    # per-injecting-session denominator without reading the raw file.
    log = tmp_path / "logs"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(log))
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    recall_telemetry.record(
        [{"item_id": "o-1"}], query_terms=["x"], surface="recall-inject",
        hint_form="tool", injected_into="S-a", now=now)
    recall_telemetry.record(
        [{"item_id": "o-2"}], query_terms=["x"], surface="recall-inject",
        hint_form="shell", injected_into="S-a", now=now)
    recall_telemetry.record(
        [{"item_id": "o-3"}], query_terms=["x"], surface="recall-inject",
        hint_form="tool", injected_into="S-b", now=now)
    # A legacy-shaped call: no injected_into at all, must bucket as unknown
    # rather than being dropped or crashing the aggregation.
    recall_telemetry.record(
        [{"item_id": "o-4"}], query_terms=["x"], surface="recall-search",
        now=now)
    out = recall_telemetry.stats(now=now)
    by_session = out["lifetime"]["by_injected_into"]
    assert by_session["S-a"]["deliveries"] == 2
    assert by_session["S-a"]["by_hint_form"] == {"tool": 1, "shell": 1}
    assert by_session["S-b"]["deliveries"] == 1
    assert by_session["S-b"]["by_hint_form"] == {"tool": 1}
    assert by_session["unknown"]["deliveries"] == 1
    assert by_session["unknown"]["by_hint_form"] == {"unknown": 1}


def test_stats_reads_a_legacy_row_missing_the_injected_into_key(
        tmp_path, monkeypatch):
    # Rows written before this field existed carry no `injected_into` key at
    # all, not a null one. Reading them back must not raise, and they must
    # bucket as unknown rather than being mistaken for provenance.
    log = tmp_path / "logs"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(log))
    log.mkdir()
    (log / "recall-delivery.jsonl").write_text(
        json.dumps({"at": "2026-09-11T00:00:00Z", "surface": "recall-inject",
                    "session_id": "S-captured", "match_score": 0.5,
                    "term_hits": 1}) + "\n",
        encoding="utf-8",
    )
    out = recall_telemetry.stats(now=datetime(2026, 9, 11, tzinfo=timezone.utc))
    assert out["lifetime"]["deliveries"] == 1
    assert out["lifetime"]["by_injected_into"]["unknown"]["deliveries"] == 1


def test_stats_reads_a_legacy_row_missing_the_hint_form_key(tmp_path, monkeypatch):
    # #1036: rows written before this field existed carry no `hint_form` key
    # at all, not a null one. Reading them back must not raise.
    log = tmp_path / "logs"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(log))
    log.mkdir()
    (log / "recall-delivery.jsonl").write_text(
        json.dumps({"at": "2026-09-11T00:00:00Z", "surface": "recall-inject",
                    "match_score": 0.5, "term_hits": 1}) + "\n",
        encoding="utf-8",
    )
    out = recall_telemetry.stats(now=datetime(2026, 9, 11, tzinfo=timezone.utc))
    assert out["lifetime"]["deliveries"] == 1


def test_record_carries_the_via_and_defaults_to_unknown(tmp_path, monkeypatch):
    # #1053: which surface actually wrote the row — the MCP tool or the CLI
    # command — so tool-versus-shell follow-through has a value to group by.
    # Omitting the argument (every call site that predates #1053) must record
    # None rather than inventing a value; an out-of-vocabulary value is
    # dropped the same way an out-of-vocabulary hint_form is.
    log = tmp_path / "logs"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(log))
    recall_telemetry.record(
        [{"item_id": "o-mcp"}], query_terms=["ledger"],
        surface="recall-search", via="mcp",
        now=datetime(2026, 9, 17, tzinfo=timezone.utc),
    )
    recall_telemetry.record(
        [{"item_id": "o-cli"}], query_terms=["ledger"],
        surface="recall-search", via="cli",
        now=datetime(2026, 9, 17, tzinfo=timezone.utc),
    )
    recall_telemetry.record(
        [{"item_id": "o-legacy-call"}], query_terms=["ledger"],
        surface="recall-search",
        now=datetime(2026, 9, 17, tzinfo=timezone.utc),
    )
    recall_telemetry.record(
        [{"item_id": "o-bogus"}], query_terms=["ledger"],
        surface="recall-search", via="carrier-pigeon",
        now=datetime(2026, 9, 17, tzinfo=timezone.utc),
    )
    rows = [json.loads(line) for line in
            (log / "recall-delivery.jsonl").read_text().splitlines()]
    assert [r["via"] for r in rows] == ["mcp", "cli", None, None]


def test_stats_reads_a_legacy_row_missing_the_via_key(tmp_path, monkeypatch):
    # Rows written before this field existed carry no `via` key at all, not a
    # null one. Reading them back must not raise.
    log = tmp_path / "logs"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(log))
    log.mkdir()
    (log / "recall-delivery.jsonl").write_text(
        json.dumps({"at": "2026-09-11T00:00:00Z", "surface": "recall-search",
                    "match_score": 0.5, "term_hits": 1}) + "\n",
        encoding="utf-8",
    )
    out = recall_telemetry.stats(now=datetime(2026, 9, 11, tzinfo=timezone.utc))
    assert out["lifetime"]["deliveries"] == 1


def test_stats_pairs_injections_with_same_session_pulls_by_via(
        tmp_path, monkeypatch):
    # #1053: the follow-through summary — for a session that received a
    # recall-inject hint, how many recall-search pulls landed attributed to
    # that SAME live session, split by via (tool vs shell).
    log = tmp_path / "logs"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(log))
    now = datetime(2026, 9, 17, tzinfo=timezone.utc)
    recall_telemetry.record(
        [{"item_id": "o-1"}], query_terms=["x"], surface="recall-inject",
        hint_form="tool", injected_into="S-real", now=now)
    recall_telemetry.record(
        [{"item_id": "o-2"}], query_terms=["x"], surface="recall-search",
        via="mcp", injected_into="S-real", now=now)
    recall_telemetry.record(
        [{"item_id": "o-3"}], query_terms=["x"], surface="recall-search",
        via="cli", injected_into="S-real", now=now)
    out = recall_telemetry.stats(now=now)
    pairing = out["lifetime"]["follow_through"]["S-real"]
    assert pairing["injections"] == 1
    assert pairing["pulls"] == 2
    assert pairing["by_via"] == {"mcp": 1, "cli": 1}


def test_stats_follow_through_ignores_a_pull_with_no_matching_injection(
        tmp_path, monkeypatch):
    # A pull's `injected_into` is agent-supplied, untrusted text — a made-up
    # session id (or one that simply never received a hint) must pair with
    # nothing rather than minting a phantom bucket.
    log = tmp_path / "logs"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(log))
    now = datetime(2026, 9, 17, tzinfo=timezone.utc)
    recall_telemetry.record(
        [{"item_id": "o-made-up"}], query_terms=["x"], surface="recall-search",
        via="mcp", injected_into="S-never-injected", now=now)
    out = recall_telemetry.stats(now=now)
    assert out["lifetime"]["follow_through"] == {}


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
