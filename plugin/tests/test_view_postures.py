"""`view` applies the registry's read postures once (#1132 PR 10a, D10.1).

Damaged bytes are planted by an independent byte writer; TRANSIENT and OS
errors, which bytes cannot express, go through the `jsonl.read` seam.
"""

import inspect


from daimon_briefing import (config, jsonl, refutations, store, surfaces, view)
from daimon_briefing.jsonl import Health

PROJECT = "/p/postures"
OTHER = "/p/postures-other"
SECRET = "the vault root token rotates on friday"


def _bucket(project=PROJECT):
    return config.checkpoint_dir() / store.project_slug(project)


def _plant(name, data: bytes, project=PROJECT):
    path = _bucket(project) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as handle:                 # not a package appender
        handle.write(data)
    return path


def _seam(monkeypatch, name, result, project=PROJECT):
    """Make `jsonl.read` answer `result` for one ledger of one bucket."""
    real = jsonl.read
    target = _bucket(project) / name

    def read(path, *a, **k):
        if path == target:
            return result
        return real(path, *a, **k)

    monkeypatch.setattr(jsonl, "read", read)


def _write(project, texts, sid="S1"):
    cp = {"session_id": sid, "created": "2026-08-01T00:00:00Z",
          "working_context": {"recent_decisions": [
              {"text": t, "trust": "inferred"} for t in texts],
              "open_questions": []},
          "epistemic_snapshot": {}}
    store.write_checkpoint(sid, cp, project_dir=project)


def test_the_hand_list_of_ledgers_is_gone():
    assert not hasattr(view, "LEDGERS")


def test_the_snapshot_reports_every_registry_ledger(tmp_checkpoint_dir):
    snap = view.snapshot(PROJECT)
    assert set(snap.health) == set(surfaces.bucket_ledger_names())
    assert set(snap.health.values()) == {Health.ABSENT}
    assert snap.notes() == ()


def test_a_degraded_ledger_is_noted_with_the_repair_hint(tmp_checkpoint_dir):
    store.append_event("o-aaaaaa", "resolved", project_dir=PROJECT)
    _plant("events.jsonl", b'{"kind": "resolution", "item_ref": "o-bb')
    snap = view.snapshot(PROJECT)
    assert snap.notes() == (
        "⚠ events.jsonl is degraded (torn); run: daimon ledger repair events",)
    assert snap.closed is False


def test_a_garbage_ledger_is_noted_with_the_repair_hint(tmp_checkpoint_dir):
    _plant("amendments.jsonl", b"<<<<<<< HEAD\n")
    snap = view.snapshot(PROJECT)
    assert snap.notes() == (
        "⚠ amendments.jsonl is unreadable (garbage); "
        "run: daimon ledger repair amendments",)


def test_an_undecodable_ledger_gets_the_repair_hint_too(tmp_checkpoint_dir):
    _plant("events.jsonl", b"\xff\xfe not utf-8\n")
    note = view.snapshot(PROJECT).notes()[0]
    assert note.endswith("run: daimon ledger repair events")


def test_the_trust_hint_names_the_trust_verb(tmp_checkpoint_dir):
    _plant("trust.jsonl", b"<<<<<<< HEAD\n")
    (note,) = view.snapshot(PROJECT).notes()
    assert note == ("⚠ trust.jsonl is unreadable (garbage); "
                    "run: daimon trust repair")


def test_an_os_error_names_permissions_and_status(tmp_checkpoint_dir,
                                                  monkeypatch):
    _seam(monkeypatch, "events.jsonl",
          jsonl.Read(Health.UNREADABLE, [], detail="EIO"))
    (note,) = view.snapshot(PROJECT).notes()
    assert note == ("⚠ events.jsonl is unreadable (EIO); "
                    "check permissions (EIO); run: daimon status")


def test_a_transient_ledger_says_retry(tmp_checkpoint_dir, monkeypatch):
    _seam(monkeypatch, "events.jsonl",
          jsonl.Read(Health.TRANSIENT, [], detail="EBUSY"))
    (note,) = view.snapshot(PROJECT).notes()
    assert note == "⚠ events.jsonl is transient (EBUSY); retry"


def test_verification_and_forget_hits_are_open_until_unreadable(
        tmp_checkpoint_dir):
    _plant("verification.jsonl", b'{"ts": "x", "item_ref": "o-a')
    _plant("forget-hits.jsonl", b'{"ts": "x", "key": "k')
    snap = view.snapshot(PROJECT)
    assert snap.health["verification.jsonl"] is Health.DEGRADED
    assert snap.notes() == ()
    _plant("verification.jsonl", b"\nnot json at all\n")
    (note,) = view.snapshot(PROJECT).notes()
    assert note.startswith("⚠ verification.jsonl is unreadable (garbage)")


def test_a_ledger_outside_the_old_five_is_now_noted(tmp_checkpoint_dir):
    _plant("relations.jsonl", b"<<<<<<< HEAD\n")
    _plant("request_policy_tombstones.jsonl", b'{"torn')
    notes = view.snapshot(PROJECT).notes()
    assert [n.split(" is ")[0] for n in notes] == [
        "⚠ relations.jsonl", "⚠ request_policy_tombstones.jsonl"]


def test_a_transient_trust_ledger_closes_the_full_snapshot(
        tmp_checkpoint_dir, monkeypatch):
    _seam(monkeypatch, "trust.jsonl",
          jsonl.Read(Health.TRANSIENT, [], detail="EBUSY"))
    snap = view.snapshot(PROJECT)
    assert snap.closed is True
    assert snap.notes() == ("⚠ trust.jsonl is transient (EBUSY); retry",)


def test_an_unreadable_trust_ledger_closes_it(tmp_checkpoint_dir):
    _plant("trust.jsonl", b"<<<<<<< HEAD\n")
    assert view.snapshot(PROJECT).closed is True


def test_an_unreadable_events_ledger_does_not_close_it(tmp_checkpoint_dir):
    _plant("events.jsonl", b"<<<<<<< HEAD\n")
    assert view.snapshot(PROJECT).closed is False


def test_notes_are_capped_at_five_lines_then_a_count(tmp_checkpoint_dir):
    for name in ("events.jsonl", "refutations.jsonl", "amendments.jsonl",
                 "requests.jsonl", "relations.jsonl",
                 "request_policy_tombstones.jsonl", "verification.jsonl"):
        _plant(name, b"<<<<<<< HEAD\n")
    notes = view.snapshot(PROJECT).notes()
    assert len(notes) == 6
    # seven ledger lines plus forget-incomplete (the events ledger is bad)
    assert notes[-1] == "⚠ and 3 more notes; run: daimon status"
    assert all(n.startswith("⚠ ") for n in notes)


def test_notes_never_carry_a_value_or_a_path(tmp_checkpoint_dir):
    store.append_event("o-aaaaaa", "resolved", project_dir=PROJECT)
    _plant("events.jsonl", SECRET.encode() + b"\n")
    joined = "\n".join(view.snapshot(PROJECT).notes())
    assert SECRET not in joined
    assert str(config.checkpoint_dir()) not in joined


# ---- rulings: unreadable comes from the read, not from a second strict read


def _ruling():
    refutations.assert_ruling(
        subject="public posts", verdict="no internal numbers",
        scope="publishing", evidence=["issue:693"], channel="cli-tty",
        ratified=True, project_dir=PROJECT)


def test_rulings_are_unreadable_when_the_read_could_not_scan(
        tmp_checkpoint_dir, monkeypatch):
    _ruling()
    _seam(monkeypatch, "refutations.jsonl",
          jsonl.Read(Health.UNREADABLE, [], detail="EIO"))
    snap = view.snapshot(PROJECT)
    assert snap.rulings.state == "unreadable" and snap.rulings.rows == []


def test_rulings_stay_read_when_only_a_garbage_line_is_present(
        tmp_checkpoint_dir):
    _ruling()
    _plant("refutations.jsonl", b"<<<<<<< HEAD\n")
    snap = view.snapshot(PROJECT)
    assert snap.rulings.state == "read" and len(snap.rulings.rows) == 1
    assert snap.health["refutations.jsonl"] is Health.UNREADABLE
    assert any("refutations.jsonl is unreadable" in n for n in snap.notes())


def test_rulings_are_unreadable_for_an_undecodable_line(tmp_checkpoint_dir):
    _ruling()
    _plant("refutations.jsonl", b"\xff\xfe\n")
    assert view.snapshot(PROJECT).rulings.state == "unreadable"


def test_events_has_no_strict_mode_any_more():
    assert "strict" not in inspect.signature(refutations.events).parameters


def test_the_standalone_rulings_read_agrees_with_the_snapshot(
        tmp_checkpoint_dir):
    from daimon_briefing import briefing
    _ruling()
    _plant("refutations.jsonl", b"\xff\xfe\n")
    assert briefing.rulings_read(PROJECT).state == "unreadable"
    assert briefing.rulings_read(PROJECT).state == view.snapshot(
        PROJECT).rulings.state


def test_request_policy_history_grants_nothing_when_the_ledger_is_unscannable(
        tmp_checkpoint_dir):
    refutations.assert_ruling(
        subject="info asks from the api project", verdict="answer them",
        scope="requests", evidence=["issue:961"], channel="cli-tty",
        ratified=True, project_dir=PROJECT)
    clean = refutations.request_policy_history(project_dir=PROJECT)
    assert refutations.request_policy_history(
        project_dir=PROJECT, rows=[], unscannable="EIO") == frozenset()
    assert clean == refutations.request_policy_history(project_dir=PROJECT)
    _plant("refutations.jsonl", b"\xff\xfe\n")
    assert refutations.request_policy_history(project_dir=PROJECT) == frozenset()


# ---- the light snapshot


def test_the_light_snapshot_carries_trust_and_events_health(
        tmp_checkpoint_dir):
    slug = store.project_slug(PROJECT)
    _plant("events.jsonl", b"<<<<<<< HEAD\n")
    _plant("trust.jsonl", b'{"torn')
    snap = view.judge(slug).snap
    assert snap.health["events.jsonl"] is Health.UNREADABLE
    assert snap.health["trust.jsonl"] is Health.DEGRADED
    assert snap.details["events.jsonl"] == "garbage"
    assert snap.closed is False


def test_the_light_snapshot_closes_on_transient_trust(tmp_checkpoint_dir,
                                                      monkeypatch):
    slug = store.project_slug(PROJECT)
    _seam(monkeypatch, "trust.jsonl",
          jsonl.Read(Health.TRANSIENT, [], detail="EBUSY"))
    judge = view.judge(slug)
    assert judge.closed is True
    assert judge.snap.unscannable["trust.jsonl"] == "EBUSY"


# ---- every registry ledger is read once per snapshot


def _own_reads(monkeypatch):
    bucket = _bucket()
    seen = []
    real = jsonl.read

    def spy(path, *a, **k):
        seen.append(path)
        return real(path, *a, **k)

    monkeypatch.setattr(jsonl, "read", spy)
    return lambda: [p for p in seen if p.parent == bucket]


def test_a_cold_snapshot_reads_events_twice_and_every_other_ledger_once(
        tmp_checkpoint_dir, monkeypatch):
    """The registry loop reads every ledger once. On a cold process the
    machine-wide forget walk then reads each bucket's events ledger again,
    this one's included: that second read is the true cost, named here, and
    it is paid once per process while the stats hold."""
    store.append_event("o-aaaaaa", "resolved", project_dir=PROJECT)
    own = _own_reads(monkeypatch)
    view.snapshot(PROJECT)
    names = sorted(p.name for p in own())
    expected = sorted([*surfaces.bucket_ledger_names(), "events.jsonl"])
    assert names == expected


def test_a_warm_snapshot_reads_each_registry_ledger_of_the_bucket_once(
        tmp_checkpoint_dir, monkeypatch):
    store.append_event("o-aaaaaa", "resolved", project_dir=PROJECT)
    view.forgotten_keys()                     # warm the machine-wide memo
    own = _own_reads(monkeypatch)
    view.snapshot(PROJECT)
    assert sorted(p.name for p in own()) == sorted(
        surfaces.bucket_ledger_names())


# ---- projects


def test_a_listed_bucket_knows_it_is_closed(tmp_checkpoint_dir):
    _write(PROJECT, ["a decision worth keeping"])
    _write(OTHER, ["another decision worth keeping"])
    _plant("trust.jsonl", b"<<<<<<< HEAD\n", project=OTHER)
    listed = {b.slug: b.closed for b in view.projects(None)}
    assert listed == {store.project_slug(PROJECT): False,
                      store.project_slug(OTHER): True}


def test_projects_notes_count_closed_buckets_outside_tenant_scope(
        tmp_checkpoint_dir):
    _write(PROJECT, ["a decision worth keeping"])
    _write(OTHER, ["another decision worth keeping"])
    _plant("trust.jsonl", b"<<<<<<< HEAD\n", project=OTHER)
    assert view.projects_notes(None) == (
        "⚠ 1 project(s) have a trust ledger that cannot be read",)


def test_projects_notes_carry_no_count_under_tenant_scope(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    _write(PROJECT, ["a decision worth keeping"])
    _plant("trust.jsonl", b"<<<<<<< HEAD\n")
    own = store.project_slug(PROJECT)
    assert view.projects_notes(own) == (
        "⚠ some projects have a trust ledger that cannot be read",)


def test_projects_notes_are_empty_when_nothing_is_closed(tmp_checkpoint_dir):
    _write(PROJECT, ["a decision worth keeping"])
    assert view.projects_notes(None) == ()


def test_the_hint_of_a_fold_that_raised_points_at_status_or_the_file():
    from daimon_briefing import display
    assert display.ledger_hint("events.jsonl", "unreadable",
                               "fold raised RuntimeError") == (
        "run: daimon status")
    assert display.ledger_hint("events.jsonl", "unreadable",
                               "fold raised RuntimeError",
                               on_status=True) == "check the ledger file"


def test_an_unresolvable_project_reads_no_amendments_and_no_requests():
    from daimon_briefing import amendments, requests
    assert amendments.events(project_dir="") == []
    assert requests.events(project_dir="") == []
