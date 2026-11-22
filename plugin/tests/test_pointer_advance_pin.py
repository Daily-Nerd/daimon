"""Byte pins for the pointer-advance sequence of `store.write_checkpoint`.

latest.json / prev-N.json rotation, the regress guard and the history cap are
pinned through the real writer so the sequence can move into one function
without changing a byte. A pointer copy is the exact blob written to the flat
`<session>.json`, so every assertion compares pointer bytes to the flat file of
the session that should sit in that slot."""

from daimon_briefing import config, store
from daimon_briefing.surfaces import Writer

P1 = "/p/advance-one"
P2 = "/p/advance-two"


def _cp(sid, created):
    return {"session_id": sid, "created": created, "author": "ada",
            "working_context": {"active_topic": {"text": f"topic {sid}",
                                                 "trust": "inferred"}},
            "epistemic_snapshot": {}}


def _write(sid, created, project=P1, **kw):
    return store.write_checkpoint(sid, _cp(sid, created), project_dir=project,
                                  writer=Writer.HUMAN, **kw)


def _flat(sid):
    return (config.checkpoint_dir() / f"{sid}.json").read_bytes()


def _pointers(d):
    return {p.name: p.read_bytes() for p in sorted(d.iterdir())
            if p.is_file() and (p.name == "latest.json"
                                or p.name.startswith("prev-"))}


def _expect(d, *sids):
    """Slot 0 is latest.json, slot i is prev-i.json; exact names and bytes."""
    want = {"latest.json": _flat(sids[0])}
    for i, sid in enumerate(sids[1:], 1):
        want[f"prev-{i}.json"] = _flat(sid)
    assert _pointers(d) == want


def _flat_names():
    d = config.checkpoint_dir()
    return sorted(p.stem for p in d.glob("*.json")
                  if p.name != "latest.json"
                  and not p.name.startswith("prev-")
                  and not p.name.startswith("introspection-"))


def test_first_write_has_latest_only(tmp_checkpoint_dir):
    _write("S-1", "2026-08-01T00:00:00Z")
    d = config.checkpoint_dir()
    _expect(d, "S-1")
    _expect(d / store.project_slug(P1), "S-1")


def test_rotation_fills_then_caps_at_history(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    d = config.checkpoint_dir()
    b = d / store.project_slug(P1)
    _write("S-1", "2026-08-01T00:00:00Z")
    _write("S-2", "2026-08-02T00:00:00Z")
    _expect(d, "S-2", "S-1")
    _write("S-3", "2026-08-03T00:00:00Z")
    _expect(d, "S-3", "S-2", "S-1")
    _write("S-4", "2026-08-04T00:00:00Z")
    _write("S-5", "2026-08-05T00:00:00Z")
    _expect(d, "S-5", "S-4", "S-3")
    _expect(b, "S-5", "S-4", "S-3")
    assert not (b / "prev-3.json").exists()


def test_history_one_keeps_latest_only(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "1")
    _write("S-1", "2026-08-01T00:00:00Z")
    _write("S-2", "2026-08-02T00:00:00Z")
    _expect(config.checkpoint_dir(), "S-2")


def test_older_checkpoint_hits_regress_guard(tmp_checkpoint_dir):
    d = config.checkpoint_dir()
    b = d / store.project_slug(P1)
    _write("S-1", "2026-08-01T00:00:00Z")
    _write("S-3", "2026-08-03T00:00:00Z")
    before = _pointers(d), _pointers(b)
    _write("S-2", "2026-08-02T00:00:00Z")
    assert (_pointers(d), _pointers(b)) == before  # no churn, no steal
    _expect(d, "S-3", "S-1")
    assert "S-2" in _flat_names()  # the flat file is still written


def test_rotate_false_overwrites_latest_without_history(tmp_checkpoint_dir):
    d = config.checkpoint_dir()
    _write("S-1", "2026-08-01T00:00:00Z")
    _write("S-2", "2026-08-02T00:00:00Z", rotate=False)
    _expect(d, "S-2")


def test_two_buckets_have_independent_chains(tmp_checkpoint_dir):
    d = config.checkpoint_dir()
    _write("A-1", "2026-08-01T00:00:00Z", project=P1)
    _write("B-1", "2026-08-02T00:00:00Z", project=P2)
    _write("A-2", "2026-08-03T00:00:00Z", project=P1)
    _expect(d / store.project_slug(P1), "A-2", "A-1")
    _expect(d / store.project_slug(P2), "B-1")
    _expect(d, "A-2", "B-1", "A-1")  # the flat dir is the global chain


def test_gc_keeps_newest_plus_pointer_stems(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "2")
    monkeypatch.setenv("DAIMON_CHECKPOINT_KEEP", "2")
    monkeypatch.setenv("DAIMON_GC_PIN_IMPORTANCE", "0")
    for n in range(1, 6):
        _write(f"S-{n}", f"2026-08-0{n}T00:00:00Z")
    assert _flat_names() == ["S-4", "S-5"]
    # an old session healed late is written, blocked from latest, and GC'd
    _write("S-0", "2026-07-01T00:00:00Z")
    assert _flat_names() == ["S-4", "S-5"]
    _expect(config.checkpoint_dir(), "S-5", "S-4")


def test_gc_protects_pointer_stems_outside_the_newest_window(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "3")
    monkeypatch.setenv("DAIMON_CHECKPOINT_KEEP", "1")
    monkeypatch.setenv("DAIMON_GC_PIN_IMPORTANCE", "0")
    for n in range(1, 5):
        _write(f"S-{n}", f"2026-08-0{n}T00:00:00Z")
    # newest 1 (S-4) plus the stems latest/prev-1/prev-2 point at (S-4,S-3,S-2)
    assert _flat_names() == ["S-2", "S-3", "S-4"]


def test_advance_pointers_reports_whether_latest_moved(tmp_path):
    new = '{"created": "2026-08-02T00:00:00Z"}'
    old = '{"created": "2026-08-01T00:00:00Z"}'
    t = store._created_epoch
    assert store._advance_pointers(tmp_path, new, t("2026-08-02T00:00:00Z"),
                                   True, 3) is True
    assert store._advance_pointers(tmp_path, old, t("2026-08-01T00:00:00Z"),
                                   True, 3) is False
    assert (tmp_path / "latest.json").read_text() == new
    assert not (tmp_path / "prev-1.json").exists()
