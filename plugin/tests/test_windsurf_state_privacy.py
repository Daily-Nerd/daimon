"""#607: the Windsurf adapter's own transcript store, inside the contract.

The Cascade adapter accumulates FULL RAW TRANSCRIPTS under
~/.daimon/windsurf/transcripts/<trajectory>.md — daimon writes them, so
#419's rule puts them inside the deletion contract — plus
unparsed-<event>-<stamp>.json payload dumps (secret-scrubbed at write,
never item-text scrubbed). Nothing reached either.

Why this is a PURGE and not a scan: forget stores the canonical HASH and
never the text (#321), so no component downstream holds the plaintext a
substring search of prose would need. Detection inside a transcript is
impossible by construction — exactly the chunk-cache situation (#422) —
and the same three-part answer applies: wholesale purge at forget, an age
reaper bounding what accumulates between forgets, and an INFORMATIONAL
audit line rather than a clean/dirty verdict the auditor cannot honestly
reach.

Host-authored transcripts (Codex rollouts, Claude Code JSONL) stay out of
scope: daimon reads those by path and never copies them, so they are not
daimon's to delete.
"""
import json
import os
import time

from daimon_briefing import cli, config, normalize, privacy, store, surfaces

PROJECT = "/p/windsurf-state"
CANARY = "zqxwindsurfcanary8841 the staging token rotates on fridays"
KEEPER = "an unrelated decision that must survive"


def _state_dir():
    return config.windsurf_state_dir()


def _seed_state(turns=("user", "assistant"), age_days=0.0):
    tdir = _state_dir() / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    path = tdir / "traj-1.md"
    path.write_text(
        "".join(f"**{r}**: something about {CANARY} here\n" for r in turns),
        encoding="utf-8")
    dump = _state_dir() / "unparsed-post_cascade_response-1782874461.json"
    dump.write_text(json.dumps({"prompt": CANARY}), encoding="utf-8")
    stamp = _state_dir() / "traj-1.last-activity"
    stamp.write_text(str(int(time.time())), encoding="utf-8")
    if age_days:
        old = time.time() - age_days * 86400
        for p in (path, dump, stamp):
            os.utime(p, (old, old))
    return path, dump, stamp


def _write_checkpoint():
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": CANARY, "trust": "inferred"},
            {"text": KEEPER, "trust": "inferred"}]},
    }, project_dir=PROJECT)


# ---- purge on forget ------------------------------------------------------


def test_forget_purges_the_daimon_authored_transcript_store(
        tmp_checkpoint_dir):
    path, dump, stamp = _seed_state()
    _write_checkpoint()
    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0
    assert not path.exists(), "the transcript daimon wrote must go"
    assert not dump.exists(), "unparsed payload dumps carry the same text"
    assert stamp.exists(), "activity stamps hold no item text — keep them"


def test_purge_reports_count_and_never_raises(tmp_checkpoint_dir):
    _seed_state()
    purged, err = store.purge_windsurf_state()
    assert purged == 2 and err is None
    # vacuous purge on a machine that never ran Windsurf
    purged, err = store.purge_windsurf_state()
    assert purged == 0 and err is None


def test_purge_survives_an_unreadable_state_dir(tmp_checkpoint_dir,
                                                monkeypatch):
    _seed_state()
    monkeypatch.setattr(type(_state_dir()), "glob",
                        lambda self, pat: (_ for _ in ()).throw(OSError("nope")))
    purged, err = store.purge_windsurf_state()
    assert purged == 0 and err is not None


def test_forget_survives_a_failed_purge(tmp_checkpoint_dir, monkeypatch):
    """The belief-state deletion is the primary contract — a failed purge is
    reported honestly, never fatal (the #422 posture)."""
    _seed_state()
    _write_checkpoint()
    monkeypatch.setattr(store, "purge_windsurf_state",
                        lambda: (0, "disk on fire"))
    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0


# ---- age reaper -----------------------------------------------------------


def test_reap_drops_state_older_than_the_window(tmp_checkpoint_dir):
    old_path, old_dump, old_stamp = _seed_state(age_days=30)
    reaped = store.reap_windsurf_state()
    assert sorted(p.name for p in reaped) == sorted(
        [old_path.name, old_dump.name])
    assert not old_path.exists() and not old_dump.exists()
    assert old_stamp.exists(), "a stamp is not item text"


def test_reap_spares_state_inside_the_window(tmp_checkpoint_dir):
    path, dump, _stamp = _seed_state(age_days=1)
    assert store.reap_windsurf_state() == []
    assert path.exists() and dump.exists()


def test_heal_reaps_windsurf_state_and_dry_run_only_lists(
        tmp_checkpoint_dir, capsys):
    path, _dump, _stamp = _seed_state(age_days=30)
    assert cli.main(["heal", "--dry-run"]) == 0
    assert path.name in capsys.readouterr().out
    assert path.exists(), "--dry-run must not delete"
    assert cli.main(["heal"]) == 0
    assert path.name in capsys.readouterr().out
    assert not path.exists()


def test_window_is_configurable(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_WINDSURF_STATE_DAYS", "1")
    assert config.windsurf_state_days() == 1
    path, _dump, _stamp = _seed_state(age_days=2)
    assert [p.name for p in store.reap_windsurf_state()] != []
    assert not path.exists()
    monkeypatch.setenv("DAIMON_WINDSURF_STATE_DAYS", "not-a-number")
    assert config.windsurf_state_days() == 7


# ---- audit reports it, and says what it cannot prove ----------------------


def test_audit_reports_windsurf_state_informationally(tmp_checkpoint_dir):
    """It must appear in the report — a surface nobody mentions reads as a
    surface that does not exist — but it must NOT move the exit code: the
    auditor cannot see inside prose, so neither a finding nor an
    unscannable entry would be honest."""
    _seed_state()
    _write_checkpoint()
    result = privacy.audit_project(project_dir=PROJECT)
    state = result.get("windsurf") or {}
    assert state.get("entries") == 2
    assert state.get("oldest_days") is not None
    assert privacy.exit_code([result]) == 0
    assert not any(f["surface"].startswith("windsurf")
                   for f in result["findings"])


def test_audit_render_names_the_purge_contract(tmp_checkpoint_dir, capsys):
    from daimon_briefing import render
    _seed_state()
    _write_checkpoint()
    render.render_privacy_audit([privacy.audit_project(project_dir=PROJECT)])
    out = capsys.readouterr().out
    assert "windsurf" in out.lower()
    assert "wholesale" in out.lower()


# ---- the registry stops calling this a gap -------------------------------


def test_registry_declares_windsurf_state_reachable():
    t = surfaces.match("windsurf/transcripts/traj-1.md")
    assert t.delete == "wholesale-purge" and t.plaintext is True
    u = surfaces.match("windsurf/unparsed-post_cascade_response-1.json")
    assert u.delete == "wholesale-purge"
    assert not any(s.issue == "#607" for s in surfaces.SURFACES), \
        "#607 is closed — no entry may still cite it as an open gap"


def test_forgotten_value_never_reaches_the_purge_ledger(tmp_checkpoint_dir):
    """Whatever the purge reports, it reports counts and paths — never the
    value it removed (#321: removal means the content leaves the trail)."""
    _seed_state()
    _write_checkpoint()
    key = normalize.content_key(CANARY)
    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0
    events = store._events_path(PROJECT).read_text()
    assert CANARY not in events and key in events
