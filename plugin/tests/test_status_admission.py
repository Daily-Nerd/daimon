"""`status` counts a refused admission from the whole serialize log and names
the cure (#1132 PR 10b, D10.4/D10.6): the class, the hint, the in-scope bucket
and its path, and no silent-capture banner while every failure is a refusal.
"""

import json
import time

import pytest

from daimon_briefing import cli, config, store
from daimon_briefing.cli import status as status_cmd

HINT = "run: daimon ledger repair events"


@pytest.fixture
def proj(tmp_path):
    d = tmp_path / "work" / "adm-app"
    d.mkdir(parents=True)
    return d


def _break_events(proj):
    path = store._events_path(store._resolved(str(proj)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<<<<<<< conflict\n")
    return path


def _refused_sessions(proj, tmp_path, n=2, *, transcripts=True, noise=0):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 300))
    lines = []
    for i in range(n):
        tp = tmp_path / f"s-{i}.jsonl"
        if transcripts:
            tp.write_text("{}\n")
        lines.append(f"{stamp} session-end: spawned serialize for s-{i} "
                     f"(reason: x, project: {proj}) (transcript: {tp})")
        lines.append("error: admission refused: events.jsonl is unreadable; "
                     f"{HINT} (transcript: {tp}) after 0s")
    for j in range(noise):
        lines.append(f"{stamp} session-end: spawned serialize for n-{j} "
                     f"(reason: x, project: /elsewhere) "
                     f"(transcript: /nope/n-{j}.jsonl)")
    log_dir = config.log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serialize.log").write_text("\n".join(lines) + "\n")


def test_the_payload_classifies_a_refusal_and_names_the_bucket(proj, tmp_path):
    path = _break_events(proj)
    _refused_sessions(proj, tmp_path)
    payload, _rc = cli.status_payload(str(proj))
    classes = [f["class"] for f in payload["outstanding"]]
    assert classes == ["admission-refused", "admission-refused"]
    [entry] = payload["ledgers"]["admission"]
    assert entry == {"slug": store.project_slug(store._resolved(str(proj))), "count": 2,
                     "state": "unreadable", "hint": HINT,
                     "path": str(path)}
    json.dumps(payload)


def test_the_payload_has_no_admission_entry_when_nothing_is_held(proj):
    payload, _rc = cli.status_payload(str(proj))
    assert payload["ledgers"]["admission"] == []


def test_a_refusal_beyond_the_200_line_tail_is_still_counted(proj, tmp_path):
    _break_events(proj)
    _refused_sessions(proj, tmp_path, n=1, noise=300)
    payload, _rc = cli.status_payload(str(proj))
    assert [f["class"] for f in payload["outstanding"]
            if f["sid"] == "s-0"] == ["admission-refused"]
    assert payload["ledgers"]["admission"][0]["count"] == 1


def test_the_health_warning_names_the_hint_not_heal_alone(proj, tmp_path):
    _break_events(proj)
    _refused_sessions(proj, tmp_path)
    payload, _rc = cli.status_payload(str(proj))
    [warning] = [w for w in payload["health"]["warnings"]
                 if "not serialized" in w]
    assert warning == ("2 session(s) not serialized: events.jsonl is "
                       f"unreadable; {HINT}, then daimon heal")
    assert "run 'daimon heal'" not in " ".join(payload["health"]["warnings"])


def test_other_failures_keep_their_own_warning_beside_the_refusals(
        proj, tmp_path):
    _break_events(proj)
    _refused_sessions(proj, tmp_path, n=1)
    log = config.log_dir() / "serialize.log"
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    other = tmp_path / "boom.jsonl"
    other.write_text("{}\n")
    log.write_text(log.read_text() + (
        f"{stamp} session-end: spawned serialize for boom "
        f"(reason: x, project: {proj}) (transcript: {other})\n"
        f"error: model said no (transcript: {other}) after 3s\n"))
    payload, _rc = cli.status_payload(str(proj))
    warnings = payload["health"]["warnings"]
    assert any("1 session failed to serialize — run 'daimon heal'" in w
               for w in warnings)
    assert any("1 session(s) not serialized" in w for w in warnings)


def test_the_silent_capture_banner_is_suppressed_for_pure_refusals(
        proj, tmp_path, monkeypatch):
    _break_events(proj)
    _refused_sessions(proj, tmp_path)
    monkeypatch.setattr(
        status_cmd, "_capture_alarm",
        lambda now: {"verdict": "fail", "spawns": 5, "checkpoints": 0,
                     "window_days": 14})
    payload, _rc = cli.status_payload(str(proj))
    assert payload["capture_alarm"] is None


def test_the_banner_stays_when_something_else_also_failed(
        proj, tmp_path, monkeypatch):
    _break_events(proj)
    _refused_sessions(proj, tmp_path, n=1)
    log = config.log_dir() / "serialize.log"
    other = tmp_path / "boom.jsonl"
    other.write_text("{}\n")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    log.write_text(log.read_text() + (
        f"{stamp} session-end: spawned serialize for boom "
        f"(reason: x, project: {proj}) (transcript: {other})\n"
        f"error: model said no (transcript: {other}) after 3s\n"))
    alarm = {"verdict": "fail", "spawns": 5, "checkpoints": 0,
             "window_days": 14}
    monkeypatch.setattr(status_cmd, "_capture_alarm", lambda now: alarm)
    payload, _rc = cli.status_payload(str(proj))
    assert payload["capture_alarm"] == alarm


def test_the_human_status_prints_the_bucket_count_hint_and_path(
        proj, tmp_path, capsys):
    path = _break_events(proj)
    _refused_sessions(proj, tmp_path)
    cli.main(["status", "--project", str(proj)])
    out = capsys.readouterr().out
    slug = store.project_slug(store._resolved(str(proj)))
    line = next(ln for ln in out.splitlines()
                if ln.startswith("⚠ admission refused"))
    assert line == (f"⚠ admission refused: 2 session(s) of {slug} not "
                    f"serialized: events.jsonl is unreadable; {HINT} "
                    f"({path})")


def test_status_rc_is_the_documented_existence_test_whatever_is_held(
        proj, tmp_path):
    _break_events(proj)
    _refused_sessions(proj, tmp_path)
    _payload, rc = cli.status_payload(str(proj))
    assert rc == 1  # no checkpoint exists for this project: unchanged
