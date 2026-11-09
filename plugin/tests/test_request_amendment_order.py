"""#1321: requests and amendments order by the nanosecond stamp their folds
already sort rows by.

`ts` is the row's `order` rendered at second resolution, so two records touched
inside one second tie on `updated_at` / `created_at` and the id (a hash)
decided which a reader showed first and which fell past the render cap. Both
folds now keep `updated_order` / `created_order` and every recency reader sorts
on those.

Every test builds records one nanosecond apart and picks the inputs so the id
tiebreak is adversarial: the ids DESCEND with age, which means the old string
sort put the newest record where the cap cuts.
"""

import hashlib
import json

import pytest

from daimon_briefing import amendments, requests, store, trust
from daimon_briefing import pending
from tests import _masking as m

SENDER = "/p/order-1321-sender"
RECIPIENT = "/p/order-1321-recipient"
TO = store.project_slug(RECIPIENT)
# Mid-second on purpose: the whole test stays inside one rendered second,
# 2023-11-14T22:13:20Z.
SAME_SECOND = 1_700_000_000 * 10 ** 9 + 100
OPENED_TS = "2023-11-14T22:13:20Z"
ITEM = "o-" + "ab12cd" * 2
WHY = "the same why"


class NanoClock:
    """Reading k returns `start + k` nanoseconds: rows land 1 ns apart."""

    def __init__(self, start_ns=SAME_SECOND):
        self._now = start_ns - 1
        self._ids = 0

    def now_ns(self):
        self._now += 1
        return self._now

    def new_id(self):
        self._ids += 1
        return hashlib.sha256(f"nano:{self._ids}".encode()).hexdigest()[:32]


def _oldest_has_the_largest_id(make_id, count):
    """`count` labels in creation order whose ids DESCEND with age, so a sort
    on a tie that falls back to the id puts the newest record first/cut."""
    labels = sorted((f"candidate claim number {i}" for i in range(64)),
                    key=make_id, reverse=True)[:count]
    ids = [make_id(label) for label in labels]
    assert ids == sorted(ids, reverse=True)
    return labels


def _ask_labels(count):
    sender_slug = store.project_slug(SENDER)
    return _oldest_has_the_largest_id(
        lambda ask: requests.make_id(sender_slug, ask, WHY, OPENED_TS), count)


def _open_asks(count):
    return [requests.open_request(to=TO, ask=ask, why=WHY,
                                  channel="cli-agent", project_dir=SENDER)
            for ask in _ask_labels(count)]


def _row_orders(rid, event, *projects):
    return [r["order"] for project in projects
            for r in requests.events(project)
            if r["request_id"] == rid and r["event"] == event]


# ---------------------------------------------------------------- requests

def test_the_request_panel_cuts_the_oldest_when_the_cap_is_reached(
        tmp_checkpoint_dir, use_clock):
    use_clock(NanoClock())
    ids = _open_asks(requests.RENDER_CAP + 1)

    panel = requests.renderable(SENDER)

    assert [r["request_id"] for r in panel["rows"]] == ids[:0:-1]
    assert panel["overflow"] == 1


def test_the_request_panel_leads_with_the_newer_of_two(
        tmp_checkpoint_dir, use_clock):
    use_clock(NanoClock())
    older, newer = _open_asks(2)
    assert older > newer  # the id tiebreak alone would lead with the older

    panel = requests.renderable(SENDER)

    assert [r["request_id"] for r in panel["rows"]] == [newer, older]


def test_listed_puts_the_newer_request_last_within_a_state_group(
        tmp_checkpoint_dir, use_clock):
    use_clock(NanoClock())
    older, newer = _open_asks(2)

    got = requests.listed(SENDER)

    # `listed` is oldest first inside a group; the id tiebreak would have
    # put the larger id (the older request) last.
    assert [r["request_id"] for r in got.rows] == [older, newer]


def test_inbox_puts_the_newer_request_last_within_a_state_group(
        tmp_checkpoint_dir, use_clock):
    use_clock(NanoClock())
    older, newer = _open_asks(2)

    got = requests.inbox(RECIPIENT)

    assert [r["request_id"] for r in got.rows] == [older, newer]


def test_inbox_panel_cuts_the_oldest_when_the_cap_is_reached(
        tmp_checkpoint_dir, use_clock):
    use_clock(NanoClock())
    ids = _open_asks(requests.RENDER_CAP + 1)

    panel = requests.inbox_renderable(RECIPIENT)

    assert [r["request_id"] for r in panel["rows"]] == ids[:0:-1]


def test_verdict_panel_cuts_the_oldest_when_the_cap_is_reached(
        tmp_checkpoint_dir, use_clock):
    use_clock(NanoClock())
    ids = _open_asks(requests.RENDER_CAP + 1)
    for rid in ids:
        requests.accept(rid, channel="cli-tty", project_dir=RECIPIENT)

    panel = requests.verdict_renderable(SENDER)

    assert [r["request_id"] for r in panel["rows"]] == ids[:0:-1]


def test_a_new_request_record_carries_its_opening_rows_order(
        tmp_checkpoint_dir, use_clock):
    use_clock(NanoClock())
    (rid,) = _open_asks(1)
    (opened,) = _row_orders(rid, "opened", SENDER)

    record = requests.get(rid, project_dir=SENDER)

    assert record["updated_order"] == opened


def test_a_revision_moves_updated_order_to_the_revising_row(
        tmp_checkpoint_dir, use_clock):
    use_clock(NanoClock())
    (rid,) = _open_asks(1)
    requests.revise(rid, channel="cli-tty", why="a sharper why",
                    project_dir=SENDER)
    (revised,) = _row_orders(rid, "revised", SENDER)

    record = requests.get(rid, project_dir=SENDER)

    assert record["updated_order"] == revised


@pytest.mark.parametrize("act", ["accept", "reply", "done-claim"])
def test_the_recipients_acts_move_updated_order(
        tmp_checkpoint_dir, use_clock, act):
    """Each of these lands on a different line of the fold; all of them
    stamp the record with the row they were folded from."""
    use_clock(NanoClock())
    (rid,) = _open_asks(1)
    requests.accept(rid, channel="cli-tty", project_dir=RECIPIENT)
    if act == "reply":
        requests.reply(rid, "halfway", channel="cli-tty",
                       project_dir=RECIPIENT)
    elif act == "done-claim":
        requests.done(rid, channel="cli-agent", evidence="shipped",
                      project_dir=RECIPIENT)
    last = {"accept": "accepted", "reply": "replied",
            "done-claim": "done"}[act]
    (landed,) = _row_orders(rid, last, SENDER, RECIPIENT)

    record = requests.sent(SENDER).by_id[rid]

    assert record["updated_order"] == landed


def test_request_rows_without_an_order_fold_as_zero(tmp_checkpoint_dir,
                                                    use_clock):
    use_clock(NanoClock())
    (rid,) = _open_asks(1)
    requests.revise(rid, channel="cli-tty", why="a sharper why",
                    project_dir=SENDER)
    rows = []
    for row in requests.events(SENDER):
        row = dict(row)
        del row["order"]
        rows.append(row)

    record = requests.fold(rows)[rid]

    assert record["updated_order"] == 0


# -------------------------------------------------------------- amendments

def _claims(count):
    return _oldest_has_the_largest_id(
        lambda quote: amendments.make_id(ITEM, "progressed", quote), count)


def _propose_all(count):
    return [amendments.propose(item_id=ITEM, change="progressed",
                               evidence=quote, channel="cli-tty",
                               project_dir=SENDER)
            for quote in _claims(count)]


def test_the_amendment_cap_cuts_the_oldest_one(tmp_checkpoint_dir,
                                               use_clock):
    use_clock(NanoClock())
    ids = _propose_all(amendments.RENDER_CAP + 1)

    group = amendments.renderable(SENDER)[ITEM]

    # Shown oldest-first, so the kept three are the newest three in order.
    assert [r["amendment_id"] for r in group["rows"]] == ids[1:]
    assert group["overflow"] == 1


def test_an_amendment_record_carries_the_order_of_its_first_row(
        tmp_checkpoint_dir, use_clock):
    use_clock(NanoClock())
    (a_id,) = _propose_all(1)
    amendments.reject(a_id, channel="cli-tty", project_dir=SENDER)
    amendments.propose(item_id=ITEM, change="progressed",
                       evidence=_claims(1)[0], channel="cli-tty",
                       project_dir=SENDER)  # reopens the rejected record
    proposals = [r["order"] for r in amendments.events(SENDER)
                 if r["amendment_id"] == a_id and r["event"] == "proposed"]
    assert len(proposals) == 2

    record = amendments.get(a_id, project_dir=SENDER)

    # A reopened proposal keeps the creation stamp of the first one.
    assert record["created_order"] == proposals[0]
    assert record["updated_order"] == proposals[1]


def test_amendment_rows_without_an_order_fold_as_zero(tmp_checkpoint_dir,
                                                      use_clock):
    use_clock(NanoClock())
    (a_id,) = _propose_all(1)
    rows = []
    for row in amendments.events(SENDER):
        row = dict(row)
        del row["order"]
        rows.append(row)

    record = amendments.fold(rows)[a_id]

    assert record["created_order"] == 0
    assert record["updated_order"] == 0


def test_the_current_state_of_an_item_is_its_newest_ratified_change(
        tmp_checkpoint_dir, use_clock):
    """`pending._current_state` picks the newest ratified amendment; two in
    one second must not be told apart by id."""
    use_clock(NanoClock())
    quote = "the same claim"
    # Folded in proposal order, so the record dict lists `first` ahead of
    # `second`; the one ratified LAST is `first`, and it is the newest.
    first_change, second_change = "progressed", "blocked"
    first = amendments.propose(item_id=ITEM, change=first_change,
                               evidence=quote, channel="cli-agent",
                               project_dir=SENDER)
    second = amendments.propose(item_id=ITEM, change=second_change,
                                evidence=quote, channel="cli-agent",
                                project_dir=SENDER)
    amendments.ratify(second, channel="cli-tty", project_dir=SENDER)
    amendments.ratify(first, channel="cli-tty", project_dir=SENDER)

    state = pending._current_state(
        amendments.records(SENDER), ITEM, exclude_id="a-000000000000")

    assert state == first_change


def test_amend_list_puts_the_newer_amendment_last(tmp_checkpoint_dir,
                                                  use_clock, capsys):
    use_clock(NanoClock())
    ids = [amendments.propose(item_id=ITEM, change="progressed",
                              evidence=quote, channel="cli-tty",
                              project_dir=m.PROJECT)
           for quote in _claims(2)]

    rc, out, err = m.run(capsys, "amend", "list", "--json")

    assert rc == 0, err
    assert [r["amendment_id"] for r in json.loads(out)] == ids


# ------------------------------------------------------------------- trust

def test_trust_list_puts_the_newer_quarantine_last(tmp_checkpoint_dir,
                                                   use_clock, capsys):
    use_clock(NanoClock())
    kind = "decision"
    texts = _oldest_has_the_largest_id(
        lambda text: trust.make_id(kind, m.SLUG, trust.value_key(text)), 2)
    ids = [str(trust.propose(text=text, kind=kind, reason="r",
                             evidence=["issue:1321"], channel="cli-tty",
                             project_dir=m.PROJECT))
           for text in texts]
    assert ids[0] > ids[1]  # the id tiebreak alone would list the older last

    rc, out, err = m.run(capsys, "trust", "list", "--json")

    assert rc == 0, err
    assert [r["quarantine_id"] for r in json.loads(out)] == ids


def test_trust_records_carry_the_order_of_their_last_row(tmp_checkpoint_dir,
                                                         use_clock):
    use_clock(NanoClock())
    tid = str(trust.propose(text="a quarantined value long enough", kind="decision", reason="r",
                            evidence=["issue:1321"], channel="cli-agent",
                            project_dir=SENDER))
    trust.confirm(tid, channel="cli-tty", project_dir=SENDER)
    last = [r["order"] for r in trust.events(SENDER)
            if r["event"] == "confirmed"]

    record = trust.get(tid, project_dir=SENDER)

    assert record["updated_order"] == last[0]


def test_trust_rows_without_an_order_fold_as_zero(tmp_checkpoint_dir,
                                                  use_clock):
    use_clock(NanoClock())
    tid = str(trust.propose(text="a quarantined value long enough", kind="decision", reason="r",
                            evidence=["issue:1321"], channel="cli-tty",
                            project_dir=SENDER))
    rows = []
    for row in trust.events(SENDER):
        row = dict(row)
        del row["order"]
        rows.append(row)

    assert trust.fold(rows)[tid]["updated_order"] == 0
