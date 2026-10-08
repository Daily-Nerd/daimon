"""The registry's write column (#1132 PR 10b, D10.0).

`Surface.write` holds, per writer class, the posture for (DEGRADED,
TRANSIENT, UNREADABLE). ABSENT and OK always PROCEED, and CURE is PROCEED on
every row by construction, so neither is a column. The tables below are the
human copy of R2.3: a change to a posture has to change this file too.
"""

import pytest

from daimon_briefing import surfaces
from daimon_briefing.jsonl import Health
from daimon_briefing.surfaces import WritePosture as W
from daimon_briefing.surfaces import Writer as WR

PROCEED, REFUSE, SKIP = W.PROCEED, W.REFUSE, W.SKIP
HUMAN, ADMISSION, EMITTER, CURE = (WR.HUMAN, WR.ADMISSION, WR.EMITTER,
                                   WR.CURE)

_H = (PROCEED, REFUSE, REFUSE)       # a human or an admission write
_E = (PROCEED, SKIP, SKIP)           # an emitter write

EXPECTED = {
    "trust.jsonl": {HUMAN: _H},
    "events.jsonl": {HUMAN: _H, ADMISSION: _H, EMITTER: _E},
    "refutations.jsonl": {HUMAN: _H},
    "amendments.jsonl": {HUMAN: _H, EMITTER: _E},
    "requests.jsonl": {HUMAN: _H, EMITTER: _E},
    "relations.jsonl": {HUMAN: _H},
    "request_policy_tombstones.jsonl": {HUMAN: _H},
    "verification.jsonl": {EMITTER: _E},
    "forget-hits.jsonl": {EMITTER: _E},
}


def test_the_table_covers_exactly_the_declared_bucket_ledgers():
    assert set(EXPECTED) == set(surfaces.bucket_ledger_names())


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_bucket_ledger_row_carries_its_write_postures(name):
    assert dict(surfaces.bucket_ledger(name).write) == EXPECTED[name]


def test_the_quarantine_sidecar_is_a_human_write_target():
    row = surfaces.match("checkpoints/{slug}/events.quarantined-lines")
    assert dict(row.write) == {HUMAN: _H}


def test_the_own_team_tombstones_proceed_in_every_state():
    row = surfaces.match("team/r/projects/a/authors/b/tombstones.jsonl")
    assert dict(row.write) == {HUMAN: (PROCEED, PROCEED, PROCEED)}


def test_the_recall_delivery_log_is_an_emitter_write():
    row = surfaces.match("logs/recall-delivery.jsonl")
    assert dict(row.write) == {EMITTER: _E}


def test_no_other_row_declares_a_write_posture():
    carrying = {s.shape for s in surfaces.SURFACES if s.write}
    assert carrying == {
        "checkpoints/{slug}/events.jsonl",
        "checkpoints/{slug}/refutations.jsonl",
        "checkpoints/{slug}/amendments.jsonl",
        "checkpoints/{slug}/requests.jsonl",
        "checkpoints/{slug}/verification.jsonl",
        "checkpoints/{slug}/forget-hits.jsonl",
        "checkpoints/{slug}/*.quarantined-lines",
        "checkpoints/{slug}/relations.jsonl",
        "checkpoints/{slug}/trust.jsonl",
        "checkpoints/{slug}/request_policy_tombstones.jsonl",
        "team/{remote}/**/tombstones.jsonl",
        "logs/recall-delivery.jsonl",
    }


def test_cure_is_never_a_column_and_never_declared():
    for s in surfaces.SURFACES:
        assert CURE not in dict(s.write)


def test_a_column_has_one_posture_per_state_and_one_entry_per_writer():
    for s in surfaces.SURFACES:
        writers = [w for w, _ in s.write]
        assert len(writers) == len(set(writers))
        for _w, column in s.write:
            assert len(column) == 3
            assert all(isinstance(p, W) for p in column)


@pytest.mark.parametrize("name", sorted(EXPECTED))
@pytest.mark.parametrize("writer", list(WR))
@pytest.mark.parametrize("state,index", [
    (Health.DEGRADED, 0), (Health.TRANSIENT, 1), (Health.UNREADABLE, 2)])
def test_write_posture_reads_the_registry(name, writer, state, index):
    row = surfaces.bucket_ledger(name)
    if writer is CURE:
        assert surfaces.write_posture(row, writer, state.value) is PROCEED
    elif writer in EXPECTED[name]:
        assert (surfaces.write_posture(row, writer, state.value)
                is EXPECTED[name][writer][index])
    else:
        with pytest.raises(LookupError):
            surfaces.write_posture(row, writer, state.value)


@pytest.mark.parametrize("name", sorted(EXPECTED))
@pytest.mark.parametrize("writer", list(WR))
@pytest.mark.parametrize("state", [Health.OK, Health.ABSENT])
def test_an_ok_or_absent_ledger_always_proceeds(name, writer, state):
    row = surfaces.bucket_ledger(name)
    assert surfaces.write_posture(row, writer, state.value) is PROCEED
