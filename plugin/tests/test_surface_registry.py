"""#601: the declared surface registry — new stores must declare a delete
strategy.

Four shipped defects of one class (#583, quote/scene #599, events item_text
#599, team copies #600-pending) came from the same structural hole: three
hand-maintained parallel lists (store._plaintext_surfaces, privacy's
exemptions, recall._fingerprint) each answered "what files exist and what may
they hold" differently, and a new file shape silently inherited a hole in
whichever list its author forgot. The registry is the single declaration:
every file shape daimon writes under ~/.daimon states whether it can hold
item plaintext, how deletion reaches it, and who owns it. The auditor derives
its exemptions from it; the write-audit guard refuses shapes it has never
seen declared; a plaintext shape with no reachable deletion must name the
tracking issue for its gap.
"""
import os
import time

from daimon_briefing import cli, config, privacy, recall, store, surfaces


# ---- declaration hygiene --------------------------------------------------


def test_every_entry_declares_the_full_contract():
    assert surfaces.SURFACES, "registry must not be empty"
    seen = set()
    for s in surfaces.SURFACES:
        assert s.shape and s.owner, f"underspecified entry: {s}"
        assert s.shape not in seen, f"duplicate shape: {s.shape}"
        seen.add(s.shape)
        assert s.delete in surfaces.DELETE_STRATEGIES, \
            f"{s.shape}: unknown delete strategy {s.delete!r}"


def test_plaintext_never_pairs_with_exempt():
    for s in surfaces.SURFACES:
        if s.plaintext:
            assert s.delete != "exempt-no-plaintext", \
                f"{s.shape} holds plaintext but claims the exemption"
        else:
            assert s.delete == "exempt-no-plaintext", \
                f"{s.shape} holds no plaintext yet declares {s.delete}"


def test_known_gaps_name_their_tracking_issue():
    gaps = [s for s in surfaces.SURFACES if s.delete == "known-gap"]
    assert gaps, "the team mirror gap (#600) must be declared, not hidden"
    for s in gaps:
        assert s.issue, f"{s.shape}: a known gap must cite its issue"


def test_the_load_bearing_shapes_are_registered():
    for shape in ("checkpoints/{slug}/events.jsonl",
                  "checkpoints/{slug}/verification.jsonl",
                  "checkpoints/{slug}/forget-hits.jsonl",
                  "checkpoints/.chunk-cache/*",
                  "recall.db",
                  "recall.db.{pid}.tmp*"):
        assert surfaces.match(shape) is not None, f"unregistered: {shape}"


def test_windsurf_state_is_declared_not_invisible():
    """Adversarial finding (BLOCKER): the windsurf adapter accumulates FULL
    RAW TRANSCRIPTS under ~/.daimon/windsurf/transcripts — the largest
    plaintext store daimon writes, and the registry claimed completeness
    without it. #607 brought it inside the contract (wholesale purge on
    forget, age reaper on heal); the activity stamps and installed hook
    copies hold no item text."""
    t = surfaces.match("windsurf/transcripts/traj-1.md")
    assert t is not None and t.delete == "wholesale-purge"
    u = surfaces.match("windsurf/unparsed-post_cascade_response-123.json")
    assert u is not None and u.delete == "wholesale-purge"
    assert surfaces.match("windsurf/traj-1.last-activity").delete \
        == "exempt-no-plaintext"
    assert surfaces.match("windsurf/traj-1.last-serialize").delete \
        == "exempt-no-plaintext"
    assert surfaces.match("hooks/daimon-windsurf-hooks.py").delete \
        == "exempt-no-plaintext"


def test_pid_placeholder_matches_digits_only():
    """{pid} must not degrade to a bare wildcard: recall.db.bak.tmp and
    recall.db.tmp are a user's own files and must stay UNDECLARED so the
    registry-derived reaper cannot touch them."""
    reap = surfaces.match("recall.db.44594.tmp")
    assert reap is not None and reap.delete == "reap"
    assert surfaces.match("recall.db.44594.tmp-journal").delete == "reap"
    assert surfaces.match("recall.db.bak.tmp") is None
    assert surfaces.match("recall.db.tmp") is None
    assert surfaces.match("recall.db.tmpfoo") is None


def test_recall_db_declares_lazy_rebuild_not_rewrite():
    """Nothing rewrites recall.db at forget time — rows leave at the next
    fingerprint-triggered rebuild, and if no recall command ever runs the
    plaintext persists. The strategy name must say so."""
    assert surfaces.match("recall.db").delete == "lazy-rebuild"


# ---- pattern matching -----------------------------------------------------


def test_match_classifies_write_audit_patterns():
    assert surfaces.match("checkpoints/{slug}/events.jsonl").delete \
        == "append-tombstone"
    assert surfaces.match("checkpoints/{hash}.json").plaintext is True
    assert surfaces.match("checkpoints/{slug}/S1.json").plaintext is True
    assert surfaces.match("team/{remote}/README.md").delete \
        == "exempt-no-plaintext"
    assert surfaces.match("team/{remote}/projects/p/authors/a/S1.json").delete \
        == "known-gap"
    assert surfaces.match("checkpoints/latest.json.bak-1782874461") is None
    assert surfaces.match("somewhere/unregistered.bin") is None


# ---- derivations ----------------------------------------------------------


def test_exempt_suffix_refuses_a_registry_without_one(monkeypatch):
    """A registry stripped of its suffix exemption must fail loudly — a
    silent empty string would quietly un-exempt every receipt sidecar and
    flood the audit with false residue."""
    import pytest

    monkeypatch.setattr(surfaces, "SURFACES", ())
    with pytest.raises(LookupError):
        surfaces.exempt_suffix()


def test_exempt_suffix_refuses_two_suffix_exemptions(monkeypatch):
    """privacy._is_plaintext_free compares against exactly ONE suffix —
    declaration order silently deciding the winner is the guess the
    registry exists to forbid."""
    import pytest

    two = (surfaces.Surface("a/*.receipt", "x", False,
                            "exempt-no-plaintext", "none", audit_exempt=True),
           surfaces.Surface("b/*.sidecar", "y", False,
                            "exempt-no-plaintext", "none", audit_exempt=True))
    monkeypatch.setattr(surfaces, "SURFACES", two)
    with pytest.raises(LookupError):
        surfaces.exempt_suffix()


def test_reap_is_registry_derived_not_a_parallel_predicate(
        tmp_path, monkeypatch):
    """The reaper's filter IS surfaces.match() — remove the reap declaration
    and the reaper must delete nothing, proving there is no second
    hand-written predicate (the parallel-list defect this issue exists to
    kill, reintroduced in the one path that deletes files)."""
    dead, journal, _fresh = _plant_orphans()
    no_reap = tuple(s for s in surfaces.SURFACES if s.delete != "reap")
    monkeypatch.setattr(surfaces, "SURFACES", no_reap)
    assert recall.reap_dead_snapshots() == []
    assert dead.exists() and journal.exists()


def test_privacy_exemptions_derive_from_the_registry():
    """The auditor's name-based exemption set is a VIEW of the registry, not
    a fourth parallel list."""
    assert privacy._EXEMPT_NAMES == surfaces.exempt_names()
    assert privacy._EXEMPT_SUFFIX == surfaces.exempt_suffix()
    # The registry hardcodes the lock filename in a shape string — this pin
    # is what breaks if store._LOCK_NAME is ever renamed without the
    # registry following (the shape is a copy, not a reference).
    assert store._LOCK_NAME in surfaces.exempt_names()


# ---- the reap verb --------------------------------------------------------


def _plant_orphans():
    db = config.recall_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    dead = db.parent / f"{db.name}.44594.tmp"
    journal = db.parent / f"{db.name}.44594.tmp-journal"
    dead.write_text("dead snapshot bytes")
    journal.write_text("journal bytes")
    old = time.time() - 2 * 3600
    os.utime(dead, (old, old))
    os.utime(journal, (old, old))
    fresh = db.parent / f"{db.name}.{os.getpid()}.tmp"
    fresh.write_text("live rebuild in flight")
    return dead, journal, fresh


def test_reap_removes_dead_snapshots_and_spares_fresh_ones(tmp_path):
    dead, journal, fresh = _plant_orphans()
    reaped = recall.reap_dead_snapshots()
    assert sorted(p.name for p in reaped) == [dead.name, journal.name]
    assert not dead.exists() and not journal.exists()
    assert fresh.exists(), "a fresh in-flight rebuild tmp must survive"


def test_reap_skips_non_tmp_siblings_directories_and_glob_errors(
        tmp_path, monkeypatch):
    db = config.recall_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    backup = db.parent / f"{db.name}.backup"      # no .tmp — never a target
    backup.write_text("user's own backup")
    dirlike = db.parent / f"{db.name}.44594.tmp"   # directory, not a file
    dirlike.mkdir()
    old = time.time() - 2 * 3600
    os.utime(backup, (old, old))
    os.utime(dirlike, (old, old))
    assert recall.reap_dead_snapshots() == []
    assert backup.exists() and dirlike.is_dir()


def test_reap_takes_only_pid_shaped_strands_never_user_files(
        tmp_path, monkeypatch):
    """Adversarial finding: the first filter (`.tmp` substring) was a strict
    superset of the declared shape `recall.db.{pid}.tmp*` — a user backup
    named recall.db.tmp / recall.db.bak.tmp / recall.db.tmp.gz beside a
    DAIMON_RECALL_DB override would have been silently deleted. The reaper
    deletes ONLY what rebuild() manufactures: <db>.<digits>.tmp plus its
    sqlite dash-sidecars."""
    db = config.recall_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    old = time.time() - 2 * 3600
    spared = []
    for name in (f"{db.name}.tmp", f"{db.name}.bak.tmp",
                 f"{db.name}.tmp.gz", f"{db.name}.tmpl"):
        p = db.parent / name
        p.write_text("not daimon's to delete")
        os.utime(p, (old, old))
        spared.append(p)
    dead = db.parent / f"{db.name}.44594.tmp-journal"
    dead.write_text("strand")
    os.utime(dead, (old, old))
    assert [p.name for p in recall.reap_dead_snapshots()] == [dead.name]
    for p in spared:
        assert p.exists(), f"{p.name} is a user file, not a strand"
    # an undeletable strand is skipped, not fatal, and not reported reaped
    dead, journal, _fresh = _plant_orphans()
    real_unlink = type(dead).unlink

    def deny_dead(self, missing_ok=False):
        if self.name == dead.name:
            raise OSError("EPERM")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(type(dead), "unlink", deny_dead)
    assert [p.name for p in recall.reap_dead_snapshots()] == [journal.name]
    monkeypatch.undo()
    # an unreadable parent aborts quietly with nothing reaped
    monkeypatch.setattr(type(db.parent), "glob",
                        lambda self, pat: (_ for _ in ()).throw(OSError()))
    assert recall.reap_dead_snapshots() == []


def test_reap_never_touches_the_live_db(tmp_path):
    db = config.recall_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_text("the live derived index")
    _plant_orphans()
    recall.reap_dead_snapshots()
    assert db.exists()


def test_heal_reaps_orphans_and_dry_run_only_lists(tmp_path, capsys):
    dead, journal, fresh = _plant_orphans()
    assert cli.main(["heal", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert dead.name in out
    assert dead.exists() and journal.exists(), "--dry-run must not delete"
    assert cli.main(["heal"]) == 0
    out = capsys.readouterr().out
    assert dead.name in out
    assert not dead.exists() and not journal.exists()
    assert fresh.exists()


def test_reap_clears_the_audit_unscannable_class(tmp_path):
    """The four dead -journal sidecars were what pinned a real install at
    exit 3 (cannot-prove): unopenable as databases, so the audit honestly
    refused to certify. After the reap they are gone, not explained away."""
    _write_min_checkpoint()
    dead, journal, fresh = _plant_orphans()
    fresh.unlink()          # leave only the dead pair
    before = privacy.audit_project(project_dir=_P)
    assert any(journal.name in u for u in before["unscannable"])
    recall.reap_dead_snapshots()
    after = privacy.audit_project(project_dir=_P)
    assert not any(journal.name in u for u in after["unscannable"])


_P = "/p/surface-registry"


def _write_min_checkpoint():
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "a decision that stays", "trust": "inferred"}]},
    }, project_dir=_P)
