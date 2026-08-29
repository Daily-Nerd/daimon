"""#827 hard requirement: the generated validator accepts today's corpus unchanged.

Fixture-free, like tests/ui/test_producer_contract.py: reads whatever real
checkpoints exist on the machine and pits the table-generated validator and
normalizers against the FROZEN REFERENCE contract (the pre-#827 predicates,
transcribed in tests/test_field_table.py — never the live serializer, which
now delegates to field_table and would make every comparison f(x) == f(x)).
Divergence from the ratified contract therefore surfaces on real producer
output — not just on shapes a test author imagined.

The store is resolved independently of DAIMON_CHECKPOINT_DIR (the root conftest
redirects that to an isolated tmp home per test, which would make this skip
forever). The `.chunk-cache/` wrapper blobs are excluded BEFORE the file limit
is applied — dot-dirs sort early, so filtering after truncation would let cache
blobs eat census slots and silently shrink the real corpus under test (the
never-truncate-a-census failure shape). Skips when there is no store (CI,
fresh machines): the value is on developer machines and in dogfooding.

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
from tests.test_field_table import (
    _reference_checkpoint_reason,
    _reference_sanitize,
)

_REAL_STORE = Path(os.path.expanduser("~")) / ".daimon" / "checkpoints"


def _checkpoints(limit=400):
    if not _REAL_STORE.is_dir():
        return []
    out = []
    for path in sorted(_REAL_STORE.rglob("*.json")):
        if len(out) >= limit:
            break
        if any(part.startswith(".")
               for part in path.relative_to(_REAL_STORE).parts):
            continue  # .chunk-cache wrappers are not checkpoints
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue  # torn or foreign file: every reader tolerates these too
        if isinstance(data, dict):
            out.append(data)
    return out


def _validated_items(checkpoint):
    """Item dicts from exactly the sections the validator walks — the free-form
    contradictions_flagged section is out of census scope by its own row's
    contract, and ONLY that section (#828 review: a global key subtraction
    would excuse unknown keys on validated items corpus-wide)."""
    for r in field_table.ENVELOPE_RULES:
        validate = dict(r.constraints).get("validate")
        if validate not in ("item-required", "item-list-required",
                            "item-list-optional"):
            continue
        section, key = r.name.split(".", 1)
        block = checkpoint.get(section)
        if not isinstance(block, dict):
            continue
        if validate == "item-required":
            item = block.get(key)
            if isinstance(item, dict):
                yield item
            continue
        entries = block.get(key)
        if isinstance(entries, list):
            for item in entries:
                if isinstance(item, dict):
                    yield item


@pytest.fixture(scope="module")
def real_checkpoints():
    cps = _checkpoints()
    if not cps:
        pytest.skip("no local checkpoint store to check the field table against")
    return cps


def test_generated_validator_agrees_with_the_frozen_contract_on_every_checkpoint(
        real_checkpoints):
    diverged = sum(
        1 for cp in real_checkpoints
        if field_table.checkpoint_reason(cp) != _reference_checkpoint_reason(cp))
    assert diverged == 0, (
        f"generated validator diverged from the frozen contract on {diverged} "
        f"of {len(real_checkpoints)} real checkpoints"
    )


def test_generated_validator_accepts_everything_the_frozen_contract_accepts(
        real_checkpoints):
    accepted = [cp for cp in real_checkpoints
                if _reference_checkpoint_reason(cp) is None]
    if not accepted:
        pytest.skip("no contract-valid checkpoints in the local store")
    rejected = sum(1 for cp in accepted
                   if field_table.checkpoint_reason(cp) is not None)
    assert rejected == 0, (
        f"generated validator rejected {rejected} of {len(accepted)} checkpoints "
        "the ratified contract accepts — the no-behavior-change bar (#827)"
    )


def test_every_envelope_key_in_the_corpus_is_in_the_table(real_checkpoints):
    known = {r.name for r in field_table.ENVELOPE_RULES if "." not in r.name}
    unknown = {k for cp in real_checkpoints for k in cp} - known
    assert not unknown, (
        f"{len(unknown)} envelope field(s) exist on real checkpoints but not in "
        f"the field table: {sorted(unknown)} — the table census is incomplete"
    )


def test_every_item_key_in_the_corpus_is_in_the_table(real_checkpoints):
    known = {r.name for r in field_table.ITEM_RULES}
    unknown = set()
    for cp in real_checkpoints:
        for item in _validated_items(cp):
            unknown |= set(item) - known
    assert not unknown, (
        f"{len(unknown)} item field(s) exist on real checkpoints but not in the "
        f"field table: {sorted(unknown)} — the table census is incomplete"
    )


def test_generated_normalizers_are_noops_exactly_where_the_contract_says(
        real_checkpoints):
    # serializer.iter_items is the WALK the live sanitizers use (it includes
    # dict-shaped contradiction entries); walking it on both sides keeps this
    # a pure engine-vs-contract comparison over every item the producer wrote.
    diverged = 0
    for cp in real_checkpoints:
        a, b = copy.deepcopy(cp), copy.deepcopy(cp)
        for item in serializer.iter_items(a):
            _reference_sanitize(item)
        for item in serializer.iter_items(b):
            field_table.normalize_field(item, "importance")
            field_table.normalize_field(item, "scene")
        if a != b:
            diverged += 1
    assert diverged == 0, (
        f"generated normalizers diverged from the frozen contract on {diverged} "
        f"of {len(real_checkpoints)} real checkpoints"
    )
