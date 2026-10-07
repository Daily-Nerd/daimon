"""`briefing.prepare` and `briefing.stamp`: the one preparation every briefing
host shares (#1132 PR 7a). Stores are written by the real writers."""

import dataclasses
import json
import time
from types import MappingProxyType

import pytest

from daimon_briefing import (amendments, briefing, normalize, store, trust,
                             view)

from ._prepared import in_hand

PROJECT = "/p/prepare"
OTHER = "/p/prepare-other"
NOW = time.time()
T_RESOLVED = "SENTINEL-resolved the gateway retry loop"
T_QUARANTINED = "SENTINEL-quarantined who owns the migration"
T_FORGOTTEN = "SENTINEL-forgotten adopt the strangler pattern"
T_KEPT = "an unrelated decision that stays visible"
T_TOPIC = "SENTINEL-topic the weekly sync cadence"


def _checkpoint(sid="S-1", **over):
    wc = {
        "active_topic": {"text": T_TOPIC, "trust": "inferred"},
        "open_questions": [
            {"text": T_RESOLVED, "trust": "inferred"},
            {"text": T_QUARANTINED, "trust": "inferred"}],
        "recent_decisions": [
            {"text": T_FORGOTTEN, "trust": "inferred"},
            {"text": T_KEPT, "trust": "inferred"}]}
    wc.update(over)
    return {"session_id": sid, "created": "2026-08-01T00:00:00Z",
            "working_context": wc, "epistemic_snapshot": {}}


def _write(project=PROJECT, **over):
    cp = _checkpoint(**over)
    store.write_checkpoint(cp["session_id"], cp, project_dir=project)


def _ids(project=PROJECT):
    cp = store.read_latest_body(project_dir=project, route=store.Route.OWN,
                                admit=store.Admit.ANY)
    items = cp["working_context"]["open_questions"] \
        + cp["working_context"]["recent_decisions"]
    return {item["text"]: item["id"] for item in items}


def _texts(checkpoint):
    wc = checkpoint["working_context"]
    return [i["text"] for key in ("open_questions", "recent_decisions")
            for i in wc.get(key, [])]


def _plant(name, data, project=PROJECT):
    bucket = store.config.checkpoint_dir() / store.project_slug(project)
    bucket.mkdir(parents=True, exist_ok=True)
    (bucket / name).write_bytes(data)


def _withhold_three():
    ids = _ids()
    store.append_event(ids[T_RESOLVED], "resolved", project_dir=PROJECT)
    trust.propose(text=T_QUARANTINED, kind="question", reason="made up",
                  evidence=["issue:1"], channel="cli-tty",
                  project_dir=PROJECT)
    key = normalize.content_key(T_FORGOTTEN)
    store.append_event("i-gone", f"forgotten:{key}", kind="tombstone",
                       tombstone=True, project_dir=PROJECT)


# ---- the view and its facts ------------------------------------------------


def test_prepare_returns_the_view_and_counts_each_kind_apart(
        tmp_checkpoint_dir):
    _write()
    _withhold_three()
    got = briefing.prepare(PROJECT, NOW)
    assert _texts(got.checkpoint) == [T_KEPT]
    assert got.suppressed == 1          # the resolved loop
    assert got.quarantined == 1         # the human quarantine
    assert len(got.withheld) == 2       # quarantine + forgotten, never text
    assert {w.reason for w in got.withheld} == {"quarantine", "forgotten"}
    assert got.notes == ()
    assert got.fell_back is False
    assert got.opened.snapshot is got.snapshot


def test_a_forgotten_value_is_counted_nowhere(tmp_checkpoint_dir):
    _write()
    key = normalize.content_key(T_FORGOTTEN)
    store.append_event("i-gone", f"forgotten:{key}", kind="tombstone",
                       tombstone=True, project_dir=PROJECT)
    got = briefing.prepare(PROJECT, NOW)
    assert (got.suppressed, got.quarantined) == (0, 0)
    assert T_FORGOTTEN not in _texts(got.checkpoint)


def test_prepare_with_no_checkpoint_is_empty_and_does_not_raise(
        tmp_checkpoint_dir):
    got = briefing.prepare(PROJECT, NOW)
    assert got.checkpoint is None
    assert got.worldcheck is None and got.ledger_rows == []
    assert got.stale_items == []


def test_live_false_keeps_the_resolved_loop(tmp_checkpoint_dir):
    _write()
    _withhold_three()
    got = briefing.prepare(PROJECT, NOW, live=False)
    assert T_RESOLVED in _texts(got.checkpoint)
    assert got.suppressed == 0


def test_the_global_pointer_is_a_labelled_fallback_with_the_readers_ledgers(
        tmp_checkpoint_dir):
    _write(project=OTHER)
    trust.propose(text=T_QUARANTINED, kind="question", reason="made up",
                  evidence=["issue:1"], channel="cli-tty", project_dir=PROJECT)
    own = briefing.prepare(PROJECT, NOW)
    assert own.checkpoint is None and own.fell_back is False
    got = briefing.prepare(PROJECT, NOW, route=store.Route.OWN_ELSE_GLOBAL)
    assert got.fell_back is True
    assert T_QUARANTINED not in _texts(got.checkpoint)
    assert got.quarantined == 1


def test_worldcheck_never_probes_a_fallback_body(tmp_checkpoint_dir,
                                                 monkeypatch):
    from daimon_briefing import config, worldcheck
    monkeypatch.setattr(config, "worldcheck_enabled", lambda: True)

    def boom(*_a, **_k):
        raise AssertionError("worldcheck ran on a fallback body")
    monkeypatch.setattr(worldcheck, "check", boom)
    _write(project=OTHER)
    got = briefing.prepare(PROJECT, NOW, route=store.Route.OWN_ELSE_GLOBAL,
                           worldcheck_project=PROJECT)
    assert got.worldcheck is None


def test_worldcheck_runs_for_the_same_project_and_returns_its_rows(
        tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import config, worldcheck
    monkeypatch.setattr(config, "worldcheck_enabled", lambda: True)
    monkeypatch.setattr(worldcheck, "check", lambda cp, project: {
        "confirmed": 2, worldcheck.LEDGER_KEY: [("o-aaaaaa", "pr", "gone")]})
    _write()
    got = briefing.prepare(PROJECT, NOW, worldcheck_project=PROJECT)
    assert got.worldcheck == {"confirmed": 2}
    assert got.ledger_rows == [("o-aaaaaa", "pr", "gone")]


def test_a_failing_worldcheck_costs_only_the_worldcheck(tmp_checkpoint_dir,
                                                        monkeypatch):
    from daimon_briefing import config, worldcheck
    monkeypatch.setattr(config, "worldcheck_enabled", lambda: True)

    def boom(*_a, **_k):
        raise RuntimeError("gh is down")
    monkeypatch.setattr(worldcheck, "check", boom)
    _write()
    got = briefing.prepare(PROJECT, NOW, worldcheck_project=PROJECT)
    assert got.worldcheck is None and got.ledger_rows == []
    assert T_KEPT in _texts(got.checkpoint)


def test_prepare_takes_an_opened_the_caller_already_has(tmp_checkpoint_dir):
    _write()
    opened = view.open(PROJECT, live=True)
    got = briefing.prepare("ignored", NOW, opened=opened)
    assert got.opened is opened
    assert _texts(got.checkpoint) == _texts(opened.checkpoint)


def test_a_raise_from_the_view_is_not_swallowed(tmp_checkpoint_dir,
                                                monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("view is broken")
    monkeypatch.setattr(view, "open", boom)
    with pytest.raises(RuntimeError, match="view is broken"):
        briefing.prepare(PROJECT, NOW)


# ---- closed and degraded ---------------------------------------------------


def test_an_unreadable_trust_ledger_closes_every_item_and_says_so(
        tmp_checkpoint_dir):
    _write()
    _plant("trust.jsonl", b"<<<<<<< HEAD\n")
    got = briefing.prepare(PROJECT, NOW)
    assert got.checkpoint is not None
    assert _texts(got.checkpoint) == []
    assert "active_topic" not in got.checkpoint["working_context"]
    assert all(w.reason == "closed" for w in got.withheld)
    assert got.notes and got.notes[0].startswith("⚠ trust.jsonl is unreadable")
    assert got.quarantined == 0


def test_a_degraded_ledger_adds_a_note_and_keeps_the_items(tmp_checkpoint_dir):
    _write()
    store.append_event("o-zzzzzz", "resolved", project_dir=PROJECT)
    _plant("events.jsonl", (store.config.checkpoint_dir()
                            / store.project_slug(PROJECT)
                            / "events.jsonl").read_bytes()
           + b'{"kind": "resolution", "item_ref": "o-bb')
    got = briefing.prepare(PROJECT, NOW)
    assert got.notes == ("⚠ events.jsonl is degraded (torn)",)
    assert T_KEPT in _texts(got.checkpoint)


# ---- stamp -----------------------------------------------------------------


def _snap(**over):
    fixed = {k: MappingProxyType(v) if isinstance(v, dict) else v
             for k, v in over.items()}
    return dataclasses.replace(view.Snapshot.empty(), **fixed)


def _cp(*items):
    return {"working_context": {"open_questions": list(items)},
            "epistemic_snapshot": {}}


def test_stamp_of_nothing_returns_the_input_and_no_marks():
    cp = _cp({"id": "o-aaaaaa", "text": "x"})
    out, candidates, stale = briefing.stamp(cp, view.Snapshot.empty(), NOW)
    assert out is cp and candidates == [] and stale == []


def test_stamp_marks_candidates_claims_amendments_and_witnesses():
    cp = _cp({"id": "o-aaaaaa", "text": "a"}, {"id": "o-bbbbbb", "text": "b"},
             {"id": "o-cccccc", "text": "c"}, {"id": "o-dddddd", "text": "d"})
    events = {
        "o-aaaaaa": {"ts": "2026-07-07T00:00:00Z", "item_ref": "o-aaaaaa",
                     "status": "supersede-candidate:o-eeeeee"},
        "o-bbbbbb": {"ts": "2026-07-07T00:00:00Z", "item_ref": "o-bbbbbb",
                     "status": "resolving-candidate", "source": "agent",
                     "note": "the PR merged"},
    }
    amend = {"o-cccccc": {"rows": [{
        "amendment_id": "a-1", "change": "progressed", "evidence": "q",
        "state": "ratified", "proposed_by": "human"}], "overflow": 0}}
    corro = {"o-dddddd": {"origin": "S-0", "origins": ["S-1", "S-2"],
                          "recorded": []}}
    out, candidates, _stale = briefing.stamp(
        cp, _snap(resolutions=events, amendments=amend,
                  corroborations=corro), NOW)
    items = {i["id"]: i for i in out["working_context"]["open_questions"]}
    assert items["o-aaaaaa"]["_supersede_candidate"] == "o-eeeeee"
    assert [c[1]["id"] for c in candidates] == ["o-aaaaaa"]
    assert items["o-bbbbbb"]["_agent_claim"] == "the PR merged"
    assert items["o-cccccc"]["_amend"]["rows"][0]["id"] == "a-1"
    assert items["o-dddddd"].get("_corroborated", 0) >= 2
    assert out is not cp and "_amend" not in cp["working_context"][
        "open_questions"][2]


def test_a_resolved_item_takes_no_amendment():
    cp = _cp({"id": "o-aaaaaa", "text": "a"})
    events = {"o-aaaaaa": {"ts": "2026-07-07T00:00:00Z",
                           "item_ref": "o-aaaaaa", "status": "resolved"}}
    amend = {"o-aaaaaa": {"rows": [{"amendment_id": "a-1",
                                    "change": "progressed", "evidence": "q",
                                    "state": "ratified"}], "overflow": 0}}
    out, _c, _s = briefing.stamp(cp, _snap(resolutions=events,
                                           amendments=amend), NOW)
    assert "_amend" not in out["working_context"]["open_questions"][0]


def test_stamp_marks_a_stale_carried_item():
    cp = _cp({"id": "o-aaaaaa", "text": "a", "carried_from": "S-0",
              "first_seen": "2020-01-01T00:00:00Z"})
    out, _c, stale = briefing.stamp(cp, view.Snapshot.empty(), NOW)
    assert stale and "_stale_carried_days" in stale[0]
    again, _c, no_stale = briefing.stamp(cp, view.Snapshot.empty(), NOW,
                                         with_stale=False)
    assert no_stale == [] and again is cp


def test_stamp_of_a_non_checkpoint_is_the_input():
    assert briefing.stamp(None, view.Snapshot.empty(), NOW) == (None, [], [])


def test_prepare_stamps_false_leaves_the_marks_off(tmp_checkpoint_dir):
    _write()
    ids = _ids()
    store.append_event(ids[T_KEPT], "supersede-candidate:o-eeeeee",
                       project_dir=PROJECT)
    stamped = briefing.prepare(PROJECT, NOW)
    plain = briefing.prepare(PROJECT, NOW, stamps=False)
    kept = [i for i in stamped.checkpoint["working_context"]["recent_decisions"]
            if i["text"] == T_KEPT]
    assert kept and "_supersede_candidate" in kept[0]
    kept = [i for i in plain.checkpoint["working_context"]["recent_decisions"]
            if i["text"] == T_KEPT]
    assert kept and "_supersede_candidate" not in kept[0]
    assert plain.stale_items == []


def test_the_amendment_stamp_reaches_prepare(tmp_checkpoint_dir):
    _write()
    ids = _ids()
    amendments.propose(item_id=ids[T_KEPT], change="progressed",
                       evidence="the PR merged", channel="cli-tty",
                       project_dir=PROJECT)
    got = briefing.prepare(PROJECT, NOW)
    item = [i for i in got.checkpoint["working_context"]["recent_decisions"]
            if i["text"] == T_KEPT][0]
    assert item["_amend"]["rows"]


# ---- a checkpoint in hand --------------------------------------------------


def test_prepare_over_a_checkpoint_in_hand(tmp_checkpoint_dir):
    _write()
    _withhold_three()
    cp = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                admit=store.Admit.ANY)
    got = in_hand(cp, PROJECT, NOW)
    assert _texts(got.checkpoint) == [T_KEPT]
    assert (got.suppressed, got.quarantined) == (1, 1)
    empty = in_hand(None, PROJECT, NOW)
    assert empty.checkpoint is None and empty.notes == ()


def _stale_checkpoint(first_seen):
    return {"session_id": "S-stale",
            "working_context": {
                "open_questions": [{"text": "a carried claim",
                                    "trust": "inferred", "id": "o-aaaaaa",
                                    "carried_from": "S-prev",
                                    "first_seen": first_seen}],
                "recent_decisions": []},
            "epistemic_snapshot": {}}


def _days_ago(days, now=NOW):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - days * 86400))


def test_a_future_stamp_clamps_to_age_zero(tmp_checkpoint_dir):
    cp = _stale_checkpoint(_days_ago(-5))
    assert briefing._carried_age_days(
        cp["working_context"]["open_questions"][0], {}, NOW) == 0.0
    assert in_hand(cp, PROJECT, NOW).stale_items == []


def test_a_missing_stamp_is_fail_open_not_stale(tmp_checkpoint_dir):
    cp = _stale_checkpoint(None)
    assert in_hand(cp, PROJECT, NOW).stale_items == []


def test_every_host_carries_the_stale_mark_and_calls_prepare(
        tmp_checkpoint_dir, monkeypatch, capsys):
    """The CLI brief, `daimon loops`, the MCP tool and the Hermes hook all
    go through `prepare`, so the stale mark cannot differ by host."""
    from daimon_briefing import cli, hooks, mcp_tools
    cp = _stale_checkpoint(_days_ago(12, time.time()))
    cp["working_context"]["open_questions"][0]["text"] = (
        "stale carried claim")
    store.write_checkpoint("S-stale", cp, project_dir=PROJECT)
    seen = []
    real = briefing.prepare

    def _spy(project, now, **kw):
        seen.append(project)
        return real(project, now, **kw)
    monkeypatch.setattr(briefing, "prepare", _spy)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    assert cli.main(["brief", "--project", PROJECT]) == 0
    assert "[? unverified] stale carried claim" in capsys.readouterr().out
    assert len(seen) == 1
    out = mcp_tools.HANDLERS["daimon_brief"](
        {"slug": store.project_slug(PROJECT)})
    assert "[? unverified] stale carried claim" in out
    assert len(seen) == 2
    ctx = hooks.pre_llm_call(session_id="S-new", user_message="hi",
                             conversation_history=[], is_first_turn=True,
                             model="m", platform="cli")
    assert "[? unverified] stale carried claim" in ctx["context"]
    assert len(seen) == 3
    assert cli.main(["loops", "--project", PROJECT]) == 0
    assert len(seen) == 4


# ---- json check that the stored body is untouched --------------------------


def test_the_stored_checkpoint_is_never_touched(tmp_checkpoint_dir):
    _write()
    _withhold_three()
    before = (store.config.checkpoint_dir() / store.project_slug(PROJECT)
              / "latest.json").read_bytes()
    briefing.prepare(PROJECT, NOW)
    after = (store.config.checkpoint_dir() / store.project_slug(PROJECT)
             / "latest.json").read_bytes()
    assert before == after
    assert json.loads(before)["working_context"]["recent_decisions"]


def test_a_reopen_event_refreshes_the_stale_age(tmp_checkpoint_dir):
    """The stale fold reads the snapshot's resolutions (a read-only mapping):
    an item reopened yesterday is not stale, however old it was born."""
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(time.time() - 40 * 86400))
    _write(open_questions=[{"text": "an old carried loop", "trust": "inferred",
                            "carried_from": "S-prev", "first_seen": old}])
    item_id = _ids()["an old carried loop"]
    assert briefing.prepare(PROJECT, time.time()).stale_items
    store.append_event(item_id, "reopened", project_dir=PROJECT)
    assert briefing.prepare(PROJECT, time.time()).stale_items == []
