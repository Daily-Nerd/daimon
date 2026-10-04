"""The per-install `.ledger-census` marker (#1132 PR 2b): the first
checkpoint write into a bucket runs the census once and records its counts.
The marker never costs the write it rides on."""

import json
import os

from daimon_briefing import config, ledger_census, privacy, store, surfaces

PROJECT = "/p/marker-app"
SECRET = "a distinctive row value the marker must never copy"


def _marker():
    return config.checkpoint_dir() / store.project_slug(PROJECT) / ".ledger-census"


def _write(sid="S1"):
    return store.write_checkpoint(sid, {"session_id": sid}, project_dir=PROJECT)


def _bucket():
    d = config.checkpoint_dir() / store.project_slug(PROJECT)
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_the_first_write_records_the_census_in_the_marker():
    (_bucket() / "trust.jsonl").write_bytes(
        json.dumps({"reason": SECRET}).encode() + b'\n{"cut\n')
    assert _write() is not None
    marker = json.loads(_marker().read_text(encoding="utf-8"))
    assert isinstance(marker["version"], int)
    assert isinstance(marker["ts"], str) and marker["ts"].endswith("Z")
    assert marker["ledgers"]["trust.jsonl"] == {
        "state": "degraded", "torn": 1, "split": 0, "garbage": 0,
        "tombstoned_present": 0}
    assert marker["ledgers"]["events.jsonl"]["state"] == "absent"
    assert SECRET not in _marker().read_text(encoding="utf-8")


def test_an_existing_marker_is_left_alone_and_the_census_not_rerun(monkeypatch):
    _write("S1")
    first = _marker().read_bytes()
    calls = []
    real = ledger_census.census_bucket
    monkeypatch.setattr(ledger_census, "census_bucket",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    _write("S2")
    assert calls == []
    assert _marker().read_bytes() == first


def test_a_census_failure_leaves_the_checkpoint_written(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("census exploded")

    monkeypatch.setattr(ledger_census, "census_bucket", boom)
    path = _write()
    assert path is not None and path.exists()
    assert (_bucket() / "latest.json").exists()
    assert not _marker().exists()


def test_a_marker_write_failure_leaves_the_checkpoint_written(monkeypatch):
    real = store._atomic_write

    def refuse(path, blob, **kw):
        if path.name == ".ledger-census":
            raise OSError("disk full")
        return real(path, blob, **kw)

    monkeypatch.setattr(store, "_atomic_write", refuse)
    path = _write()
    assert path is not None and path.exists()
    assert not _marker().exists()


def test_the_next_write_retries_after_a_failed_census(monkeypatch):
    real = ledger_census.census_bucket
    monkeypatch.setattr(ledger_census, "census_bucket",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    _write("S1")
    monkeypatch.setattr(ledger_census, "census_bucket", real)
    _write("S2")
    assert _marker().exists()


def test_no_marker_under_the_kill_switch_even_for_the_forget_rewrite(
        monkeypatch):
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    store.write_checkpoint("S1", {"session_id": "S1"}, project_dir=PROJECT,
                           allow_disabled=True)
    assert not _marker().exists()


def test_the_marker_scans_no_checkpoint_json(monkeypatch):
    seen = []
    real = ledger_census.census_bucket
    monkeypatch.setattr(ledger_census, "census_bucket",
                        lambda slug, **k: seen.append(k) or real(slug, **k))
    _write()
    assert seen == [{"checkpoints": False}]


def test_the_marker_is_a_declared_plaintext_free_surface():
    shape = "checkpoints/{slug}/.ledger-census"
    row = surfaces.match(shape)
    assert row is not None and row.shape == shape
    assert row.plaintext is False and row.audit_exempt is True
    assert ".ledger-census" in surfaces.exempt_names()
    assert ".ledger-census" in privacy._EXEMPT_NAMES
    assert ".ledger-census" not in surfaces.bucket_ledger_names()


def test_the_audit_does_not_call_a_marker_an_unknown_file():
    _write()
    assert _marker().exists()
    result = privacy.audit_project(project_dir=PROJECT)
    assert result["unscannable"] == []


def test_the_marker_name_is_one_constant_for_store_and_census():
    assert store._LEDGER_CENSUS_NAME == ".ledger-census"
    assert ledger_census.MARKER_NAME == store._LEDGER_CENSUS_NAME
    assert os.sep not in ledger_census.MARKER_NAME


def test_the_marker_carries_the_unavailable_forgotten_check():
    from daimon_briefing import normalize
    (_bucket() / "events.jsonl").write_bytes(
        json.dumps({"item_ref": "i-1", "ts": "2026-01-01T00:00:00Z",
                    "status": "forgotten:" + normalize.content_key(SECRET)}
                   ).encode() + b'\n{"note": "\xff"}\n')
    (_bucket() / "trust.jsonl").write_bytes(json.dumps({"reason": SECRET}).encode())
    _write()
    marker = json.loads(_marker().read_text(encoding="utf-8"))
    assert marker["forgotten_check"] == "unavailable"
    assert marker["ledgers"]["trust.jsonl"]["tombstoned_present"] is None
