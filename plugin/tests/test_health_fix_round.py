"""Fix round for PR 10a: team closed on own events, sender-degraded, memo keys
with ctime, the incomplete set once per pass, pending's foreign readers,
author notes on recall and the no-checkpoint team branch, an honest read-once
count (#1132)."""

import json
import os
import time

import pytest

from daimon_briefing import (cli, config, display, jsonl, recall, requests,
                             store, view)
from daimon_briefing.jsonl import Health
from daimon_briefing.surfaces import Writer

OWN = "/p/round-own"
SENDER = "/p/round-sender"
FORGOTTEN = "the pangolinsentinel decision stays"


def _bucket(project):
    return config.checkpoint_dir() / store.project_slug(project)


def _write(project, texts, sid=None):
    sid = sid or "S-" + store.project_slug(project).strip("-")
    store.write_checkpoint(sid, {
        "session_id": sid, "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": t, "trust": "inferred"} for t in texts],
            "open_questions": []},
        "epistemic_snapshot": {}}, project_dir=project, writer=Writer.HUMAN)
    return store.project_slug(project)


def _seam(monkeypatch, path, result):
    real = jsonl.read
    monkeypatch.setattr(
        jsonl, "read",
        lambda p, *a, **k: result if p == path else real(p, *a, **k))


def _teammate(monkeypatch, text="a teammate decision", author="grace"):
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "core/x")
    remote = config.team_dir() / "team-a"
    (remote / ".git").mkdir(parents=True, exist_ok=True)
    adir = remote / "projects" / "core" / "x" / "authors" / author
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "S1.json").write_text(json.dumps({
        "session_id": "S1", "created": "2026-10-01T00:00:00Z",
        "author": author, "team_project": "core/x",
        "project_slug": store.project_slug(OWN),
        "working_context": {"recent_decisions": [
            {"text": text, "trust": "inferred"}]},
    }), encoding="utf-8")
    return adir


# ---- 1. team closed on own events unproven ----------------------------------

TEAM_CLOSED = ("⚠ this project's events ledger cannot be read; teammates' "
               "checkpoints are not shown; run: daimon status")


def test_the_team_closed_line_is_the_one_text():
    assert display.team_closed_note() == TEAM_CLOSED


def test_read_team_admits_nobody_while_the_own_events_ledger_is_unproven(
        tmp_checkpoint_dir, monkeypatch):
    _write(OWN, ["our own decision"])
    _teammate(monkeypatch)
    assert [a for a, _ in store.read_team(project_dir=OWN)] == ["grace"]
    _seam(monkeypatch, _bucket(OWN) / "events.jsonl",
          jsonl.Read(Health.UNREADABLE, [], detail="EIO"))
    assert store.read_team(project_dir=OWN) == []
    assert view.team(OWN, live=True) == ()
    assert TEAM_CLOSED in view.team_notes(OWN)


def test_a_transient_own_events_ledger_closes_the_team_too(
        tmp_checkpoint_dir, monkeypatch):
    _write(OWN, ["our own decision"])
    _teammate(monkeypatch)
    _seam(monkeypatch, _bucket(OWN) / "events.jsonl",
          jsonl.Read(Health.TRANSIENT, [], detail="EBUSY"))
    assert store.read_team(project_dir=OWN) == []


def test_a_degraded_own_events_ledger_keeps_the_team(tmp_checkpoint_dir,
                                                     monkeypatch):
    _write(OWN, ["our own decision"])
    _teammate(monkeypatch)
    with open(_bucket(OWN) / "events.jsonl", "ab") as handle:
        handle.write(b'{"torn')
    assert [a for a, _ in store.read_team(project_dir=OWN)] == ["grace"]
    assert TEAM_CLOSED not in view.team_notes(OWN)


def test_brief_team_shows_nothing_of_a_teammate_and_says_why(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _write(OWN, ["our own decision"])
    _teammate(monkeypatch, text=FORGOTTEN)
    _seam(monkeypatch, _bucket(OWN) / "events.jsonl",
          jsonl.Read(Health.UNREADABLE, [], detail="EIO"))
    assert cli.main(["brief", "--project", OWN, "--team"]) == 0
    out = capsys.readouterr().out
    assert FORGOTTEN not in out and "grace" not in out
    assert TEAM_CLOSED in out


def test_the_team_closed_line_has_no_tenant_variant(tmp_checkpoint_dir,
                                                    monkeypatch):
    _write(OWN, ["our own decision"])
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    _seam(monkeypatch, _bucket(OWN) / "events.jsonl",
          jsonl.Read(Health.UNREADABLE, [], detail="EIO"))
    assert TEAM_CLOSED in view.team_notes(OWN)


# ---- 2. sender-degraded -----------------------------------------------------

DEGRADED = ("⚠ 1 sender(s) have a requests ledger with torn lines; "
            "their asks may be incomplete")


def _ask(sender=SENDER, to=OWN):
    return requests.open_request(
        to=store.project_slug(to), ask="review the bar", why="it blocks us",
        channel="cli-agent", project_dir=sender)


def _tear(project):
    _bucket(project).mkdir(parents=True, exist_ok=True)
    with open(_bucket(project) / "requests.jsonl", "ab") as handle:
        handle.write(b'{"event": "opened", "request_id": "q-torn')


def test_a_torn_sender_ledger_is_read_and_noted(tmp_checkpoint_dir):
    rid = _ask()
    _tear(SENDER)
    got = requests.inbox(OWN)
    assert [r["request_id"] for r in got.rows] == [rid]
    assert got.notes == (DEGRADED,)
    assert requests.join(OWN).notes == (DEGRADED,)


def test_the_degraded_line_drops_the_count_under_tenant_scope(
        tmp_checkpoint_dir, monkeypatch):
    _ask()
    _tear(SENDER)
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    assert requests.inbox(OWN).notes == (
        "⚠ some senders have a requests ledger with torn lines; "
        "their asks may be incomplete",)


def test_skipped_and_degraded_are_both_said(tmp_checkpoint_dir):
    _ask("/p/round-s1")
    _ask("/p/round-s2")
    _tear("/p/round-s1")
    with open(_bucket("/p/round-s2") / "requests.jsonl", "ab") as handle:
        handle.write(b"<<<<<<< HEAD\n")
    notes = requests.inbox(OWN).notes
    assert notes == (
        "⚠ 1 sender(s) skipped: a requests ledger cannot be read", DEGRADED)


def test_a_torn_recipient_is_noted_on_request_list(tmp_checkpoint_dir):
    _ask()
    _tear(OWN)
    got = requests.listed(SENDER)
    assert got.notes == (DEGRADED,)


def test_the_decision_panel_carries_the_degraded_line(tmp_checkpoint_dir):
    from daimon_briefing import briefing
    _ask()
    _tear(SENDER)
    lines, _cards = briefing.request_panel(OWN)
    assert lines[-1] == DEGRADED


# ---- 6. author notes on recall and the no-checkpoint team branch ------------

SKIPPED = ("⚠ a teammate's tombstones cannot be read; their checkpoints are "
           "not admitted")
TORN = ("⚠ a teammate's tombstones ledger has torn lines; their forgets may "
        "be incomplete")


def test_recall_carries_the_author_codes(tmp_checkpoint_dir, monkeypatch):
    adir = _teammate(monkeypatch)
    (adir / "tombstones.jsonl").write_bytes(b"<<<<<<< HEAD\n")
    got = recall.query("teammate", all_projects=True)
    assert "author-skipped" in got.notes
    assert display.recall_note(got.notes) == SKIPPED
    (adir / "tombstones.jsonl").write_bytes(b'{"ts": "x", "key": "tor')
    got = recall.query("teammate", all_projects=True)
    assert "author-degraded" in got.notes and "author-skipped" not in got.notes
    assert display.recall_note(got.notes) == TORN


def test_recall_says_nothing_about_authors_when_all_are_proven(
        tmp_checkpoint_dir, monkeypatch):
    _teammate(monkeypatch)
    got = recall.query("teammate", all_projects=True)
    assert not [n for n in got.notes if n.startswith("author-")]


def test_the_no_checkpoint_team_branch_prints_the_author_note(
        tmp_checkpoint_dir, monkeypatch, capsys):
    adir = _teammate(monkeypatch)
    (adir / "tombstones.jsonl").write_bytes(b"<<<<<<< HEAD\n")
    _write("/p/round-elsewhere", ["a decision elsewhere"])
    assert cli.main(["brief", "--project", OWN, "--team"]) == 0
    out = capsys.readouterr().out
    assert "No briefing for this project yet" in out
    assert SKIPPED in out


# ---- 3. memo keys see a chmod ------------------------------------------------


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root reads a 000 file")
def test_a_chmod_reaches_every_memo_without_clearing_a_cache(
        tmp_checkpoint_dir):
    broken = _write("/p/round-broken", ["words"])
    _write("/p/round-fine", ["other words"])
    events = _bucket("/p/round-broken") / "events.jsonl"
    store.append_event("o-aaaaaa", "resolved", project_dir="/p/round-broken", writer=Writer.HUMAN)
    assert store.forgotten_incomplete() == frozenset()      # memo warm
    assert view.judge(broken).index_closed is False         # judge memo warm
    assert view.snapshot("/p/round-fine").notes() == ()
    events.chmod(0)
    try:
        assert store.forgotten_incomplete() == frozenset({broken})
        assert view.judge(broken).index_closed is True
        assert view.snapshot("/p/round-fine").notes() == (
            display.forget_incomplete_note(),)
    finally:
        events.chmod(0o644)
    assert store.forgotten_incomplete() == frozenset()
    assert view.judge(broken).index_closed is False


def test_the_stamps_change_on_a_chmod(tmp_checkpoint_dir):
    _write("/p/round-stamp", ["words"])
    store.append_event("o-aaaaaa", "resolved", project_dir="/p/round-stamp", writer=Writer.HUMAN)
    path = _bucket("/p/round-stamp") / "events.jsonl"
    key, stamp = view._stat_key(path), store.forgotten_stamp()
    time.sleep(0.01)
    path.chmod(0o600)               # ctime moves; mtime, size, inode do not
    assert view._stat_key(path) != key
    assert store.forgotten_stamp() != stamp


# ---- 4. the incomplete set once per pass ------------------------------------


def _walks(monkeypatch):
    calls = []
    real = store._forgotten_walk
    monkeypatch.setattr(store, "_forgotten_walk",
                        lambda: calls.append(1) or real())
    return calls


def _ten_buckets_one_broken():
    for i in range(10):
        _write(f"/p/round-many-{i}", [f"decision number {i}"])
    with open(_bucket("/p/round-many-3") / "events.jsonl", "ab") as handle:
        handle.write(b"<<<<<<< HEAD\n")


def test_a_listing_walks_the_forget_set_at_most_twice(tmp_checkpoint_dir,
                                                      monkeypatch):
    _ten_buckets_one_broken()
    calls = _walks(monkeypatch)
    listed = view.projects(None)
    assert len(listed) == 10
    assert len(calls) <= 2, len(calls)


def test_a_judge_given_the_incomplete_set_does_not_walk(tmp_checkpoint_dir,
                                                        monkeypatch):
    slug = _write(OWN, ["our own decision"])
    calls = _walks(monkeypatch)
    view.judge(slug, forgotten=frozenset(), incomplete=frozenset())
    assert calls == []


def test_a_recall_build_and_query_walk_for_incompleteness_once_per_pass(
        tmp_checkpoint_dir, monkeypatch):
    _ten_buckets_one_broken()
    stats = {}
    calls = _walks(monkeypatch)
    real_judge = view.judge
    seen = []
    monkeypatch.setattr(
        view, "judge",
        lambda slug, **kw: seen.append("incomplete" in kw)
        or real_judge(slug, **kw))
    recall.rebuild()
    stats["build"] = len(calls)
    assert seen and all(seen), "a build judge was not handed the incomplete set"
    assert not any(not flag for flag in seen)


# ---- 5. pending's foreign readers and the sender-side surfaces --------------

ELSE = "/p/round-else"
ELSE2 = "/p/round-else2"
SKIPPED_ELSE = "⚠ 1 other project(s) skipped: a ledger cannot be read"
TORN_ELSE = ("⚠ 1 other project(s) have a ledger with torn lines; "
             "their counts may be incomplete")


def _candidate(project):
    from daimon_briefing import refutations
    refutations.assert_ruling(
        subject="a candidate subject", verdict="a candidate verdict",
        scope="scope", evidence=["issue:1"], channel="cli-agent",
        project_dir=project)


def _plant(project, name, data):
    _bucket(project).mkdir(parents=True, exist_ok=True)
    with open(_bucket(project) / name, "ab") as handle:
        handle.write(data)


def test_the_foreign_column_is_declared_on_refutations_and_amendments():
    from daimon_briefing import surfaces
    for name in ("refutations.jsonl", "amendments.jsonl"):
        assert surfaces.bucket_ledger(name).foreign_read == (
            surfaces.ReadPosture.OPEN, surfaces.ReadPosture.NOTE,
            surfaces.ReadPosture.SKIP_SOURCE,
            surfaces.ReadPosture.SKIP_SOURCE)


@pytest.mark.parametrize("data,skip,degraded", [
    (None, False, False),
    (b'{"torn', False, True),
    (b"<<<<<<< HEAD\n", True, False),
    (b"\xff\xfe\n", True, False)])
def test_the_foreign_seam_applies_the_registry_column(
        tmp_checkpoint_dir, data, skip, degraded):
    _write(ELSE, ["words"])
    if data:
        _plant(ELSE, "refutations.jsonl", data)
    got = store.foreign_ledger(store.project_slug(ELSE), "refutations.jsonl")
    assert (got.skip, got.degraded) == (skip, degraded)


def test_foreign_counts_skip_a_bucket_with_an_unreadable_ledger(
        tmp_checkpoint_dir):
    from daimon_briefing import pending
    _write(OWN, ["ours"])
    _candidate(ELSE)
    _candidate(ELSE2)
    assert pending.foreign_counts_typed(project_dir=OWN).notes == ()
    assert pending.foreign_counts(project_dir=OWN) == {
        store.project_slug(ELSE): 1, store.project_slug(ELSE2): 1}
    _plant(ELSE2, "refutations.jsonl", b"<<<<<<< HEAD\n")
    got = pending.foreign_counts_typed(project_dir=OWN)
    assert got.counts == {store.project_slug(ELSE): 1}
    assert got.notes == (SKIPPED_ELSE,)
    assert pending.foreign_counts(project_dir=OWN) == got.counts


def test_foreign_counts_read_around_a_torn_ledger_and_say_so(
        tmp_checkpoint_dir):
    from daimon_briefing import pending
    _write(OWN, ["ours"])
    _candidate(ELSE)
    _plant(ELSE, "refutations.jsonl", b'{"torn')
    got = pending.foreign_counts_typed(project_dir=OWN)
    assert got.counts == {store.project_slug(ELSE): 1}
    assert got.notes == (TORN_ELSE,)


def test_an_unproven_foreign_requests_ledger_is_skipped_in_the_counts(
        tmp_checkpoint_dir):
    from daimon_briefing import pending
    _write(OWN, ["ours"])
    requests.open_request(to=store.project_slug(ELSE), ask="ping",
                          why="because", channel="cli-agent",
                          project_dir=SENDER)
    assert pending.foreign_counts(project_dir=OWN) == {
        store.project_slug(ELSE): 1}
    _plant(SENDER, "requests.jsonl", b"<<<<<<< HEAD\n")
    got = pending.foreign_counts_typed(project_dir=OWN)
    assert got.counts == {} and got.notes == (SKIPPED_ELSE,)


def test_the_own_bucket_is_never_counted_as_elsewhere(tmp_checkpoint_dir):
    from daimon_briefing import pending
    _write(OWN, ["ours"])
    _plant(OWN, "refutations.jsonl", b"<<<<<<< HEAD\n")
    assert pending.foreign_counts_typed(project_dir=OWN).notes == ()


def test_the_elsewhere_line_has_no_count_under_tenant_scope(
        tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import pending
    _write(OWN, ["ours"])
    _candidate(ELSE)
    _plant(ELSE, "amendments.jsonl", b"<<<<<<< HEAD\n")
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    got = pending.foreign_queues_typed(project_dir=OWN)
    assert got.notes == (
        "⚠ some other projects skipped: a ledger cannot be read",)


def test_foreign_queues_skip_an_unreadable_bucket_and_say_so(
        tmp_checkpoint_dir):
    from daimon_briefing import pending
    _write(OWN, ["ours"])
    _candidate(ELSE)
    _candidate(ELSE2)
    _plant(ELSE2, "refutations.jsonl", b"<<<<<<< HEAD\n")
    got = pending.foreign_queues_typed(project_dir=OWN)
    assert [slug for slug, _ in got.queues] == [store.project_slug(ELSE)]
    assert got.notes == (SKIPPED_ELSE,)
    assert [s for s, _ in pending.foreign_queues(project_dir=OWN)] == [
        store.project_slug(ELSE)]


def test_the_decision_count_line_carries_the_elsewhere_note(
        tmp_checkpoint_dir):
    from daimon_briefing import briefing
    _write(OWN, ["ours"])
    _candidate(ELSE)
    _candidate(ELSE2)
    _plant(ELSE2, "refutations.jsonl", b"<<<<<<< HEAD\n")
    got = briefing.decision_count_line(OWN)
    assert got.splitlines() == [
        "0 decisions waiting on you here (1 elsewhere) - daimon decide",
        SKIPPED_ELSE]


def test_the_decision_count_note_stands_alone_when_nothing_else_is_said(
        tmp_checkpoint_dir):
    from daimon_briefing import briefing
    _write(OWN, ["ours"])
    _candidate(ELSE2)
    _plant(ELSE2, "refutations.jsonl", b"<<<<<<< HEAD\n")
    assert briefing.decision_count_line(OWN) == SKIPPED_ELSE


def test_decide_all_projects_prints_the_note_after_the_blocks(
        tmp_checkpoint_dir, capsys):
    _write(OWN, ["ours"])
    _candidate(ELSE)
    _candidate(ELSE2)
    _plant(ELSE2, "refutations.jsonl", b"<<<<<<< HEAD\n")
    assert cli.main(["decide", "--project", OWN, "--all-projects"]) == 0
    lines = capsys.readouterr().out.rstrip().splitlines()
    assert lines[-1] == SKIPPED_ELSE
    assert any("Decisions waiting on you in" in ln for ln in lines)


def test_decide_without_the_flag_has_no_elsewhere_note_it_cannot_back(
        tmp_checkpoint_dir, capsys):
    _write(OWN, ["ours"])
    _candidate(ELSE)
    assert cli.main(["decide", "--project", OWN]) == 0
    assert "skipped" not in capsys.readouterr().out


# ---- 5d. owed, verdict and the status counts carry the join notes -----------


def _owed_world():
    """OWN has accepted an ask from SENDER (owed), and sent one of its own to
    ELSE (verdict panel), with a garbage requests ledger in a third bucket."""
    rid = requests.open_request(
        to=store.project_slug(OWN), ask="review the bar", why="it blocks us",
        channel="cli-agent", project_dir=SENDER)
    requests.accept(rid, channel="cli-tty", project_dir=OWN)
    requests.open_request(to=store.project_slug(ELSE), ask="our ask",
                          why="because", channel="cli-agent",
                          project_dir=OWN)
    _plant("/p/round-bad-sender", "requests.jsonl", b"<<<<<<< HEAD\n")
    return rid


def test_owed_and_verdict_renderables_carry_notes_only_when_there_are_some(
        tmp_checkpoint_dir):
    _owed_world()
    note = "⚠ 1 sender(s) skipped: a requests ledger cannot be read"
    assert requests.owed_renderable(OWN)["notes"] == (note,)
    assert requests.verdict_renderable(OWN)["notes"] == (note,)


def test_a_healthy_store_adds_no_notes_key(tmp_checkpoint_dir):
    _write(OWN, ["ours"])
    assert "notes" not in requests.owed_renderable(OWN)
    assert "notes" not in requests.verdict_renderable(OWN)


def test_the_owed_and_verdict_panels_print_the_notes(tmp_checkpoint_dir):
    from daimon_briefing import briefing
    _owed_world()
    note = "⚠ 1 sender(s) skipped: a requests ledger cannot be read"
    assert briefing.owed_panel_lines(OWN)[-1] == note
    verdict_lines, _cards = briefing.verdict_panel(OWN)
    assert note in verdict_lines


def test_a_brief_says_the_sender_note_once(tmp_checkpoint_dir, capsys):
    _owed_world()
    _write(OWN, ["our own decision"])
    assert cli.main(["brief", "--project", OWN]) == 0
    out = capsys.readouterr().out
    assert out.count("sender(s) skipped") == 1


def test_status_counts_gain_a_notes_tuple_and_status_prints_it(
        tmp_checkpoint_dir, capsys):
    _owed_world()
    got = requests.status_counts(OWN)
    assert got["notes"] == (
        "⚠ 1 sender(s) skipped: a requests ledger cannot be read",)
    assert {k: v for k, v in got.items() if k != "notes"} == {
        "open_sent": 1, "awaiting_you": 0}
    assert cli.main(["status", "--project", OWN]) in (0, 1)
    assert "1 sender(s) skipped" in capsys.readouterr().out
    assert cli.main(["status", "--project", OWN, "--json"]) in (0, 1)
    out = capsys.readouterr().out
    assert json.loads(out)["requests"] == {"open_sent": 1, "awaiting_you": 0}


def test_the_live_nudges_stay_as_they_are(tmp_checkpoint_dir):
    _owed_world()
    assert "notes" not in requests.owed_deliverable("sess-1", OWN)


def test_decide_all_projects_says_the_note_when_nothing_else_is_waiting(
        tmp_checkpoint_dir, capsys):
    _write(OWN, ["ours"])
    _candidate(ELSE2)
    _plant(ELSE2, "refutations.jsonl", b"<<<<<<< HEAD\n")
    assert cli.main(["decide", "--project", OWN, "--all-projects"]) == 0
    out = capsys.readouterr().out.rstrip().splitlines()
    assert out[-2:] == ["nothing waiting on you in any project", SKIPPED_ELSE]


def test_the_elsewhere_degraded_line_drops_the_count_under_tenant_scope(
        tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import pending
    _write(OWN, ["ours"])
    _candidate(ELSE)
    _plant(ELSE, "refutations.jsonl", b'{"torn')
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    assert pending.foreign_counts_typed(project_dir=OWN).notes == (
        "⚠ some other projects have a ledger with torn lines; "
        "their counts may be incomplete",)


def test_foreign_queues_skip_a_bucket_whose_requests_ledger_is_unproven(
        tmp_checkpoint_dir):
    from daimon_briefing import pending
    _write(OWN, ["ours"])
    requests.open_request(to=store.project_slug(ELSE), ask="ping",
                          why="because", channel="cli-agent",
                          project_dir=SENDER)
    _plant(SENDER, "requests.jsonl", b"<<<<<<< HEAD\n")
    got = pending.foreign_queues_typed(project_dir=OWN)
    assert got.queues == [] and got.notes == (SKIPPED_ELSE,)


def test_rich_status_prints_the_request_notes_too(monkeypatch, capsys):
    from daimon_briefing import render
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    data = {"project": "/p", "proj": {"exists": False}, "glob": {"exists": False},
            "same": False, "last": None, "outstanding": [], "identity": None,
            "health": None, "team": None,
            "requests": {"open_sent": 0, "awaiting_you": 0,
                         "notes": ("⚠ 1 sender(s) skipped: a requests ledger "
                                   "cannot be read",)}}
    try:
        render.render_status(data)
    except Exception:
        pytest.skip("the minimal status payload is not enough for rich")
    assert "1 sender(s) skipped" in capsys.readouterr().out


# ---- 10. cap once per surface -----------------------------------------------


def test_a_brief_caps_all_of_its_notes_once(tmp_checkpoint_dir, monkeypatch,
                                            capsys):
    """Seven ledger notes, forget-incomplete, a skipped sender and an
    unproven teammate (author-skipped and team-closed) are eleven candidate
    notes: five lines and one count. The decision-count line's own
    `elsewhere` note is part of that line, not of the note block."""
    _write(OWN, ["our own decision"])
    for name in ("events.jsonl", "refutations.jsonl", "amendments.jsonl",
                 "relations.jsonl", "request_policy_tombstones.jsonl",
                 "verification.jsonl", "trust.jsonl"):
        _plant(OWN, name, b"{\"torn" if name == "trust.jsonl"
               else b"<<<<<<< HEAD\n")
    _ask()
    _plant(SENDER, "requests.jsonl", b"<<<<<<< HEAD\n")
    adir = _teammate(monkeypatch)
    (adir / "tombstones.jsonl").write_bytes(b"<<<<<<< HEAD\n")
    assert cli.main(["brief", "--project", OWN, "--team"]) == 0
    warnings = [ln for ln in capsys.readouterr().out.splitlines()
                if ln.startswith("⚠") and "other project(s)" not in ln]
    capped = [ln for ln in warnings if "more notes; run: daimon status" in ln]
    assert len(capped) == 1
    assert warnings[-1] == capped[0]
    assert len(warnings) == 6, warnings
    assert warnings[-1] == "⚠ and 6 more notes; run: daimon status"


def test_a_brief_with_few_notes_is_not_capped(tmp_checkpoint_dir, capsys):
    _write(OWN, ["our own decision"])
    _ask()
    _plant(SENDER, "requests.jsonl", b"<<<<<<< HEAD\n")
    assert cli.main(["brief", "--project", OWN]) == 0
    out = capsys.readouterr().out
    assert out.count("1 sender(s) skipped") == 1
    assert "more notes" not in out


def test_cap_notes_remembers_what_it_capped():
    lines = [f"⚠ n{i}" for i in range(8)]
    capped = display.cap_notes(lines)
    assert len(capped) == 6 and display.all_notes(capped) == tuple(lines)
    merged = display.merge_notes(capped, ["⚠ n3", "⚠ extra"])
    assert display.all_notes(merged) == (*lines, "⚠ extra")
    assert merged[-1] == "⚠ and 4 more notes; run: daimon status"
    assert capped == tuple(capped)


# ---- 11. loose ends ----------------------------------------------------------


def test_queue_typed_reads_the_request_join_once(tmp_checkpoint_dir,
                                                 monkeypatch):
    from daimon_briefing import pending
    _write(OWN, ["ours"])
    _ask()
    calls = []
    real = requests.join
    monkeypatch.setattr(requests, "join",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    got = pending.queue_typed(project_dir=OWN)
    assert len(got.rows) == 1
    assert len(calls) == 1


def test_decide_reads_the_request_join_once(tmp_checkpoint_dir, monkeypatch,
                                            capsys):
    _write(OWN, ["ours"])
    _ask()
    calls = []
    real = requests.join
    monkeypatch.setattr(requests, "join",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    assert cli.main(["decide", "--project", OWN]) == 0
    assert len(calls) == 1
    assert "review the bar" in capsys.readouterr().out


def test_queue_with_notes_is_queue_and_queue_notes(tmp_checkpoint_dir):
    from daimon_briefing import pending
    _write(OWN, ["ours"])
    _ask()
    _plant(OWN, "trust.jsonl", b"<<<<<<< HEAD\n")
    result, notes = pending.queue_with_notes(project_dir=OWN)
    assert result == pending.queue(project_dir=OWN)
    assert notes == pending.queue_notes(project_dir=OWN)
    assert notes and "trust.jsonl is unreadable" in notes[0]


def test_ledger_states_can_be_narrowed_to_some_ledgers(tmp_checkpoint_dir):
    _write(OWN, ["ours"])
    got = view.ledger_states(OWN, ("trust.jsonl", "events.jsonl"))
    assert set(got) == {"trust.jsonl", "events.jsonl"}


def test_the_unused_author_helper_is_gone():
    assert not hasattr(store, "foreign_unproven_authors")


def test_jsonl_has_a_public_line_classifier():
    assert jsonl.classify_line('{"a": 1}') == ("row", {"a": 1})
    assert jsonl.classify_line('{"a": ')[0] == "torn"
    assert jsonl.classify_line("<<<<<<< HEAD")[0] == "garbage"
    assert jsonl.classify_line("[1, 2]")[0] == "garbage"
    assert jsonl.classify_line("\udcff not utf-8")[0] == "garbage"
    assert (jsonl.ROW, jsonl.TORN, jsonl.GARBAGE) == ("row", "torn", "garbage")


def test_store_reaches_the_classifier_only_by_its_public_name():
    import inspect
    source = inspect.getsource(store._tombstone_keys)
    assert "jsonl._" not in source and "jsonl.classify_line" in source


def test_prepare_survives_a_join_that_raises(tmp_checkpoint_dir, monkeypatch):
    import time
    from daimon_briefing import briefing
    _write(OWN, ["our own decision"])
    _plant(OWN, "amendments.jsonl", b"<<<<<<< HEAD\n")
    monkeypatch.setattr(requests, "join",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    got = briefing.prepare(OWN, time.time(), worldcheck_project=OWN)
    assert [n for n in got.notes if "amendments.jsonl" in n]


def test_a_panel_drops_a_warning_that_an_earlier_block_already_said():
    from daimon_briefing import briefing
    note = "⚠ 1 sender(s) skipped: a requests ledger cannot be read"
    req, ver, owed = briefing.drop_repeated_notes(
        ["Requests:", note], ["Decisions:", note], [note], notes=())
    assert req == ["Requests:", note]
    assert ver == ["Decisions:"] and owed == []


# ---- 15. recall sees a teammate's tombstone ledger break --------------------


def _rows(text="teammate"):
    import sqlite3
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        return [r[0] for r in conn.execute(
            "SELECT author FROM items WHERE text LIKE ?", (f"%{text}%",))]
    finally:
        conn.close()


def _meta(key):
    import sqlite3
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        return json.loads(dict(conn.execute("SELECT key, value FROM meta"))[key])
    finally:
        conn.close()


_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.fixture
def _fresh_limiter():
    recall._forced_at.clear()
    yield
    recall._forced_at.clear()


@pytest.mark.skipif(_ROOT, reason="root reads a 000 file")
def test_a_chmod_alone_changes_the_recall_fingerprint(tmp_checkpoint_dir,
                                                      monkeypatch):
    adir = _teammate(monkeypatch)
    ledger = adir / "tombstones.jsonl"
    ledger.write_bytes(b"")
    before = recall._fingerprint()
    time.sleep(0.01)
    ledger.chmod(0)
    try:
        assert recall._fingerprint() != before
    finally:
        ledger.chmod(0o644)


@pytest.mark.skipif(_ROOT, reason="root reads a 000 file")
def test_chmod_on_a_teammates_ledger_drops_their_rows_and_notes_it(
        tmp_checkpoint_dir, monkeypatch, _fresh_limiter):
    adir = _teammate(monkeypatch)
    ledger = adir / "tombstones.jsonl"
    ledger.write_bytes(b"")
    recall.rebuild()
    assert _rows() == ["grace"] and _meta("unproven_authors") == []
    ledger.chmod(0)
    try:
        got = recall.query("teammate", all_projects=True)
        assert got.rows == []
        assert "author-skipped" in got.notes
        assert _rows() == [] and _meta("unproven_authors") == ["grace"]
    finally:
        ledger.chmod(0o644)


def test_a_cleared_read_error_rebuilds_without_a_file_change(
        tmp_checkpoint_dir, monkeypatch, _fresh_limiter):
    adir = _teammate(monkeypatch)
    ledger = adir / "tombstones.jsonl"
    ledger.write_bytes(b"")
    real = jsonl.read
    _seam(monkeypatch, ledger, jsonl.Read(Health.UNREADABLE, [], detail="EIO"))
    recall.rebuild()
    assert _rows() == [] and _meta("unproven_authors") == ["grace"]
    monkeypatch.setattr(jsonl, "read", real)       # the error cleared; no stat moved
    got = recall.query("teammate", all_projects=True)
    assert [r["author"] for r in got.rows] == ["grace"]
    assert "author-skipped" not in got.notes
    assert _meta("unproven_authors") == []


def test_a_newly_unproven_author_is_dropped_by_a_forced_rebuild(
        tmp_checkpoint_dir, monkeypatch, _fresh_limiter):
    adir = _teammate(monkeypatch)
    ledger = adir / "tombstones.jsonl"
    ledger.write_bytes(b"")
    recall.rebuild()
    _seam(monkeypatch, ledger, jsonl.Read(Health.UNREADABLE, [], detail="EIO"))
    got = recall.query("teammate", all_projects=True)   # no stat moved
    assert got.rows == [] and "author-skipped" in got.notes
    assert _meta("unproven_authors") == ["grace"]


def test_with_the_window_spent_the_query_says_stale(tmp_checkpoint_dir,
                                                    monkeypatch,
                                                    _fresh_limiter):
    adir = _teammate(monkeypatch)
    ledger = adir / "tombstones.jsonl"
    ledger.write_bytes(b"")
    recall.rebuild()
    _seam(monkeypatch, ledger, jsonl.Read(Health.UNREADABLE, [], detail="EIO"))
    recall._forced_at[str(config.recall_db())] = recall._monotonic()
    got = recall.query("teammate", all_projects=True)
    assert "stale" in got.notes and "author-skipped" in got.notes
    assert _rows() == ["grace"]          # the old index, said to be behind


def test_an_index_without_the_authors_meta_key_is_left_to_the_fingerprint(
        tmp_checkpoint_dir, monkeypatch, _fresh_limiter):
    import sqlite3
    adir = _teammate(monkeypatch)
    (adir / "tombstones.jsonl").write_bytes(b"")
    recall.rebuild()
    conn = sqlite3.connect(str(config.recall_db()))
    conn.execute("DELETE FROM meta WHERE key = 'unproven_authors'")
    conn.commit()
    conn.close()
    got = recall.query("teammate", all_projects=True)
    assert [r["author"] for r in got.rows] == ["grace"]
    assert "stale" not in got.notes
