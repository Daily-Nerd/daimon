"""The clock seam: one place where a ledger row gets its order and event id.

With no clock active the wall clock and uuid4 are read at call time, exactly as
before. Under `clock.use(...)` a host test gets reproducible `order`, `ts`,
`event_id` and request ids (anamnesis golden snapshots).
"""

import asyncio
import hashlib
import json
import threading
import time
import uuid

import pytest

from daimon_briefing import (amendments, clock, config, refutations,
                             relations, requests, store, testing, trust)

SENDER = "/p/clock-sender"
RECIPIENT = "/p/clock-recipient"
ASK = "review the retrieval bar proposal before Friday"
WHY = "it blocks the release note we owe the team"
START = 1_700_000_000 * 10 ** 9


def _path(root, project_dir, name):
    return root / store.project_slug(project_dir) / name


def _session(root):
    """open + accept + a ruling, all under one StepClock, into `root`."""
    with config.checkpoint_dir_override(root), \
            clock.use(clock.StepClock(START)):
        q_id = requests.open_request(
            to=store.project_slug(RECIPIENT), ask=ASK, why=WHY,
            channel="cli-tty", project_dir=SENDER)
        requests.accept(q_id, channel="cli-tty", project_dir=RECIPIENT)
        refutations.assert_ruling(
            subject="s", verdict="v", scope="cross-project requests",
            evidence=["issue:1"], channel="cli-tty", ratified=True,
            project_dir=RECIPIENT)
    return q_id


# ---- default path is unchanged ---------------------------------------------


def test_no_clock_reads_the_wall_clock_and_uuid4_at_call_time(monkeypatch):
    monkeypatch.setattr(time, "time_ns", lambda: 42)
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=7))
    assert clock.now_ns() == 42
    assert clock.new_id() == uuid.UUID(int=7).hex


def test_a_monkeypatched_wall_clock_is_still_observed_by_a_ledger(
        monkeypatch):
    monkeypatch.setattr(time, "time_ns", lambda: START)
    row = amendments._stamp("proposed", "a-abcdef123456", "cli-tty")
    assert row["order"] == START
    assert row["ts"] == "2023-11-14T22:13:20Z"
    assert len(row["event_id"]) == 32


# ---- StepClock -------------------------------------------------------------


def test_step_clock_counts_in_steps_and_hashes_ids():
    c = clock.StepClock(START, step_ns=2 * 10 ** 9, seed="x")
    assert [c.now_ns(), c.now_ns()] == [START, START + 2 * 10 ** 9]
    assert c.new_id() == hashlib.sha256(b"x:0").hexdigest()[:32]
    assert c.new_id() == hashlib.sha256(b"x:1").hexdigest()[:32]


def test_step_clock_below_one_second_is_refused():
    with pytest.raises(ValueError):
        clock.StepClock(START, step_ns=999_999_999)


def test_step_clock_satisfies_the_protocol():
    assert isinstance(clock.StepClock(START), clock.Clock)


def test_two_fresh_runs_write_identical_ledger_bytes(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert _session(a) == _session(b)
    for project_dir, name in ((SENDER, "requests.jsonl"),
                              (RECIPIENT, "requests.jsonl"),
                              (RECIPIENT, "refutations.jsonl")):
        pa, pb = _path(a, project_dir, name), _path(b, project_dir, name)
        assert pa.exists() == pb.exists()
        if pa.exists():
            assert pa.read_bytes() == pb.read_bytes(), name
    assert _path(a, SENDER, "requests.jsonl").exists()
    assert _path(a, RECIPIENT, "refutations.jsonl").exists()


# ---- precedence ------------------------------------------------------------


def test_an_explicit_kwarg_beats_the_active_clock():
    with clock.use(clock.StepClock(START)):
        row = amendments._stamp("proposed", "a-abcdef123456", "cli-tty",
                                now_ns=START + 5, event_id="fixed-id")
    assert row["order"] == START + 5
    assert row["event_id"] == "fixed-id"


def test_relations_stamp_accepts_an_event_id():
    row = relations._stamp("proposed", "rel-0123456789abcdef", "cli-tty",
                           event_id="fixed")
    assert row["event_id"] == "fixed"


def test_each_ledger_stamp_reads_the_active_clock():
    with clock.use(clock.StepClock(START)):
        rows = [
            requests._stamp("opened", "q-abcdef123456", "cli-tty"),
            refutations._stamp("asserted", "r-abcdef123456", "cli-tty"),
            trust._stamp("quarantined", "tr-abcdef123456", "cli-tty"),
            relations._stamp("proposed", "rel-0123456789abcdef", "cli-tty"),
        ]
    assert [r["order"] for r in rows] == [START + i * 10 ** 9
                                          for i in range(4)]
    assert len({r["event_id"] for r in rows}) == 4


def test_requests_and_refutations_share_one_clock_domain(tmp_path):
    _session(tmp_path)
    request_orders = [
        json.loads(ln)["order"] for ln in
        _path(tmp_path, SENDER, "requests.jsonl").read_text().splitlines()]
    ruling_orders = [
        json.loads(ln)["order"] for ln in
        _path(tmp_path, RECIPIENT, "refutations.jsonl")
        .read_text().splitlines()]
    assert request_orders and ruling_orders
    # open_request, the accept row and the ruling were drawn from ONE
    # counter, so every ruling order is later than the open's.
    assert min(ruling_orders) > min(request_orders)
    assert len(set(request_orders) | set(ruling_orders)) == (
        len(request_orders) + len(ruling_orders))


# ---- propagation -----------------------------------------------------------


def test_the_clock_propagates_through_asyncio_to_thread():
    async def run():
        return await asyncio.to_thread(clock.now_ns)

    with clock.use(clock.StepClock(START)):
        assert asyncio.run(run()) == START


def test_the_clock_does_not_reach_a_plain_thread(monkeypatch):
    monkeypatch.setattr(time, "time_ns", lambda: 99)
    seen = []
    with clock.use(clock.StepClock(START)):
        t = threading.Thread(target=lambda: seen.append(clock.now_ns()))
        t.start()
        t.join()
    assert seen == [99]


def test_use_restores_the_previous_clock():
    with clock.use(clock.StepClock(START)):
        with clock.use(clock.StepClock(0)):
            assert clock.now_ns() == 0
        assert clock.now_ns() == START


# ---- the public test surface ------------------------------------------------


def test_testing_exports_are_pinned():
    assert sorted(testing.__all__) == ["Clock", "StepClock", "deterministic"]
    assert testing.deterministic is clock.use
    assert testing.StepClock is clock.StepClock
