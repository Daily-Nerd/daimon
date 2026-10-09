"""`amend list` masks the quote it prints and the note it dumps (#1132 PR 11c)."""

import json

from daimon_briefing import amendments, jsonl, view
from daimon_briefing.jsonl import Health
from tests import _masking as m


def _propose(**kw):
    values = dict(item_id="o-0123456789ab", change="progressed",
                  evidence="the PR merged on friday", channel="cli-tty",
                  note="", project_dir=m.PROJECT)
    values.update(kw)
    return amendments.propose(**values)


def test_the_quote_and_the_note_are_masked_in_text_and_json(
        tmp_checkpoint_dir, capsys):
    aid = _propose(evidence=m.QUARANTINED, note=m.FORGOTTEN)
    tid = m.quarantine()
    m.forget()
    rc, out, _ = m.run(capsys, "amend", "list")
    assert rc == 0 and "SECRET" not in out and aid in out
    assert f"[withheld: quarantine {tid}]" in out
    rc, out, _ = m.run(capsys, "amend", "list", "--json")
    row = json.loads(out)[0]
    assert rc == 0 and "SECRET" not in out
    assert row["evidence"] == f"[withheld: quarantine {tid}]"
    assert row["note"] == ""


def test_a_closed_ledger_masks_the_quote_and_keeps_the_note(
        tmp_checkpoint_dir, capsys, monkeypatch):
    _propose(evidence="a quote of an item", note="a person's own words")
    real = jsonl.read
    target = tmp_checkpoint_dir / m.SLUG / "trust.jsonl"
    monkeypatch.setattr(
        jsonl, "read", lambda path, *a, **k: jsonl.Read(
            Health.TRANSIENT, [], detail="EBUSY") if path == target
        else real(path, *a, **k))
    view._judge_memo.clear()
    assert view.judge(m.SLUG).snap.closed
    rc, out, _ = m.run(capsys, "amend", "list", "--json")
    row = json.loads(out)[0]
    assert rc == 0
    assert row["evidence"] == "[withheld: trust ledger unreadable]"
    assert row["note"] == "a person's own words"


def test_a_judge_that_fails_shows_nothing(tmp_checkpoint_dir, capsys,
                                          monkeypatch):
    _propose()

    def boom(*_a, **_k):
        raise RuntimeError("no")

    monkeypatch.setattr(view, "masked", boom)
    rc, out, err = m.run(capsys, "amend", "list")
    assert rc == 2 and out == "" and err.count("\n") == 1
