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
import os
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
    assert summary.hook_seen[CC] == {"rows": 1, "scope": "machine",
                                     "last_ts": "2026-09-06T08:00:00Z"}
    assert summary.hook_seen["codex"]["last_ts"] == "2026-09-06T09:00:00Z"


def test_hook_liveness_is_machine_wide_and_says_so(tmp_path):
    """A project-level row carries no cwd and no project (spec 3.4), so it
    cannot be scoped and must not be presented as this project's liveness.
    Rendering another project's activity stamp as this one's writes that
    project's timeline into this transcript (scar 0055)."""
    _arm(tmp_path)
    _write_log(_row(cause="no-match"))
    assert checks.firing_summary(str(tmp_path)).hook_seen[CC]["scope"] == \
        "machine"


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


# ---- `ruling checks`: what is armed, and what each host does with it ------


def _checks_table(project, *extra):
    from daimon_briefing import cli
    return cli.main(["ruling", "checks", "--project", str(project), *extra])


def test_ruling_checks_crosses_every_ruling_with_every_host(tmp_path, capsys):
    """The CLI has no notion of which host it is on, so the table enumerates
    the profiles rather than guessing. A missing row is a host whose column
    an author would never see."""
    ruling_id = _arm(tmp_path)
    checks.sync(str(tmp_path))
    assert _checks_table(tmp_path) == 0
    out = capsys.readouterr().out
    assert ruling_id in out
    for host in ("claude-code", "codex", "windsurf"):
        assert host in out


def test_the_mode_column_is_what_the_host_delivers_not_what_was_asked(
        tmp_path, capsys):
    """Spec 5. An author asked for warn; codex documents no warn channel and
    windsurf is unmeasured, so the same intent reads three ways."""
    _arm(tmp_path, intent="warn")
    checks.sync(str(tmp_path))
    _checks_table(tmp_path)
    out = capsys.readouterr().out
    assert "claude-code   warn" in out
    assert "codex         record-only" in out
    assert "windsurf      unsupported" in out


def test_never_fired_is_not_the_same_cell_as_zero_counts(tmp_path, capsys):
    """Constraint 2. A check that has never run and a check that ran and
    found nothing are different facts, and the second is the only one that
    says the wiring works."""
    ruling_id = _arm(tmp_path)
    checks.sync(str(tmp_path))
    _checks_table(tmp_path)
    before = [ln for ln in capsys.readouterr().out.splitlines()
              if CC in ln][0]
    assert before.endswith("never fired")
    _write_log(_row(ruling_id=ruling_id, outcome="clean"))
    _checks_table(tmp_path)
    out = capsys.readouterr().out
    after = [ln for ln in out.splitlines() if CC in ln][0]
    assert "never fired" not in after
    assert "lifetime 1 clean, 0 violation, 0 unresolved" in after
    # The host that has not seen it still says so: liveness is per host.
    assert [ln for ln in out.splitlines()
            if "codex" in ln][0].endswith("never fired")


def test_a_proposed_check_shows_no_liveness_cell(tmp_path, capsys):
    """Spec 8.5's own test. `proposed` is a lifecycle, not a mode: nothing
    is armed, so there is nothing that could have fired, and a `never fired`
    cell would report a wiring that does not exist."""
    _propose(tmp_path)
    _checks_table(tmp_path)
    out = capsys.readouterr().out
    assert "proposed, not armed" in out
    assert "never fired" not in out and "lifetime" not in out


def test_a_disarmed_check_shows_no_liveness_cell(tmp_path, capsys):
    ruling_id = _arm(tmp_path)
    refutations.retire(ruling_id, channel="cli-tty", project_dir=str(tmp_path))
    _checks_table(tmp_path)
    out = capsys.readouterr().out
    assert "disarmed" in out
    assert "never fired" not in out and "lifetime" not in out


def test_an_unsupported_host_shows_no_liveness_cell(tmp_path, capsys):
    """A column that reads `unsupported` has no channel to fire through, so
    a count beside it would be a number about nothing."""
    _arm(tmp_path)
    checks.sync(str(tmp_path))
    _checks_table(tmp_path)
    line = [ln for ln in capsys.readouterr().out.splitlines()
            if "windsurf" in ln][0]
    assert line.strip() == "windsurf      unsupported"


def test_the_header_reports_the_manifest_state(tmp_path, capsys):
    _arm(tmp_path)
    checks.sync(str(tmp_path))
    _checks_table(tmp_path)
    assert "armed 1 of 1 wanted" in capsys.readouterr().out
    _write_manifest([])
    _checks_table(tmp_path)
    assert "manifest drifted, run daimon check sync" in capsys.readouterr().out


def test_the_header_tells_no_manifest_from_an_unreadable_one(
        tmp_path, capsys):
    _arm(tmp_path)
    checks.sync(str(tmp_path))
    _manifest_path().unlink()
    _checks_table(tmp_path)
    assert "no manifest" in capsys.readouterr().out
    _manifest_path().write_text("{not json", encoding="utf-8")
    _checks_table(tmp_path)
    assert "manifest unreadable" in capsys.readouterr().out


def test_the_header_reports_every_host_the_hook_ran_on(tmp_path, capsys):
    """The project-level rows. They prove the hook is wired even when no
    check of this project's ever matched a command."""
    _arm(tmp_path)
    checks.sync(str(tmp_path))
    _write_log(_row(cause="no-match", ts="2026-09-06T08:00:00Z"),
               _row(cause="no-manifest", host="codex",
                    ts="2026-09-06T09:00:00Z"))
    _checks_table(tmp_path)
    out = capsys.readouterr().out
    assert ("hook seen on claude-code (any project), "
            "last 2026-09-06T08:00:00Z") in out
    assert "hook seen on codex (any project), last 2026-09-06T09:00:00Z" in out


def test_the_json_hosts_object_labels_the_line_machine_wide(tmp_path,
                                                            capsys):
    _arm(tmp_path)
    _write_log(_row(cause="no-match"))
    _checks_table(tmp_path, "--json")
    assert json.loads(capsys.readouterr().out)["hosts"][CC]["scope"] == \
        "machine"


def test_a_ledger_with_no_checks_says_so_at_zero(tmp_path, capsys):
    """Scar 0057: a reporting read never refuses to signal a state."""
    refutations.assert_ruling(
        subject="no check here", verdict="a rule with nothing to run",
        scope="publishing", evidence=["issue:943"], channel="cli-tty",
        ratified=True, project_dir=str(tmp_path))
    assert _checks_table(tmp_path) == 0
    assert "no rulings carry a check" in capsys.readouterr().out


def test_another_projects_ruling_never_reaches_the_table(tmp_path, capsys):
    """Scar 0055. Both the manifest and the firing log are global; the table
    is scoped by project equality on this project's ledger."""
    other = tmp_path / "elsewhere"
    other.mkdir()
    mine = _arm(tmp_path)
    theirs = _arm(other, subject="their posts", match="git push --force")
    checks.sync(str(tmp_path))
    checks.sync(str(other))
    _write_log(_row(ruling_id=theirs, outcome="violation"))
    _checks_table(tmp_path)
    out = capsys.readouterr().out
    assert mine in out and theirs not in out
    assert "git push --force" not in out


def test_ruling_checks_json_has_a_fixed_shape(tmp_path, capsys):
    ruling_id = _arm(tmp_path)
    checks.sync(str(tmp_path))
    _write_log(_row(ruling_id=ruling_id, outcome="clean"))
    _checks_table(tmp_path, "--json")
    payload = json.loads(capsys.readouterr().out)
    assert list(payload) == ["rows", "manifest", "hosts", "log"]
    row = payload["rows"][0]
    assert list(row) == ["ruling_id", "lifecycle", "intent", "host", "mode",
                         "last_fired", "clean", "violation", "unresolved"]
    assert len(payload["rows"]) == 3  # one per host profile
    assert payload["manifest"]["drift"] is False


def test_ruling_checks_json_says_null_where_there_is_no_liveness_cell(
        tmp_path, capsys):
    """Not zero. A JSON consumer that read 0 clean for a proposed check
    would report a check that ran and found nothing."""
    _propose(tmp_path)
    _checks_table(tmp_path, "--json")
    row = json.loads(capsys.readouterr().out)["rows"][0]
    assert row["last_fired"] is None and row["clean"] is None


def test_ruling_checks_json_on_an_empty_ledger_is_still_the_shape(
        tmp_path, capsys):
    assert _checks_table(tmp_path, "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows"] == []
    assert list(payload) == ["rows", "manifest", "hosts", "log"]


def test_ruling_checks_takes_no_slug(tmp_path):
    from daimon_briefing import cli
    with pytest.raises(SystemExit):
        cli.main(["ruling", "checks", "--slug", "-some-bucket"])


# ---- the `daimon stats` line, three wordings, plain and rich -------------
#
# Aggregated across hosts on purpose: the CLI cannot know which host it is
# running on, and `ruling checks` is where the per-host split lives.


def _stats(tmp_path, *extra):
    from daimon_briefing import cli
    return cli.main(["stats", *extra])


def test_stats_checks_line_says_none_armed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    assert _stats(tmp_path) == 0
    assert "checks: none armed" in capsys.readouterr().out


def test_stats_checks_line_says_armed_but_never_fired(
        tmp_path, monkeypatch, capsys):
    """Constraint 2, on the surface an operator actually reads. Before this
    line an armed check that never ran was indistinguishable from one that
    ran clean every time."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm(tmp_path)
    assert _stats(tmp_path) == 0
    assert "checks: 1 armed, never fired" in capsys.readouterr().out


def test_stats_checks_line_renders_lifetime_counts(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    ruling_id = _arm(tmp_path)
    _write_log(
        _row(ruling_id=ruling_id, outcome="clean"),
        _row(ruling_id=ruling_id, outcome="violation", mode="enforce",
             decision_emitted="deny"),
        _row(ruling_id=ruling_id, outcome="unresolved", cause="check-timeout"),
    )
    assert _stats(tmp_path) == 0
    assert ("checks (lifetime): 3 fired, 1 clean, 1 violation, "
            "1 unresolved, 1 denied") in capsys.readouterr().out


def test_stats_json_carries_checks_at_the_tail(tmp_path, monkeypatch, capsys):
    """Key order is part of the --json contract, so a new fact is appended."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm(tmp_path)
    assert _stats(tmp_path, "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert list(payload)[-1] == "checks"
    assert payload["checks"] == {"armed": 1, "proposed": 0, "fired": 0,
                                 "clean": 0, "violation": 0, "unresolved": 0,
                                 "denied": 0, "log_state": "absent"}


@pytest.mark.parametrize("build,wording", [
    (lambda p: None, "checks: none armed"),
    (lambda p: _arm(p), "checks: 1 armed, never fired"),
])
def test_the_rich_stats_block_carries_the_same_wording(
        build, wording, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("daimon_briefing.render.supports_rich", lambda: True)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    build(tmp_path)
    assert _stats(tmp_path) == 0
    out = capsys.readouterr().out
    assert "checks (this project)" in out
    assert wording in out


def test_the_rich_stats_block_carries_the_lifetime_wording(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("daimon_briefing.render.supports_rich", lambda: True)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    ruling_id = _arm(tmp_path)
    _write_log(_row(ruling_id=ruling_id, outcome="clean"))
    assert _stats(tmp_path) == 0
    assert ("checks (lifetime): 1 fired, 1 clean, 0 violation, "
            "0 unresolved, 0 denied") in capsys.readouterr().out


def test_a_proposed_check_is_counted_but_never_called_armed(
        tmp_path, monkeypatch, capsys):
    """`none armed` is the honest reading of a project whose only check is a
    candidate: nothing runs before any action."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _propose(tmp_path)
    assert _stats(tmp_path, "--json") == 0
    assert json.loads(capsys.readouterr().out)["checks"]["proposed"] == 1
    assert _stats(tmp_path) == 0
    assert "checks: none armed" in capsys.readouterr().out


def test_another_projects_firings_never_reach_this_projects_stats(tmp_path,
                                                                  monkeypatch,
                                                                  capsys):
    other = tmp_path / "elsewhere"
    other.mkdir()
    _arm(tmp_path)
    theirs = _arm(other, subject="their posts")
    _write_log(_row(ruling_id=theirs, outcome="violation"))
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    assert _stats(tmp_path) == 0
    assert "checks: 1 armed, never fired" in capsys.readouterr().out


# ---- the `daimon status` line, quiet by default ---------------------------


def _status(tmp_path, *extra):
    from daimon_briefing import cli
    return cli.main(["status", *extra])


def test_status_says_nothing_when_no_check_exists(tmp_path, monkeypatch,
                                                  capsys):
    """The quiet-by-default family (#113's rule): no line, no false alarm on
    a machine that never used the feature."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _status(tmp_path)
    assert "checks:" not in capsys.readouterr().out


def test_status_reports_an_armed_check_that_has_never_fired(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm(tmp_path)
    _status(tmp_path)
    assert "checks: 1 armed, never fired" in capsys.readouterr().out


def test_status_reports_the_age_of_the_last_firing(tmp_path, monkeypatch,
                                                   capsys):
    import time as _time

    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    ruling_id = _arm(tmp_path)
    stamp = _time.strftime("%Y-%m-%dT%H:%M:%SZ",
                           _time.gmtime(_time.time() - 7200))
    _write_log(_row(ruling_id=ruling_id, outcome="clean", ts=stamp))
    _status(tmp_path)
    assert "checks: 1 armed, last fired 2h ago" in capsys.readouterr().out


def test_status_counts_proposed_checks_beside_armed_ones(
        tmp_path, monkeypatch, capsys):
    """A candidate arms nothing, so it cannot be counted as armed. It is
    still the thing a human has to act on, so it is not silent either."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _propose(tmp_path)
    _status(tmp_path)
    assert "checks: 0 armed (1 proposed), never fired" in capsys.readouterr().out


def test_status_appends_the_drift_pointer(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm(tmp_path)
    checks.sync(str(tmp_path))
    _write_manifest([])
    _status(tmp_path)
    out = capsys.readouterr().out
    assert ("checks: 1 armed, never fired · manifest drifted, "
            "run daimon check sync") in out


def test_status_json_carries_checks_at_the_tail(tmp_path, monkeypatch,
                                                capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm(tmp_path)
    _status(tmp_path, "--json")
    payload = json.loads(capsys.readouterr().out)
    assert list(payload)[-1] == "checks"
    assert payload["checks"]["armed"] == 1
    assert payload["checks"]["last_ts"] == ""


def test_status_json_carries_null_when_nothing_is_armed_or_proposed(
        tmp_path, monkeypatch, capsys):
    """dict-or-None, the same optional-fact convention `handoff` and
    `recall_index` use — never a fabricated zero shape."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _status(tmp_path, "--json")
    assert json.loads(capsys.readouterr().out)["checks"] is None


def test_the_status_line_never_moves_the_exit_code(tmp_path, monkeypatch):
    """rc is a checkpoint-presence test and nothing else. Drift does not
    change it, the same way hook drift never has."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm(tmp_path)
    checks.sync(str(tmp_path))
    before = _status(tmp_path)
    _write_manifest([])
    assert _status(tmp_path) == before


def test_the_status_fact_fails_open_to_no_line(tmp_path, monkeypatch,
                                               capsys):
    """Every best-effort status fact is wrapped this way: a broken reader
    must never take `status` down with it."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm(tmp_path)
    monkeypatch.setattr(checks, "audit",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert _status(tmp_path) in (0, 1)
    assert "checks:" not in capsys.readouterr().out


def test_the_rich_status_line_carries_the_same_wording(tmp_path, monkeypatch,
                                                       capsys):
    from daimon_briefing import render

    monkeypatch.setattr(render, "supports_rich", lambda: True)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm(tmp_path)
    _status(tmp_path)
    assert "checks: 1 armed, never fired" in capsys.readouterr().out


def test_render_status_tolerates_a_dict_without_the_checks_key(capsys):
    """`render_status` is called with hand-built dicts in this suite and by
    the MCP lane; a new fact must not make a missing key an exception."""
    from daimon_briefing import render

    render.render_status({"project": "/p", "proj": {"exists": False},
                          "glob": {"exists": False}, "last": None})
    assert "checks:" not in capsys.readouterr().out


# ---- `ruling show`: liveness beside the check it already prints -----------


def _show(tmp_path, ruling_id, *extra):
    from daimon_briefing import cli
    return cli.main(["ruling", "show", ruling_id, "--project", str(tmp_path),
                     *extra])


def test_show_says_never_for_an_armed_check_that_has_not_run(tmp_path,
                                                             capsys):
    ruling_id = _arm(tmp_path)
    assert _show(tmp_path, ruling_id) == 0
    out = capsys.readouterr().out
    assert "  Check: armed" in out
    assert "  Fired: never" in out


def test_show_names_the_host_and_the_lifetime_counts(tmp_path, capsys):
    ruling_id = _arm(tmp_path)
    _write_log(
        _row(ruling_id=ruling_id, outcome="clean", ts="2026-09-06T08:00:00Z"),
        _row(ruling_id=ruling_id, outcome="violation",
             ts="2026-09-06T11:00:00Z"),
    )
    _show(tmp_path, ruling_id)
    assert ("  Fired: last 2026-09-06T11:00:00Z on claude-code · lifetime "
            "1 clean, 1 violation, 0 unresolved") in capsys.readouterr().out


def test_show_omits_the_line_for_a_proposed_check(tmp_path, capsys):
    """Spec 8.5. Nothing is armed, so nothing could have fired, and `never`
    here would read as a wiring that ran and found nothing."""
    ruling_id = _propose(tmp_path)
    _show(tmp_path, ruling_id)
    out = capsys.readouterr().out
    assert "proposed, not armed" in out and "Fired:" not in out


def test_show_omits_the_line_for_a_disarmed_check(tmp_path, capsys):
    ruling_id = _arm(tmp_path)
    refutations.retire(ruling_id, channel="cli-tty", project_dir=str(tmp_path))
    _show(tmp_path, ruling_id)
    out = capsys.readouterr().out
    assert "Check: disarmed" in out and "Fired:" not in out


def test_ruling_list_is_untouched(tmp_path, capsys):
    """The compact lane keeps its exact shape: `list` passes no summary, so
    the branch cannot reach it, and the per-record cost of folding the
    firing log is not paid for every ruling in the project."""
    from daimon_briefing import cli

    ruling_id = _arm(tmp_path)
    _write_log(_row(ruling_id=ruling_id, outcome="clean"))
    assert cli.main(["ruling", "list", "--project", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "[check: armed]" in out and "Fired:" not in out


def test_the_briefing_ruling_lines_are_a_different_function_and_stay_quiet(
        tmp_path):
    """`briefing.ruling_lines` renders the standing-rulings section and knows
    nothing about checks. Same name shape as the CLI helper, different
    function; slice 5 must not have reached it."""
    from daimon_briefing import briefing

    ruling_id = _arm(tmp_path)
    _write_log(_row(ruling_id=ruling_id, outcome="clean"))
    text = "\n".join(briefing.ruling_lines(project_dir=str(tmp_path)))
    assert ruling_id not in text or "Fired:" not in text
    assert "Fired:" not in text


def test_the_show_line_is_dropped_when_the_summary_cannot_be_read(
        tmp_path, capsys, monkeypatch):
    """A ruling's own record is the answer `show` owes; liveness is an
    extra, and losing the record over the extra would be the wrong trade."""
    ruling_id = _arm(tmp_path)
    monkeypatch.setattr(checks, "firing_summary",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert _show(tmp_path, ruling_id) == 0
    out = capsys.readouterr().out
    assert "  Check: armed" in out and "Fired:" not in out


# ---- an unreadable firing log is neither silent nor clean ----------------
#
# Constraint 2 says an EMPTY log reads as silent. A log daimon cannot read is
# a third state: it does not say nothing ran, it says daimon cannot tell you.
# Rendering it as `never fired` is how an author widens the pattern on a gate
# that has been firing all along. The manifest half of this slice already
# keeps its four states apart; this is the same rule for the log.

def _log_is_a_directory():
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()


def _log_denies_reads():
    _write_log(_row(outcome="clean"))
    _log_path().chmod(0o000)


_LOG_BREAKERS = [
    pytest.param(_log_is_a_directory, id="directory-in-its-place"),
    pytest.param(
        _log_denies_reads, id="chmod-000",
        marks=pytest.mark.skipif(
            hasattr(os, "geteuid") and os.geteuid() == 0,
            reason="root reads a 000 file, so the branch is unreachable")),
]


@pytest.mark.parametrize("break_log", _LOG_BREAKERS)
def test_the_reader_reports_an_unreadable_log_as_its_own_state(break_log,
                                                               tmp_path):
    _arm(tmp_path)
    break_log()
    summary = checks.firing_summary(str(tmp_path))
    assert summary.log_state == "unreadable"
    assert summary.path == str(_log_path())


@pytest.mark.parametrize("break_log", _LOG_BREAKERS)
def test_stats_says_the_log_is_unreadable_rather_than_never_fired(
        break_log, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm(tmp_path)
    break_log()
    assert _stats(tmp_path) == 0
    out = capsys.readouterr().out
    assert "checks: 1 armed, firing log unreadable" in out
    assert "never fired" not in out


@pytest.mark.parametrize("break_log", _LOG_BREAKERS)
def test_status_says_the_log_is_unreadable_rather_than_never_fired(
        break_log, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm(tmp_path)
    break_log()
    _status(tmp_path)
    out = capsys.readouterr().out
    assert "checks: 1 armed, firing log unreadable" in out
    assert "never fired" not in out


@pytest.mark.parametrize("break_log", _LOG_BREAKERS)
def test_ruling_checks_heads_the_table_with_the_log_state(
        break_log, tmp_path, capsys):
    _arm(tmp_path)
    checks.sync(str(tmp_path))
    break_log()
    _checks_table(tmp_path)
    out = capsys.readouterr().out
    assert f"firing log unreadable at {_log_path()}" in out
    assert "never fired" not in out and "lifetime" not in out


@pytest.mark.parametrize("break_log", _LOG_BREAKERS)
def test_show_says_unknown_rather_than_never(break_log, tmp_path, capsys):
    ruling_id = _arm(tmp_path)
    break_log()
    _show(tmp_path, ruling_id)
    out = capsys.readouterr().out
    assert "  Fired: unknown, firing log unreadable" in out
    assert "Fired: never" not in out


def test_the_rich_renderers_carry_the_unreadable_wording_too(
        tmp_path, monkeypatch, capsys):
    from daimon_briefing import render

    monkeypatch.setattr(render, "supports_rich", lambda: True)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm(tmp_path)
    _log_is_a_directory()
    _stats(tmp_path)
    assert "checks: 1 armed, firing log unreadable" in capsys.readouterr().out
    _status(tmp_path)
    assert "checks: 1 armed, firing log unreadable" in capsys.readouterr().out


def test_the_json_surfaces_carry_the_log_state(tmp_path, monkeypatch,
                                               capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm(tmp_path)
    _log_is_a_directory()
    _stats(tmp_path, "--json")
    assert json.loads(capsys.readouterr().out)["checks"]["log_state"] == \
        "unreadable"
    _status(tmp_path, "--json")
    assert json.loads(capsys.readouterr().out)["checks"]["log_state"] == \
        "unreadable"
    _checks_table(tmp_path, "--json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["log"] == {"state": "unreadable", "path": str(_log_path())}
    assert payload["rows"][0]["last_fired"] is None


def test_an_absent_log_still_reads_as_never_fired(tmp_path, monkeypatch,
                                                  capsys):
    """The distinction only cuts one way: nothing ever ran is a real answer,
    and turning it into a warning would make every fresh install look broken."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm(tmp_path)
    assert _stats(tmp_path, "--json") == 0
    assert json.loads(capsys.readouterr().out)["checks"]["log_state"] == \
        "absent"
    _stats(tmp_path)
    assert "checks: 1 armed, never fired" in capsys.readouterr().out


def test_nothing_armed_outranks_an_unreadable_log(tmp_path, monkeypatch,
                                                  capsys):
    """What is armed is a LEDGER fact and the log cannot change it. Reporting
    the log's state where there is nothing to run would be a warning about
    a feature this project does not use."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _log_is_a_directory()
    _stats(tmp_path)
    assert "checks: none armed" in capsys.readouterr().out


def test_the_log_is_never_materialized_whole(tmp_path, monkeypatch):
    """`daimon status` folds this log on every run and the log has no cap
    yet, so the read has to be a stream. `read_text` on a 34 MB log held
    150 MB of resident memory before this; a caller that reaches for it
    again reintroduces that."""
    ruling_id = _arm(tmp_path)
    _write_log(*[_row(ruling_id=ruling_id, outcome="clean")] * 50)
    slurped = []
    real = Path.read_text

    def _watch(self, *a, **kw):
        if self.name == "checks.jsonl":
            slurped.append(str(self))
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _watch)
    summary = checks.firing_summary(str(tmp_path))
    assert slurped == [], "the firing log must be streamed, not slurped"
    assert summary.rulings[(ruling_id, CC)]["clean"] == 50


def test_streaming_keeps_every_earlier_guarantee(tmp_path):
    """One pass, and the same answers: malformed lines skipped, foreign ids
    dropped, project-level rows folded per host, greatest stamp wins."""
    other = tmp_path / "elsewhere"
    other.mkdir()
    mine = _arm(tmp_path)
    theirs = _arm(other, subject="their posts")
    _write_log(
        "not json", "",
        _row(ruling_id=mine, outcome="clean", ts="2026-09-06T12:00:00Z"),
        _row(ruling_id=mine, outcome="violation", ts="2026-09-06T09:00:00Z"),
        _row(ruling_id=theirs, outcome="violation"),
        _row(cause="no-match", ts="2026-09-06T07:00:00Z"),
    )
    summary = checks.firing_summary(str(tmp_path))
    fold = summary.rulings[(mine, CC)]
    assert (fold["clean"], fold["violation"]) == (1, 1)
    assert fold["last_ts"] == "2026-09-06T12:00:00Z"
    assert list(summary.rulings) == [(mine, CC)]
    assert summary.hook_seen[CC]["rows"] == 1
