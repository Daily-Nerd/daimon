"""#1319: rulings order by the nanosecond stamp the fold already sorts by.

`ts` is the `order` rendered at second resolution, so two rulings activated
inside one second tie on it and the id (a hash of subject and scope) decided
which the reader saw first. The fold keeps `activated_order` / `updated_order`
and every recency reader sorts on those.
"""

import hashlib

from daimon_briefing import briefing, refutations

PROJECT = "/p/ruling-order"
# Mid-second on purpose: the whole test stays inside one rendered second.
SAME_SECOND = 1_700_000_000 * 10 ** 9 + 100


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


def _rule(subject, verdict="a standing verdict"):
    return refutations.assert_ruling(
        subject=subject, verdict=verdict, scope="tests",
        evidence=["issue:1319"], channel="cli-tty", ratified=True,
        project_dir=PROJECT)


def _older_then_newer():
    """Two subjects, the older one chosen to hold the LARGER id, so the id
    tiebreak alone would put the older ruling first."""
    first, second = "alpha ruling subject", "omega ruling subject"
    id_first = refutations.make_id(first, "tests")
    id_second = refutations.make_id(second, "tests")
    return (first, second) if id_first > id_second else (second, first)


def _rows(ref_id, *events):
    return [r for r in refutations.read_events(PROJECT).rows
            if r.get("refutation_id") == ref_id
            and (not events or r.get("event") in events)]


def test_rulings_read_puts_the_newer_ruling_first_inside_one_second(
        tmp_checkpoint_dir, use_clock):
    use_clock(NanoClock())
    older_subject, newer_subject = _older_then_newer()
    older = _rule(older_subject)
    newer = _rule(newer_subject)
    assert older > newer  # the id tiebreak alone would lead with the older

    got = briefing.rulings_read(PROJECT)

    assert [r["refutation_id"] for r in got.rows] == [newer, older]


def test_listing_puts_the_newer_active_record_first_inside_one_second(
        tmp_checkpoint_dir, use_clock):
    use_clock(NanoClock())
    older_subject, newer_subject = _older_then_newer()
    older = _rule(older_subject)
    newer = _rule(newer_subject)

    listed = refutations.listing(polarity="ruling", project_dir=PROJECT)

    # `listing` is oldest-update first among the active; the id tiebreak
    # would have put the larger id (the older ruling) last.
    assert [r["refutation_id"] for r in listed] == [older, newer]


def test_fold_carries_the_activating_rows_order(tmp_checkpoint_dir,
                                                use_clock):
    use_clock(NanoClock())
    ref_id = _rule("founded active")
    (founding,) = _rows(ref_id, "ruled")

    record = refutations.get(ref_id, project_dir=PROJECT)

    assert record["activated_order"] == founding["order"]
    assert record["updated_order"] == founding["order"]


def test_activated_order_moves_to_the_ratifying_row(tmp_checkpoint_dir,
                                                    use_clock):
    use_clock(NanoClock())
    ref_id = refutations.assert_ruling(
        subject="proposed by an agent", verdict="agent text", scope="tests",
        evidence=["issue:1319"], channel="cli-agent", ratified=False,
        project_dir=PROJECT)
    candidate = refutations.get(ref_id, project_dir=PROJECT)
    assert candidate["state"] == "candidate"
    assert candidate["activated_order"] is None

    refutations.ratify(ref_id, channel="cli-tty", project_dir=PROJECT)
    (ratified,) = _rows(ref_id, "ratified")

    record = refutations.get(ref_id, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record["activated_order"] == ratified["order"]
    assert record["updated_order"] == ratified["order"]


def test_a_retired_ruling_keeps_the_stamp_of_the_activation_it_ended(
        tmp_checkpoint_dir, use_clock):
    use_clock(NanoClock())
    ref_id = _rule("retired after being active")
    (founding,) = _rows(ref_id, "ruled")
    refutations.retire(ref_id, channel="cli-tty", evidence=["issue:1319"],
                       project_dir=PROJECT)

    record = refutations.get(ref_id, project_dir=PROJECT)

    # Mirrors `activated_at`, which a retire also leaves in place. A retired
    # ruling cannot be revived, so there is no later activation to move to.
    assert record["state"] != "active"
    assert record["activated_order"] == founding["order"]
    assert record["updated_order"] > founding["order"]


def test_activated_order_moves_to_a_revision_that_activates(
        tmp_checkpoint_dir, use_clock):
    # Rulings activate only through ratify; a plain refutation also
    # activates through a human revision, the fourth stamping site.
    use_clock(NanoClock())
    ref_id = refutations.assert_refutation(
        subject="proposed then revised active", verdict="agent text",
        scope="tests", evidence=["issue:1319"], channel="cli-agent",
        project_dir=PROJECT)
    (proposed,) = _rows(ref_id)

    refutations.revise(ref_id, channel="cli-tty", evidence=["issue:1319"],
                       verdict="human text", ratified=True,
                       project_dir=PROJECT)
    (revised,) = _rows(ref_id, "revised")

    record = refutations.get(ref_id, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record["activated_order"] == revised["order"]
    assert record["activated_order"] > proposed["order"]


def test_a_row_without_order_folds_as_zero(tmp_checkpoint_dir):
    row = refutations._stamp("ruled", refutations.make_id("old", "tests"),
                             "cli-tty", now_ns=SAME_SECOND)
    row.update({"subject": "old", "verdict": "pre-order row",
                "scope": "tests", "anchors": [], "revisit_when": "",
                "evidence": [], "ratified": True})
    del row["order"]

    record = refutations.fold([row])[row["refutation_id"]]

    assert record["activated_order"] == 0
    assert record["updated_order"] == 0


def test_search_ranks_the_newer_of_two_equal_hits_first_inside_one_second(
        tmp_checkpoint_dir, use_clock):
    from daimon_briefing import refutation_guard

    use_clock(NanoClock())
    older = _rule("deploy rule one", verdict="never deploy on a friday")
    newer = _rule("deploy rule two", verdict="never deploy on a friday")

    hits = refutation_guard.search("deploy", project_dir=PROJECT)

    assert [h["refutation_id"] for h in hits] == [newer, older]
