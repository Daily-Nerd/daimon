"""PR 12: one module runs every phase of a forget and reports every step it
could not vouch for.

Each failure here is a real one: a directory made unwritable with `chmod`, so
the step genuinely cannot write. The only substituted function is the one in
the isolation test, which has to raise something a real step never would.

ISOLATION, for anyone who runs `daimon forget`, `unforget` or `forget --resume`
by hand: the wholesale purges are machine-wide and keyed to HOME and
DAIMON_LOG_DIR, so redirect all three from one temp root, never only the
checkpoint dir:

    t=$(mktemp -d); HOME=$t/home DAIMON_LOG_DIR=$t/logs \
        DAIMON_CHECKPOINT_DIR=$t/store daimon forget ...

The test suite does this for every test through the autouse fixture in
conftest.py (DAIMON_LOG_DIR, the windsurf, checks, recall and team dirs).
"""
import pytest

from daimon_briefing import cli, config, forget, jsonl, normalize, store, surfaces
from daimon_briefing.surfaces import Writer

PROJECT = "/p/forget-run"
VALUE = "zqxrunstep4410 the payroll export token lives in vault七"
KEEPER = "an unrelated decision that must survive"
KEY = normalize.content_key(VALUE)


def _cp(sid, items):
    return {"session_id": sid, "created": "2026-08-01T00:00:00Z",
            "working_context": {"recent_decisions": [
                {"text": t, "trust": "inferred"} for t in items]}}


def _seed(sessions=("S1", "S2")):
    for sid in sessions:
        store.write_checkpoint(sid, _cp(sid, [VALUE, KEEPER]),
                               project_dir=PROJECT, writer=Writer.HUMAN)
    return config.checkpoint_dir() / store.project_slug(PROJECT)


def _forget(capsys):
    rc = cli.main(["forget", VALUE, "--project", PROJECT])
    return rc, capsys.readouterr().out


# ---- the steps come from the registry, in phase order ----------------------

def test_the_steps_are_the_registry_steps_once_each_in_phase_order():
    registry = []
    for row in surfaces.SURFACES:
        for step in row.steps:
            if step.fn not in registry:
                registry.append(step.fn)
    assert sorted(p.step.fn for p in forget.STEPS) == sorted(registry)
    order = [surfaces.PHASES.index(p.step.phase) for p in forget.STEPS]
    assert order == sorted(order)
    assert forget.STEPS[0].step.fn == "forget.write_tombstone"
    assert forget.STEPS[-1].step.phase == "derived"


def test_a_ledger_scope_runs_the_ledger_steps_and_no_tombstone():
    ledger = {p.step.fn for p in forget.STEPS if p.ledger}
    assert "refutations.forget_content_key" in ledger
    assert "relations.forget_item_id" in ledger
    assert "ledger_repair.forget_quarantined_lines" in ledger
    assert "store.scrub_content_key" not in ledger
    assert "store.publish_tombstone" not in ledger


# ---- a clean forget ---------------------------------------------------------

def test_a_clean_forget_reaches_every_step(tmp_checkpoint_dir, capsys):
    _seed()
    rc, out = _forget(capsys)
    assert rc == 0, out
    assert "unreached" not in out
    assert KEY in store.forgotten_content_keys(PROJECT)


def test_dry_run_writes_nothing(tmp_checkpoint_dir, capsys):
    bucket = _seed()
    before = {p: p.read_bytes() for p in bucket.rglob("*") if p.is_file()}
    rc = cli.main(["forget", VALUE, "--project", PROJECT, "--dry-run"])
    capsys.readouterr()
    assert rc == 0
    assert {p: p.read_bytes() for p in bucket.rglob("*") if p.is_file()} \
        == before
    assert KEY not in store.forgotten_content_keys(PROJECT)


# ---- every step that cannot write is said, and the rest still run -----------

def _lock_bucket(locked, bucket):
    # The ledger already exists, so the tombstone can still be appended to it;
    # what the locked directories stop is replacing a checkpoint file.
    store.append_event("i-seed", "reopened", project_dir=PROJECT,
                       writer=Writer.HUMAN)
    locked(bucket)
    locked(config.checkpoint_dir())


def _fill_chunk_cache(locked, bucket):
    cache = config.checkpoint_dir() / ".chunk-cache"
    cache.mkdir(parents=True)
    (cache / "c1.json").write_text("{}", encoding="utf-8")
    locked(cache)


def _fill_windsurf(locked, bucket):
    transcripts = config.windsurf_state_dir() / "transcripts"
    transcripts.mkdir(parents=True)
    (transcripts / "t1.md").write_text("a turn", encoding="utf-8")
    locked(transcripts)


def _fill_crash_log(locked, bucket):
    logs = config.log_dir()
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "serialize-crash.log").write_text("Traceback", encoding="utf-8")
    locked(logs)


def _fill_backend_log(locked, bucket):
    logs = config.log_dir()
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "backend-stderr.log").write_text("stderr", encoding="utf-8")
    locked(logs)


_LEGACY = ("2026-08-01T00:00:00 WARNING daimon_briefing.serializer: quote "
           "verification: downgraded verbatim->inferred: {text}\n")


def _fill_serialize_log(locked, bucket):
    logs = config.log_dir()
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "serialize.log").write_text(_LEGACY.format(text=VALUE),
                                        encoding="utf-8")
    locked(logs)


def _fill_team_mirror(locked, bucket):
    author_dirs = sorted((config.team_dir()).rglob("S1.json"))
    assert author_dirs, "the dual write mirrored a copy"
    locked(author_dirs[0].parent)


def _fill_recall(locked, bucket):
    locked(config.recall_db().parent)


UNWRITABLE = [
    ("checkpoint copies", _lock_bucket),
    ("chunk cache", _fill_chunk_cache),
    ("windsurf transcripts", _fill_windsurf),
    ("serializer crash log", _fill_crash_log),
    ("backend stderr log", _fill_backend_log),
    ("serialize.log", _fill_serialize_log),
    ("own team-mirror copies", _fill_team_mirror),
    ("recall index", _fill_recall),
]


@pytest.mark.parametrize("label,prepare", UNWRITABLE,
                         ids=[u[0] for u in UNWRITABLE])
def test_a_step_that_cannot_write_is_unreached_and_exits_4(
        label, prepare, tmp_checkpoint_dir, monkeypatch, capsys, locked):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    bucket = _seed()
    prepare(locked, bucket)
    rc, out = _forget(capsys)
    assert rc == 4, out
    assert f"unreached: {label} " in out
    assert "(exit 4)" in out
    assert "surface(s) were not reached" in out
    # the tombstone landed, so the value is withheld everywhere meanwhile
    assert KEY in store.forgotten_content_keys(PROJECT)


def test_one_unreached_step_does_not_stop_the_steps_after_it(
        tmp_checkpoint_dir, monkeypatch, capsys, locked):
    """The chunk cache cannot be emptied; the windsurf store, the logs and the
    ledgers behind it still are."""
    _seed()
    cache = config.checkpoint_dir() / ".chunk-cache"
    cache.mkdir(parents=True)
    (cache / "c1.json").write_text("{}", encoding="utf-8")
    locked(cache)
    transcripts = config.windsurf_state_dir() / "transcripts"
    transcripts.mkdir(parents=True)
    kept = transcripts / "t1.md"
    kept.write_text("a turn", encoding="utf-8")
    rc, out = _forget(capsys)
    assert rc == 4
    assert not kept.exists(), "a later step did not run"
    assert "unreached: chunk cache " in out


# ---- a step that raises -----------------------------------------------------

SECRET = "the staging password is correct horse battery staple"


def _boom(*_args, **_kwargs):
    raise ValueError(f"cannot handle {SECRET}")


def _fine(*_args, **_kwargs):
    return jsonl.Reached(("later",))


def test_a_step_that_raises_is_isolated_and_names_only_its_type(monkeypatch):
    first = surfaces.Step("tests.test_forget_run._boom", "value", "first",
                          call="bare")
    second = surfaces.Step("tests.test_forget_run._fine", "value", "second",
                           call="bare")
    monkeypatch.setattr(forget, "STEPS", (
        forget.Planned(first, "x/first", False),
        forget.Planned(second, "x/second", False)))
    monkeypatch.setattr(forget, "_resolve", lambda fn: {
        first.fn: _boom, second.fn: _fine}[fn])
    outcome = forget.run(forget.Target(KEY))
    assert [d.step.label for d in outcome.done] == ["first", "second"]
    [bad] = outcome.unreached
    assert bad.name == "first" and bad.detail == "ValueError"
    assert outcome.done[1].reached and outcome.done[1].removed == ("later",)
    assert SECRET not in repr(outcome)


def test_a_result_no_step_should_return_is_unreached_never_clean(monkeypatch):
    odd = surfaces.Step("tests.test_forget_run._odd", "value", "odd",
                        call="bare")
    monkeypatch.setattr(forget, "STEPS", (forget.Planned(odd, "x/odd", False),))
    monkeypatch.setattr(forget, "_resolve", lambda fn: lambda: object())
    outcome = forget.run(forget.Target(KEY))
    assert [u.detail for u in outcome.unreached] == ["TypeError"]


def test_a_tombstone_that_is_not_written_stops_the_run(tmp_checkpoint_dir):
    """An unknown project has no ledger: nothing is written and nothing after
    the tombstone runs."""
    outcome = forget.run(forget.Target(KEY, "i-1", ("i-1",), ""))
    assert outcome.refused is True
    assert [d.step.fn for d in outcome.done] == ["forget.write_tombstone"]


# ---- the sibling ids come from every surface --------------------------------

def test_siblings_are_read_from_every_surface_before_any_scrub(
        tmp_checkpoint_dir):
    for sid in ("S1", "S2"):
        cp = _cp(sid, [VALUE, KEEPER])
        cp["working_context"]["open_questions"] = [
            {"text": VALUE, "trust": "inferred"}]
        store.write_checkpoint(sid, cp, project_dir=PROJECT,
                               writer=Writer.HUMAN)
    held = {item["id"] for _p, _s, _k, item in store.items_for_project(PROJECT)
            if normalize.content_key(item.get("text") or "") == KEY}
    assert len(held) >= 1
    named = sorted(held)[0]
    got = forget.siblings(PROJECT, KEY, named)
    assert set(got) == held and list(got) == sorted(got)
    assert forget.siblings(PROJECT, KEY, "r-not-an-item") == tuple(
        sorted(held | {"r-not-an-item"}))


def _nothing(*_args, **_kwargs):
    return None


def _a_bare_list(*_args, **_kwargs):
    return []


@pytest.mark.parametrize("stub", [_nothing, _a_bare_list],
                         ids=["none", "bare-list"])
def test_a_step_that_judges_nothing_is_unreached(monkeypatch, stub):
    """Every step returns a `Reached`; a bare list or nothing says nothing
    about the surface and is never taken as reached (scar 0135)."""
    quiet = surfaces.Step("tests.test_forget_run._nothing", "value", "quiet",
                          call="bare")
    monkeypatch.setattr(forget, "STEPS", (forget.Planned(quiet, "x/q", False),))
    monkeypatch.setattr(forget, "_resolve", lambda fn: stub)
    outcome = forget.run(forget.Target(KEY))
    assert [u.detail for u in outcome.unreached] == ["unjudged"]


def test_the_ruling_gate_is_inside_run_and_refuses_without_an_answer(
        tmp_checkpoint_dir):
    """A direct call with an ACTIVE ruling in reach and no callable writes
    nothing and returns the refusal; the callable's answer is honoured."""
    from tests.test_rulings import PROJECT as RULING_PROJECT
    from tests.test_rulings import _rule
    subject = "internal numbers never appear in public posts"
    ruling_id = _rule(channel="cli-tty", ratified=True)
    key = normalize.content_key(subject)
    target = forget.Target(key, "i-gone", ("i-gone",), RULING_PROJECT)
    bucket = config.checkpoint_dir() / store.project_slug(RULING_PROJECT)

    def snapshot():
        return {p: p.read_bytes() for p in bucket.rglob("*") if p.is_file()}

    bytes_before = snapshot()
    outcome = forget.run(target)
    assert outcome.refusal.startswith("refused: this would remove ACTIVE")
    assert outcome.done == []
    assert snapshot() == bytes_before
    declined = forget.run(target, approve_rulings=lambda _r: "not removed")
    assert declined.refusal == "not removed" and declined.done == []
    from daimon_briefing import refutations
    assert refutations.get(ruling_id, project_dir=RULING_PROJECT) is not None
    # without the value phase there is nothing to ask
    only_wholesale = forget.run(target, phases={"derived"})
    assert only_wholesale.refusal == ""


def test_an_outcome_is_none_for_a_step_that_did_not_run():
    assert forget.Outcome([]).get("store.scrub_content_key") is None
