"""worldcheck-outcomes.jsonl: the first confirmed and the first contradicted
outcome per note (#1184, design D10).

Invariants pinned here:
- the WRITER owns first-only: a key already on disk is never written again
  (a race costs one duplicate row, never a loop of them);
- the READER owns the fold: duplicates collapse to the EARLIEST `ts`;
- rows are a pointer and a hash, never note text;
- both halves fail open, like `verification.jsonl`.
"""

import json

import pytest

from daimon_briefing import cli, config, jsonl, store, worldcheck
from tests.test_worldcheck import _cp, _git_repo

PROJECT = "/repo/x"
KEY = "a" * 64
OTHER = "b" * 64


def _path():
    return config.checkpoint_dir() / store.project_slug(PROJECT) / (
        "worldcheck-outcomes.jsonl")


def _plant(rows):
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _row(outcome="contradicted", *, ref="o-111aaa", check="pr-state", key=KEY,
         ts="2026-10-01T10:00:00.000000Z"):
    return {"ts": ts, "item_ref": ref, "check": check, "outcome": outcome,
            "claim_key": key}


def _on_disk(path=None):
    return list(jsonl.read(path or _path()).rows)


# ---- the writer owns first-only ---------------------------------------------


def test_the_first_outcome_of_each_kind_is_written_once(tmp_checkpoint_dir):
    assert store.append_world_outcomes(
        [("o-111aaa", "pr-state", "confirmed", KEY)], project_dir=PROJECT)
    assert store.append_world_outcomes(
        [("o-111aaa", "pr-state", "contradicted", KEY)], project_dir=PROJECT)
    # the same two again, on a later brief: nothing lands
    assert not store.append_world_outcomes(
        [("o-111aaa", "pr-state", "confirmed", KEY),
         ("o-111aaa", "pr-state", "contradicted", KEY)], project_dir=PROJECT)
    rows = _on_disk()
    assert [(r["outcome"]) for r in rows] == ["confirmed", "contradicted"]
    assert set(rows[0]) == {"ts", "item_ref", "check", "outcome", "claim_key"}


def test_a_note_is_keyed_by_item_class_and_claim(tmp_checkpoint_dir):
    batch = [("o-111aaa", "pr-state", "confirmed", KEY),
             ("o-111aaa", "pr-state", "confirmed", OTHER),
             ("o-111aaa", "file-exists", "confirmed", KEY),
             ("o-222bbb", "pr-state", "confirmed", KEY)]
    assert store.append_world_outcomes(batch, project_dir=PROJECT)
    assert len(_on_disk()) == 4


def test_duplicates_inside_one_batch_write_one_row(tmp_checkpoint_dir):
    batch = [("o-111aaa", "pr-state", "confirmed", KEY)] * 3
    assert store.append_world_outcomes(batch, project_dir=PROJECT)
    assert len(_on_disk()) == 1


@pytest.mark.parametrize("bad", [
    ("o-111aaa", "pr-state", "confirmed", "not-hex"),
    ("o-111aaa", "dependency-version", "confirmed", KEY),
    ("o-111aaa", "pr-state", "skipped", KEY),
    ("", "pr-state", "confirmed", KEY),
    (None, "pr-state", "confirmed", KEY),
])
def test_a_malformed_row_is_not_written(tmp_checkpoint_dir, bad):
    assert not store.append_world_outcomes([bad], project_dir=PROJECT)
    assert not _path().exists()


def test_the_kill_switch_writes_nothing(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    assert not store.append_world_outcomes(
        [("o-111aaa", "pr-state", "confirmed", KEY)], project_dir=PROJECT)
    assert not _path().exists()


def test_an_unreadable_ledger_skips_the_append_and_keeps_its_bytes(
        tmp_checkpoint_dir):
    _path().parent.mkdir(parents=True)
    _path().write_bytes(b"\nnot json at all\n")
    assert not store.append_world_outcomes(
        [("o-111aaa", "pr-state", "confirmed", KEY)], project_dir=PROJECT)
    assert _path().read_bytes() == b"\nnot json at all\n"


# ---- the reader owns the fold -----------------------------------------------


def test_the_reader_keeps_the_earliest_row_per_key(tmp_checkpoint_dir):
    late = _row(ts="2026-10-02T10:00:00.000000Z")
    early = _row(ts="2026-10-01T09:00:00.000000Z")
    _plant([late, early, _row("confirmed")])  # race: later row first on disk
    folded = store.first_world_outcomes(project_dir=PROJECT)
    assert folded[("o-111aaa", "pr-state", KEY, "contradicted")] == early
    assert len(folded) == 2


def test_equal_timestamps_fold_deterministically(tmp_checkpoint_dir):
    a, b = _row(), {**_row(), "claim_key": KEY, "item_ref": "o-111aaa"}
    b["extra"] = "z"
    _plant([a, b])
    first = store.first_world_outcomes(project_dir=PROJECT)
    _plant([b, a])
    assert store.first_world_outcomes(project_dir=PROJECT) == first


def test_a_row_this_module_could_not_have_written_is_ignored(tmp_checkpoint_dir):
    _plant([_row(key="short"), _row(check="receipt"), _row("skipped"),
            _row(ts="yesterday"), _row(ref=""), {"item_ref": 4}, _row(ref="o-1")])
    assert list(store.first_world_outcomes(project_dir=PROJECT)) == [
        ("o-1", "pr-state", KEY, "contradicted")]


def test_a_missing_or_garbage_ledger_reads_as_empty(tmp_checkpoint_dir):
    assert store.first_world_outcomes(project_dir=PROJECT) == {}
    _path().parent.mkdir(parents=True)
    _path().write_bytes(b"\xff\xfe not utf-8 \x00")
    assert store.first_world_outcomes(project_dir=PROJECT) == {}


def test_the_reader_can_be_pointed_at_a_bucket(tmp_checkpoint_dir):
    _plant([_row()])
    assert len(store.first_world_outcomes(bucket=_path().parent)) == 1


# ---- from what check() returns to the file ----------------------------------


def _checked(monkeypatch, tmp_path, texts):
    """The ledger rows of one `check()` per text (one claim per run, so each
    item keeps the id `o-000001`: a different note needs a different claim)."""
    project = _git_repo(tmp_path / "repo", branches=["feat/397-there"])
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    rows = []
    for text in texts:
        stats = worldcheck.check(_cp([text]), str(project))
        rows += stats[worldcheck.LEDGER_KEY]
    return str(project), rows


def test_both_outcomes_reach_the_file_once_across_briefs(
        tmp_checkpoint_dir, monkeypatch, tmp_path):
    project, rows = _checked(monkeypatch, tmp_path, [
        "work continues on branch feat/397-there, unmerged",   # confirmed
        "work continues on branch feat/397-slice-2, unmerged"])   # contradicted
    cli._write_worldcheck_outcomes(rows, project)
    cli._write_worldcheck_outcomes(rows, project)          # the next brief
    path = config.checkpoint_dir() / store.project_slug(project) / (
        "worldcheck-outcomes.jsonl")
    got = _on_disk(path)
    assert sorted((r["item_ref"], r["check"], r["outcome"]) for r in got) == [
        ("o-000001", "branch-state", "confirmed"),
        ("o-000001", "branch-state", "contradicted")]
    text = path.read_text()
    assert "feat/397-there" not in text and "feat/397-slice-2" not in text


def test_receipt_rows_and_idless_items_write_no_outcome(
        tmp_checkpoint_dir, monkeypatch):
    project = "/repo/x"
    cli._write_worldcheck_outcomes(
        [("o-111aaa", "receipt", "receipt-invalid"),
         ("o-111aaa", worldcheck.LEDGER_CONFIRM_CHECK, "receipt-valid")],
        project)
    assert not _path().exists()


def test_a_contradiction_stays_a_ledger_row_and_a_confirmation_stays_off_it(
        tmp_checkpoint_dir, monkeypatch, tmp_path):
    """The outcomes file is a SECOND stream: verification.jsonl keeps its
    contract (problems found), and a first confirmation is not read as a cure."""
    project, rows = _checked(monkeypatch, tmp_path, [
        "work continues on branch feat/397-there, unmerged"])
    cli._write_worldcheck_ledger(rows, project)
    cli._write_worldcheck_outcomes(rows, project)
    assert store.verification_rows(project) == []
    assert len(store.first_world_outcomes(project_dir=project)) == 1


def test_an_unknown_project_reads_empty_and_writes_nothing(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(store, "project_slug", lambda _p=None: "")
    assert store.first_world_outcomes(project_dir=PROJECT) == {}
    assert not store.append_world_outcomes(
        [("o-111aaa", "pr-state", "confirmed", KEY)], project_dir=PROJECT)


def test_an_oserror_on_the_write_is_a_false_not_a_raise(tmp_checkpoint_dir):
    _path().parent.parent.mkdir(parents=True, exist_ok=True)
    _path().parent.write_text("a file where the bucket directory belongs")
    assert not store.append_world_outcomes(
        [("o-111aaa", "pr-state", "confirmed", KEY)], project_dir=PROJECT)
