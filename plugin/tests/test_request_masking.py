"""The request verbs print what they read through `view.masked`, across the
join between a sender's bucket and a recipient's (#1132 PR 11c)."""

import json

from daimon_briefing import cli, requests, store, view
from tests import _masking as m

PEER = "/p/" + m.QUARANTINED          # a sender whose directory name is a value
OTHER_PEER = "/p/another-sender"
OTHER_SLUG = store.project_slug(OTHER_PEER)


def _ask(sender=PEER, to=m.SLUG, **kw):
    values = dict(to=to, ask="a plain ask", why="a plain why",
                  channel="cli-agent", project_dir=sender)
    values.update(kw)
    return requests.open_request(**values)


def _json(capsys, *argv):
    rc, out, err = m.run(capsys, *argv)
    assert rc == 0, err
    return json.loads(out)


def test_an_inbox_ask_and_its_sender_label_are_masked(tmp_checkpoint_dir,
                                                       capsys):
    rid = _ask(ask=m.QUARANTINED, why=m.FORGOTTEN)
    tid = m.quarantine()
    m.forget()
    rc, out, _ = m.run(capsys, "request", "inbox")
    assert rc == 0 and "SECRET" not in out
    rows = _json(capsys, "request", "inbox", "--json")
    row = next(r for r in rows if r["request_id"] == rid)
    assert row["ask"] == f"[withheld: quarantine {tid}]"
    assert row["why"] == ""                         # forgotten: blank
    assert row["from_label"] == f"[withheld: quarantine {tid}]"


def test_the_senders_own_quarantine_masks_what_the_recipient_reads(
        tmp_checkpoint_dir, capsys):
    """The ask lives in the sender's bucket and the sender quarantined its
    wording; the recipient's own bucket holds no such record."""
    from daimon_briefing import trust
    rid = _ask(sender=OTHER_PEER, ask=m.QUARANTINED)
    tid = trust.propose(text=m.QUARANTINED, kind="decision", reason="r",
                        evidence=["issue:1"], channel="cli-tty",
                        project_dir=OTHER_PEER)
    rows = _json(capsys, "request", "inbox", "--json")
    row = next(r for r in rows if r["request_id"] == rid)
    assert row["from_slug"] == OTHER_SLUG
    assert row["ask"] == f"[withheld: quarantine {tid}]"
    rc, out, _ = m.run(capsys, "request", "inbox")
    assert rc == 0 and "SECRET" not in out


def test_the_sender_sees_a_recipients_note_masked(tmp_checkpoint_dir, capsys,
                                                   monkeypatch):
    rid = requests.open_request(to=OTHER_SLUG, ask="from us to them",
                                why="w", channel="cli-tty",
                                project_dir=m.PROJECT)
    requests.accept(rid, channel="cli-tty", note=m.QUARANTINED,
                    project_dir=OTHER_PEER)
    tid = m.quarantine()
    rows = _json(capsys, "request", "list", "--json")
    row = next(r for r in rows if r["request_id"] == rid)
    assert row["note"] == f"[withheld: quarantine {tid}]"
    rc, out, _ = m.run(capsys, "request", "list")
    assert rc == 0 and "SECRET" not in out


def test_folded_replies_completion_and_authors_are_masked(tmp_checkpoint_dir,
                                                           capsys):
    rid = requests.open_request(
        to=m.SLUG, ask="census", why="w", channel="ui",
        author=m.QUARANTINED, project_dir=m.PROJECT)
    requests.accept(rid, channel="ui", note="ok", author=m.FORGOTTEN,
                    project_dir=m.PROJECT)
    requests.reply(rid, m.QUARANTINED, m.FORGOTTEN, channel="ui",
                   author=m.QUARANTINED, project_dir=m.PROJECT)
    requests.done(rid, channel="ui", evidence=m.QUARANTINED,
                  author=m.QUARANTINED, project_dir=m.PROJECT)
    tid = m.quarantine()
    m.forget()
    for verb in ("list", "inbox"):
        rc, out, _ = m.run(capsys, "request", verb)
        assert rc == 0 and "SECRET" not in out, verb
        rows = _json(capsys, "request", verb, "--json")
        row = next(r for r in rows if r["request_id"] == rid)
        assert row["done_evidence"] == f"[withheld: quarantine {tid}]"
        assert row["opened_act_author"].startswith("[withheld")
        assert row["verdict_act_author"] == ""
        assert row["replies"][0]["note"].startswith("[withheld")
        assert row["replies"][0]["evidence"] == ""
        assert row["replies"][0]["act_author"].startswith("[withheld")


def test_a_closed_trust_ledger_keeps_a_persons_ask_readable(
        tmp_checkpoint_dir, capsys, monkeypatch):
    from daimon_briefing import jsonl
    from daimon_briefing.jsonl import Health
    _ask(sender=m.PROJECT, ask="a plain ask")
    real = jsonl.read
    target = tmp_checkpoint_dir / m.SLUG / "trust.jsonl"
    monkeypatch.setattr(
        jsonl, "read", lambda path, *a, **k: jsonl.Read(
            Health.TRANSIENT, [], detail="EBUSY") if path == target
        else real(path, *a, **k))
    view._judge_memo.clear()
    assert view.judge(m.SLUG).snap.closed
    rc, out, _ = m.run(capsys, "request", "list")
    assert rc == 0 and "a plain ask" in out


def test_open_and_the_verdict_verbs_echo_masked_and_keep_the_stored_text(
        tmp_checkpoint_dir, capsys, monkeypatch):
    tid = m.quarantine()
    m.human(monkeypatch)
    rc, out, _ = m.run(capsys, "request", "open", f"--to={m.SLUG}", "--ask",
                       m.QUARANTINED, "--why", "w", "--anyway")
    assert rc == 0 and "SECRET" not in out
    assert f"[withheld: quarantine {tid}]" in out
    stored = requests.listing(project_dir=m.PROJECT)[0]
    assert stored["ask"] == m.QUARANTINED          # the ledger keeps the words
    rid = stored["request_id"]
    rc, out, _ = m.run(capsys, "request", "accept", rid, "--note", m.QUARANTINED)
    assert rc == 0 and "SECRET" not in out
    rc, out, _ = m.run(capsys, "request", "reply", rid, "--note", m.QUARANTINED,
                       "--evidence", "e")
    assert rc == 0 and "SECRET" not in out
    rc, out, _ = m.run(capsys, "request", "done", rid, "--evidence",
                       m.QUARANTINED)
    assert rc == 0 and "SECRET" not in out


def test_revise_echoes_masked(tmp_checkpoint_dir, capsys, monkeypatch):
    rid = _ask(sender=m.PROJECT)
    m.quarantine()
    m.human(monkeypatch)
    rc, out, _ = m.run(capsys, "request", "revise", rid, "--ask",
                       m.QUARANTINED)
    assert rc == 0 and "SECRET" not in out and "withheld" in out


def test_a_judge_that_fails_shows_nothing(tmp_checkpoint_dir, capsys,
                                          monkeypatch):
    _ask(sender=m.PROJECT)

    def boom(*_a, **_k):
        raise RuntimeError("no")

    monkeypatch.setattr(view, "masked", boom)
    rc, out, err = m.run(capsys, "request", "list")
    assert rc == 2 and out == "" and "a plain ask" not in err
    assert err.count("\n") == 1


# ---- the per-prompt hook: print after judging, stamp after printing --------


def _inject(capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_LIVE_DELIVERY", "1")
    capsys.readouterr()
    rc = cli.main(["request-inject", "--session", "S-census",
                   "--project", m.PROJECT])
    return rc, capsys.readouterr().out


def test_the_hook_prints_a_masked_card_and_then_stamps(tmp_checkpoint_dir,
                                                       capsys, monkeypatch):
    _ask(ask=m.QUARANTINED, why=m.FORGOTTEN)
    m.quarantine()
    m.forget()
    rc, out = _inject(capsys, monkeypatch)
    assert rc == 0 and "SECRET" not in out and "withheld" in out
    rc, again = _inject(capsys, monkeypatch)        # delivered: not shown twice
    assert rc == 0 and again.strip() == ""


def test_the_hook_prints_nothing_and_stamps_nothing_when_masked_raises(
        tmp_checkpoint_dir, capsys, monkeypatch):
    _ask()
    monkeypatch.setenv("DAIMON_LIVE_DELIVERY", "1")
    real = view.masked

    def boom(*_a, **_k):
        raise RuntimeError("no")

    monkeypatch.setattr(view, "masked", boom)
    rc, out = _inject(capsys, monkeypatch)
    assert rc == 0 and out == ""
    monkeypatch.setattr(view, "masked", real)       # nothing was stamped
    rc, out = _inject(capsys, monkeypatch)
    assert rc == 0 and "a plain ask" in out


# ---- the MCP tool and the viewer route ------------------------------------


def test_the_mcp_inbox_tool_masks_every_row(tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import mcp_tools
    rid = _ask(sender=OTHER_PEER, ask=m.QUARANTINED, why=m.FORGOTTEN)
    tid = m.quarantine()
    m.forget()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", m.PROJECT)
    got = mcp_tools.HANDLERS["requests_inbox"]({"project": m.PROJECT})
    assert "SECRET" not in got.text
    row = next(r for r in json.loads(got.text) if r["request_id"] == rid)
    assert row["ask"] == f"[withheld: quarantine {tid}]" and row["why"] == ""


def test_the_mcp_inbox_tool_fails_closed_when_masked_raises(
        tmp_checkpoint_dir, monkeypatch):
    import pytest

    from daimon_briefing import mcp_tools

    def boom(*_a, **_k):
        raise RuntimeError("no")

    _ask(sender=OTHER_PEER)
    monkeypatch.setattr(view, "masked", boom)
    with pytest.raises(Exception):
        mcp_tools.HANDLERS["requests_inbox"]({"project": m.PROJECT})
