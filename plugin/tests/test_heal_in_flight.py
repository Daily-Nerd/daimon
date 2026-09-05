"""#936: a heal repair that is still running must read as in flight, not as
an exhausted failure. A later spawn line opens a new attempt; a pending attempt
with a fresh heartbeat is `repairing`; status shows it and heal says so."""

import time

from daimon_briefing import ledger, render


def _stamp(now, ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - ago))


def _text(now):
    return (f"{_stamp(now, 600)} session-end: spawned serialize for S1 "
            f"(reason: other, project: /p) (transcript: /t/S1.jsonl)\n"
            "error: LLM call failed on merge level 1, group 1 of 1: ChatError: "
            "command backend timed out (transcript: /t/S1.jsonl) after 900s\n"
            f"{_stamp(now, 30)} session-start: retry serialize for S1 (prior: error)\n")


def test_a_later_spawn_line_opens_a_new_attempt():
    now = 1_800_000_000.0
    e = ledger._session_ledger(_text(now), now)["S1"]
    assert e["spawned"] is True
    assert e["retried"] is True
    assert e["result_kind"] is None
    assert e["result_line"] is None
    assert e["spawn_age"] == 30


def test_a_pending_retry_with_a_fresh_heartbeat_is_repairing():
    now = 1_800_000_000.0
    led = ledger._session_ledger(_text(now), now)
    out = ledger._outstanding_failures(
        led, now, lambda s: False, 1800, lambda p: True,
        heartbeat_age=lambda s: 5)
    assert [(f["sid"], f["kind"], f["class"], f["heartbeat_age"]) for f in out] == [
        ("S1", "in-flight", "repairing", 5)]


def test_a_pending_retry_with_a_stale_heartbeat_falls_through_to_the_hung_rule():
    now = 1_800_000_000.0
    led = ledger._session_ledger(_text(now), now)
    led["S1"]["spawn_age"] = 4000
    out = ledger._outstanding_failures(
        led, now, lambda s: False, 1800, lambda p: True,
        heartbeat_age=lambda s: 3900)
    assert [(f["sid"], f["kind"], f["class"]) for f in out] == [("S1", "hung", "hung")]


def test_a_first_attempt_in_flight_stays_silent_as_before():
    now = 1_800_000_000.0
    text = (f"{_stamp(now, 30)} session-end: spawned serialize for S2 "
            f"(reason: other, project: /p) (transcript: /t/S2.jsonl)\n")
    led = ledger._session_ledger(text, now)
    out = ledger._outstanding_failures(
        led, now, lambda s: False, 1800, lambda p: True,
        heartbeat_age=lambda s: 5)
    assert out == []


def test_status_renders_the_repair_in_flight():
    lines = render._outstanding_lines([{
        "sid": "S1", "kind": "in-flight", "class": "repairing", "age": 30,
        "age_str": "30s", "heartbeat_age": 5, "transcript": "/t/S1.jsonl",
        "project": "/p", "spawned": True, "line": None}])
    assert lines == ["  - S1  repairing (heartbeat 5s ago) — heal is running, do not force a second one"]


def test_heal_reports_a_running_repair_instead_of_skipping_silently(monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr(ledger, "heartbeat_age", lambda sid, now=None: 5)
    monkeypatch.setattr(ledger, "_has_checkpoint", lambda sid: False)
    plan = ledger._heal_plan(_text(now), now)
    assert plan["target"] is None
    assert [(s["sid"], s["reason"]) for s in plan["skipped"]] == [
        ("S1", "repair already running (heartbeat 5s ago)")]


def test_status_health_does_not_count_a_running_repair_as_a_failure():
    from daimon_briefing import cli
    proj = {"exists": True, "age_seconds": 10, "session_id": "P", "path": "/x"}
    glob = {"exists": True, "session_id": "P", "same_session_as_project": True}
    repairing = [{"sid": "S1", "kind": "in-flight", "class": "repairing", "age": 30,
                  "age_str": "30s", "heartbeat_age": 5, "transcript": "/t", "project": "/p",
                  "spawned": True, "line": None}]
    h = cli._status_health(proj, glob, repairing, [], now=1000.0)
    assert not any("failed to serialize" in w for w in h["warnings"])
    assert h["ok"] is True
