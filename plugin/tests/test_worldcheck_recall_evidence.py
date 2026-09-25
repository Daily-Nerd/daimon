"""Derived world evidence reaches recall without cross-claim cures."""

import json

import pytest

from daimon_briefing import cli, config, recall, store, worldcheck
from tests.test_recall import _cp
from tests.test_recall_invalidated_by import _receipt_row, _write_ledger
from tests.test_worldcheck import _cp as _carried_cp
from tests.test_worldcheck import _enable_probes, _git_repo
from tests.test_worldcheck import fake_gh  # noqa: F401 - pytest fixture


def test_file_evidence_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    project = tmp_path / "project"
    project.mkdir()
    text = "file `axolotl.py` exists"
    cp = _carried_cp([text])
    item = cp["working_context"]["open_questions"][0]
    store.write_checkpoint("S-1", _cp("S-1", questions=[item]),
                           project_dir=project)
    assert recall.search("axolotl", project_dir=project)[0]["invalidated_by"] is None

    stats = worldcheck.check(cp, project)
    assert stats["file-exists:contradicted"] == 1
    rows = stats[worldcheck.LEDGER_KEY]
    assert not store.verification_rows(project)  # probes remain read-only
    cli._write_worldcheck_ledger(rows, project)
    hits = recall.search("axolotl", project_dir=project)
    assert hits[0]["invalidated_by"].startswith("file-exists:")
    assert hits[0]["cured_by"] is None

    (project / "axolotl.py").touch()
    stats = worldcheck.check(_carried_cp([text]), project)
    cli._write_worldcheck_ledger(stats[worldcheck.LEDGER_KEY], project)
    hits = recall.search("axolotl", project_dir=project)
    assert hits[0]["invalidated_by"] is None
    assert hits[0]["cured_by"].startswith("file-exists-ok:")
    before = store.verification_rows(project)
    cli._write_worldcheck_ledger(stats[worldcheck.LEDGER_KEY], project)
    assert store.verification_rows(project) == before  # sparse confirmations
    assert store.verification_counts(project) == {"file-exists": 1}
    (project / "axolotl.py").unlink()
    stats = worldcheck.check(_carried_cp([text]), project)
    cli._write_worldcheck_ledger(stats[worldcheck.LEDGER_KEY], project)
    assert recall.search("axolotl", project_dir=project)[0]["invalidated_by"]


def _world_row(check, *, key="a" * 64, ts="2026-08-29T10:00:00Z"):
    return {"item_ref": "o-111aaa", "check": check, "claim_key": key,
            "reason": "claim-confirmed" if check.endswith("-ok") else "claim-contradicted",
            "ts": ts}


@pytest.mark.parametrize("cls", ["file-exists", "branch-state", "pr-state"])
def test_receipt_cure_cannot_clear_world_contradiction(
        cls, tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-1", _cp("S-1", questions=[
        {"text": "axolotl exporter", "id": "o-111aaa", "trust": "inferred"}]),
        project_dir="/repo/x")
    slug = store.project_slug("/repo/x")
    rows = [_world_row(cls), _receipt_row("o-111aaa"),
            {**_receipt_row("o-111aaa", ts="2026-08-29T11:00:00Z",
                            reason="receipt-valid"), "check": "receipt-ok"}]
    _write_ledger(slug, rows)
    hit = recall.search("axolotl exporter", project_dir="/repo/x")[0]
    assert hit["invalidated_by"].startswith(cls + ":")
    assert hit["cured_by"] is None
    # A different claim of the SAME class cannot cure this one either.
    rows.append(_world_row(cls + "-ok", key="b" * 64,
                           ts="2026-08-29T12:00:00Z"))
    _write_ledger(slug, rows)
    assert recall.search("axolotl exporter", project_dir="/repo/x")[0]["invalidated_by"]
    rows.append(_world_row(cls + "-ok", ts="2026-08-29T13:00:00Z"))
    _write_ledger(slug, list(reversed(rows)))  # timestamps, not append order
    hit = recall.search("axolotl exporter", project_dir="/repo/x")[0]
    assert hit["invalidated_by"] is None
    assert hit["cured_by"].startswith(cls + "-ok:")


def test_world_cure_cannot_clear_receipt_contradiction(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-1", _cp("S-1", questions=[
        {"text": "axolotl exporter", "id": "o-111aaa", "trust": "inferred"}]),
        project_dir="/repo/x")
    _write_ledger(store.project_slug("/repo/x"), [
        _receipt_row("o-111aaa"), _world_row("file-exists"),
        _world_row("file-exists-ok", ts="2026-08-29T12:00:00Z")])
    hit = recall.search("axolotl exporter", project_dir="/repo/x")[0]
    assert hit["invalidated_by"].startswith("receipt:")
    assert hit["cured_by"] is None


def test_claim_identity_and_unknown_probe_neutrality(tmp_path):
    first = worldcheck.check(_carried_cp(["file `axolotl.py` exists"]), tmp_path)
    second = worldcheck.check(_carried_cp(["file `capybara.py` exists"]), tmp_path)
    a = first[worldcheck.LEDGER_KEY][0]
    b = second[worldcheck.LEDGER_KEY][0]
    assert a[3] != b[3]
    assert "axolotl" not in json.dumps(a)  # no raw target in the ledger
    # Unsafe/outside-project paths remain skipped, never evidence.
    skipped = worldcheck.check(_carried_cp(["file `../axolotl.py` exists"]), tmp_path)
    assert worldcheck.LEDGER_KEY not in skipped


def test_unmatched_confirmation_writes_nothing(tmp_path):
    (tmp_path / "axolotl.py").touch()
    stats = worldcheck.check(_carried_cp(["file `axolotl.py` exists"]), tmp_path)
    cli._write_worldcheck_ledger(stats[worldcheck.LEDGER_KEY], tmp_path)
    assert store.verification_rows(tmp_path) == []
    assert not (config.checkpoint_dir() / store.project_slug(tmp_path) /
                "verification.jsonl").exists()


@pytest.mark.parametrize("cls,text", [
    ("branch-state", "axolotl exporter on branch feat/probe, unmerged"),
    ("pr-state", "axolotl exporter PR #60 awaiting review"),
])
def test_cli_brief_demotes_search_and_suggest_then_recovers(
        cls, text, tmp_path, monkeypatch, capsys, fake_gh):  # noqa: F811 - imported fixture
    project = _git_repo(tmp_path / "project", branches=["main"])
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(project))
    monkeypatch.setattr(worldcheck, "BUDGET_SECONDS", 300.0)
    gh, _ = fake_gh('echo \'{"state":"MERGED"}\'')
    _enable_probes(monkeypatch, gh)
    cp = _carried_cp([text])
    store.write_checkpoint("S-live", _cp("S-live", questions=[
        {"text": "axolotl exporter reference", "trust": "inferred", "id": "o-clean"}]),
        project_dir=project)
    store.write_checkpoint("S-bad", _cp("S-bad", questions=
        cp["working_context"]["open_questions"]), project_dir=project)
    recall.search("axolotl exporter", project_dir=project)  # warm the index
    assert cli.main(["brief"]) == 0
    assert "state changed since capture" in capsys.readouterr().out
    for hits in [recall.search("axolotl exporter", project_dir=project),
                 recall.suggest("axolotl exporter", project_dir=project,
                                current_session="S-now", limit=5)]:
        assert [h["session_id"] for h in hits] == ["S-live", "S-bad"]
        assert hits[1]["invalidated_by"].startswith(cls + ":")
    if cls == "branch-state":
        ref = project / ".git" / "refs" / "heads" / "feat" / "probe"
        ref.parent.mkdir()
        ref.write_text("0" * 40 + "\n")
    else:
        fake_gh('echo \'{"state":"OPEN"}\'')
    assert cli.main(["brief"]) == 0
    capsys.readouterr()
    hits = recall.search("axolotl exporter", project_dir=project)
    recovered = next(h for h in hits if h["session_id"] == "S-bad")
    assert recovered["invalidated_by"] is None
    assert recovered["cured_by"].startswith(cls + "-ok:")


@pytest.mark.parametrize("patch", [
    {"claim_key": None}, {"claim_key": "raw/path.py"},
    {"check": "dependency-version"}, {"check": ["file-exists"]},
    {"reason": "model-says-stale"}, {"ts": "not-a-timestamp"},
])
def test_malformed_or_unapproved_evidence_is_ignored(tmp_checkpoint_dir, patch):
    _write_ledger(store.project_slug("/repo/x"), [{**_world_row("file-exists"), **patch}])
    assert store.latest_invalidation_verdicts(project_dir="/repo/x") == {}


def test_confirmation_gate_is_claim_specific(tmp_checkpoint_dir):
    _write_ledger(store.project_slug("/repo/x"), [_world_row("file-exists")])
    assert not store.append_world_cure("o-111aaa", "file-exists-ok", "b" * 64,
                                       project_dir="/repo/x")
    assert not store.append_world_cure("o-111aaa", "branch-state-ok", "a" * 64,
                                       project_dir="/repo/x")
    assert len(store.verification_rows("/repo/x")) == 1


def test_same_target_different_assertion_has_different_identity(tmp_path):
    present = worldcheck.check(_carried_cp(["branch feat/probe is unmerged"]),
                               _git_repo(tmp_path / "project"))
    absent = worldcheck.check(_carried_cp(["branch feat/probe was deleted"]),
                              tmp_path / "project")
    assert present[worldcheck.LEDGER_KEY][0][3] != absent[worldcheck.LEDGER_KEY][0][3]
