"""The registry's read columns (#1132 PR 10a, D10.0).

`Surface.read` holds the posture per state for (ABSENT, DEGRADED, TRANSIENT,
UNREADABLE); `Surface.foreign_read` the same for the two shapes read across
buckets or authors. The tables below are the human copy of R2.3: a change to
a posture has to change this file too.
"""

import pytest

from daimon_briefing import surfaces, view
from daimon_briefing.jsonl import Health
from daimon_briefing.surfaces import ReadPosture as P

OPEN, NOTE, CLOSED, SKIP = (P.OPEN, P.NOTE, P.CLOSED, P.SKIP_SOURCE)

EXPECTED = {
    "trust.jsonl": (OPEN, NOTE, CLOSED, CLOSED),
    "events.jsonl": (OPEN, NOTE, NOTE, NOTE),
    "refutations.jsonl": (OPEN, NOTE, NOTE, NOTE),
    "amendments.jsonl": (OPEN, NOTE, NOTE, NOTE),
    "requests.jsonl": (OPEN, NOTE, NOTE, NOTE),
    "relations.jsonl": (OPEN, NOTE, NOTE, NOTE),
    "request_policy_tombstones.jsonl": (OPEN, NOTE, NOTE, NOTE),
    "verification.jsonl": (OPEN, OPEN, OPEN, NOTE),
    "forget-hits.jsonl": (OPEN, OPEN, OPEN, NOTE),
}


def test_the_table_covers_exactly_the_declared_bucket_ledgers():
    assert set(EXPECTED) == set(surfaces.bucket_ledger_names())


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_bucket_ledger_row_carries_its_read_postures(name):
    assert surfaces.bucket_ledger(name).read == EXPECTED[name]


def test_the_quarantine_sidecar_reads_like_a_plaintext_ledger():
    row = surfaces.match("checkpoints/{slug}/events.quarantined-lines")
    assert row is not None and row.read == (OPEN, NOTE, NOTE, NOTE)


def test_foreign_read_is_declared_where_another_bucket_or_author_is_read():
    carrying = {s.shape for s in surfaces.SURFACES if s.foreign_read}
    assert carrying == {"checkpoints/{slug}/requests.jsonl",
                        "checkpoints/{slug}/refutations.jsonl",
                        "checkpoints/{slug}/amendments.jsonl",
                        "team/{remote}/**/tombstones.jsonl",
                        "team/{remote}/**/quarantines.jsonl"}
    assert surfaces.bucket_ledger("requests.jsonl").foreign_read == (
        OPEN, NOTE, SKIP, SKIP)
    # PR 13, H7: a torn tail on either published team ledger is the newest
    # claim, so the author is skipped, not read around with a note.
    for name in ("tombstones.jsonl", "quarantines.jsonl"):
        team = surfaces.match(f"team/r/projects/a/authors/b/{name}")
        assert team.foreign_read == (OPEN, SKIP, SKIP, SKIP), name
        # Own rows of the team sidecar are a write target, never read for the
        # set.
        assert team.read == (), name


def test_a_posture_is_always_one_of_the_four_and_has_one_per_state():
    for s in surfaces.SURFACES:
        for column in (s.read, s.foreign_read):
            assert len(column) in (0, 4)
            assert all(isinstance(p, ReadPosture) for p in column)


ReadPosture = surfaces.ReadPosture


@pytest.mark.parametrize("name", sorted(EXPECTED))
@pytest.mark.parametrize("state,index", [
    (Health.ABSENT, 0), (Health.DEGRADED, 1), (Health.TRANSIENT, 2),
    (Health.UNREADABLE, 3)])
def test_view_posture_reads_the_registry(name, state, index):
    assert view.posture(name, state) is EXPECTED[name][index]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_an_ok_ledger_is_always_open(name):
    assert view.posture(name, Health.OK) is OPEN


def test_an_undeclared_ledger_name_is_a_bug_not_an_open_posture():
    with pytest.raises(LookupError):
        view.posture("nothing.jsonl", Health.OK)
