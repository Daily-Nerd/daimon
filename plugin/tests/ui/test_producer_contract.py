"""Fixture-free contract: the reader must not null a field the producer wrote.

Every other test in this directory builds its own checkpoint, which means it can
only ever assert the shape its author IMAGINED. Two live bugs got in that way and
were then pinned green: `importance` was normalized against a 1-5 bound while the
producer scores 1-10 (serializer.py:151, :678), and `quote_provenance.verifier` was
normalized as a string while the producer writes an object (provenance.py:188). A
wrong guess resolves to None, and None is indistinguishable from honest absence, so
nothing raised.

This test takes no fixtures. It reads whatever real checkpoints exist on the machine
and asserts the reader preserves what the producer actually wrote, so a future
divergence is a test failure instead of a shipped lie.

It skips when there is no store (CI, a fresh machine). That is deliberate: the value
is on developer machines and in dogfooding, where real producer output lives.

Failure messages carry field names and COUNTS only, never checkpoint content or
paths. Real checkpoints hold private project data, and a pytest failure line can
reach CI logs and pull request comments.
"""
import json
import os
from pathlib import Path

import pytest

from daimon_ui import reader

# Resolved independently of DAIMON_CHECKPOINT_DIR: the root conftest redirects that
# to an isolated tmp home for every test, which would make this one skip forever.
_REAL_STORE = Path(os.path.expanduser("~")) / ".daimon" / "checkpoints"


def _checkpoints(limit=400):
    if not _REAL_STORE.is_dir():
        return []
    out = []
    for path in sorted(_REAL_STORE.rglob("*.json"))[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue  # torn or foreign file: the reader tolerates these too
        if isinstance(data, dict):
            out.append(data)
    return out


def _raw_items(checkpoint):
    """Every raw item dict the producer wrote, from both blocks."""
    for block in (checkpoint.get("working_context"), checkpoint.get("epistemic_snapshot")):
        if not isinstance(block, dict):
            continue
        for value in block.values():
            if isinstance(value, dict):
                yield value
            elif isinstance(value, list):
                for entry in value:
                    if isinstance(entry, dict):
                        yield entry


@pytest.fixture(scope="module")
def real_items():
    items = [item for cp in _checkpoints() for item in _raw_items(cp)]
    if not items:
        pytest.skip("no local checkpoint store to check the producer contract against")
    return items


def test_reader_preserves_every_importance_the_producer_wrote(real_items):
    written = [i for i in real_items
               if isinstance(i.get("importance"), int)
               and not isinstance(i.get("importance"), bool)]
    if not written:
        pytest.skip("no rated items in the local store")
    dropped = sum(1 for i in written if reader._norm_item(i)["importance"] is None)
    assert dropped == 0, (
        f"reader nulled 'importance' on {dropped} of {len(written)} items the "
        "producer rated; the reader's accepted range disagrees with the producer's"
    )


def test_reader_preserves_every_quote_verifier_the_producer_wrote(real_items):
    written = [i for i in real_items
               if isinstance(i.get("quote_provenance"), dict)
               and i["quote_provenance"].get("verifier")]
    if not written:
        pytest.skip("no provenanced items in the local store")
    dropped = sum(1 for i in written
                  if (reader._norm_item(i)["quote_provenance"] or {}).get("verifier") is None)
    assert dropped == 0, (
        f"reader nulled 'quote_provenance.verifier' on {dropped} of {len(written)} "
        "items that carry one; the reader's expected shape disagrees with the producer's"
    )


def test_reader_never_invents_an_item_from_a_producer_item(real_items):
    """Weaker but broader: a producer item must normalize to something, never None.
    Catches a whole-item rejection the two field checks above would miss."""
    dropped = sum(1 for i in real_items if reader._norm_item(i) is None)
    assert dropped == 0, (
        f"reader rejected {dropped} of {len(real_items)} items the producer wrote"
    )
