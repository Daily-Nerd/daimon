"""#620 interim: a foreign tombstone apply must name what it cannot reach.

`store.apply_foreign_tombstones` calls `scrub_content_key` and nothing else,
so it walks the `*.json` checkpoint shapes only. It printed an unqualified
success line while eleven other plaintext surface classes kept the value.
An irreversible, machine-wide operation reporting a clean sweep it did not
perform is the silent-success shape this issue exists to remove.

The structural fix retires with the copy model (#752). Until then the honest
report is the whole deliverable: say the coverage, name the gap.

NEGATIVE CONTROL (#944): the existing test for this path asserts no residue
using the same surface enumeration the scrubber itself walks, so it cannot
fail for a missing surface class no matter how many are missing. Every
assertion here derives its expectation from the REGISTRY instead, and the
disagreeing pair below is the point: one shape must be covered and another
must not, so a function stuck returning everything (or nothing) fails.
"""
from daimon_briefing import surfaces


def test_the_covered_shapes_are_all_declared_plaintext_surfaces():
    """A shape claimed as covered that the registry does not declare is a
    typo that would silently shrink the reported gap."""
    declared = {s.shape for s in surfaces.SURFACES if s.plaintext}
    assert surfaces.FOREIGN_APPLY_SHAPES <= declared, \
        surfaces.FOREIGN_APPLY_SHAPES - declared


def test_the_gap_is_every_other_plaintext_surface():
    """Derived from the registry, never hardcoded: adding a plaintext surface
    without adding it to the covered set makes it appear in the gap on its
    own, rather than being silently absorbed into a clean success line."""
    expected = {s.shape for s in surfaces.SURFACES
                if s.plaintext} - surfaces.FOREIGN_APPLY_SHAPES
    assert set(surfaces.foreign_apply_gap()) == expected


def test_the_gap_disagrees_with_itself():
    """The negative control. A gap function stuck at 'everything' or at
    'nothing' would satisfy a one-sided test; only a disagreeing pair
    separates a live answer from either dead one."""
    gap = set(surfaces.foreign_apply_gap())
    assert "checkpoints/{slug}/events.jsonl" in gap, "a known-missed class"
    assert "checkpoints/{slug}/*.json" not in gap, "a known-covered class"


def test_the_gap_names_every_class_the_issue_reproduced():
    """#620 reproduced the value surviving in each of these after an armed
    apply reported success. If a later change quietly starts claiming one,
    this fails rather than the success line growing more confident."""
    gap = set(surfaces.foreign_apply_gap())
    for shape in ("checkpoints/{slug}/events.jsonl",
                  "checkpoints/.chunk-cache/*",
                  "logs/serialize-crash.log",
                  "windsurf/transcripts/*.md",
                  "team/{remote}/**/*.json"):
        assert shape in gap, shape


def test_the_gap_is_not_empty_and_is_sorted():
    """Empty would mean full coverage, which is the claim this issue exists
    to stop anyone making by accident."""
    gap = surfaces.foreign_apply_gap()
    assert gap, "an empty gap asserts a clean sweep that does not happen"
    assert list(gap) == sorted(gap)


# ---- the reported line, which is the user-visible half ----


def _run_apply(monkeypatch, capsys, tmp_path, applied):
    from daimon_briefing import config, store
    from daimon_briefing.cli import team
    import argparse
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "ckpt"))
    monkeypatch.setattr(config, "team_apply_forget", lambda: True)
    monkeypatch.setattr(store, "apply_foreign_tombstones",
                        lambda **kw: list(applied))
    monkeypatch.setattr(team.recall, "warm", lambda *a, **k: None)
    monkeypatch.setattr(team, "_sync", lambda *a, **k: None, raising=False)
    args = argparse.Namespace(apply_forget=True)
    try:
        team._cmd_team_sync(args)
    except AttributeError:
        team.cmd_sync(args)
    return capsys.readouterr().out


def test_the_success_line_names_what_it_did_not_reach(monkeypatch, capsys,
                                                      tmp_path):
    """The whole deliverable. Before this, the line read as a clean sweep of
    every surface holding the value; eleven plaintext classes kept it."""
    out = _run_apply(monkeypatch, capsys, tmp_path, ["a.json", "b.json"])
    assert "2" in out
    assert "not reached" in out.lower() or "did not reach" in out.lower()
    assert "checkpoints/{slug}/events.jsonl" in out


def test_a_zero_surface_apply_still_names_the_gap(monkeypatch, capsys,
                                                  tmp_path):
    """Zero rewritten is the case most likely to read as 'nothing to do'.
    It is not: the value may sit in every class the walk never opens."""
    out = _run_apply(monkeypatch, capsys, tmp_path, [])
    assert "checkpoints/{slug}/events.jsonl" in out
