"""#939: a serialize outcome a container host can actually reach.

`spawn_serialize` detaches the child and discards both streams: stdout to
DEVNULL (success and skip lines destroyed outright) and stderr to
`crash_log_path()`, a separate file. The only durable record is
`serialize.log`, which a container runtime does not collect. So an operator
grepping the container log for a capture record finds nothing, and a healthy
capture is byte-identical to a dead feature.

DAIMON_LOG_STDOUT opts a host into an inherited stdout, which the runtime
already captures. Off by default: on a terminal host an inherited descriptor
prints capture results into the user's shell minutes after the session ended.

Stdout, not stderr, deliberately. #194 moved serializer/llm diagnostics OFF
stderr because stderr lands in serialize-crash.log and misreads as a crash.
That separation is load-bearing and every test here asserts it survives.
"""
import importlib.util
import subprocess
import types
from pathlib import Path

import pytest


def _lib():
    src = (Path(__file__).parent.parent / "daimon_briefing" / "_hooks"
           / "_daimon_hook_lib.py")
    spec = importlib.util.spec_from_file_location("_stdout_hook_lib_under_test",
                                                  src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_subprocess(recorded):
    def popen(*args, **kwargs):
        recorded.append(kwargs)
        return None
    return types.SimpleNamespace(popen=None, Popen=popen,
                                 DEVNULL=subprocess.DEVNULL)


def _spawn(monkeypatch, tmp_path):
    """Spawn once with the seams faked; return the recorded Popen kwargs."""
    lib = _lib()
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "logs"))
    recorded: list = []
    monkeypatch.setattr(lib, "subprocess", _fake_subprocess(recorded))
    monkeypatch.setattr(lib, "_serialize_in_flight", lambda _p: False)
    lib.spawn_serialize("daimon", "/t/x.jsonl", None)
    assert recorded, "the spawn seam never fired"
    return recorded[0]


def test_stdout_is_discarded_by_default(monkeypatch, tmp_path):
    """The default must not change. A terminal host would otherwise get
    capture results printed into its shell minutes after the session ended."""
    monkeypatch.delenv("DAIMON_LOG_STDOUT", raising=False)
    assert _spawn(monkeypatch, tmp_path)["stdout"] is subprocess.DEVNULL


def test_the_flag_inherits_stdout_so_a_runtime_can_collect_it(monkeypatch,
                                                              tmp_path):
    """None means inherit. In a container fd 1 belongs to the runtime's log
    pipe, inherited all the way down, so the detached child keeps writing to
    it for as long as the container runs."""
    monkeypatch.setenv("DAIMON_LOG_STDOUT", "1")
    assert _spawn(monkeypatch, tmp_path)["stdout"] is None


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_the_flag_accepts_the_same_words_every_other_daimon_flag_does(
        value, monkeypatch, tmp_path):
    """Behavioral equality with config._flag. A flag that reads only "1"
    while every sibling reads four words is a trap set for the operator."""
    monkeypatch.setenv("DAIMON_LOG_STDOUT", value)
    assert _spawn(monkeypatch, tmp_path)["stdout"] is None


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  "])
def test_a_falsey_value_leaves_the_default_alone(value, monkeypatch, tmp_path):
    monkeypatch.setenv("DAIMON_LOG_STDOUT", value)
    assert _spawn(monkeypatch, tmp_path)["stdout"] is subprocess.DEVNULL


def test_the_flag_is_readable_from_the_env_file_not_only_the_process(
        monkeypatch, tmp_path):
    """`_config_get` consults the env file as a fallback, and a container host
    may ship settings that way rather than in the process environment."""
    env_file = tmp_path / "env"
    env_file.write_text("DAIMON_LOG_STDOUT=1\n", encoding="utf-8")
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
    monkeypatch.delenv("DAIMON_LOG_STDOUT", raising=False)
    assert _spawn(monkeypatch, tmp_path)["stdout"] is None


@pytest.mark.parametrize("flag", [None, "1"])
def test_stderr_still_goes_to_the_crash_sink_either_way(flag, monkeypatch,
                                                        tmp_path):
    """#194's separation is the reason this feature uses stdout at all. If the
    flag ever moved stderr, result lines would start arriving in
    serialize-crash.log and read as crashes, which is the bug we refused."""
    if flag is None:
        monkeypatch.delenv("DAIMON_LOG_STDOUT", raising=False)
    else:
        monkeypatch.setenv("DAIMON_LOG_STDOUT", flag)
    stderr = _spawn(monkeypatch, tmp_path)["stderr"]
    assert stderr is not subprocess.DEVNULL and stderr is not None, \
        "stderr must stay pointed at the crash sink"
    assert getattr(stderr, "name", "").endswith("serialize-crash.log")


# ---- the CLI half: all three outcomes reach the one surface ----


@pytest.fixture
def _isolated_store(tmp_path, monkeypatch):
    """Never let a manual/CLI serialize touch the real store (scar: a builder
    wrote scratch buckets into it and overwrote the global pointer)."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "ckpt"))
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "logs"))


def _serialize_missing(capsys, tmp_path):
    """Drive the `transcript not found` result line and return (out, err)."""
    from daimon_briefing import cli
    rc = cli._run_serialize(tmp_path / "nope.jsonl", None)
    assert rc == 2
    captured = capsys.readouterr()
    return captured.out, captured.err


def test_an_error_line_stays_off_stdout_by_default(_isolated_store, capsys,
                                                   tmp_path, monkeypatch):
    monkeypatch.delenv("DAIMON_LOG_STDOUT", raising=False)
    out, err = _serialize_missing(capsys, tmp_path)
    assert "transcript not found" in err
    assert "transcript not found" not in out


def test_the_flag_puts_the_error_line_on_stdout_too(_isolated_store, capsys,
                                                    tmp_path, monkeypatch):
    """A failed capture is the outcome an operator most needs to see, and it
    was the one the container surface never carried: success and skip print to
    stdout already, errors went only to stderr and thence to the crash file."""
    monkeypatch.setenv("DAIMON_LOG_STDOUT", "1")
    out, err = _serialize_missing(capsys, tmp_path)
    assert "transcript not found" in out
    assert "transcript not found" in err, \
        "stderr must keep carrying it; the crash sink is not being replaced"


def test_the_mirrored_line_is_byte_identical_to_the_logged_one(
        _isolated_store, capsys, tmp_path, monkeypatch):
    """_run_serialize's contract: the logged string is byte-identical to the
    printed one so the raw, timestamp-free result regexes still match it. A
    mirror that reformatted would silently fall out of stats and status."""
    from daimon_briefing import ledger
    monkeypatch.setenv("DAIMON_LOG_STDOUT", "1")
    out, _ = _serialize_missing(capsys, tmp_path)
    line = out.strip().splitlines()[-1]
    logged = (Path(tmp_path / "logs" / "serialize.log")
              .read_text(encoding="utf-8").strip().splitlines()[-1])
    assert logged.endswith(line), (logged, line)
    assert ledger._is_result_line(line), "stats and status would not count it"
