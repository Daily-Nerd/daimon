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
    with caplog.at_level(logging.INFO):
        store.write_checkpoint("S-echo", _checkpoint(),
                               project_dir=PROJECT, admit=True)
    echo = [r for r in caplog.records if "ruling echo" in r.getMessage()]
    assert echo
    # INFO, not WARNING: a stable ruling being re-minted every session is an
    # expected, measured event — not an anomaly worth an alert per capture.
    assert all(r.levelno == logging.INFO for r in echo)
    assert all(VERDICT not in r.getMessage() for r in echo)
    assert any("content hash" in r.getMessage() for r in echo)


def test_rendered_line_forms_are_dropped_too(tmp_checkpoint_dir):
    # What the briefing puts in context is not the bare verdict — it is
    # "§ <verdict>" with an optional "  [<authority>-written]" suffix. An
    # extractor copying that line verbatim is the most literal echo shape
    # there is; the key set must cover it.
    ruling_id = refutations.assert_ruling(
        subject="friday payment deploys", verdict=VERDICT,
        scope="payments service", evidence=["issue:693"],
        channel="cli-agent", ratified=False, project_dir=PROJECT)
    refutations.ratify(ruling_id, channel="cli-tty", project_dir=PROJECT)
    cp = _checkpoint(text=f"§ {VERDICT}  [agent-written]")
    cp["epistemic_snapshot"]["strong_beliefs"].append(
        {"text": f"§ {VERDICT}", "trust": "inferred"})
    out = store.write_checkpoint("S-echo", cp, project_dir=PROJECT,
                                 admit=True)
    beliefs = _beliefs(out)
    assert f"§ {VERDICT}  [agent-written]" not in beliefs
    assert f"§ {VERDICT}" not in beliefs
    assert "an unrelated belief survives" in beliefs


def test_active_refutation_never_drops_anything(tmp_checkpoint_dir):
    # The polarity scope IS the safety property: an active REFUTATION whose
    # verdict matches an item's text must never delete it at admission —
    # that would hand the negative-polarity ledger a deletion power the
    # design denies it.
    ref = refutations.assert_refutation(
        subject="friday payment deploys", verdict=VERDICT,
        scope="payments service", evidence=["issue:693"],
        channel="cli-tty", ratified=True, project_dir=PROJECT)
    assert refutations.get(ref, project_dir=PROJECT)["state"] == "active"
    out = store.write_checkpoint("S-echo", _checkpoint(),
                                 project_dir=PROJECT, admit=True)
    assert VERDICT in _beliefs(out)
    stats = store.forget_hit_stats(project_dir=PROJECT)
    assert stats["ruling_echo_count"] == 0


def test_torn_checkpoint_shapes_never_crash_the_matcher(tmp_checkpoint_dir):
    # Legacy/torn blobs: a section that is not a dict, a list key that is
    # not a list — the matcher tolerates both, same posture as the forget
    # gate's walk.
    from daimon_briefing import normalize, policy
    torn = {
        "session_id": "S-torn",
        "working_context": "not-a-dict",
        "epistemic_snapshot": {"strong_beliefs": "not-a-list",
                               "uncertainties": [
                                   {"text": VERDICT, "trust": "inferred"}]},
    }
    dropped = policy.drop_matching_items(
        torn, {normalize.content_key(VERDICT)})
    assert [i["text"] for i in dropped] == [VERDICT]


def test_hand_edited_empty_verdict_ruling_never_breaks_the_filter(
        tmp_checkpoint_dir):
    # A raw-appended active ruling with an empty verdict contributes no key
    # and must not stop a real ruling's echo from dropping.
    row = refutations._stamp(
        "ruled", refutations.make_id("empty verdict", "tests"), "cli-tty")
    row.update({"subject": "empty verdict", "verdict": "", "scope": "tests",
                "anchors": [], "revisit_when": "", "evidence": [],
                "ratified": True})
    assert refutations.append(row, project_dir=PROJECT)
    _active_ruling()
    out = store.write_checkpoint("S-echo", _checkpoint(),
                                 project_dir=PROJECT, admit=True)
    assert VERDICT not in _beliefs(out)
    assert store.forget_hit_stats(project_dir=PROJECT)[
        "ruling_echo_count"] == 1


def test_status_rich_path_surfaces_echo_drops(tmp_checkpoint_dir,
                                              monkeypatch, capsys):
    import pytest as _pytest
    _pytest.importorskip("rich")
    from daimon_briefing import cli, render
    _active_ruling()
    store.write_checkpoint("S-echo", _checkpoint(),
                           project_dir=PROJECT, admit=True)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    monkeypatch.delenv("DAIMON_PLAIN", raising=False)
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    assert "ruling echo" in capsys.readouterr().out


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
    # capture.run is the hook-side admission caller; its write must carry the
    # filter. Spy on the seam and assert the kwarg the store actually
    # RECEIVES — a source-text assertion here was once satisfied by a
    # comment, which left the primary admission path guarded by nothing.
    from daimon_briefing import capture
    seen = {}

    def _spy(session_id, checkpoint, project_dir=None, admit=False, **kw):
        seen["admit"] = admit
        return None  # kill-switch shape: capture returns right after

    monkeypatch.setattr(capture.store, "write_checkpoint", _spy)
    monkeypatch.setattr(
        capture.serializer, "serialize_strict",
        lambda *a, **k: {"session_id": "S-spy", "working_context": {},
                         "epistemic_snapshot": {}})
    assert capture.run("S-spy", [], project=PROJECT, chat=None,
                       deadline=None) is None
    assert seen.get("admit") is True


def test_item_quoting_a_ruling_keeps_quote_and_trust(tmp_checkpoint_dir):
    # An item whose own text is fresh but whose QUOTE repeats the ruling is a
    # genuine witness, not an echo copy. A ruling carries no deletion
    # promise; the filter must never strip fields or downgrade trust the way
    # the forget gate does.
    _active_ruling()
    cp = _checkpoint(text="the team restated the deploy rule today")
    cp["epistemic_snapshot"]["strong_beliefs"][0].update(
        {"quote": VERDICT, "trust": "verbatim"})
    out = store.write_checkpoint("S-echo", cp, project_dir=PROJECT,
                                 admit=True)
    import json
    stored = json.loads(out.read_text(encoding="utf-8"))
    item = stored["epistemic_snapshot"]["strong_beliefs"][0]
    assert item["quote"] == VERDICT
    assert item["trust"] == "verbatim"


def test_active_topic_survives_echo_filter(tmp_checkpoint_dir):
    # The active topic is working context, not a decaying belief copy —
    # deleting it is context loss, not deduplication.
    _active_ruling()
    cp = _checkpoint(text="unrelated belief")
    cp["working_context"]["active_topic"] = {"text": VERDICT,
                                             "trust": "inferred"}
    out = store.write_checkpoint("S-echo", cp, project_dir=PROJECT,
                                 admit=True)
    import json
    stored = json.loads(out.read_text(encoding="utf-8"))
    assert stored["working_context"]["active_topic"]["text"] == VERDICT


def test_stats_last_hit_at_excludes_echo_rows(tmp_checkpoint_dir):
    import json
    _active_ruling()
    path = store._forget_hits_path(PROJECT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"ts": "2020-01-01T00:00:00Z", "key": "k-forget"}) + "\n",
        encoding="utf-8")
    store.write_checkpoint("S-echo", _checkpoint(),
                           project_dir=PROJECT, admit=True)
    stats = store.forget_hit_stats(project_dir=PROJECT)
    assert stats["count"] == 1
    assert stats["ruling_echo_count"] == 1
    # The forget line's timestamp must stay the forget ledger's own.
    assert stats["last_hit_at"] == "2020-01-01T00:00:00Z"
    assert stats["ruling_echo_last_at"] > "2020-01-01T00:00:00Z"


def test_anchor_attach_rewrite_never_echo_drops(tmp_checkpoint_dir, tmp_path,
                                                capsys):
    # The anchor --attach rewrite re-writes a previously-admitted
    # checkpoint; it must not strip items even when a ruling matching one
    # was ratified in between.
    from daimon_briefing import cli, refutations as refs
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "mod.py").write_text("def fn():\n    pass\n", encoding="utf-8")
    store.write_checkpoint("S-echo", _checkpoint(), project_dir=proj)
    refs.assert_ruling(
        subject="friday payment deploys", verdict=VERDICT,
        scope="payments service", evidence=["issue:693"],
        channel="cli-tty", ratified=True, project_dir=proj)
    rc = cli.main(["anchor", "mod.py", "fn",
                   "--attach", "unrelated belief survives",
                   "--project", str(proj)])
    assert rc == 0
    out = store.read_latest(project_dir=proj)
    beliefs = [i["text"] for i in out["epistemic_snapshot"]["strong_beliefs"]]
    assert VERDICT in beliefs


def test_forget_rewrite_never_echo_drops(tmp_checkpoint_dir, monkeypatch,
                                         capsys):
    # The forget rewrite deletes exactly what the user named — an active
    # ruling matching a DIFFERENT stored item must not widen the deletion.
    from daimon_briefing import cli
    store.write_checkpoint("S-echo", _checkpoint(), project_dir=PROJECT)
    _active_ruling()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    stored = store.read_latest(project_dir=PROJECT)
    doomed = next(i["id"] for i in
                  stored["epistemic_snapshot"]["strong_beliefs"]
                  if i["text"] == "an unrelated belief survives")
    assert cli.main(["forget", doomed]) == 0
    out = store.read_latest(project_dir=PROJECT)
    beliefs = [i["text"] for i in out["epistemic_snapshot"]["strong_beliefs"]]
    assert VERDICT in beliefs  # the ruling echo was NOT the forget target


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
