"""The silent lane readers learn the ledgers' health (#1132 PR 10a, D10.8).

`pending.queue_typed` carries notes for the ledgers its lanes read;
`checks._firing_summary` reads the ruling ledger through the rows seam and
reports `unreadable` when the read cannot vouch for it.
"""

import json


from daimon_briefing import (api, checks, cli, config, jsonl, pending,
                             refutations, requests, store, view)
from daimon_briefing.jsonl import Health

PROJECT = "/p/health-rows"
SENDER = "/p/health-rows-sender"


def _bucket(project=PROJECT):
    return config.checkpoint_dir() / store.project_slug(project)


def _plant(name, data: bytes, project=PROJECT):
    path = _bucket(project) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as handle:
        handle.write(data)


def _ruling():
    return refutations.assert_ruling(
        subject="public posts", verdict="no internal numbers",
        scope="publishing", evidence=["issue:693"], channel="cli-tty",
        ratified=True, project_dir=PROJECT)


# ---- pending ---------------------------------------------------------------


def test_queue_typed_has_the_rows_of_queue_and_no_notes_when_healthy(
        tmp_checkpoint_dir):
    got = pending.queue_typed(project_dir=PROJECT)
    assert got.rows == pending.queue(project_dir=PROJECT)["rows"]
    assert got.notes == ()
    assert api.queue is pending.queue


def test_queue_typed_notes_a_lane_ledger_that_is_not_read_as_is(
        tmp_checkpoint_dir):
    _plant("trust.jsonl", b"<<<<<<< HEAD\n")
    _plant("amendments.jsonl", b'{"torn')
    got = pending.queue_typed(project_dir=PROJECT)
    assert got.notes == (
        "⚠ amendments.jsonl is degraded (torn); "
        "run: daimon ledger repair amendments",
        "⚠ trust.jsonl is unreadable (garbage); run: daimon trust repair")


def test_queue_typed_notes_a_skipped_sender(tmp_checkpoint_dir):
    requests.open_request(to=store.project_slug(PROJECT), ask="ping",
                          why="because", channel="cli-agent",
                          project_dir=SENDER)
    _plant("requests.jsonl", b"<<<<<<< HEAD\n", project=SENDER)
    got = pending.queue_typed(project_dir=PROJECT)
    assert got.rows == []
    assert got.notes == (
        "⚠ 1 sender(s) skipped: a requests ledger cannot be read",)


def test_decide_prints_the_notes(tmp_checkpoint_dir, capsys):
    _plant("trust.jsonl", b"<<<<<<< HEAD\n")
    assert cli.main(["decide", "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "⚠ trust.jsonl is unreadable (garbage)" in out


# ---- the firing summary ----------------------------------------------------


def _firing_log(ruling_id):
    path = config.log_dir() / "checks.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "ts": "2026-10-01T00:00:00Z", "ruling_id": ruling_id, "host": "h",
        "outcome": "fired", "duration_ms": 1}) + "\n", encoding="utf-8")


def test_the_firing_summary_counts_the_projects_rulings(tmp_checkpoint_dir):
    rid = _ruling()
    _firing_log(rid)
    got = checks._firing_summary(PROJECT)
    assert got.log_state == "read"
    assert any(key[0] == rid for key in got.rulings)


def test_the_firing_summary_is_unreadable_when_the_ruling_read_cannot_scan(
        tmp_checkpoint_dir, monkeypatch):
    rid = _ruling()
    _firing_log(rid)
    real = jsonl.read
    target = refutations._path(PROJECT)

    def read(path, *a, **k):
        if path == target:
            return jsonl.Read(Health.UNREADABLE, [], detail="EIO")
        return real(path, *a, **k)

    monkeypatch.setattr(jsonl, "read", read)
    assert checks._firing_summary(PROJECT).log_state == "unreadable"


def test_the_firing_summary_reads_the_ruling_ledger_once(tmp_checkpoint_dir,
                                                         monkeypatch):
    rid = _ruling()
    _firing_log(rid)
    seen = []
    real = jsonl.read

    def spy(path, *a, **k):
        seen.append(path)
        return real(path, *a, **k)

    monkeypatch.setattr(jsonl, "read", spy)
    checks._firing_summary(PROJECT)
    assert seen.count(refutations._path(PROJECT)) == 1


def test_refutations_records_takes_rows(tmp_checkpoint_dir):
    _ruling()
    rows = jsonl.read(refutations._path(PROJECT)).rows
    assert refutations.records(rows=rows) == refutations.records(
        project_dir=PROJECT)
    assert view.snapshot(PROJECT).rulings.state == "read"
