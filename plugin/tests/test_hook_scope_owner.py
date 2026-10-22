"""The host hook scripts do not decide a read scope (#1223).

Four hooks print the briefing at session start: Claude Code
(`daimon-session-brief.py`), Codex, Gemini and Kimi. Each used to slug the
payload cwd itself, look at `<ckpt>/<slug>/latest.json`, fall back to the
global `latest.json`, gate on its existence and label the age of the file IT
chose. That was a second decision beside the CLI's. Now `daimon brief --label`
(the scope owner) prints the one label line for the checkpoint it actually
rendered, and the hook prints what it was told. An older CLI that rejects
`--label` gets a plain `daimon brief` and the hook invents no label."""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from daimon_briefing import cli, store
from daimon_briefing.surfaces import Writer

ROOT = Path(__file__).parents[2]
HOOK_DIR = ROOT / "hook"
PKG_HOOK_DIR = ROOT / "plugin" / "daimon_briefing" / "_hooks"
VENV_BIN = Path(sys.executable).parent
OWN = "/p/own-project"
NEVER = "/p/never-seen"


def _ck(base, sid):
    return {**base, "session_id": sid}


def _payload(host, cwd):
    data = {"session_id": "S-new"}
    if cwd is not None:
        data["cwd"] = cwd
    if host == "kimi":
        data.update({"hook_event_name": "UserPromptSubmit",
                     "client_type": "kimi_code_cli", "is_steer": False,
                     "prompt": [{"type": "text", "text": "where were we"}]})
    elif host == "gemini":
        data["source"] = "startup"
    return data


HOSTS = {
    "claude": "daimon-session-brief.py",
    "codex": "daimon-codex-session-start.py",
    "gemini": "daimon-gemini-session-start.py",
    "kimi": "daimon-kimi-user-prompt-submit.py",
}


def _run(host, cwd, tmp_path, extra_env=None, path_prefix=None):
    env = {**os.environ,
           "PATH": f"{path_prefix or VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}",
           "HOME": str(tmp_path)}
    env.pop("KIMI_CODE_HOME", None)
    env.update(extra_env or {})
    proc = subprocess.run(
        [sys.executable, str(HOOK_DIR / HOSTS[host])],
        input=json.dumps(_payload(host, cwd)), capture_output=True, text=True,
        env=env, timeout=30, cwd=str(tmp_path))
    out = proc.stdout
    if host in ("codex", "gemini") and out.strip():
        out = json.loads(out).get("hookSpecificOutput", {}).get(
            "additionalContext", "")
    return proc.returncode, out


@pytest.fixture(params=sorted(HOSTS))
def host(request):
    return request.param


@pytest.fixture
def world(tmp_checkpoint_dir, sample_checkpoint):
    store.write_checkpoint("S-own", _ck(sample_checkpoint, "S-own"),
                           project_dir=OWN, writer=Writer.HUMAN)
    store.write_checkpoint("S-global", _ck(sample_checkpoint, "S-global"),
                           project_dir="/p/elsewhere", writer=Writer.HUMAN)


# ---- the CLI prints the label --------------------------------------------


def test_brief_label_names_the_checkpoint_it_rendered(world, capsys,
                                                      monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", OWN)
    assert cli.main(["brief", "--label"]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith("DAIMON BRIEFING (checkpoint: S-own")
    assert "global fallback" not in first


def test_brief_label_marks_an_opted_in_global_fallback(world, capsys,
                                                       monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", NEVER)
    monkeypatch.setenv("DAIMON_BRIEF_GLOBAL_FALLBACK", "1")
    assert cli.main(["brief", "--label"]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert "checkpoint: S-global" in first
    assert "global fallback" in first


def test_brief_label_is_absent_when_no_checkpoint_was_rendered(world, capsys,
                                                               monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", NEVER)
    assert cli.main(["brief", "--label"]) == 0
    out = capsys.readouterr().out
    assert "DAIMON BRIEFING" not in out
    assert "S-global" not in out


def test_without_the_flag_brief_prints_no_label(world, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", OWN)
    assert cli.main(["brief"]) == 0
    assert "DAIMON BRIEFING" not in capsys.readouterr().out


# ---- every host prints what the CLI decided ------------------------------


def test_own_checkpoint_is_labelled_without_a_fallback(world, host, tmp_path):
    rc, out = _run(host, OWN, tmp_path)
    assert rc == 0
    assert "DAIMON BRIEFING" in out
    assert "checkpoint: S-own" in out
    assert "global fallback" not in out


def test_a_foreign_global_checkpoint_is_never_labelled_or_shown_by_default(
        world, host, tmp_path):
    rc, out = _run(host, NEVER, tmp_path)
    assert rc == 0
    assert "checkpoint: S-global" not in out
    assert "global fallback" not in out


def test_the_operator_opt_in_labels_the_global_fallback(world, host, tmp_path):
    rc, out = _run(host, NEVER, tmp_path,
                   {"DAIMON_BRIEF_GLOBAL_FALLBACK": "1"})
    assert rc == 0
    assert "checkpoint: S-global" in out
    assert "global fallback" in out


def test_a_tenant_home_never_serves_the_global_checkpoint(world, host,
                                                          tmp_path):
    rc, out = _run(host, NEVER, tmp_path,
                   {"DAIMON_BRIEF_GLOBAL_FALLBACK": "1",
                    "DAIMON_TENANT_SCOPED": "1"})
    assert rc == 0
    assert "S-global" not in out
    assert "global fallback" not in out


def test_a_payload_without_a_cwd_does_not_pick_the_global_pointer(
        world, host, tmp_path):
    rc, out = _run(host, None, tmp_path)
    assert rc == 0
    assert "checkpoint: S-global" not in out
    assert "checkpoint: S-own" not in out


def _fake_cli(tmp_path, *, reject_label):
    fake = tmp_path / "fakebin"
    fake.mkdir()
    log = tmp_path / "argv.txt"
    script = fake / "daimon"
    guard = ('case " $* " in *" --label "*) exit 2;; esac\n'
             if reject_label else "")
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{log}"\n'
        'if [ "$1" != "brief" ]; then exit 0; fi\n'
        f"{guard}"
        'case " $* " in *" --label "*) '
        'echo "DAIMON BRIEFING (checkpoint: FROM-CLI, written 1m ago)";; esac\n'
        'echo "BODY-FROM-CLI"\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return fake, log


def test_the_hook_prints_the_label_the_cli_gave_it_and_adds_none(
        world, host, tmp_path):
    fake, _ = _fake_cli(tmp_path, reject_label=False)
    rc, out = _run(host, OWN, tmp_path, path_prefix=fake)
    assert rc == 0
    assert out.count("DAIMON BRIEFING") == 1
    assert "FROM-CLI" in out and "BODY-FROM-CLI" in out
    assert "checkpoint: S-own" not in out


def test_an_older_cli_that_rejects_label_gets_a_plain_brief_and_no_invented_label(
        world, host, tmp_path):
    fake, log = _fake_cli(tmp_path, reject_label=True)
    rc, out = _run(host, OWN, tmp_path, path_prefix=fake)
    assert rc == 0
    assert "BODY-FROM-CLI" in out
    assert "DAIMON BRIEFING" not in out
    briefs = [ln for ln in log.read_text().splitlines()
              if ln.startswith("brief")]
    assert "--label" in briefs[0]
    assert "--label" not in briefs[-1]


# ---- structural: no hook decides routing itself --------------------------

_HOOK_DIRS = (HOOK_DIR, PKG_HOOK_DIR)


def test_no_hook_script_reads_a_latest_pointer_or_decides_a_fallback():
    offenders = []
    for d in _HOOK_DIRS:
        for path in sorted(d.glob("*.py")):
            if path.name == "_daimon_hook_lib.py":
                continue
            text = path.read_text(encoding="utf-8")
            for needle in ("latest.json", "lib.slug(", "lib.checkpoint_dir(",
                           "lib.age_line(", "fallback = "):
                if needle in text:
                    offenders.append(f"{path.relative_to(ROOT)}: {needle}")
    assert not offenders, (
        "hook scripts must not decide a scope; `daimon brief --label` owns "
        f"it: {offenders}")


def test_the_hook_lib_has_no_age_line_to_label_a_checkpoint_with():
    for d in _HOOK_DIRS:
        text = (d / "_daimon_hook_lib.py").read_text(encoding="utf-8")
        assert "def age_line" not in text
