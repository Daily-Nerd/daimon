"""`status` shows the ledger census (#1132 PR 2b): a `ledgers` payload field
and compact human lines. No enforcement: the exit code is the one status
always had, whatever the census finds."""

import json

import pytest

from daimon_briefing import cli, config, normalize, store

SECRET = "a forgotten value status must never echo"


@pytest.fixture
def proj(tmp_path):
    d = tmp_path / "work" / "census-app"
    d.mkdir(parents=True)
    return d


def _bucket(proj):
    d = config.checkpoint_dir() / store.project_slug(str(proj))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _line(**kw):
    return json.dumps(kw).encode("utf-8") + b"\n"


def test_payload_gains_ledgers_at_the_tail_and_keeps_every_field(proj):
    payload, _rc = cli.status_payload(str(proj))
    assert list(payload)[-1] == "ledgers"
    for key in ("project", "global", "last_serialize", "health", "checks",
                "identity", "requests", "handoff"):
        assert key in payload
    ledgers = payload["ledgers"]
    assert set(ledgers) == {"ledgers", "undeclared", "forgotten_check",
                           "checkpoints", "other"}
    assert ledgers["ledgers"]["trust.jsonl"] == {
        "state": "absent", "torn": 0, "split": 0, "garbage": 0,
        "tombstoned_present": 0}
    json.dumps(payload)  # --json must still serialize


def test_payload_reports_a_degraded_ledger_and_a_tombstoned_value(proj):
    d = _bucket(proj)
    slug = store.project_slug(str(proj))
    (d / "events.jsonl").write_bytes(_line(
        ts="2026-01-01T00:00:00Z", kind="resolution", item_ref="i-1",
        status=f"forgotten:{normalize.content_key(SECRET)}", source="cli"))
    (d / "trust.jsonl").write_bytes(_line(reason=SECRET) + b'{"cut\n')
    payload, _rc = cli.status_payload(str(proj))
    entry = payload["ledgers"]["ledgers"]["trust.jsonl"]
    assert (entry["state"], entry["torn"], entry["tombstoned_present"]) == (
        "degraded", 1, 1)
    assert slug in json.dumps(payload["identity"])
    assert SECRET not in json.dumps(payload["ledgers"])


def test_status_exit_code_ignores_ledger_health(proj, capsys):
    rc_clean = cli.main(["status", "--project", str(proj)])
    (_bucket(proj) / "trust.jsonl").write_bytes(b"<<<<<<< HEAD\n")
    rc_broken = cli.main(["status", "--project", str(proj)])
    capsys.readouterr()
    assert rc_clean == rc_broken
    _payload, rc = cli.status_payload(str(proj))
    assert rc == rc_broken


def test_human_status_is_one_compact_line_when_all_ledgers_are_fine(
        proj, capsys):
    (_bucket(proj) / "requests.jsonl").write_bytes(_line(a=1))
    cli.main(["status", "--project", str(proj)])
    out = capsys.readouterr().out.splitlines()
    ledger_lines = [ln for ln in out if "ledger" in ln.lower()
                    and "checkpoint" not in ln.lower()]
    assert ledger_lines == ["ledgers: 1 ok, 8 absent"]


def test_human_status_names_each_ledger_that_is_not_ok(proj, capsys):
    d = _bucket(proj)
    (d / "trust.jsonl").write_bytes(_line(a=1) + b'{"cut\n')
    (d / "amendments.jsonl").write_bytes(b"not json\n")
    (d / "stray.jsonl").write_bytes(_line(a=1))
    cli.main(["status", "--project", str(proj)])
    out = capsys.readouterr().out
    assert "ledger trust.jsonl: degraded (1 torn)" in out
    assert "ledger amendments.jsonl: unreadable (1 garbage)" in out
    assert "undeclared ledger file: stray.jsonl" in out
    assert "ledgers: " not in out  # the compact line is for the all-fine case


def test_human_status_flags_a_forgotten_value_without_showing_it(proj, capsys):
    d = _bucket(proj)
    (d / "events.jsonl").write_bytes(_line(
        ts="2026-01-01T00:00:00Z", kind="resolution", item_ref="i-1",
        status=f"forgotten:{normalize.content_key(SECRET)}", source="cli"))
    (d / "trust.jsonl").write_bytes(_line(reason=SECRET))
    cli.main(["status", "--project", str(proj)])
    out = capsys.readouterr().out
    assert "ledger trust.jsonl: ok, 1 row still carries a forgotten value" in out
    assert SECRET not in out


def test_human_status_flags_checkpoint_residue(proj, capsys):
    d = _bucket(proj)
    slug = store.project_slug(str(proj))
    section, key = store._ITEM_LISTS[0]
    (d / "events.jsonl").write_bytes(_line(
        ts="2026-01-01T00:00:00Z", kind="resolution", item_ref="i-1",
        status=f"forgotten:{normalize.content_key(SECRET)}", source="cli"))
    (d / "S1.json").write_text(json.dumps({
        "project_slug": slug,
        section: {key: [{"id": "i-1", "text": SECRET}]}}), encoding="utf-8")
    cli.main(["status", "--project", str(proj)])
    out = capsys.readouterr().out
    assert "checkpoint surfaces: 1 item still carries a forgotten value" in out
    assert SECRET not in out


def test_human_status_names_a_machine_file_that_is_not_ok(proj, capsys):
    logs = config.log_dir()
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "checks.jsonl").write_bytes(b"not json\n")
    cli.main(["status", "--project", str(proj)])
    out = capsys.readouterr().out
    assert "ledger file logs/checks.jsonl: unreadable (1 garbage)" in out


def test_a_census_failure_never_takes_status_down(proj, monkeypatch, capsys):
    from daimon_briefing import ledger_census

    def boom(*_a, **_k):
        raise RuntimeError("census exploded")

    monkeypatch.setattr(ledger_census, "census_bucket", boom)
    payload, _rc = cli.status_payload(str(proj))
    assert payload["ledgers"] is None
    cli.main(["status", "--project", str(proj)])
    assert "ledger" not in capsys.readouterr().out.lower().replace(
        "last serialize", "")


def test_rich_status_prints_the_same_ledger_lines(proj, capsys, monkeypatch):
    from daimon_briefing import render
    (_bucket(proj) / "trust.jsonl").write_bytes(b'{"cut\n')
    monkeypatch.delenv("DAIMON_PLAIN", raising=False)
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    cli.main(["status", "--project", str(proj)])
    assert "ledger trust.jsonl: degraded (1 torn)" in capsys.readouterr().out


def test_no_bucket_name_means_no_census():
    assert cli._status_ledgers(None) is None


def test_human_status_says_the_forgotten_check_could_not_run(proj, capsys):
    d = _bucket(proj)
    (d / "events.jsonl").write_bytes(
        _line(ts="2026-01-01T00:00:00Z", kind="resolution", item_ref="i-1",
              status=f"forgotten:{normalize.content_key(SECRET)}", source="cli")
        + b'{"note": "\xff"}\n')
    (d / "trust.jsonl").write_bytes(_line(reason=SECRET))
    cli.main(["status", "--project", str(proj)])
    out = capsys.readouterr().out
    assert ("⚠ forgotten-value check unavailable: events.jsonl is unreadable"
            in out)
    assert "ledgers: " not in out
    assert SECRET not in out


def test_human_status_counts_checkpoint_files_it_could_not_scan(proj, capsys):
    d = _bucket(proj)
    (d / "events.jsonl").write_bytes(_line(
        ts="2026-01-01T00:00:00Z", kind="resolution", item_ref="i-1",
        status=f"forgotten:{normalize.content_key(SECRET)}", source="cli"))
    (d / "S1.json").write_bytes(b"not json")
    cli.main(["status", "--project", str(proj)])
    out = capsys.readouterr().out
    assert "⚠ checkpoint surfaces: 1 file could not be scanned" in out
