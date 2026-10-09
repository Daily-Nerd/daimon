"""The queue's lanes hand out masked text, and the briefing's count asks for
none (#1132 PR 11c)."""

import json

import pytest

from daimon_briefing import (amendments, api, pending, refutations, requests,
                             view)
from tests import _masking as m

OTHER = "/p/pending-other-bucket"
OTHER_SLUG = m.store.project_slug(OTHER)


def _rows(project=m.PROJECT, **kw):
    return pending.queue(project_dir=project, **kw)["rows"]


def _plant():
    """One candidate in every lane, each carrying a quarantined whole value."""
    requests.open_request(to=m.SLUG, ask=m.QUARANTINED, why=m.FORGOTTEN,
                          channel="cli-agent", project_dir=OTHER)
    refutations.assert_ruling(
        subject="s", verdict=m.QUARANTINED, scope="sc", evidence=["issue:1"],
        channel="cli-agent", project_dir=m.PROJECT)
    refutations.assert_refutation(
        subject=m.QUARANTINED, verdict="v", scope="sc2",
        evidence=["issue:1"], channel="cli-agent", project_dir=m.PROJECT)
    m.quarantine(m.QUARANTINED)
    from daimon_briefing import trust
    trust.propose(text="a different claim that is long enough", kind="decision",
                  reason=m.QUARANTINED, evidence=["issue:2"],
                  channel="cli-agent", project_dir=m.PROJECT)


def test_every_lane_hands_out_masked_headlines(tmp_checkpoint_dir):
    _plant()
    m.forget()
    rows = _rows()
    assert {r["kind"] for r in rows} >= {"request", "ruling", "refutation",
                                         "trust"}
    blob = json.dumps(rows)
    assert "SECRET" not in blob
    assert "withheld: quarantine" in blob


def test_api_queue_is_the_masked_queue(tmp_checkpoint_dir):
    _plant()
    assert api.queue is pending.queue
    assert "SECRET" not in json.dumps(api.queue(project_dir=m.PROJECT))


def test_the_amendment_lane_masks_its_quote(tmp_checkpoint_dir):
    aid = amendments.propose(item_id="o-0123456789ab", change="progressed",
                             evidence=m.QUARANTINED, channel="cli-agent",
                             project_dir=m.PROJECT)
    amendments.verify(aid, role="assistant", project_dir=m.PROJECT)
    m.quarantine(m.QUARANTINED)
    rows = [r for r in _rows() if r["kind"] == "amendment"]
    assert rows and "SECRET" not in json.dumps(rows)
    assert rows[0]["amend"]["evidence"].startswith("[withheld: quarantine")


def test_a_row_is_masked_by_the_bucket_it_was_read_from(tmp_checkpoint_dir):
    """A foreign bucket's own queue is judged by that bucket's quarantines, and
    this bucket's quarantine does not reach into it."""
    from daimon_briefing import trust
    refutations.assert_ruling(
        subject="s", verdict=m.QUARANTINED, scope="sc", evidence=["issue:1"],
        channel="cli-agent", project_dir=OTHER)
    assert "SECRET-Q" in json.dumps(_rows(OTHER))        # nothing quarantined
    trust.propose(text=m.QUARANTINED, kind="decision", reason="r",
                  evidence=["issue:1"], channel="cli-tty", project_dir=OTHER)
    assert "SECRET" not in json.dumps(_rows(OTHER))


def test_the_briefing_count_asks_for_no_text_and_no_judge(
        tmp_checkpoint_dir, monkeypatch):
    _plant()

    def boom(*_a, **_k):
        raise RuntimeError("no")

    monkeypatch.setattr(view, "masked", boom)
    monkeypatch.setattr(view, "judge", boom)
    assert len(pending.queue(project_dir=m.PROJECT, masked=False)["rows"]) >= 3


def test_a_judge_that_fails_fails_the_call_not_a_lane(tmp_checkpoint_dir,
                                                      monkeypatch):
    _plant()

    def boom(*_a, **_k):
        raise RuntimeError("no")

    monkeypatch.setattr(view, "judge", boom)
    with pytest.raises(RuntimeError):
        pending.queue(project_dir=m.PROJECT)


def test_a_masker_that_fails_drops_the_lane_not_the_text(tmp_checkpoint_dir,
                                                         monkeypatch):
    _plant()

    def boom(*_a, **_k):
        raise RuntimeError("no")

    monkeypatch.setattr(view, "masked", boom)
    assert _rows() == []          # fail-open per lane: less is shown, never more


def test_decide_prints_masked_cards_and_foreign_ones_by_their_own_bucket(
        tmp_checkpoint_dir, capsys):
    _plant()
    for flags in ((), ("--all-projects",)):
        rc, out, _ = m.run(capsys, "decide", *flags)
        assert rc == 0 and "SECRET" not in out, flags
        assert "withheld: quarantine" in out


def test_queue_typed_carries_masked_rows_and_the_notes(tmp_checkpoint_dir):
    _plant()
    typed = pending.queue_typed(project_dir=m.PROJECT)
    assert typed.rows and "SECRET" not in json.dumps(typed.rows)
    assert isinstance(typed.notes, tuple)
