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

from daimon_briefing import checks, config, refutations

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
