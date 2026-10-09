"""The refute verbs print what they read through `view.masked`, text and JSON
(#1132 PR 11c)."""

import json

from daimon_briefing import refutations
from tests import _masking as m


def _record(**kw):
    values = dict(subject="a plain subject", verdict=m.VISIBLE, scope="census",
                  evidence=["measurement:replay"], channel="cli-agent",
                  project_dir=m.PROJECT)
    values.update(kw)
    return refutations.assert_refutation(**values)


def test_show_masks_a_quarantined_verdict_in_text_and_json(
        tmp_checkpoint_dir, capsys):
    rid = _record(verdict=m.QUARANTINED)
    tid = m.quarantine()
    rc, out, _ = m.run(capsys, "refute", "show", rid)
    assert rc == 0 and "SECRET-Q" not in out
    assert f"[withheld: quarantine {tid}]" in out
    rc, out, _ = m.run(capsys, "refute", "show", rid, "--json")
    assert rc == 0 and "SECRET-Q" not in out
    assert json.loads(out)["verdict"] == f"[withheld: quarantine {tid}]"


def test_a_forgotten_value_is_blank_not_marked(tmp_checkpoint_dir, capsys):
    rid = _record(scope=m.FORGOTTEN)
    m.forget()
    rc, out, _ = m.run(capsys, "refute", "show", rid, "--json")
    assert rc == 0 and "SECRET-F" not in out
    assert json.loads(out)["scope"] == ""
    assert "forgotten" not in out


def test_list_and_search_mask_every_row_once(tmp_checkpoint_dir, capsys):
    rid = _record(subject=m.QUARANTINED, scope="one")
    _record(subject="another subject", scope="two", verdict=m.FORGOTTEN)
    m.quarantine()
    m.forget()
    for verb in (("list",), ("search", "subject")):
        for flags in ((), ("--json",)):
            rc, out, _ = m.run(capsys, "refute", *verb, *flags)
            assert rc == 0 and "SECRET" not in out, (verb, flags)
    rc, out, _ = m.run(capsys, "refute", "list", "--json")
    rows = {r["refutation_id"]: r for r in json.loads(out)}
    assert rows[rid]["subject"].startswith("[withheld: quarantine")


def test_guard_masks_the_anchors_it_matched(tmp_checkpoint_dir, capsys):
    rid = _record(anchors=[m.QUARANTINED.casefold()])
    refutations.ratify(rid, channel="cli-tty", project_dir=m.PROJECT)
    m.quarantine()
    rc, out, _ = m.run(capsys, "refute", "guard", "zzz", "--anchor",
                       m.QUARANTINED, "--json")
    assert rc == 0 and "secret-q" not in out.lower()
    row = json.loads(out)[0]
    assert row["guard_match"]["anchors"] == [
        row["guard_match"]["anchors"][0]]
    assert row["guard_match"]["anchors"][0].startswith("[withheld")


def test_ratify_refuses_a_record_with_withheld_text_and_writes_nothing(
        tmp_checkpoint_dir, capsys, monkeypatch):
    rid = _record(verdict=m.QUARANTINED)
    tid = m.quarantine()
    m.human(monkeypatch)
    before = refutations.get(rid, project_dir=m.PROJECT)
    rc, out, err = m.run(capsys, "refute", "ratify", rid)
    assert rc == 2 and "SECRET-Q" not in out + err
    assert "error: refute ratify refused: this record has withheld text; " \
        "nothing was written" in err
    assert f"note: daimon trust show {tid} on a terminal" in err
    assert refutations.get(rid, project_dir=m.PROJECT) == before


def test_ratify_of_a_forgotten_text_refuses_without_a_note(
        tmp_checkpoint_dir, capsys, monkeypatch):
    rid = _record(verdict=m.FORGOTTEN)
    m.forget()
    m.human(monkeypatch)
    rc, out, err = m.run(capsys, "refute", "ratify", rid)
    assert rc == 2 and "trust show" not in err
    assert "this record has withheld text" in err


def test_a_clean_record_still_ratifies_and_echoes(tmp_checkpoint_dir, capsys,
                                                  monkeypatch):
    rid = _record()
    m.quarantine()                      # a quarantine elsewhere changes nothing
    m.human(monkeypatch)
    rc, out, _ = m.run(capsys, "refute", "ratify", rid)
    assert rc == 0 and m.VISIBLE in out
    assert refutations.get(rid, project_dir=m.PROJECT)["state"] == "active"


def test_revise_and_overturn_refuse_a_withheld_record_too(
        tmp_checkpoint_dir, capsys, monkeypatch):
    rid = _record(subject=m.QUARANTINED)
    m.quarantine()
    m.human(monkeypatch)
    rc, _, err = m.run(capsys, "refute", "revise", rid, "--verdict", "new",
                       "--evidence", "measurement:x")
    assert rc == 2 and "refute revise refused" in err
    rc, _, err = m.run(capsys, "refute", "overturn", rid, "--evidence",
                       "measurement:x")
    assert rc == 2 and "refute overturn refused" in err
    assert refutations.get(rid, project_dir=m.PROJECT)["verdict"] == m.VISIBLE


def test_a_closed_trust_ledger_keeps_a_refutation_readable(
        tmp_checkpoint_dir, capsys, monkeypatch):
    """7a policy: a person's refutation stays readable when the trust ledger
    cannot be read; the verb does not refuse on a closed snapshot alone."""
    from daimon_briefing import jsonl, view
    from daimon_briefing.jsonl import Health
    rid = _record()
    real = jsonl.read
    target = tmp_checkpoint_dir / m.SLUG / "trust.jsonl"
    monkeypatch.setattr(
        jsonl, "read", lambda path, *a, **k: jsonl.Read(
            Health.TRANSIENT, [], detail="EBUSY") if path == target
        else real(path, *a, **k))
    view._judge_memo.clear()
    assert view.judge(m.SLUG).snap.closed       # the seam really closed it
    rc, out, _ = m.run(capsys, "refute", "show", rid)
    assert rc == 0 and m.VISIBLE in out


def test_a_judge_that_fails_shows_nothing(tmp_checkpoint_dir, capsys,
                                          monkeypatch):
    from daimon_briefing import view
    rid = _record()

    def boom(*_a, **_k):
        raise RuntimeError("no")

    monkeypatch.setattr(view, "masked", boom)
    rc, out, err = m.run(capsys, "refute", "show", rid)
    assert rc == 2 and out == "" and m.VISIBLE not in err
    assert err.count("\n") == 1


def test_the_viewer_route_masks_both_lanes(tmp_checkpoint_dir):
    from daimon_ui import server
    _record(verdict=m.QUARANTINED)
    refutations.assert_ruling(
        subject="s", verdict=m.QUARANTINED, scope="sc", evidence=["issue:1"],
        channel="cli-tty", project_dir=m.PROJECT)
    m.quarantine()
    payload = server._refutations_payload(m.SLUG)
    assert payload["ok"] and payload["rows"] and payload["rulings"]
    assert "SECRET" not in json.dumps(payload)
    assert payload["rows"][0]["verdict"].startswith("[withheld: quarantine")
    assert payload["rulings"][0]["verdict"].startswith("[withheld: quarantine")
