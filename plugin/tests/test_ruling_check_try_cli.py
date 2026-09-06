"""#943 slice 2: `daimon ruling check try`, the dry run before ratification.

The one verb in this family that EXECUTES a body, so it carries the same
double check ratification does: a CLI pre-check on the observed channel, and
a module guard that an in-process caller cannot walk around. It writes
nothing: not the firing log, not the manifest, and not a body under
~/.daimon/checks.
"""

import hashlib

import pytest

from daimon_briefing import checks, cli, config, refutations

PROJECT = "/p/check-try"
CLEAN = "#!/bin/sh\nexit 0\n"
DIRTY = ("#!/bin/sh\ngrep -q FORBIDDEN \"$DAIMON_CHECK_SUBJECT\" "
         "&& { echo 'the subject says FORBIDDEN' >&2; exit 1; }\nexit 0\n")
BROKEN = "#!/bin/sh\nexit 7\n"
MATCH = "gh pr create"


@pytest.fixture
def _tty(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)


def _sha(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _propose(body=CLEAN, subject="public posts", scope="publishing"):
    return refutations.assert_ruling(
        subject=subject, verdict=f"the rule for {subject}", scope=scope,
        evidence=["issue:943"], channel="cli-agent",
        check={"match": MATCH, "body": body, "intent": "warn"},
        project_dir=PROJECT)


def _arm(body=CLEAN, **kwargs):
    ruling_id = _propose(body, **kwargs)
    refutations.ratify(ruling_id, channel="cli-tty", check_sha256=_sha(body),
                       project_dir=PROJECT)
    return ruling_id


def _try(ruling_id, command="gh pr create --title x", extra=()):
    return cli.main(["ruling", "check", "try", ruling_id,
                     "--command", command, "--project", PROJECT, *extra])


# ---- the human-only double check ------------------------------------------


def test_an_agent_channel_is_refused_before_the_body_is_read(
        tmp_checkpoint_dir, _tty, capsys):
    """The CLI refuses without calling the writer: never invoke a runner to
    harvest its error string, and never execute the body to find out."""
    ruling_id = _arm()
    assert _try(ruling_id, extra=["--by", "agent"]) == 1
    assert "requires a human channel" in capsys.readouterr().out


def test_a_non_interactive_caller_cannot_claim_the_human_channel(
        tmp_checkpoint_dir, monkeypatch, capsys):
    ruling_id = _arm()
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    assert _try(ruling_id) == 1
    assert "no interactive terminal" in capsys.readouterr().out


def test_the_module_guard_refuses_an_agent_channel_reaching_it_directly(
        tmp_checkpoint_dir):
    """The real guard. `ui` and `signed` reach try_run without passing the
    CLI at all, so the authority test has to live here too."""
    ruling_id = _arm()
    with pytest.raises(refutations.RefutationError, match="human channel"):
        checks.try_run(ruling_id, "gh pr create", channel="cli-agent",
                       project_dir=PROJECT)


def test_an_in_process_human_channel_is_allowed(tmp_checkpoint_dir):
    ruling_id = _arm()
    outcome = checks.try_run(ruling_id, "gh pr create", channel="ui",
                            project_dir=PROJECT)
    assert outcome.outcome == "clean"


# ---- the three exit codes -------------------------------------------------


def test_a_clean_check_exits_zero(tmp_checkpoint_dir, _tty, capsys):
    assert _try(_arm(CLEAN)) == 0
    assert "outcome: clean" in capsys.readouterr().out


def test_a_violation_exits_one_and_shows_the_check_s_own_reason(
        tmp_checkpoint_dir, _tty, tmp_path, capsys):
    body_file = tmp_path / "body.md"
    body_file.write_text("this text is FORBIDDEN\n", encoding="utf-8")
    ruling_id = _arm(DIRTY)
    rc = _try(ruling_id, command=f"gh pr create --body-file {body_file}")
    out = capsys.readouterr().out
    assert rc == 1
    assert "outcome: violation" in out
    assert "the subject says FORBIDDEN" in out


def test_an_unresolved_subject_exits_three(tmp_checkpoint_dir, _tty, capsys):
    """Three, the auditors' convention for "cannot prove". Never folded into
    zero, which would read as clean, or into one, which would read as a
    violation the check never found."""
    ruling_id = _arm(CLEAN)
    rc = _try(ruling_id, command="gh pr create --body-file /nope/gone.md")
    out = capsys.readouterr().out
    assert rc == 3
    assert "outcome: unresolved" in out
    assert "cause: file-missing" in out


def test_a_crashing_check_exits_three(tmp_checkpoint_dir, _tty, capsys):
    assert _try(_arm(BROKEN)) == 3
    assert "cause: check-crashed" in capsys.readouterr().out


def test_the_run_reports_how_long_it_took(tmp_checkpoint_dir, _tty, capsys):
    _try(_arm(CLEAN))
    assert "duration:" in capsys.readouterr().out


# ---- which body it runs ---------------------------------------------------


def test_a_candidate_s_proposed_body_can_be_tried_before_ratification(
        tmp_checkpoint_dir, _tty, capsys):
    """The whole point of the verb: new checks arm in warn first, and try
    exists so a body can be tested BEFORE a human arms it."""
    ruling_id = _propose(CLEAN)
    assert refutations.get(ruling_id,
                           project_dir=PROJECT)["state"] == "candidate"
    assert _try(ruling_id) == 0
    assert "outcome: clean" in capsys.readouterr().out


def test_an_active_ruling_runs_its_armed_body_not_a_pending_proposal(
        tmp_checkpoint_dir, _tty, tmp_path, capsys):
    body_file = tmp_path / "body.md"
    body_file.write_text("this text is FORBIDDEN\n", encoding="utf-8")
    ruling_id = _arm(CLEAN)
    refutations.revise(ruling_id, channel="cli-agent", evidence=["issue:943"],
                       check={"match": MATCH, "body": DIRTY, "intent": "warn"},
                       project_dir=PROJECT)
    rc = _try(ruling_id, command=f"gh pr create --body-file {body_file}")
    assert rc == 0, "the armed body is the clean one; the proposal is not armed"


def test_the_pending_proposal_is_reachable_behind_a_flag(
        tmp_checkpoint_dir, _tty, tmp_path, capsys):
    body_file = tmp_path / "body.md"
    body_file.write_text("this text is FORBIDDEN\n", encoding="utf-8")
    ruling_id = _arm(CLEAN)
    refutations.revise(ruling_id, channel="cli-agent", evidence=["issue:943"],
                       check={"match": MATCH, "body": DIRTY, "intent": "warn"},
                       project_dir=PROJECT)
    rc = _try(ruling_id, command=f"gh pr create --body-file {body_file}",
              extra=["--proposed"])
    assert rc == 1
    assert "outcome: violation" in capsys.readouterr().out


def test_asking_for_a_proposal_that_does_not_exist_is_refused(
        tmp_checkpoint_dir, _tty, capsys):
    assert _try(_arm(CLEAN), extra=["--proposed"]) == 1
    assert "no proposed check" in capsys.readouterr().out


def test_a_ruling_with_no_check_is_refused(tmp_checkpoint_dir, _tty, capsys):
    ruling_id = refutations.assert_ruling(
        subject="unrelated", verdict="a rule with no check", scope="elsewhere",
        evidence=["issue:943"], channel="cli-agent", project_dir=PROJECT)
    assert _try(ruling_id) == 1
    assert "carries no check" in capsys.readouterr().out


def test_an_unknown_id_is_refused(tmp_checkpoint_dir, _tty, capsys):
    assert _try("r-000000000000") == 1
    assert "unknown ruling" in capsys.readouterr().out


def test_a_refutation_id_is_refused(tmp_checkpoint_dir, _tty, capsys):
    other = refutations.assert_refutation(
        subject="a losing approach", verdict="it lost", scope="elsewhere",
        evidence=["issue:943"], channel="cli-agent", project_dir=PROJECT)
    assert _try(other) == 1
    assert "refutation" in capsys.readouterr().out


# ---- it writes nothing ----------------------------------------------------


def test_a_dry_run_writes_no_firing_log(tmp_checkpoint_dir, _tty):
    """A try is a rehearsal. Counting it as a firing would make the liveness
    surface report a check that has never actually guarded an action."""
    _try(_arm(CLEAN))
    assert not (config.log_dir() / "checks.jsonl").exists()


def test_a_dry_run_of_a_candidate_arms_nothing(tmp_checkpoint_dir, _tty):
    _try(_propose(CLEAN))
    base = config.checks_dir()
    assert not (base / "manifest.json").exists()
    assert not base.exists() or list(base.glob("*.sh")) == []


def test_the_body_is_materialized_outside_the_checks_directory(
        tmp_checkpoint_dir, _tty, monkeypatch):
    """A body under ~/.daimon/checks would be indistinguishable from an armed
    one to the hook that reads that directory."""
    seen = []
    real = checks.checks_runtime.run

    def spy(entry_or_body, subject, **kwargs):
        seen.append(str(entry_or_body.get("body_path")))
        return real(entry_or_body, subject, **kwargs)

    monkeypatch.setattr(checks.checks_runtime, "run", spy)
    _try(_arm(CLEAN))
    assert seen and str(config.checks_dir()) not in seen[0]
    assert str(config.log_dir()) not in seen[0]


def test_the_temporary_body_is_removed_after_the_run(
        tmp_checkpoint_dir, _tty, monkeypatch):
    from pathlib import Path
    seen = []
    real = checks.checks_runtime.run

    def spy(entry_or_body, subject, **kwargs):
        seen.append(str(entry_or_body.get("body_path")))
        return real(entry_or_body, subject, **kwargs)

    monkeypatch.setattr(checks.checks_runtime, "run", spy)
    _try(_arm(CLEAN))
    assert seen and not Path(seen[0]).exists()


def test_an_armed_ruling_s_try_leaves_the_manifest_byte_identical(
        tmp_checkpoint_dir, _tty):
    ruling_id = _arm(CLEAN)
    manifest = config.checks_dir() / "manifest.json"
    before = (manifest.read_bytes(), manifest.stat().st_mtime_ns)
    _try(ruling_id)
    assert (manifest.read_bytes(), manifest.stat().st_mtime_ns) == before


# ---- the flags ------------------------------------------------------------


def test_the_command_is_required(tmp_checkpoint_dir, _tty):
    with pytest.raises(SystemExit):
        cli.main(["ruling", "check", "try", "r-000000000000",
                  "--project", PROJECT])


def test_the_working_directory_can_be_named(tmp_checkpoint_dir, _tty, tmp_path):
    """A relative file argument resolves against it, the way the hook payload
    would supply the action's cwd."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "body.md").write_text("this text is FORBIDDEN\n", encoding="utf-8")
    ruling_id = _arm(DIRTY)
    rc = _try(ruling_id, command="gh pr create --body-file body.md",
              extra=["--cwd", str(work)])
    assert rc == 1


def test_without_a_working_directory_a_relative_path_misses(
        tmp_checkpoint_dir, _tty, capsys):
    ruling_id = _arm(DIRTY)
    rc = _try(ruling_id, command="gh pr create --body-file nowhere-at-all.md")
    assert rc == 3
    assert "cause: file-missing" in capsys.readouterr().out
