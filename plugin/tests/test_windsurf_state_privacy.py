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


def _hook_module():
    """The Windsurf adapter, loaded as a module (it is a standalone script
    with a hyphenated name, so it cannot be imported normally)."""
    import importlib.util
    from pathlib import Path as _P
    src = (_P(__file__).parent.parent / "daimon_briefing" / "_hooks"
           / "daimon-windsurf-hooks.py")
    spec = importlib.util.spec_from_file_location("_ws_hook_under_test", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


# ---- the writer and the deleter must agree on WHERE ----------------------


def test_hook_and_package_resolve_the_same_state_dir(tmp_path, monkeypatch):
    """Adversarial finding (MAJOR): the hook hardcoded ~/.daimon/windsurf
    while the package honored DAIMON_WINDSURF_DIR, so with the var set the
    hook kept writing plaintext into one directory while purge, reap and
    audit all reported cleanly on an empty other one. Two halves of one
    feature, tested against two different directories, agreeing about
    nothing. Pin them together."""
    hook = _hook_module()
    override = tmp_path / "elsewhere"
    monkeypatch.setenv("DAIMON_WINDSURF_DIR", str(override))
    assert config.windsurf_state_dir() == override
    assert hook.state_dir() == override
    assert hook.transcript_dir() == override / "transcripts"


def test_provenance_resolves_against_the_same_state_dir(tmp_path, monkeypatch):
    """Third component of the same split: SourceResolver hardcoded the home
    path too, so with the override set every Windsurf receipt would report
    absent-local over a transcript that is on disk."""
    from daimon_briefing import provenance
    override = tmp_path / "elsewhere"
    monkeypatch.setenv("DAIMON_WINDSURF_DIR", str(override))
    tdir = override / "transcripts"
    tdir.mkdir(parents=True)
    (tdir / "traj-9.md").write_text("**user**: hi\n", encoding="utf-8")
    resolver = provenance.SourceResolver()
    cands = resolver._candidates({"host": "windsurf", "session_id": "traj-9"})
    assert any(p == tdir / "traj-9.md" for p in cands), cands
    assert provenance.infer_host(tdir / "traj-9.md")[0] == "windsurf"


def test_both_sides_default_under_the_real_daimon_home(tmp_path, monkeypatch):
    """The DEFAULT path is the field path — with the var redirected suite-wide
    by conftest, nothing else asserts it. A typo in either default is
    otherwise invisible (it survived a `windsurf` -> `windsurfXX` mutant)."""
    hook = _hook_module()
    monkeypatch.delenv("DAIMON_WINDSURF_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(config.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(hook.Path, "home", lambda: tmp_path)
    expected = tmp_path / ".daimon" / "windsurf"
    assert config.windsurf_state_dir() == expected
    assert hook.state_dir() == expected


def test_state_window_defaults_to_seven_days_and_never_goes_below_one(
        monkeypatch):
    """0 means DISABLE for every other DAIMON_WINDSURF_* knob; here an
    unclamped 0 made the cutoff `now`, so a live capture buffer was deleted
    at the next heal. Clamp at 1 — the reaper has no off switch, and
    silently deleting live state is the wrong reading of an ambiguous 0."""
    monkeypatch.delenv("DAIMON_WINDSURF_STATE_DAYS", raising=False)
    assert config.windsurf_state_days() == 7
    for raw in ("0", "-1", "not-a-number"):
        monkeypatch.setenv("DAIMON_WINDSURF_STATE_DAYS", raw)
        assert config.windsurf_state_days() >= 1, raw


# ---- purge on forget ------------------------------------------------------


def test_forget_dry_run_never_purges(tmp_checkpoint_dir):
    """The higher-blast-radius call site had no test at all: a purge
    inserted into the dry-run branch passed 580 tests."""
    path, dump, _stamp = _seed_state()
    _write_checkpoint()
    assert cli.main(["forget", CANARY, "--project", PROJECT, "--dry-run"]) == 0
    assert path.exists() and dump.exists()


def test_a_refused_forget_never_purges(tmp_checkpoint_dir):
    path, dump, _stamp = _seed_state()
    _write_checkpoint()
    assert cli.main(["forget", "no such value here", "--project", PROJECT]) == 1
    assert path.exists() and dump.exists()


def test_purge_never_follows_a_symlinked_directory_out_of_its_root(
        tmp_checkpoint_dir, tmp_path):
    """provenance.SourceResolver already refuses to READ outside this root;
    the deleting path must not be laxer than the reading one."""
    outside = tmp_path / "my-notes"
    outside.mkdir()
    (outside / "important.md").write_text("a user file", encoding="utf-8")
    _state_dir().mkdir(parents=True, exist_ok=True)
    (_state_dir() / "transcripts").symlink_to(outside, target_is_directory=True)
    purged, _err = store.purge_windsurf_state()
    assert purged == 0
    assert (outside / "important.md").exists(), "escaped its own root"


def test_forget_states_the_purge_is_machine_wide(tmp_checkpoint_dir, capsys):
    """The store is keyed by trajectory, not project — a forget in ANY
    project purges every Windsurf transcript on the machine. That is the
    only option (the files carry no project attribution), so it must be
    said out loud rather than discovered."""
    _seed_state()
    _write_checkpoint()
    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0
    out = capsys.readouterr().out.lower()
    assert "all projects" in out or "machine-wide" in out





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
