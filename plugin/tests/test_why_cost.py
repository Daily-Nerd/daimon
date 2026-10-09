"""What an exact-id lookup costs (#1132 PR 11a): bounded by this project's
bucket, never by the machine. The store holds the shape that made the old walk
slow: many other projects' session files in the shared flat directory. A lookup
of an id nothing holds must parse none of them, open no session file, and
rebuild nothing."""

import json
import time
from pathlib import Path

import pytest

from daimon_briefing import config, recall, store, view
from daimon_briefing.surfaces import Writer

PROJECT = "/p/why-cost"
SLUG = store.project_slug(PROJECT)
NOISE = 150
MINE = 12


def _cp(sid, created, project_slug=None):
    cp = {"session_id": sid, "created": created, "author": "ada",
          "working_context": {
              "active_topic": {"text": f"topic {sid}", "trust": "inferred"},
              "open_questions": [
                  {"text": f"question {j} of {sid}", "trust": "inferred"}
                  for j in range(20)]},
          "epistemic_snapshot": {}}
    if project_slug:
        cp["project_slug"] = project_slug
    return cp


@pytest.fixture
def busy_store(tmp_checkpoint_dir):
    for i in range(MINE):
        store.write_checkpoint(f"S-{i:03d}", _cp(f"S-{i:03d}", f"2026-08-{i + 1:02d}T10:00:00Z"),
                               project_dir=PROJECT, writer=Writer.HUMAN)
    root = config.checkpoint_dir()
    for i in range(NOISE):
        cp = _cp(f"N-{i:03d}", "2026-07-01T10:00:00Z", f"-other-{i % 10}")
        (root / f"N-{i:03d}.json").write_text(json.dumps(cp))
    recall.rebuild()
    return root


def test_a_missing_id_parses_no_session_file_and_rebuilds_nothing(
        busy_store, monkeypatch):
    read = []
    real = Path.read_text

    def spy(self, *a, **k):
        read.append(self)
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", spy)

    def boom(*_a, **_k):
        raise AssertionError("an id lookup must not walk or rebuild")

    monkeypatch.setattr(store, "project_surfaces", boom)
    monkeypatch.setattr(recall, "rebuild", boom)
    monkeypatch.setattr(recall, "_rebuild_forced", boom)
    monkeypatch.setattr(recall, "_ensure_fresh", boom)
    got = view.lookup_many(PROJECT, ["o-ffffffffffff"])
    assert isinstance(got["o-ffffffffffff"], view.Absent)
    flat = [p for p in read if p.parent == busy_store]
    assert flat == [], "a flat session file was parsed"
    # the window: at most the pointers of this one bucket
    pointers = [p for p in read if p.parent == busy_store / SLUG
                and p.suffix == ".json"]
    assert len(pointers) <= config.checkpoint_history()


def test_lineage_of_a_missing_id_pays_one_snapshot_and_one_window(
        busy_store, monkeypatch):
    calls = {"snapshot": 0, "pointers": 0}
    for name in calls:
        real = getattr(view, name)

        def spy(*a, _r=real, _n=name, **k):
            calls[_n] += 1
            return _r(*a, **k)

        monkeypatch.setattr(view, name, spy)
    view.lineage(PROJECT, "o-ffffffffffff")
    assert calls == {"snapshot": 1, "pointers": 1}


def test_the_lookup_is_far_cheaper_than_the_machine_wide_walk(busy_store,
                                                              capsys):
    """Figures for the record (run with -s). The old path parsed every JSON
    file of every project (`store.project_surfaces`); the bound here is the
    shape (an order of magnitude), not a wall-clock budget."""
    t0 = time.perf_counter()
    surfaces = store.project_surfaces(PROJECT)
    walk_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    view.lookup_many(PROJECT, ["o-ffffffffffff"])
    lookup_ms = (time.perf_counter() - t0) * 1000
    with capsys.disabled():
        print(f"\nwhy cost: old walk {walk_ms:.1f} ms over {len(surfaces)} "
              f"files in scope ({NOISE + MINE} flat files on disk); "
              f"lookup_many {lookup_ms:.1f} ms")
    assert lookup_ms < max(walk_ms, 20.0) * 2
