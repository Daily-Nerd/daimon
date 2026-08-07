"""#605: the serializer crash sink, inside the deletion contract.

`logs/serialize-crash.log` is the detached serialize child's RAW stderr fd —
_daimon_hook_lib.spawn_serialize points it there, and the Windsurf finalizer
arms its sleeper the same way. An uncaught traceback carries whatever the
crashing frame held: the exception message, a repr'd argument, a quoted
config line. Nothing ever removed those bytes. forget did not walk the file,
no reaper bounded it, and `status` redacted the tail on READ (#513) while the
original stayed on disk underneath.

Three parts, the shape #607 settled on for the Windsurf transcript store:

  * a WHOLESALE purge at forget — the tombstone is a canonical HASH (#321),
    so no component downstream holds the plaintext a substring search of a
    traceback would need, and detection is impossible by construction;
  * secret redaction at CAPTURE, so the excepthook stops writing to a file
    nobody scrubs the very thing redaction exists to catch (#104);
  * a byte cap at the write seam, so what accumulates between forgets is
    bounded rather than unbounded-forever.

The canary below uses redact.py's aws-key shape — the same synthetic literal
test_redact.py uses. A canary shaped like a vendor prefix this install does
not issue would prove nothing about redaction (scar: a `sk-proj` grep passed
on every non-OpenAI install and then matched its own instructions).
"""
import contextlib
import importlib.util
import io
import os
import re
import subprocess
import sys
import time
import types
from pathlib import Path

from daimon_briefing import cli, config, store, surfaces

PROJECT = "/p/crash-log"
CANARY = "zqxcrashcanary4417 the migration lock is held by the old worker"
KEEPER = "an unrelated decision that must survive"
SECRET = "AKIAIOSFODNN7EXAMPLE"


def _crash_path() -> Path:
    """Where the DELETER looks — cli status reads this exact expression."""
    return config.log_dir() / "serialize-crash.log"


def _lib():
    """The shared hook library, loaded under its own module name (it is a
    standalone script the package cannot import)."""
    src = (Path(__file__).parent.parent / "daimon_briefing" / "_hooks"
           / "_daimon_hook_lib.py")
    spec = importlib.util.spec_from_file_location("_crash_hook_lib_under_test",
                                                  src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hook_module():
    """The Windsurf adapter — the crash log's SECOND writer."""
    src = (Path(__file__).parent.parent / "daimon_briefing" / "_hooks"
           / "daimon-windsurf-hooks.py")
    spec = importlib.util.spec_from_file_location("_ws_hook_crash_under_test",
                                                  src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_subprocess(recorded):
    """subprocess stand-in for the two spawn seams: records the Popen kwargs
    (so the stderr fd can be asserted) and never starts a real child."""
    def popen(*args, **kwargs):
        recorded.append(kwargs)
        return None
    return types.SimpleNamespace(popen=None, Popen=popen,
                                 DEVNULL=subprocess.DEVNULL)


def _seed_crash_log(text: str | None = None) -> Path:
    path = _crash_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        text if text is not None else
        "--- crash 2026-08-06T00:00:00Z pid=1 cmd=serialize ---\n"
        "Traceback (most recent call last):\n"
        '  File "serializer.py", line 1, in <module>\n'
        f"RuntimeError: {CANARY}\n",
        encoding="utf-8")
    return path


def _write_checkpoint():
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": CANARY, "trust": "inferred"},
            {"text": KEEPER, "trust": "inferred"}]},
    }, project_dir=PROJECT)


# ---- the writer and the deleter must agree on WHERE ----------------------


def test_hook_and_package_resolve_the_same_crash_log(tmp_path, monkeypatch):
    """The #607 split, one directory over: the hook hardcoded ~/.daimon while
    the CLI honored the override, so with the var set the child kept writing
    tracebacks into one directory while the purge reported cleanly on an
    empty other one. A zero-file purge says nothing, so the divergence would
    be silent. The moment this file entered the deletion contract, the
    writer had to read the same var the deleter does."""
    lib = _lib()
    override = tmp_path / "elsewhere"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(override))
    assert config.log_dir() == override
    assert lib.crash_log_path() == override / "serialize-crash.log"


def test_both_sides_default_under_the_real_daimon_home(tmp_path, monkeypatch):
    """The DEFAULT path is the field path — with the var redirected suite-wide
    by conftest, nothing else asserts it, and a typo in either default is
    otherwise invisible. Resolved per CALL on both sides, so a home that
    moves (a test, a hook running under a different HOME than the CLI) cannot
    split the writer from the deleter."""
    lib = _lib()
    monkeypatch.delenv("DAIMON_LOG_DIR", raising=False)
    monkeypatch.setenv("DAIMON_ENV_FILE", str(tmp_path / "no-such-env-file"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    expected = tmp_path / ".daimon" / "logs" / "serialize-crash.log"
    assert config.log_dir() / "serialize-crash.log" == expected
    assert lib.crash_log_path() == expected


def test_the_env_file_alone_still_lands_both_sides_in_one_place(
        tmp_path, monkeypatch):
    """The hook read the PROCESS env only while config._get falls back to
    ~/.daimon/env (scar 0036). That file is not an exotic configuration — it
    exists precisely because a GUI-launched host inherits no shell profile,
    so it is the channel a real install uses. Set there and nowhere else, the
    child wrote its tracebacks to ~/.daimon/logs while forget purged the
    configured directory and reported a clean zero: permanent residue, under
    a registry entry claiming the surface is reachable."""
    lib = _lib()
    monkeypatch.delenv("DAIMON_LOG_DIR", raising=False)
    custom = tmp_path / "custom-logs"
    env_file = Path(os.environ["DAIMON_ENV_FILE"])
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(f"export DAIMON_LOG_DIR={custom}\n", encoding="utf-8")
    assert config.log_dir() == custom, "fixture never configured the deleter"
    assert lib.crash_log_path() == custom / "serialize-crash.log"


def test_env_file_only_config_is_written_and_purged_end_to_end(
        tmp_checkpoint_dir, tmp_path, monkeypatch):
    """The path assertion above is the unit; this is the claim that matters —
    what the WRITER actually created is what the PURGE actually removes."""
    lib = _lib()
    monkeypatch.delenv("DAIMON_LOG_DIR", raising=False)
    custom = tmp_path / "custom-logs"
    env_file = Path(os.environ["DAIMON_ENV_FILE"])
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(f"DAIMON_LOG_DIR={custom}\n", encoding="utf-8")
    monkeypatch.setattr(lib, "subprocess", _fake_subprocess([]))
    lib.spawn_serialize("daimon", "/t/x.jsonl", None)
    written = custom / "serialize-crash.log"
    assert written.exists(), "the writer missed the configured directory"
    written.write_text(
        "--- crash 2026-08-06T00:00:00Z pid=1 cmd=serialize ---\n"
        f"RuntimeError: {CANARY}\n", encoding="utf-8")
    _write_checkpoint()
    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0
    assert not written.exists(), "the purge looked somewhere else"


def test_writer_and_deleter_agree_over_the_whole_probe_table(
        tmp_path, monkeypatch):
    """Behavioral equality, the idiom hung_after_seconds already uses: every
    probe asserts identity with config.log_dir() rather than with a literal
    this test would also have to get right.

    The strip axis is load-bearing and was wrong. config.log_dir() does NOT
    strip, so "  /tmp/dlogs  " names a directory whose name carries spaces
    and "   " is a RELATIVE directory named three spaces. A writer that
    stripped resolved both somewhere the purge never looks — the same split
    as the env-file gap, reached through a different door."""
    lib = _lib()
    for raw in (None, "", "   ", "  /tmp/dlogs  ", "/tmp/dlogs", "~/dlogs"):
        monkeypatch.delenv("DAIMON_LOG_DIR", raising=False)
        if raw is not None:
            monkeypatch.setenv("DAIMON_LOG_DIR", raw)
        assert lib.crash_log_path() == (
            config.log_dir() / "serialize-crash.log"), repr(raw)

    # The env-file PARSER is a second copy of config._file_values, so it can
    # drift line-form by line-form. Each of these discriminates one rule:
    # quoting, the export prefix, whitespace, comments, an empty value.
    monkeypatch.delenv("DAIMON_LOG_DIR", raising=False)
    env_file = Path(os.environ["DAIMON_ENV_FILE"])
    env_file.parent.mkdir(parents=True, exist_ok=True)
    resolved = set()
    for line in (f"DAIMON_LOG_DIR={tmp_path}/plain",
                 f'DAIMON_LOG_DIR="{tmp_path}/quoted"',
                 f"DAIMON_LOG_DIR='{tmp_path}/single'",
                 f"export DAIMON_LOG_DIR={tmp_path}/exported",
                 f"  export   DAIMON_LOG_DIR =  {tmp_path}/spaced  ",
                 f"# DAIMON_LOG_DIR={tmp_path}/commented",
                 "DAIMON_LOG_DIR=",
                 "DAIMON_LOG_DIR=~/tilde",
                 f"NOT_THE_VAR={tmp_path}/other",
                 f"DAIMON_LOG_DIR={tmp_path}/first\nDAIMON_LOG_DIR={tmp_path}/last"):
        env_file.write_text(line + "\n", encoding="utf-8")
        assert lib.crash_log_path() == (
            config.log_dir() / "serialize-crash.log"), line
        resolved.add(lib.crash_log_path())
    assert len(resolved) > 1, \
        "every probe resolved alike — the env file was never read at all"


# ---- purge on forget ------------------------------------------------------


def test_forget_purges_the_crash_log(tmp_checkpoint_dir):
    path = _seed_crash_log()
    assert CANARY in path.read_text(encoding="utf-8"), "fixture wrote no canary"
    _write_checkpoint()
    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0
    assert not path.exists(), "the traceback daimon wrote must go"


def test_forget_dry_run_never_purges(tmp_checkpoint_dir):
    """The higher-blast-radius call site: a purge inserted into the dry-run
    branch deletes on a command whose whole promise is that it does not."""
    path = _seed_crash_log()
    _write_checkpoint()
    assert cli.main(["forget", CANARY, "--project", PROJECT, "--dry-run"]) == 0
    assert path.exists()


def test_a_refused_forget_never_purges(tmp_checkpoint_dir):
    path = _seed_crash_log()
    _write_checkpoint()
    assert cli.main(["forget", "no such value here", "--project", PROJECT]) == 1
    assert path.exists()


def test_purge_reports_count_and_never_raises(tmp_checkpoint_dir):
    _seed_crash_log()
    assert store.purge_crash_log() == (1, None)
    # vacuous purge on a machine whose serializer never crashed
    assert store.purge_crash_log() == (0, None)


def test_purge_never_unlinks_through_a_symlink(tmp_checkpoint_dir, tmp_path):
    """A symlink standing in for the crash sink would have the purge delete a
    file daimon never created. The reading path (_crash_log_info) only ever
    reads a tail; the DELETING path must not be laxer."""
    outside = tmp_path / "my-notes"
    outside.mkdir()
    kept = outside / "important.log"
    kept.write_text("a user file", encoding="utf-8")
    path = _crash_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(kept)
    purged, err = store.purge_crash_log()
    assert purged == 0
    assert err is not None, "a refusal the user never hears about is a leak"
    assert kept.exists(), "unlinked a file daimon did not write"


def test_a_directory_named_like_the_crash_log_is_never_unlinked(
        tmp_checkpoint_dir):
    """The sink is a fixed NAME, not a type check. A directory sitting there
    must be refused before unlink() sees it, and refused OUT LOUD — a zero
    with no error reads as "the machine never crashed"."""
    decoy = _crash_path()
    decoy.mkdir(parents=True)
    (decoy / "inside.txt").write_text("not daimon's to delete",
                                      encoding="utf-8")
    purged, err = store.purge_crash_log()
    assert purged == 0 and err is not None
    assert (decoy / "inside.txt").exists()


def test_purge_reports_a_file_it_could_not_remove(tmp_checkpoint_dir):
    """The count is a privacy CLAIM — "the tracebacks are gone" — so a file
    that survived a read-only log dir must not be inside it, and the error
    must surface for the forget warning to carry."""
    path = _seed_crash_log()
    path.parent.chmod(0o500)          # readable and stat-able, not writable
    try:
        purged, err = store.purge_crash_log()
    finally:
        path.parent.chmod(0o700)
    assert purged == 0, "only a file that actually went may be counted"
    assert err is not None, "a silent partial purge reads as a clean one"
    assert CANARY in path.read_text(encoding="utf-8")


def test_a_log_dir_it_cannot_see_is_never_reported_clean(tmp_checkpoint_dir):
    """Path.is_file() answers False for "absent" and for "I am not allowed to
    look" alike, and the second one purging silently is a clean-purge claim
    made without evidence. lstat separates them: absent is (0, None), blind
    is an error the forget warning carries."""
    path = _seed_crash_log()
    path.parent.chmod(0o000)          # neither readable nor searchable
    try:
        purged, err = store.purge_crash_log()
    finally:
        path.parent.chmod(0o700)
    assert purged == 0
    assert err is not None, "a blind purge must not read as an empty one"
    assert CANARY in path.read_text(encoding="utf-8")


def test_a_corrupt_env_file_fails_the_purge_closed(monkeypatch, tmp_path):
    """config._file_values catches OSError, but a non-UTF-8 env file raises
    UnicodeDecodeError — a ValueError — out of read_text, past that net and
    out of config.log_dir(). The purge's NEVER-raises contract has to hold
    anyway: resolution failure is (0, err), not an exception. The writer
    resolves through the same accessors and raises identically, so nothing
    was written where this purge cannot look."""
    monkeypatch.delenv("DAIMON_LOG_DIR", raising=False)
    env_file = tmp_path / "env"
    env_file.write_bytes(b"DAIMON_LOG_DIR=\xff\xfe broken\n")
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
    purged, err = store.purge_crash_log()
    assert purged == 0
    assert err is not None, "a purge that cannot even resolve its target " \
                            "must not read as a clean zero"


def test_forget_survives_a_failed_purge(tmp_checkpoint_dir, monkeypatch,
                                        capsys):
    """The belief-state deletion is the primary contract — a failed purge is
    reported honestly, never fatal (the #422 posture)."""
    _seed_crash_log()
    _write_checkpoint()
    monkeypatch.setattr(store, "purge_crash_log", lambda: (0, "disk on fire"))
    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0
    assert "disk on fire" in capsys.readouterr().out


def test_forget_survives_a_purge_that_raises(tmp_checkpoint_dir, monkeypatch,
                                             capsys):
    """purge_crash_log promises a tuple and never an exception, so the belt
    around the call site reads as dead code — it is not. It is what keeps a
    future bug in the purge from taking the SCRUB down with it, and the
    scrub is the contract the user actually invoked."""
    _seed_crash_log()
    _write_checkpoint()

    def boom():
        raise RuntimeError("purge exploded")

    monkeypatch.setattr(store, "purge_crash_log", boom)
    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "purge exploded" in out, "a swallowed failure is an invisible leak"
    latest = store.read_latest(project_dir=PROJECT, fallback=False)
    texts = [i["text"] for i
             in latest["working_context"]["recent_decisions"]]
    assert texts == [KEEPER], "the checkpoint scrub must survive the blowup"


def test_forget_states_the_purge_is_machine_wide(tmp_checkpoint_dir, capsys):
    """One crash log per machine, not per project — a forget in ANY project
    removes every serializer traceback on the box. That is the only option
    (the file carries no project attribution), so it must be said out loud
    rather than discovered."""
    _seed_crash_log()
    _write_checkpoint()
    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0
    out = capsys.readouterr().out.lower()
    assert "crash log" in out
    assert "all projects" in out or "machine-wide" in out


# ---- redaction at capture -------------------------------------------------


def test_excepthook_redacts_the_traceback_and_keeps_the_header(capsys):
    """#513 redacted this file on READ while the disk kept the original. The
    only process that can scrub it at WRITE is the crashing one — nothing
    else sits in the path between the child's stderr fd and the file."""
    try:
        raise RuntimeError(f"connecting with {SECRET} failed")
    except RuntimeError:
        exc_info = sys.exc_info()
    cli._crash_stamp_excepthook(*exc_info)
    err = capsys.readouterr().err
    assert re.fullmatch(
        r"--- crash \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z pid=\d+ "
        r"cmd=\S+ ---", err.splitlines()[0]), "the #92 header shape is parsed"
    assert SECRET not in err
    assert "[redacted:aws-key]" in err
    assert "RuntimeError: connecting with" in err, "the crash stays readable"


def test_the_redacted_block_still_parses_as_a_crash(tmp_checkpoint_dir):
    """status reads this file back through _crash_log_info, which finds the
    exception line by INDENTATION (traceback frames are indented, the raising
    line is not). Formatting the traceback ourselves must not disturb that."""
    try:
        raise RuntimeError(f"secret {SECRET} in the message")
    except RuntimeError:
        exc_info = sys.exc_info()
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        cli._crash_stamp_excepthook(*exc_info)
    path = _seed_crash_log(buf.getvalue())
    # The DISK bytes are the new guarantee. _crash_log_info redacts its own
    # output (#513), so asserting on last_line alone would pass unchanged
    # against the old excepthook and prove nothing.
    assert SECRET not in path.read_text(encoding="utf-8")
    info = cli._crash_log_info(path, now=time.time())
    assert info is not None, "the header must still register as a crash"
    assert info["last_line"].startswith("RuntimeError: secret ")


def test_excepthook_falls_back_when_redaction_breaks(monkeypatch, capsys):
    """Fail-open, redact.py's own posture: a swallowed traceback is a crash
    nobody can diagnose, which costs more than the redaction it was buying."""
    def boom(_s):
        raise RuntimeError("redaction exploded")

    monkeypatch.setattr(cli.redact, "redact_text", boom)
    try:
        raise ValueError("the traceback must survive")
    except ValueError:
        exc_info = sys.exc_info()
    cli._crash_stamp_excepthook(*exc_info)
    err = capsys.readouterr().err
    assert err.splitlines()[0].startswith("--- crash ")
    assert "ValueError: the traceback must survive" in err


# ---- the byte cap at the write seam --------------------------------------


def test_spawn_trims_an_oversized_crash_log_and_keeps_the_tail(
        tmp_checkpoint_dir, monkeypatch):
    """Between forgets the file grew forever. The cap bounds the residue —
    and it must keep the TAIL, because status reports the LAST crash and the
    newest traceback is the one anyone is debugging."""
    lib = _lib()
    path = _crash_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    newest = b"RuntimeError: the newest crash line\n"
    path.write_bytes(b"o" * (lib.CRASH_LOG_MAX_BYTES + 4096) + newest)
    recorded: list = []
    monkeypatch.setattr(lib, "subprocess", _fake_subprocess(recorded))
    lib.spawn_serialize("daimon", "/t/x.jsonl", None)
    data = path.read_bytes()
    assert len(data) <= lib.CRASH_LOG_KEEP_BYTES
    assert data.endswith(newest), "the newest crash is the one status reports"
    assert recorded and recorded[0]["stderr"] is not None, "child still spawned"


def test_a_crash_log_under_the_cap_is_left_alone(tmp_checkpoint_dir,
                                                 monkeypatch):
    lib = _lib()
    path = _crash_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"--- crash 2026-08-06T00:00:00Z pid=1 cmd=serialize ---\n")
    before = path.read_bytes()
    recorded: list = []
    monkeypatch.setattr(lib, "subprocess", _fake_subprocess(recorded))
    lib.spawn_serialize("daimon", "/t/x.jsonl", None)
    assert path.read_bytes() == before, "trimming is not truncating"


def test_a_trim_that_cannot_run_never_blocks_the_spawn(tmp_path):
    """The log is diagnostics; a capture is the product. Trimming must fail
    silently rather than raise into a spawn seam whose whole contract is
    that it starts the child."""
    lib = _lib()
    lib.trim_crash_log(tmp_path / "no-such-dir" / "serialize-crash.log")


def test_the_finalizer_writes_where_forget_looks(tmp_checkpoint_dir,
                                                 monkeypatch):
    """The Windsurf sleeper is the crash log's SECOND writer and had its own
    hardcoded ~/.daimon/logs — the writer/deleter divergence #607 closed for
    the transcript store, still open one directory over."""
    hook = _hook_module()
    recorded: list = []
    monkeypatch.setattr(hook, "subprocess", _fake_subprocess(recorded))
    hook._arm_finalizer("traj-1", Path("/t/traj-1.md"))
    assert recorded, "the finalizer never armed"
    assert _crash_path().exists(), \
        "the sleeper's stderr must land where the purge looks"


# ---- the registry stops calling this a gap -------------------------------


def test_registry_declares_the_crash_log_reachable():
    s = surfaces.match("logs/serialize-crash.log")
    assert s is not None
    assert s.plaintext is True and s.delete == "wholesale-purge"
    assert s.walker == "forget"
    assert not any(x.issue == "#605" for x in surfaces.SURFACES), \
        "#605 is closed — no entry may still cite it as an open gap"
    assert surfaces.match("logs/serialize.log").delete == "exempt-no-plaintext"
