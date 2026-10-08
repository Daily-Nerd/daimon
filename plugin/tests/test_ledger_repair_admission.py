"""What `ledger repair` tells the human after a refusal (#1132 PR 10b, D10.4,
decision 3): how many sessions wait for `heal`, and what to review by hand.
"""

import time

from daimon_briefing import cli, config, ledger, store

PROJECT = "/work/repair-admission"
HINT = "run: daimon ledger repair events"


def _events():
    path = store._events_path(store._resolved(PROJECT))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _refusals(tmp_path, n):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 300))
    lines = []
    for i in range(n):
        tp = tmp_path / f"waiting-{i}.jsonl"
        tp.write_text("{}\n")
        lines.append(f"{stamp} session-end: spawned serialize for waiting-{i} "
                     f"(reason: x, project: {PROJECT}) (transcript: {tp})")
        lines.append("error: admission refused: events.jsonl is unreadable; "
                     f"{HINT} (transcript: {tp}) after 0s")
    log_dir = config.log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serialize.log").write_text("\n".join(lines) + "\n")


def test_the_repair_says_how_many_sessions_wait_for_heal(
        tmp_checkpoint_dir, tmp_path, capsys):
    _events().write_bytes(b"<<<<<<< conflict\n")
    _refusals(tmp_path, 3)
    assert cli.main(["ledger", "repair", "events", "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert ("3 session(s) wait to be serialized; daimon heal repairs one "
            "per run") in out


def test_a_dry_run_does_not_claim_sessions_are_waiting(
        tmp_checkpoint_dir, tmp_path, capsys):
    _events().write_bytes(b"<<<<<<< conflict\n")
    _refusals(tmp_path, 2)
    cli.main(["ledger", "repair", "events", "--project", PROJECT,
              "--dry-run"])
    assert "wait to be serialized" not in capsys.readouterr().out


def test_repairing_another_ledger_says_nothing_about_sessions(
        tmp_checkpoint_dir, tmp_path, capsys):
    _events().write_bytes(b"<<<<<<< conflict\n")
    _refusals(tmp_path, 2)
    trust = _events().parent / "trust.jsonl"
    trust.write_bytes(b"<<<<<<< conflict\n")
    cli.main(["trust", "repair", "--project", PROJECT])
    assert "wait to be serialized" not in capsys.readouterr().out


def test_no_waiting_sessions_means_no_line(tmp_checkpoint_dir, capsys):
    _events().write_bytes(b"<<<<<<< conflict\n")
    cli.main(["ledger", "repair", "events", "--project", PROJECT])
    assert "wait to be serialized" not in capsys.readouterr().out


def test_admission_waiting_counts_only_this_projects_healable_refusals(
        tmp_checkpoint_dir, tmp_path):
    _refusals(tmp_path, 2)
    slug = store.project_slug(store._resolved(PROJECT))
    # The ledger reads fine, so the refusals are healable now.
    assert ledger.admission_waiting(slug) == 2
    assert ledger.admission_waiting("another-slug") == 0
    # While it is still unproven they are held, not waiting.
    _events().write_bytes(b"<<<<<<< conflict\n")
    assert ledger.admission_waiting(slug) == 0


def test_the_repair_prints_the_sidecar_row_count_and_the_manual_review(
        tmp_checkpoint_dir, capsys):
    _events().write_bytes(b'{"ok": 1}\n{"torn": \n<<<<<<< conflict\n')
    assert cli.main(["ledger", "repair", "events", "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert ("the sidecar now holds 2 row(s); a value fused into a torn row "
            "needs manual review of that file") in out
