"""`checks.sync` judges `refutations.jsonl` before it rebuilds the manifest
(#1132 PR 10b, D10.8): an unproven ledger yields no armed entries for this
project, and a rebuild from that would silently disarm every one of its checks.
A person's sync refuses; a forget's own re-derivation (a cure) proceeds.
"""

import errno

import pytest

from daimon_briefing import checks, checks_runtime, config, jsonl, store
from daimon_briefing.surfaces import Writer
from tests.test_checks_sync import PROJECT, _arm, _manifest


def _break_ledger(garbage=True):
    path = (config.checkpoint_dir() / store.project_slug(PROJECT)
            / "refutations.jsonl")
    if garbage:
        path.write_bytes(path.read_bytes() + b"<<<<<<< conflict\n")
    return path


def test_the_writer_keyword_is_required(tmp_checkpoint_dir):
    with pytest.raises(TypeError):
        checks.sync(PROJECT)
    with pytest.raises(TypeError):
        checks.sync_layers(PROJECT)


def test_a_human_sync_of_a_garbage_ledger_is_refused_and_disarms_nothing(
        tmp_checkpoint_dir):
    _arm()
    assert checks.sync(PROJECT, writer=Writer.HUMAN).ok
    before = (config.checks_dir() / "manifest.json").read_bytes()
    _break_ledger()
    report = checks.sync(PROJECT, writer=Writer.HUMAN)
    assert not report.ok and report.armed == 0
    assert "refutations.jsonl is unreadable" in report.reason
    assert (config.checks_dir() / "manifest.json").read_bytes() == before
    assert len(_manifest().entries) == 1


def test_a_human_sync_of_a_transient_ledger_is_refused_too(
        tmp_checkpoint_dir, monkeypatch):
    _arm()
    checks.sync(PROJECT, writer=Writer.HUMAN)
    before = (config.checks_dir() / "manifest.json").read_bytes()
    real = jsonl._read_bytes

    def flaky(path):
        if path.name == "refutations.jsonl":
            raise OSError(errno.EAGAIN, "busy")
        return real(path)
    monkeypatch.setattr(jsonl, "_read_bytes", flaky)
    monkeypatch.setattr(jsonl.time, "sleep", lambda _s: None)
    report = checks.sync(PROJECT, writer=Writer.HUMAN)
    assert not report.ok
    assert "transient" in report.reason
    assert (config.checks_dir() / "manifest.json").read_bytes() == before


def test_a_cure_syncs_whatever_the_ledger_says(tmp_checkpoint_dir):
    _arm()
    _break_ledger()
    report = checks.sync(PROJECT, writer=Writer.CURE)
    assert report.ok and report.armed == 1  # the good lines still arm it


def test_a_degraded_ledger_is_proven_so_a_human_sync_proceeds(
        tmp_checkpoint_dir):
    _arm()
    path = _break_ledger(garbage=False)
    path.write_bytes(path.read_bytes() + b'{"torn": ')
    assert checks.sync(PROJECT, writer=Writer.HUMAN).ok


def test_a_missing_ledger_proceeds(tmp_checkpoint_dir):
    assert checks.sync("/p/never-ruled", writer=Writer.HUMAN).ok


def test_sync_layers_judges_each_layer_with_the_same_writer(
        tmp_checkpoint_dir):
    assert checks.sync_layers(PROJECT, writer=Writer.HUMAN) == []


def test_the_manifest_reader_is_unchanged(tmp_checkpoint_dir):
    assert checks_runtime.load_manifest(
        config.checks_dir() / "manifest.json").entries == []
