"""PR 12: `daimon forget --resume` finishes every phase for every standing
tombstone.

A forget that stopped halfway (a surface that could not be written, a crash
after the id row), and every forget made before the key row existed, leave a
standing tombstone with work behind it. Resume is the one verb that finishes
it: target-less, human only, idempotent, and it says what it could not reach.
"""
import pytest

from daimon_briefing import cli, config, jsonl, normalize, store
from daimon_briefing.surfaces import Writer

PROJECT = "/p/forget-resume"
VALUE = "zqxresume5512 the on-call rotation moves to the west coast team"
OTHER_VALUE = "zqxresume5513 the data retention window is ninety days"
KEEPER = "an unrelated decision that must survive"
KEY = normalize.content_key(VALUE)
OTHER_KEY = normalize.content_key(OTHER_VALUE)


def _item(text):
    return {"text": text, "trust": "inferred"}


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)


def _seed(*values):
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            _item(v) for v in (*values, KEEPER)]},
    }, project_dir=PROJECT, writer=Writer.HUMAN)
    stored = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                    admit=store.Admit.ANY)
    return {i["text"]: i["id"]
            for i in stored["working_context"]["recent_decisions"]}


def _legacy_tombstone(item_id, key):
    """A tombstone as it was before the key row: the id row alone, which is
    also what a forget that crashed right after its first write leaves."""
    assert store.append_event(
        item_id, f"forgotten:{key}", kind="tombstone", project_dir=PROJECT,
        allow_disabled=True, tombstone=True, writer=Writer.HUMAN)


def _resume(*extra):
    return cli.main(["forget", "--resume", "--project", PROJECT, *extra])


def _events_bytes():
    return store._events_path(store._resolved(PROJECT)).read_bytes()


# ---- the verb --------------------------------------------------------------

def test_resume_takes_no_target(tmp_checkpoint_dir, tty, capsys):
    assert cli.main(["forget", "some value", "--resume",
                     "--project", PROJECT]) == 2
    assert "takes no target" in capsys.readouterr().err


def test_resume_needs_a_terminal(tmp_checkpoint_dir, monkeypatch, capsys):
    ids = _seed(VALUE)
    _legacy_tombstone(ids[VALUE], KEY)
    before = _events_bytes()
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    assert _resume() == 1
    assert "requires an interactive terminal" in capsys.readouterr().out
    assert _events_bytes() == before


def test_only_goes_with_resume(tmp_checkpoint_dir, tty, capsys):
    assert cli.main(["forget", "x", "--only", "value",
                     "--project", PROJECT]) == 2
    assert "--only" in capsys.readouterr().err


def test_nothing_standing_is_nothing_to_finish(tmp_checkpoint_dir, tty, capsys):
    _seed(VALUE)
    assert _resume() == 0
    assert "no standing forget" in capsys.readouterr().out


# ---- finishing what a crash left ------------------------------------------------

def test_resume_after_a_crash_writes_the_key_row_and_scrubs_what_was_left(
        tmp_checkpoint_dir, tty, capsys):
    ids = _seed(VALUE)
    _legacy_tombstone(ids[VALUE], KEY)        # the id row landed, nothing else
    raw = (config.checkpoint_dir() / store.project_slug(PROJECT)
           / "latest.json")
    assert VALUE in raw.read_text(encoding="utf-8")
    assert _resume() == 0
    out = capsys.readouterr().out
    assert VALUE not in raw.read_text(encoding="utf-8")
    assert KEEPER in raw.read_text(encoding="utf-8")
    standing = store.project_standing(PROJECT)[KEY]
    assert len(standing.key_refs) == 1 and standing.ids == (ids[VALUE],)
    # a reopen on the id can no longer lift the value
    store.append_event(ids[VALUE], "reopen", project_dir=PROJECT,
                       writer=Writer.HUMAN)
    assert KEY in store.forgotten_content_keys(PROJECT)
    assert KEY not in out and VALUE not in out and "forgotten-key" not in out


def test_the_upgrade_of_legacy_tombstones_is_one_receipt_line_with_a_count(
        tmp_checkpoint_dir, tty, capsys):
    ids = _seed(VALUE, OTHER_VALUE)
    _legacy_tombstone(ids[VALUE], KEY)
    _legacy_tombstone(ids[OTHER_VALUE], OTHER_KEY)
    assert _resume() == 0
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if "key row" in ln]
    assert len(lines) == 1
    assert "wrote the key row for 2 standing tombstone(s)" in lines[0]
    assert "unforget" in lines[0]
    for key in (KEY, OTHER_KEY):
        assert len(store.project_standing(PROJECT)[key].key_refs) == 1


def test_a_second_resume_changes_nothing(tmp_checkpoint_dir, tty, capsys):
    ids = _seed(VALUE)
    _legacy_tombstone(ids[VALUE], KEY)
    assert _resume() == 0
    capsys.readouterr()
    before = _events_bytes()
    assert _resume() == 0
    out = capsys.readouterr().out
    assert "key row" not in out
    assert _events_bytes() == before


def test_a_lifted_forget_is_not_finished_again(tmp_checkpoint_dir, tty,
                                               capsys):
    ids = _seed(VALUE)
    _legacy_tombstone(ids[VALUE], KEY)
    store.append_event(ids[VALUE], "reopened", project_dir=PROJECT,
                       writer=Writer.HUMAN)       # the legacy lift
    capsys.readouterr()
    before = _events_bytes()
    assert _resume() == 0
    assert _events_bytes() == before
    assert KEY not in store.forgotten_content_keys(PROJECT)


# ---- the cure for a surface that could not be reached ---------------------------

def test_resume_finishes_a_step_that_could_not_write_the_first_time(
        tmp_checkpoint_dir, tty, capsys, locked):
    ids = _seed(VALUE)
    cache = config.checkpoint_dir() / ".chunk-cache"
    cache.mkdir(parents=True)
    leftover = cache / "c1.json"
    leftover.write_text("{}", encoding="utf-8")
    locked(cache)
    assert cli.main(["forget", ids[VALUE], "--project", PROJECT]) == 4
    capsys.readouterr()
    assert leftover.exists()
    assert _resume() == 4                 # still locked: said again, not lost
    out = capsys.readouterr().out
    assert "unreached: chunk cache " in out
    assert "run: daimon forget --resume" in out
    cache.chmod(0o700)                    # the cause is fixed
    assert _resume() == 0
    capsys.readouterr()
    assert not leftover.exists()


def test_only_publish_runs_the_publish_phase_and_nothing_else(
        tmp_checkpoint_dir, tty, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    ids = _seed(VALUE)
    _legacy_tombstone(ids[VALUE], KEY)
    monkeypatch.setenv("DAIMON_TEAM", "1")
    assert _resume("--only", "publish") == 0
    out = capsys.readouterr().out
    published = list(config.team_dir().rglob(store._TOMBSTONE_NAME))
    assert published and KEY in published[0].read_text(encoding="utf-8")
    raw = (config.checkpoint_dir() / store.project_slug(PROJECT)
           / "latest.json")
    assert VALUE in raw.read_text(encoding="utf-8"), "no scrub ran"
    assert "key row" not in out, "the publish phase alone writes no row"


def test_republish_is_the_publish_phase_of_a_resume(tmp_checkpoint_dir,
                                                    monkeypatch, capsys):
    """`--republish` keeps its behaviour: no target, no terminal needed, the
    same lines and the same exit codes."""
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    ids = _seed(VALUE)
    _legacy_tombstone(ids[VALUE], KEY)
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    assert cli.main(["forget", "--republish", "--project", PROJECT]) == 0
    assert "republished 1 tombstone(s) to the team sidecar" \
        in capsys.readouterr().out
    assert cli.main(["forget", "x", "--republish", "--project", PROJECT]) == 2
    capsys.readouterr()


# ---- the ruling gate holds for the finishing verb too -----------------------------

def test_resume_asks_before_it_removes_an_active_ruling(
        tmp_checkpoint_dir, tty, monkeypatch, capsys):
    from tests.test_rulings import PROJECT as RULING_PROJECT
    from tests.test_rulings import _rule
    subject = "internal numbers never appear in public posts"
    ruling_id = _rule(channel="cli-tty", ratified=True)
    key = normalize.content_key(subject)
    assert store.append_event(
        "i-gone", f"forgotten:{key}", kind="tombstone",
        project_dir=RULING_PROJECT, allow_disabled=True, tombstone=True,
        writer=Writer.HUMAN)
    from daimon_briefing import refutations
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert cli.main(["forget", "--resume", "--project", RULING_PROJECT]) == 1
    assert "not removed" in capsys.readouterr().out
    assert refutations.get(ruling_id, project_dir=RULING_PROJECT) is not None
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert cli.main(["forget", "--resume", "--project", RULING_PROJECT]) == 0
    capsys.readouterr()
    assert refutations.get(ruling_id, project_dir=RULING_PROJECT) is None


def test_the_rows_resume_writes_are_ordinary_admitted_rows(
        tmp_checkpoint_dir, tty, capsys):
    ids = _seed(VALUE)
    _legacy_tombstone(ids[VALUE], KEY)
    assert _resume() == 0
    capsys.readouterr()
    rows = jsonl.read(store._events_path(store._resolved(PROJECT))).rows
    keyrows = [r for r in rows if store.is_key_ref(r.get("item_ref"))]
    assert len(keyrows) == 1
    assert keyrows[0]["status"] == f"forgotten:{KEY}"
    assert keyrows[0]["kind"] == "tombstone"


def test_a_key_row_that_cannot_be_written_is_unreached(
        tmp_checkpoint_dir, tty, capsys):
    ids = _seed(VALUE)
    _legacy_tombstone(ids[VALUE], KEY)
    path = store._events_path(store._resolved(PROJECT))
    mode = path.stat().st_mode
    path.chmod(0o400)                       # readable, so the ledger is proven
    try:
        assert _resume() == 4
        out = capsys.readouterr().out
    finally:
        path.chmod(mode)
    assert "unreached: tombstone key row " in out
    assert store.project_standing(PROJECT)[KEY].key_refs == ()
    assert _resume() == 0                   # the cause is fixed: it finishes
    capsys.readouterr()
    assert len(store.project_standing(PROJECT)[KEY].key_refs) == 1


def test_only_runs_the_one_phase_named(tmp_checkpoint_dir, tty, capsys):
    ids = _seed(VALUE)
    _legacy_tombstone(ids[VALUE], KEY)
    raw = (config.checkpoint_dir() / store.project_slug(PROJECT)
           / "latest.json")
    assert _resume("--only", "value") == 0
    out = capsys.readouterr().out
    assert VALUE not in raw.read_text(encoding="utf-8")
    assert "purged" not in out, "the wholesale phase did not run"


def test_a_team_sidecar_that_cannot_be_written_is_said(
        tmp_checkpoint_dir, tty, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    ids = _seed(VALUE)
    _legacy_tombstone(ids[VALUE], KEY)
    monkeypatch.setenv("DAIMON_TEAM", "1")
    [adir] = store._own_team_dirs(PROJECT)
    adir.mkdir(parents=True)
    (adir / store._TOMBSTONE_NAME).write_bytes(b"<<<<<<< conflict\n")
    assert _resume() == 4
    out = capsys.readouterr().out
    assert "team sidecar(s) could not be written" in out
    assert "(exit 4)" in out


# ---- the previews write nothing -------------------------------------------------

def _plant_crash_log():
    logs = config.log_dir()
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / "serialize-crash.log"
    log.write_text("Traceback", encoding="utf-8")
    return log


def test_resume_dry_run_previews_and_writes_nothing(
        tmp_checkpoint_dir, tty, capsys):
    ids = _seed(VALUE)
    _legacy_tombstone(ids[VALUE], KEY)
    log = _plant_crash_log()
    before = _events_bytes()
    assert _resume("--dry-run") == 0
    out = capsys.readouterr().out
    assert "would finish 1 standing forget(s); nothing was written" in out
    assert "would write the key row for 1 standing tombstone(s)" in out
    assert "phases that would run: publish, value, id, wholesale, derived" in out
    assert _events_bytes() == before
    assert log.exists(), "no purge ran"
    raw = (config.checkpoint_dir() / store.project_slug(PROJECT)
           / "latest.json")
    assert VALUE in raw.read_text(encoding="utf-8")


def test_resume_dry_run_names_the_active_rulings_without_asking(
        tmp_checkpoint_dir, tty, monkeypatch, capsys):
    from tests.test_rulings import PROJECT as RULING_PROJECT
    from tests.test_rulings import _rule
    from daimon_briefing import refutations
    ruling_id = _rule(channel="cli-tty", ratified=True)
    key = normalize.content_key("internal numbers never appear in public posts")
    assert store.append_event(
        "i-gone", f"forgotten:{key}", kind="tombstone",
        project_dir=RULING_PROJECT, allow_disabled=True, tombstone=True,
        writer=Writer.HUMAN)

    def no_prompt(*_a, **_k):
        raise AssertionError("a preview asks nobody")

    monkeypatch.setattr("builtins.input", no_prompt)
    assert cli.main(["forget", "--resume", "--dry-run",
                     "--project", RULING_PROJECT]) == 0
    assert ruling_id in capsys.readouterr().out
    assert refutations.get(ruling_id, project_dir=RULING_PROJECT) is not None


def test_republish_dry_run_writes_nothing(tmp_checkpoint_dir, monkeypatch,
                                          capsys):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    ids = _seed(VALUE)
    _legacy_tombstone(ids[VALUE], KEY)
    monkeypatch.setenv("DAIMON_TEAM", "1")
    assert cli.main(["forget", "--republish", "--dry-run",
                     "--project", PROJECT]) == 0
    assert "would publish up to 1 standing tombstone(s)" \
        in capsys.readouterr().out
    assert list(config.team_dir().rglob(store._TOMBSTONE_NAME)) == []


def test_only_skips_the_legacy_key_row_upgrade(tmp_checkpoint_dir, tty, capsys):
    ids = _seed(VALUE)
    _legacy_tombstone(ids[VALUE], KEY)
    assert _resume("--only", "wholesale") == 0
    out = capsys.readouterr().out
    assert "key row" not in out
    assert store.project_standing(PROJECT)[KEY].key_refs == ()
