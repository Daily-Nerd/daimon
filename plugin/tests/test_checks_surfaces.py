"""#943 slice 5: the surfaces that say what is armed and whether it ever fired.

Constraint 2 of the idea note, in code: an empty firing log reads as SILENT,
never as clean. Every count below is lifetime and every liveness cell is a
timestamp, because a last-of-kind outcome over an append-only log is scar
0009's exact shape.

The reader is scoped to THIS project's ruling ids on purpose (scar 0055):
the firing log is global, and printing another bucket's ids into this
session's stdout writes them into this project's checkpoint.
"""

import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

from daimon_briefing import checks, checks_runtime, config, refutations

CANONICAL = Path(__file__).parents[1] / "daimon_briefing" / "checks_host.py"


def _host():
    spec = importlib.util.spec_from_file_location(
        "_checks_host_for_surfaces", CANONICAL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ch = _host()

CC = "claude-code"
MATCH = "gh pr create"
CLEAN = "#!/bin/sh\nexit 0\n"
VIOLATION = "#!/bin/sh\necho 'no em-dash in a public body' >&2\nexit 1\n"


def _sha(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _propose(project, *, body=CLEAN, match=MATCH, intent="warn",
             subject="public posts", scope="publishing"):
    return refutations.assert_ruling(
        subject=subject, verdict=f"the rule for {subject} in {scope}",
        scope=scope, evidence=["issue:943"], channel="cli-agent",
        check={"match": match, "body": body, "intent": intent},
        project_dir=str(project))


def _arm(project, *, body=CLEAN, **kw):
    ruling_id = _propose(project, body=body, **kw)
    refutations.ratify(ruling_id, channel="ui", check_sha256=_sha(body),
                       project_dir=str(project))
    return ruling_id


def _payload(command, cwd, tool="Bash"):
    return {"tool_name": tool, "tool_input": {"command": command},
            "cwd": str(cwd)}


def _fire(host, payload):
    """Drive the real host adapter so the rows under test are the rows a
    host writes, not a test's idea of them."""
    stdin = io.StringIO(json.dumps(payload))
    ch.main(host, stdin=stdin, stdout=io.StringIO(), stderr=io.StringIO())


def _log_path():
    return config.log_dir() / "checks.jsonl"


def _write_log(*lines):
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def _row(**kw):
    row = {"ts": "2026-09-06T10:00:00Z", "ruling_id": "", "host": CC,
           "mode": "", "outcome": "", "cause": "", "decision_emitted": "allow",
           "duration_ms": 0}
    row.update(kw)
    return json.dumps(row)


# ---- the first reader of the firing log -----------------------------------


def test_rows_the_real_hook_wrote_fold_per_ruling_and_host(tmp_path):
    """Written by `checks_host.main`, read by `checks.firing_summary`. A
    reader tested only against hand-built rows proves agreement with itself."""
    ruling_id = _arm(tmp_path, body=VIOLATION)
    _fire(CC, _payload(MATCH, tmp_path))
    _fire(CC, _payload(MATCH, tmp_path))
    summary = checks.firing_summary(str(tmp_path))
    assert summary.log_state == "read"
    fold = summary.rulings[(ruling_id, CC)]
    assert (fold["fired"], fold["violation"], fold["clean"]) == (2, 2, 0)
    assert fold["last_ts"]


def test_the_three_outcomes_are_counted_apart(tmp_path):
    ruling_id = _arm(tmp_path)
    _write_log(
        _row(ruling_id=ruling_id, outcome="clean", mode="warn"),
        _row(ruling_id=ruling_id, outcome="violation", mode="warn"),
        _row(ruling_id=ruling_id, outcome="unresolved", cause="check-timeout",
             mode="warn"),
    )
    fold = checks.firing_summary(str(tmp_path)).rulings[(ruling_id, CC)]
    assert (fold["clean"], fold["violation"], fold["unresolved"]) == (1, 1, 1)
    assert fold["fired"] == 3


def test_denied_counts_the_rows_the_host_was_actually_told_to_block(tmp_path):
    """A row proves the check RAN. Only `deny` closes the gap between a check
    that ran and a check that was honored, so it is its own counter."""
    ruling_id = _arm(tmp_path)
    _write_log(
        _row(ruling_id=ruling_id, outcome="violation", mode="enforce",
             decision_emitted="deny"),
        _row(ruling_id=ruling_id, outcome="violation", mode="warn",
             decision_emitted="warn"),
    )
    fold = checks.firing_summary(str(tmp_path)).rulings[(ruling_id, CC)]
    assert (fold["violation"], fold["denied"]) == (2, 1)


def test_last_fired_is_the_greatest_stamp_not_the_last_line(tmp_path):
    """Scar 0009: an append-only log is not ordered by anything but append,
    and a reader that takes the tail reports whichever row landed last."""
    ruling_id = _arm(tmp_path)
    _write_log(
        _row(ruling_id=ruling_id, outcome="clean",
             ts="2026-09-06T12:00:00Z"),
        _row(ruling_id=ruling_id, outcome="clean",
             ts="2026-09-06T09:00:00Z"),
    )
    fold = checks.firing_summary(str(tmp_path)).rulings[(ruling_id, CC)]
    assert fold["last_ts"] == "2026-09-06T12:00:00Z"


def test_a_project_level_row_is_liveness_never_a_rulings_firing(tmp_path):
    """Scar 0042: `ruling_id: ""` is a VALUE. The hook writes it for
    no-manifest, manifest-unreadable and no-match, and attributing those to a
    ruling would report a check that never ran."""
    _arm(tmp_path)
    _write_log(
        _row(cause="no-match", ts="2026-09-06T08:00:00Z"),
        _row(cause="no-manifest", host="codex", ts="2026-09-06T09:00:00Z"),
    )
    summary = checks.firing_summary(str(tmp_path))
    assert summary.rulings == {}
    assert summary.hook_seen[CC] == {"rows": 1,
                                     "last_ts": "2026-09-06T08:00:00Z"}
    assert summary.hook_seen["codex"]["last_ts"] == "2026-09-06T09:00:00Z"


def test_another_projects_ruling_id_never_reaches_the_summary(tmp_path):
    """Scar 0055: the log is global and rendering is a write. An id this
    project's ledger does not carry is discarded before anything can print
    it."""
    other = tmp_path / "elsewhere"
    other.mkdir()
    mine = _arm(tmp_path)
    theirs = _arm(other, subject="their posts")
    _write_log(_row(ruling_id=mine, outcome="clean"),
               _row(ruling_id=theirs, outcome="violation"))
    summary = checks.firing_summary(str(tmp_path))
    assert list(summary.rulings) == [(mine, CC)]
    assert summary.totals["violation"] == 0


def test_a_malformed_line_never_sinks_the_read(tmp_path):
    ruling_id = _arm(tmp_path)
    _write_log("not json", "[]", "null", "3", "",
               _row(ruling_id=ruling_id, outcome="clean"))
    summary = checks.firing_summary(str(tmp_path))
    assert summary.rulings[(ruling_id, CC)]["clean"] == 1


def test_a_row_with_an_unknown_cause_is_still_a_firing(tmp_path):
    """`log_firing` rewrites a cause outside its set to the literal
    `unknown`, so the state is signalled without bringing a path along. A
    reader that only knew the closed set would drop the row entirely."""
    ruling_id = _arm(tmp_path)
    _write_log(_row(ruling_id=ruling_id, outcome="unresolved",
                    cause="unknown"))
    fold = checks.firing_summary(str(tmp_path)).rulings[(ruling_id, CC)]
    assert (fold["fired"], fold["unresolved"]) == (1, 1)


def test_an_absent_log_is_silent_not_clean(tmp_path):
    _arm(tmp_path)
    summary = checks.firing_summary(str(tmp_path))
    assert summary.log_state == "absent"
    assert summary.rulings == {} and summary.hook_seen == {}
    assert summary.totals["fired"] == 0


def test_an_unreadable_log_says_so_rather_than_reading_empty(tmp_path):
    _arm(tmp_path)
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe not utf-8\n")
    assert checks.firing_summary(str(tmp_path)).log_state == "unreadable"


def test_the_summary_never_raises_when_the_ledger_read_explodes(
        tmp_path, monkeypatch):
    monkeypatch.setattr(refutations, "listing",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("x")))
    summary = checks.firing_summary(str(tmp_path))
    assert summary.rulings == {} and summary.totals["fired"] == 0


def test_totals_aggregate_every_host_for_this_project(tmp_path):
    """The CLI cannot know which host it is on, so the stats line sums them.
    The per-host split lives in `ruling checks`."""
    ruling_id = _arm(tmp_path)
    _write_log(_row(ruling_id=ruling_id, outcome="clean"),
               _row(ruling_id=ruling_id, outcome="violation", host="codex",
                    decision_emitted="deny"))
    totals = checks.firing_summary(str(tmp_path)).totals
    assert totals == {"fired": 2, "clean": 1, "violation": 1, "unresolved": 0,
                      "denied": 1, "last_ts": "2026-09-06T10:00:00Z"}


def test_one_ruling_folds_across_hosts_for_the_show_line(tmp_path):
    ruling_id = _arm(tmp_path)
    _write_log(
        _row(ruling_id=ruling_id, outcome="clean", ts="2026-09-06T08:00:00Z"),
        _row(ruling_id=ruling_id, outcome="violation", host="codex",
             ts="2026-09-06T11:00:00Z"),
    )
    fold = checks.firing_summary(str(tmp_path)).for_ruling(ruling_id)
    assert fold["fired"] == 2 and fold["clean"] == 1 and fold["violation"] == 1
    assert (fold["last_ts"], fold["host"]) == ("2026-09-06T11:00:00Z", "codex")


def test_a_ruling_with_no_rows_folds_to_a_never_fired_shape(tmp_path):
    ruling_id = _arm(tmp_path)
    fold = checks.firing_summary(str(tmp_path)).for_ruling(ruling_id)
    assert fold["fired"] == 0 and fold["last_ts"] == ""


def test_the_reader_uses_the_same_directory_the_hook_writes_through(
        tmp_path, monkeypatch):
    """Scar 0043: `checks_runtime.log_dir()` is a hand-copied mirror of
    `config.log_dir()`. A reader resolving the path any other way reads an
    empty directory while reporting checks armed."""
    assert Path(ch.runtime().log_dir()) == Path(config.log_dir())


@pytest.mark.parametrize("state", ["candidate", "overturned"])
def test_a_ruling_that_is_not_active_still_owns_its_past_firings(
        tmp_path, state):
    """Lifecycle gates whether a check is ARMED. It does not un-fire what
    already ran, and a disarmed ruling whose counts vanished would read as a
    check that never guarded anything."""
    ruling_id = (_propose(tmp_path) if state == "candidate"
                 else _arm(tmp_path))
    if state == "overturned":
        refutations.retire(ruling_id, channel="cli-tty",
                           project_dir=str(tmp_path))
    _write_log(_row(ruling_id=ruling_id, outcome="clean"))
    summary = checks.firing_summary(str(tmp_path))
    assert summary.rulings[(ruling_id, CC)]["clean"] == 1


# ---- the manifest audit: what the hooks read vs what the ledger wants -----


def _manifest_path():
    return config.checks_dir() / "manifest.json"


def _write_manifest(entries):
    path = _manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


def _entry(ruling_id, project, *, body=CLEAN, match=MATCH, intent="warn"):
    return {"ruling_id": ruling_id, "project_dir": str(project),
            "match": match, "intent": intent, "sha256": _sha(body),
            "armed_at": "2026-09-06T10:00:00Z"}


def test_an_armed_ruling_that_synced_is_in_step(tmp_path):
    ruling_id = _arm(tmp_path)
    checks.sync(str(tmp_path))
    audit = checks.audit(str(tmp_path))
    assert audit.state == "read" and audit.drift is False
    assert audit.wanted == [ruling_id] and audit.have == [ruling_id]
    assert (audit.missing, audit.stale) == ([], [])
    assert (audit.body_missing, audit.body_mismatch) == ([], [])


def test_a_wanted_ruling_the_manifest_never_got_is_missing(tmp_path):
    ruling_id = _arm(tmp_path)
    checks.sync(str(tmp_path))
    _write_manifest([])
    audit = checks.audit(str(tmp_path))
    assert audit.missing == [ruling_id] and audit.drift is True
    assert audit.have == []


def test_a_manifest_pinned_to_a_body_the_ledger_no_longer_wants_drifts(
        tmp_path):
    """A re-pinned hash is BOTH: missing at the hash the ledger wants and
    stale at the hash the manifest still names. Reporting only one half hides
    which side moved."""
    ruling_id = _arm(tmp_path)
    checks.sync(str(tmp_path))
    _write_manifest([_entry(ruling_id, config.resolve_project_dir(str(tmp_path)),
                            body=VIOLATION)])
    audit = checks.audit(str(tmp_path))
    assert audit.missing == [ruling_id] and audit.stale == [ruling_id]
    assert audit.drift is True


def test_an_entry_for_a_ruling_that_was_retired_is_stale(tmp_path):
    ruling_id = _arm(tmp_path)
    checks.sync(str(tmp_path))
    root = config.resolve_project_dir(str(tmp_path))
    refutations.retire(ruling_id, channel="cli-tty", project_dir=str(tmp_path))
    _write_manifest([_entry(ruling_id, root)])
    audit = checks.audit(str(tmp_path))
    assert audit.wanted == [] and audit.stale == [ruling_id]
    assert audit.drift is True


def test_a_body_the_manifest_names_and_the_disk_does_not_have_is_drift(
        tmp_path):
    ruling_id = _arm(tmp_path)
    checks.sync(str(tmp_path))
    entry = checks_runtime.load_manifest(_manifest_path()).entries[0]
    checks_runtime.body_path(entry, config.checks_dir()).unlink()
    audit = checks.audit(str(tmp_path))
    assert audit.body_missing == [ruling_id] and audit.drift is True
    assert audit.body_mismatch == []


def test_a_body_edited_out_of_band_is_caught_before_the_runner_sees_it(
        tmp_path):
    """The fourth state a manifest cannot express. The runner catches it at
    exec time as body-hash-mismatch, one action too late to be a report."""
    ruling_id = _arm(tmp_path)
    checks.sync(str(tmp_path))
    entry = checks_runtime.load_manifest(_manifest_path()).entries[0]
    path = checks_runtime.body_path(entry, config.checks_dir())
    path.chmod(0o600)
    path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    audit = checks.audit(str(tmp_path))
    assert audit.body_mismatch == [ruling_id] and audit.drift is True
    assert audit.body_missing == []


def test_no_manifest_with_nothing_wanted_is_not_drift(tmp_path):
    """An install that armed nothing is the ordinary state, not a fault."""
    audit = checks.audit(str(tmp_path))
    assert audit.state == "absent" and audit.drift is False
    assert audit.wanted == [] and audit.have == []


def test_no_manifest_with_something_wanted_is_drift(tmp_path):
    ruling_id = _arm(tmp_path)
    checks.sync(str(tmp_path))
    _manifest_path().unlink()
    audit = checks.audit(str(tmp_path))
    assert audit.state == "absent" and audit.drift is True
    assert audit.missing == [ruling_id]


def test_an_unreadable_manifest_is_its_own_state_not_an_empty_one(tmp_path):
    """daimon wrote something it can no longer read. Folding that into
    "nothing armed" would report a broken install as a fresh one."""
    _arm(tmp_path)
    _manifest_path().write_text("{not json", encoding="utf-8")
    audit = checks.audit(str(tmp_path))
    assert audit.state == "unreadable" and audit.drift is True


def test_another_projects_entries_are_neither_wanted_nor_stale(tmp_path):
    """The manifest is global. An entry belonging to a bucket this command
    was not asked about is not this project's drift, and its id must never
    reach a line this project prints (scar 0055)."""
    other = tmp_path / "elsewhere"
    other.mkdir()
    mine = _arm(tmp_path)
    theirs = _arm(other, subject="their posts")
    checks.sync(str(tmp_path))
    checks.sync(str(other))
    audit = checks.audit(str(tmp_path))
    assert audit.wanted == [mine] and audit.have == [mine]
    assert theirs not in audit.stale and audit.drift is False


def test_the_audit_carries_ids_and_nothing_else(tmp_path):
    """No match patterns, no bodies, no project directories: every list is a
    list of strings a caller may print."""
    _arm(tmp_path)
    checks.sync(str(tmp_path))
    audit = checks.audit(str(tmp_path))
    for name in ("wanted", "have", "missing", "stale", "body_missing",
                 "body_mismatch"):
        values = getattr(audit, name)
        assert all(isinstance(v, str) and v.startswith("r-") for v in values)


def test_the_audit_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(refutations, "listing",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("x")))
    audit = checks.audit(str(tmp_path))
    assert audit.state == "unreadable" and audit.drift is False


def test_a_candidate_check_is_never_wanted(tmp_path):
    """`proposed` is a lifecycle, not a mode. A manifest that carried one
    would run code no human ratified."""
    _propose(tmp_path)
    audit = checks.audit(str(tmp_path))
    assert audit.wanted == [] and audit.drift is False


# ---- `check sync --check`: the audit as a read-only verb ------------------


def _run(argv):
    from daimon_briefing import cli
    return cli.main(argv)


def test_check_sync_check_exits_zero_and_says_in_step(tmp_path, capsys):
    _arm(tmp_path)
    checks.sync(str(tmp_path))
    rc = _run(["check", "sync", "--check", "--project", str(tmp_path)])
    assert rc == 0
    assert "checks manifest: in step, 1 armed" in capsys.readouterr().out


def test_check_sync_check_exits_one_on_drift_and_names_the_ids(
        tmp_path, capsys):
    ruling_id = _arm(tmp_path)
    checks.sync(str(tmp_path))
    _write_manifest([])
    rc = _run(["check", "sync", "--check", "--project", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "checks manifest: drifted" in out
    assert f"missing from the manifest: {ruling_id}" in out
    assert "fix: daimon check sync" in out


def test_check_sync_check_exits_three_when_the_manifest_cannot_be_read(
        tmp_path, capsys):
    """Distinct from drift on purpose. Drift is a repair the fix line names;
    a manifest daimon cannot parse is a state where the audit has no opinion
    about what is armed, and a script must be able to tell them apart."""
    _arm(tmp_path)
    _manifest_path().write_text("{not json", encoding="utf-8")
    rc = _run(["check", "sync", "--check", "--project", str(tmp_path)])
    assert rc == 3
    assert "checks manifest: could not be read" in capsys.readouterr().out


def test_check_sync_check_writes_nothing(tmp_path):
    """The whole point of the flag. A repair disguised as an audit reports a
    clean machine it just made clean."""
    _arm(tmp_path)
    checks.sync(str(tmp_path))
    _write_manifest([])
    _run(["check", "sync", "--check", "--project", str(tmp_path)])
    assert checks_runtime.load_manifest(_manifest_path()).entries == []


def test_check_sync_check_json_carries_every_audit_field(tmp_path, capsys):
    ruling_id = _arm(tmp_path)
    checks.sync(str(tmp_path))
    _run(["check", "sync", "--check", "--json", "--project", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)
    assert list(payload) == ["state", "wanted", "have", "missing", "stale",
                             "body_missing", "body_mismatch", "drift"]
    assert payload["wanted"] == [ruling_id] and payload["drift"] is False


def test_check_sync_json_without_the_flag_still_reports_the_sync(
        tmp_path, capsys):
    _arm(tmp_path)
    rc = _run(["check", "sync", "--json", "--project", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0 and payload["ok"] is True and payload["armed"] == 1


def test_check_sync_check_on_an_empty_ledger_is_zero_with_a_line(
        tmp_path, capsys):
    """Scar 0057: a reporting read never refuses to signal a state. Nothing
    armed anywhere is the ordinary answer, not a failure."""
    rc = _run(["check", "sync", "--check", "--project", str(tmp_path)])
    assert rc == 0
    assert "checks manifest: in step, 0 armed" in capsys.readouterr().out
