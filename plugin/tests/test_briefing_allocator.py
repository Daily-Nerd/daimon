"""#1128 phase 4: one allocator for everything `daimon brief` prints. The
HANDOFF, drift block, teammates and the withheld note are charged to the same
byte budget as the body, and `surfaced` is stamped only for request rows that
the printed brief actually carried."""

import pytest

from daimon_briefing import briefing, cli, render, requests, store

from ._prepared import in_hand
from .test_briefing_select import NOW, _fixture_checkpoint
from daimon_briefing.surfaces import Writer


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
    cp = in_hand(_fixture_checkpoint(), "/repo/x", NOW).checkpoint
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
        }, project_dir=proj, writer=Writer.HUMAN)
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


# ---- surfaced comes from the manifest, never from text matching ----


def _checkpoint_for(route):
    return in_hand(_fixture_checkpoint(), route, NOW).checkpoint


def _ids(manifest):
    return {k: {c.request_id for c in v} for k, v in manifest.items()}


def test_render_brief_returns_the_ids_of_printed_cards(
        tmp_checkpoint_dir, monkeypatch, capsys):
    q_id = _open_ask()
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "0")
    cp = _checkpoint_for(RECIPIENT)
    printed = render.render_brief(cp, project_dir=RECIPIENT,
                                  worldcheck_project=RECIPIENT)
    capsys.readouterr()
    assert _ids(printed) == {"request": {q_id}, "verdict": set()}
    # collapsed to a count line under budget: no card, no id
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "200")
    printed = render.render_brief(cp, project_dir=RECIPIENT,
                                  worldcheck_project=RECIPIENT)
    capsys.readouterr()
    assert _ids(printed) == {"request": set(), "verdict": set()}


def test_no_worldcheck_project_means_no_cards_and_nothing_to_stamp(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _open_ask()
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "0")
    printed = render.render_brief(_checkpoint_for(RECIPIENT),
                                  project_dir=RECIPIENT)
    capsys.readouterr()
    assert _ids(printed) == {"request": set(), "verdict": set()}


def test_rich_path_reports_the_full_cards_it_printed(
        tmp_checkpoint_dir, monkeypatch, capsys):
    pytest.importorskip("rich")
    q_id = _open_ask()
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    printed = render.render_brief(_checkpoint_for(RECIPIENT),
                                  project_dir=RECIPIENT,
                                  worldcheck_project=RECIPIENT)
    capsys.readouterr()
    assert _ids(printed)["request"] == {q_id}


def test_a_missing_manifest_stamps_nothing(tmp_checkpoint_dir, monkeypatch):
    # The old rule read "no manifest means everything was shown".
    q_id = _open_ask()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", RECIPIENT)
    monkeypatch.setattr(render, "render_brief", lambda *a, **k: None)
    assert cli.main(["brief"]) == 0
    record = requests.recipient_join(project_dir=RECIPIENT)[q_id]
    assert requests.needs_surfaced_stamp(record) is True


def test_an_id_quoted_in_printed_text_is_not_a_printed_card(
        tmp_checkpoint_dir, monkeypatch):
    q_id = _open_ask()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", RECIPIENT)

    def fake(*a, **k):
        # the id appears in printed text (a note), but no card was printed
        print(f"note mentioning {q_id}")
        return {"request": frozenset(), "verdict": frozenset()}
    monkeypatch.setattr(render, "render_brief", fake)
    assert cli.main(["brief"]) == 0
    record = requests.recipient_join(project_dir=RECIPIENT)[q_id]
    assert requests.needs_surfaced_stamp(record) is True
