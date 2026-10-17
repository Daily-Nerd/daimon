"""`daimon stats` worldcheck section (#1184): notes contradicted at least once
out of notes checked at least once, beside the per-check counts, over ONE
window. Outcomes are recorded from the day the file exists and usage.log is
all-time, so the per-check half is cut at the earliest outcome row."""

import json

import pytest

from daimon_briefing import cli, config, store

CLASSES = ("branch-state", "file-exists", "pr-state")


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path / "proj"))
    return str(tmp_path / "proj")


def _outcomes(proj, rows):
    path = config.checkpoint_dir() / store.project_slug(proj) / (
        "worldcheck-outcomes.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows),
                    encoding="utf-8")


def _row(ref, outcome, ts, *, check="pr-state", key="a" * 64):
    return {"ts": ts, "item_ref": ref, "check": check, "outcome": outcome,
            "claim_key": key}


def _usage(*lines):
    log = config.log_dir() / "usage.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(f"{ts} {cmd}\n" for ts, cmd in lines),
                   encoding="utf-8")


def _slug(proj):
    return store.project_slug(proj)


def _stats(capsys, *args):
    assert cli.main(["stats", *args]) == 0
    return capsys.readouterr().out


def test_no_outcomes_is_null_never_a_ratio(proj, capsys):
    _usage(("2026-10-01T00:00:00Z",
            f"worldcheck:pr-state:confirmed:{_slug(proj)}"))
    out = json.loads(_stats(capsys, "--json"))
    nulls = {"notes_checked": None, "notes_contradicted": None,
             "checks_confirmed": None, "checks_contradicted": None,
             "unscoped_checks": None}
    assert out["worldcheck"] == {"since": None,
                                 "classes": {c: nulls for c in CLASSES}}
    text = _stats(capsys)
    assert "worldcheck (this project)" in text
    assert "none recorded" in text


def test_notes_are_counted_once_each_across_both_outcomes(proj, capsys):
    other = "b" * 64
    _outcomes(proj, [
        _row("o-a", "confirmed", "2026-10-02T10:00:00.500000Z"),
        _row("o-a", "contradicted", "2026-10-03T10:00:00.000000Z"),  # same note
        _row("o-b", "confirmed", "2026-10-02T11:00:00.000000Z"),
        _row("o-c", "contradicted", "2026-10-02T12:00:00.000000Z"),
        _row("o-a", "confirmed", "2026-10-04T10:00:00.000000Z", key=other),
    ])
    pr = json.loads(_stats(capsys, "--json"))["worldcheck"]["classes"]["pr-state"]
    assert (pr["notes_checked"], pr["notes_contradicted"]) == (4, 2)
    fe = json.loads(_stats(capsys, "--json"))["worldcheck"]["classes"]["file-exists"]
    assert fe["notes_checked"] is None and fe["notes_contradicted"] is None


def test_per_check_counts_share_the_window_of_the_outcomes(proj, capsys):
    _outcomes(proj, [_row("o-a", "confirmed", "2026-10-02T10:00:00.500000Z")])
    me = _slug(proj)
    _usage(
        ("2026-10-02T09:59:59Z", f"worldcheck:pr-state:confirmed:{me}"),   # before
        ("2026-10-02T10:00:00Z", f"worldcheck:pr-state:confirmed:{me}"),   # same second
        ("2026-10-02T10:00:01Z", f"worldcheck:pr-state:contradicted:{me}"),
        ("2026-10-02T10:00:02Z", f"worldcheck:pr-state:skipped-cap:{me}"),  # not a check
        ("2026-10-02T10:00:03Z", f"worldcheck:file-exists:confirmed:{me}"),
        ("2026-10-02T10:00:04Z", "worldcheck:receipt-probe:confirmed:" + me),
        ("2026-10-02T10:00:05Z", "brief"))
    w = json.loads(_stats(capsys, "--json"))["worldcheck"]
    assert w["since"] == "2026-10-02T10:00:00Z"
    assert w["classes"]["pr-state"]["checks_confirmed"] == 1
    assert w["classes"]["pr-state"]["checks_contradicted"] == 1
    assert w["classes"]["file-exists"]["checks_confirmed"] == 1
    assert w["classes"]["file-exists"]["checks_contradicted"] == 0


def test_only_this_projects_checks_are_counted(proj, capsys):
    _outcomes(proj, [_row("o-a", "confirmed", "2026-10-02T10:00:00.000000Z")])
    me, other = _slug(proj), "-some-other-project"
    _usage(
        ("2026-10-03T00:00:00Z", f"worldcheck:pr-state:confirmed:{me}"),
        ("2026-10-03T00:00:01Z", f"worldcheck:pr-state:confirmed:{other}"),
        ("2026-10-03T00:00:02Z", f"worldcheck:pr-state:contradicted:{other}"))
    pr = json.loads(_stats(capsys, "--json"))["worldcheck"]["classes"]["pr-state"]
    assert (pr["checks_confirmed"], pr["checks_contradicted"]) == (1, 0)
    assert pr["unscoped_checks"] == 0


def test_a_line_without_a_slug_in_the_window_is_unscoped_not_counted(
        proj, capsys):
    _outcomes(proj, [_row("o-a", "confirmed", "2026-10-02T10:00:00.000000Z")])
    me = _slug(proj)
    _usage(
        ("2026-10-01T00:00:00Z", "worldcheck:pr-state:confirmed"),   # before
        ("2026-10-03T00:00:00Z", "worldcheck:pr-state:confirmed"),
        ("2026-10-03T00:00:01Z", "worldcheck:pr-state:contradicted"),
        ("2026-10-03T00:00:02Z", f"worldcheck:pr-state:confirmed:{me}"),
        ("2026-10-03T00:00:03Z", "worldcheck:pr-state:skipped-cap"),
        ("2026-10-03T00:00:04Z", "worldcheck:pr-state:confirmed extra"))  # 3 fields
    out = _stats(capsys, "--json")
    pr = json.loads(out)["worldcheck"]["classes"]["pr-state"]
    assert (pr["checks_confirmed"], pr["checks_contradicted"]) == (1, 0)
    assert pr["unscoped_checks"] == 2
    text = _stats(capsys)
    assert "unscoped checks" in text and "pr-state 2" in text


def test_the_text_names_its_units_and_its_window(proj, capsys):
    _outcomes(proj, [_row("o-a", "contradicted", "2026-10-02T10:00:00.000000Z"),
                     _row("o-b", "confirmed", "2026-10-02T10:00:00.000000Z")])
    _usage(("2026-10-02T10:00:01Z",
            f"worldcheck:pr-state:contradicted:{_slug(proj)}"))
    text = _stats(capsys)
    assert "worldcheck (this project)" in text
    assert "since 2026-10-02T10:00:00Z (notes and checks: this project)" in text
    assert ("pr-state: 1 of 2 notes contradicted at least once; "
            "checks: 0 confirmed, 1 contradicted") in text
    assert "this machine" not in text.split("worldcheck (this project)")[1].split(
        "resolutions")[0]
    assert "unscoped" not in text
    assert "file-exists: none recorded" in text


def test_stats_json_carries_worldcheck_at_the_tail(proj, capsys):
    payload = json.loads(_stats(capsys, "--json"))
    assert list(payload)[-3:] == ["checks", "stitching", "worldcheck"]


def test_the_window_closes_at_now_so_a_future_stamp_is_outside_it(proj, capsys):
    """One window, [since, now], for both halves: a line stamped in the future
    belongs to no window the outcomes could have covered."""
    _outcomes(proj, [_row("o-a", "confirmed", "2026-10-02T10:00:00.000000Z")])
    me = _slug(proj)
    _usage(("2026-10-03T00:00:00Z", f"worldcheck:pr-state:confirmed:{me}"),
           ("2999-01-01T00:00:00Z", f"worldcheck:pr-state:contradicted:{me}"),
           ("2999-01-01T00:00:01Z", "worldcheck:pr-state:contradicted"))
    pr = json.loads(_stats(capsys, "--json"))["worldcheck"]["classes"]["pr-state"]
    assert (pr["checks_confirmed"], pr["checks_contradicted"]) == (1, 0)
