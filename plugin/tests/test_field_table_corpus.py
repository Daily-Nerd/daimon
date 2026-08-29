"""#827 hard requirement: the generated validator accepts today's corpus unchanged.

Fixture-free, like tests/ui/test_producer_contract.py: reads whatever real
checkpoints exist on the machine and pits the table-generated validator and
normalizers against the live serializer ones, so table-vs-code divergence
surfaces on real producer output — not just on shapes a test author imagined.

The store is resolved independently of DAIMON_CHECKPOINT_DIR (the root conftest
redirects that to an isolated tmp home per test, which would make this skip
forever), and the `.chunk-cache/` wrapper blobs are excluded — they are LLM-call
cache entries, not checkpoints. Skips when there is no store (CI, fresh
machines): the value is on developer machines and in dogfooding.

Failure messages carry field names and COUNTS only, never checkpoint content or
paths. Real checkpoints hold private project data, and a pytest failure line can
reach CI logs and pull request comments.
"""
import copy
import json
import os
from pathlib import Path

import pytest

from daimon_briefing import field_table, serializer

_REAL_STORE = Path(os.path.expanduser("~")) / ".daimon" / "checkpoints"


def _checkpoints(limit=400):
    if not _REAL_STORE.is_dir():
        return []
    out = []
    for path in sorted(_REAL_STORE.rglob("*.json"))[:limit]:
        if any(part.startswith(".") for part in path.relative_to(_REAL_STORE).parts):
            continue  # .chunk-cache wrappers are not checkpoints
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue  # torn or foreign file: every reader tolerates these too
        if isinstance(data, dict):
            out.append(data)
    return out


@pytest.fixture(scope="module")
def real_checkpoints():
    cps = _checkpoints()
    if not cps:
        pytest.skip("no local checkpoint store to check the field table against")
    return cps


def test_generated_validator_agrees_with_the_live_one_on_every_checkpoint(
        real_checkpoints):
    diverged = sum(
        1 for cp in real_checkpoints
        if field_table.checkpoint_reason(cp) != serializer.validation_reason(cp))
    assert diverged == 0, (
        f"generated validator diverged from the live validator on {diverged} of "
        f"{len(real_checkpoints)} real checkpoints"
    )


def test_generated_validator_accepts_everything_the_live_one_accepts(
        real_checkpoints):
    accepted = [cp for cp in real_checkpoints
                if serializer.validation_reason(cp) is None]
    if not accepted:
        pytest.skip("no live-valid checkpoints in the local store")
    rejected = sum(1 for cp in accepted
                   if field_table.checkpoint_reason(cp) is not None)
    assert rejected == 0, (
        f"generated validator rejected {rejected} of {len(accepted)} checkpoints "
        "the live validator accepts — the no-behavior-change bar (#827)"
    )


def test_every_envelope_key_in_the_corpus_is_in_the_table(real_checkpoints):
    known = {r.name for r in field_table.ENVELOPE_RULES if "." not in r.name}
    unknown = {k for cp in real_checkpoints for k in cp} - known
    assert not unknown, (
        f"{len(unknown)} envelope field(s) exist on real checkpoints but not in "
        f"the field table: {sorted(unknown)} — the table census is incomplete"
    )


def test_every_item_key_in_the_corpus_is_in_the_table(real_checkpoints):
    """Validated item sections only: contradictions_flagged is free-form by
    contract (its row says so), so its keys are not enumerable."""
    known = {r.name for r in field_table.ITEM_RULES}
    unknown = set()
    for cp in real_checkpoints:
        for item in serializer.iter_items(cp):
            unknown |= set(item) - known
    for cp in real_checkpoints:
        block = cp.get("epistemic_snapshot")
        if isinstance(block, dict):
            for entry in block.get("contradictions_flagged") or []:
                if isinstance(entry, dict):
                    unknown -= set(entry)
    assert not unknown, (
        f"{len(unknown)} item field(s) exist on real checkpoints but not in the "
        f"field table: {sorted(unknown)} — the table census is incomplete"
    )


def test_generated_normalizers_are_noops_exactly_where_the_live_ones_are(
        real_checkpoints):
    diverged = 0
    for cp in real_checkpoints:
        a, b = copy.deepcopy(cp), copy.deepcopy(cp)
        serializer.sanitize_importance(a)
        serializer.sanitize_scene(a)
        for item in serializer.iter_items(b):
            field_table.normalize_field(item, "importance")
            field_table.normalize_field(item, "scene")
        if a != b:
            diverged += 1
    assert diverged == 0, (
        f"generated normalizers diverged from the live sanitizers on {diverged} "
        f"of {len(real_checkpoints)} real checkpoints"
    )
