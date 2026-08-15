"""#693 PR 2: the ruling echo admission filter.

The standing-rulings section renders into every session's context, so the
NEXT session's extractor can mint the ruling text back as a fresh belief —
a copy that decays, drifts, and double-renders. The admission filter drops
exact echoes at the write boundary, ONLY on the two admission paths
(capture, `write-checkpoint` stdin). Rewrites (anchor --attach, the forget
rewrite) never opt in: they must not strip previously-admitted items.
"""

from daimon_briefing import refutations, store


PROJECT = "/p/ruling-echo"
VERDICT = "never deploy the payments service on a friday"


def _active_ruling():
    return refutations.assert_ruling(
        subject="friday payment deploys",
        verdict=VERDICT,
        scope="payments service",
        evidence=["issue:693"],
        channel="cli-tty",
        ratified=True,
        project_dir=PROJECT,
    )


def _checkpoint(text=VERDICT):
    return {
        "session_id": "S-echo",
        "working_context": {
            "active_topic": {"text": "deploy cadence", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [
                {"text": text, "trust": "inferred"},
                {"text": "an unrelated belief survives", "trust": "inferred"},
            ],
            "uncertainties": [],
            "contradictions_flagged": [],
        },
    }


def _beliefs(path):
    import json
    cp = json.loads(path.read_text(encoding="utf-8"))
    return [i["text"] for i in cp["epistemic_snapshot"]["strong_beliefs"]]


def test_admission_drops_exact_echo_of_active_ruling(tmp_checkpoint_dir):
    _active_ruling()
    out = store.write_checkpoint("S-echo", _checkpoint(),
                                 project_dir=PROJECT, admit=True)
    beliefs = _beliefs(out)
    assert VERDICT not in beliefs
    assert "an unrelated belief survives" in beliefs


def test_rewrite_without_admit_keeps_previously_admitted_items(
        tmp_checkpoint_dir):
    _active_ruling()
    out = store.write_checkpoint("S-echo", _checkpoint(),
                                 project_dir=PROJECT)
    assert VERDICT in _beliefs(out)


def test_candidate_ruling_never_drops_anything(tmp_checkpoint_dir):
    refutations.assert_ruling(
        subject="friday payment deploys", verdict=VERDICT,
        scope="payments service", evidence=["issue:693"],
        channel="cli-agent", ratified=False, project_dir=PROJECT)
    out = store.write_checkpoint("S-echo", _checkpoint(),
                                 project_dir=PROJECT, admit=True)
    assert VERDICT in _beliefs(out)


def test_echo_drop_is_counted_under_its_own_reason(tmp_checkpoint_dir):
    _active_ruling()
    store.write_checkpoint("S-echo", _checkpoint(),
                           project_dir=PROJECT, admit=True)
    stats = store.forget_hit_stats(project_dir=PROJECT)
    # The echo rate is its OWN measurement — it must not inflate the forget
    # suppression count the project already publishes.
    assert stats["count"] == 0
    assert stats["ruling_echo_count"] == 1


def test_echo_drop_logs_content_hash_never_text(tmp_checkpoint_dir, caplog):
    import logging
    _active_ruling()
    with caplog.at_level(logging.WARNING):
        store.write_checkpoint("S-echo", _checkpoint(),
                               project_dir=PROJECT, admit=True)
    echo_lines = [r.getMessage() for r in caplog.records
                  if "ruling echo" in r.getMessage()]
    assert echo_lines
    assert all(VERDICT not in line for line in echo_lines)
    assert any("content hash" in line for line in echo_lines)


def test_status_surfaces_echo_drops_when_nonzero(tmp_checkpoint_dir,
                                                 monkeypatch, capsys):
    from daimon_briefing import cli
    _active_ruling()
    store.write_checkpoint("S-echo", _checkpoint(),
                           project_dir=PROJECT, admit=True)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "ruling echo" in out
    assert VERDICT not in out


def test_filter_fails_open_on_ledger_read_error(tmp_checkpoint_dir,
                                                monkeypatch):
    _active_ruling()

    def boom(**kwargs):
        raise OSError("ledger unreadable")

    monkeypatch.setattr(refutations, "listing", boom)
    out = store.write_checkpoint("S-echo", _checkpoint(),
                                 project_dir=PROJECT, admit=True)
    assert out is not None
    assert VERDICT in _beliefs(out)  # fail-open: the write goes through whole


def test_capture_path_admits(tmp_checkpoint_dir, monkeypatch):
    # capture.run is the hook-side admission caller; its write must carry
    # the filter. Reaching through the real extractor is a serializer test,
    # so pin the seam instead: the call site passes admit=True.
    import inspect
    from daimon_briefing import capture
    src = inspect.getsource(capture.run)
    assert "admit=True" in src


def test_write_checkpoint_stdin_path_admits(tmp_checkpoint_dir, monkeypatch,
                                            capsys):
    import io
    import json
    import sys
    from daimon_briefing import cli
    _active_ruling()
    cp = _checkpoint()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(cp)))
    rc = cli.main(["write-checkpoint", "--project", PROJECT])
    assert rc == 0
    out = store.read_latest(project_dir=PROJECT)
    beliefs = [i["text"] for i in out["epistemic_snapshot"]["strong_beliefs"]]
    assert VERDICT not in beliefs
