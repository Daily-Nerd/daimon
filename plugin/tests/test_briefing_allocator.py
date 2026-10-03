"""#1128 phase 4: one allocator for everything `daimon brief` prints. The
HANDOFF, drift block, teammates and the withheld note are charged to the same
byte budget as the body, and `surfaced` is stamped only for request rows that
the printed brief actually carried."""

import pytest

from daimon_briefing import briefing, cli, render, requests, store

from .test_briefing_select import NOW, _fixture_checkpoint


@pytest.fixture(autouse=True)
def _plain(monkeypatch):
    monkeypatch.setattr(render, "supports_rich", lambda: False)
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "0")
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "10")


def _teammates(n=5, per=4):
    out = []
    for t in range(n):
        cp = {
            "working_context": {
                "active_topic": {"text": f"teammate {t} topic", "trust": "inferred"},
                "recent_decisions": [
                    {"text": f"t{t}-decision-{d} " + "words " * 12,
                     "trust": "inferred"} for d in range(per)],
                "open_questions": [],
            },
            "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
        }
        out.append((f"mate{t}", briefing.build(cp)))
    return out


def _printed(capsys, **kw):
    cp = briefing.annotate(
        _fixture_checkpoint(), briefing.AnnotateContext(route="/repo/x"),
        NOW).checkpoint
    render.render_brief(cp, **kw)
    return capsys.readouterr().out


def test_total_printed_bytes_stay_within_the_budget(monkeypatch, capsys):
    handoff = {"ts": "2026-08-04T18:09:41Z", "note": "hand off " * 40}
    drift = [{"item": {"text": "Adopt D-007 prompt"}, "kind": "soft",
              "anchor": {"qualified_name": "plugin/x.py::run"}}]
    trailer = ["2 resolved item(s) withheld - `daimon status --suppressed` to list"]
    for budget in (9000, 7000, 5500, 4500):
        monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", str(budget))
        out = _printed(capsys, drift=drift, teammates=_teammates(),
                       handoff=handoff, trailer=trailer)
        assert len(out.encode("utf-8")) <= budget, budget
        assert "hand off" in out and "CODE DRIFT" in out
        assert trailer[0] in out
        # the floor: two teammates always show
        assert "[mate0]" in out and "[mate1]" in out


def test_teammates_beyond_the_floor_drop_and_are_announced(monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "4500")
    out = _printed(capsys, teammates=_teammates())
    assert "[mate4]" not in out
    assert "more teammates not shown for budget" in out
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "0")
    out = _printed(capsys, teammates=_teammates())
    assert all(f"[mate{t}]" in out for t in range(5))
    assert "not shown for budget" not in out


def test_trailer_is_printed_after_the_teammates(monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "0")
    out = _printed(capsys, teammates=_teammates(2), trailer=["a note"])
    assert out.index("[mate1]") < out.index("a note")
    assert out.endswith("a note\n")


def test_select_charges_reserved_and_teammate_bytes():
    b = briefing.build(_fixture_checkpoint(), now=NOW)
    blocks = ["\n[a]\n  Decisions made:\n  - x\n"] * 4
    free = briefing.select(b, 6000, NOW)
    tight = briefing.select(b, 6000, NOW, reserved=3000,
                            teammate_blocks=blocks, teammate_header="Team:")
    body = len(briefing.render_selection(tight).encode())
    team = len(briefing.teammates_text(tight).encode())
    assert body + team + 3000 <= 6000
    assert len(tight.kept_teammates) == 2
    assert body < len(briefing.render_selection(free).encode())


# ---- surfaced is stamped from what was printed ----

RECIPIENT = "/p/alloc-recipient"
SENDER = "/p/alloc-sender"


def _open_ask():
    for proj, sess in ((SENDER, "S-alloc-sender"), (RECIPIENT, "S-alloc-recipient")):
        store.write_checkpoint(sess, {
            "session_id": sess, "created": "2026-08-16T00:00:00Z",
            "working_context": {"recent_decisions": [
                {"text": "x", "trust": "inferred"}]},
        }, project_dir=proj)
    return requests.open_request(
        to=store.project_slug(RECIPIENT), ask="publish the schema",
        why="because", channel="cli-agent",
        project_dir=store.project_slug(SENDER))


def test_a_request_row_cut_by_budget_is_not_stamped_surfaced(
        tmp_checkpoint_dir, monkeypatch, capsys):
    q_id = _open_ask()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", RECIPIENT)
    # a budget only the fixed head fits in: the request panel collapses to a
    # count line, so the card never reaches the reader
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "200")
    assert cli.main(["brief"]) == 0
    out = capsys.readouterr().out
    assert "publish the schema" not in out
    assert "cut for budget, see: daimon request inbox" in out
    record = requests.recipient_join(project_dir=RECIPIENT)[q_id]
    assert requests.needs_surfaced_stamp(record) is True


def test_a_request_row_that_was_printed_is_stamped(
        tmp_checkpoint_dir, monkeypatch, capsys):
    q_id = _open_ask()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", RECIPIENT)
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "0")
    assert cli.main(["brief"]) == 0
    assert "publish the schema" in capsys.readouterr().out
    record = requests.recipient_join(project_dir=RECIPIENT)[q_id]
    assert requests.needs_surfaced_stamp(record) is False
