"""#266: `daimon hooks status` reports whether the installed hook copies still
match the packaged versions. Drift is otherwise invisible — a stale copy keeps
*working* on old behavior after an upgrade — so this is a byte-hash audit with a
non-zero exit for CI, plus a one-line pointer on `daimon status` when drift
exists. Fixtures mirror test_hooks_install.py: HOME → tmp_path, install for
real, then mutate the installed tree."""

import json
from pathlib import Path

from daimon_briefing import cli, render

PKG_HOOKS_DIR = Path(__file__).parents[1] / "daimon_briefing" / "_hooks"

_WINDSURF_FILES = cli._HOOK_HOSTS["windsurf"]["files"]
_WINDSURF_DIR = (".daimon", "hooks")
_CODEX_DIR = (".codex", "hooks")


def _host(report, name):
    return next(h for h in report if h["host"] == name)


def _statuses(report, name):
    return {f["name"]: f["status"] for f in _host(report, name)["files"]}


# ---- nothing installed -------------------------------------------------------


def test_status_fresh_home_reports_not_installed_and_exits_zero(tmp_path, monkeypatch, capsys):
    # A machine that never ran `hooks install` is not "broken" — it is simply
    # not set up. NOT INSTALLED, zero exit (requirement 5: nothing-installed = 0).
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = cli.main(["hooks", "status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "NOT INSTALLED" in out
    assert "windsurf" in out and "codex" in out


# ---- windsurf: current / stale / missing ------------------------------------


def test_status_after_install_all_current_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "windsurf"]) == 0
    capsys.readouterr()
    rc = cli.main(["hooks", "status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "STALE" not in out and "MISSING" not in out
    assert "CURRENT" in out


def test_status_mutated_file_is_stale_and_exits_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "windsurf"]) == 0
    target = tmp_path.joinpath(*_WINDSURF_DIR)
    (target / "daimon-windsurf-hooks.py").write_text("# stale drifted copy")
    capsys.readouterr()
    rc = cli.main(["hooks", "status"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "STALE" in out
    # requirement 4: the drifted host prints the exact fix command
    assert "daimon hooks install windsurf" in out


def test_status_removed_file_is_missing_and_exits_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "windsurf"]) == 0
    target = tmp_path.joinpath(*_WINDSURF_DIR)
    (target / "redact.py").unlink()
    capsys.readouterr()
    rc = cli.main(["hooks", "status"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "MISSING" in out
    assert "daimon hooks install windsurf" in out


# ---- symlinked installs resolve to target bytes -----------------------------


def test_status_symlink_resolves_to_target_bytes(tmp_path, monkeypatch):
    # A symlinked install must be judged by the bytes it points AT, not the link.
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path.joinpath(*_WINDSURF_DIR)
    target.mkdir(parents=True)
    real = tmp_path / "real"
    real.mkdir()
    for name in _WINDSURF_FILES:
        good = real / name
        good.write_bytes((PKG_HOOKS_DIR / name).read_bytes())
        (target / name).symlink_to(good)
    report = cli._hooks_status_report(tmp_path)
    assert all(v == "CURRENT" for v in _statuses(report, "windsurf").values())

    # point one link at drifted bytes → STALE via the resolved target
    (real / "redact.py").write_text("# drifted target")
    report = cli._hooks_status_report(tmp_path)
    assert _statuses(report, "windsurf")["redact.py"] == "STALE"


def test_status_broken_symlink_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "windsurf"]) == 0
    target = tmp_path.joinpath(*_WINDSURF_DIR)
    victim = target / "redact.py"
    victim.unlink()
    victim.symlink_to(tmp_path / "does-not-exist")
    report = cli._hooks_status_report(tmp_path)
    assert _statuses(report, "windsurf")["redact.py"] == "MISSING"


# ---- codex: registration verdicts -------------------------------------------


def test_status_codex_fresh_install_registered_and_current(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "codex"]) == 0
    report = cli._hooks_status_report(tmp_path)
    codex = _host(report, "codex")
    assert codex["installed"] is True
    assert codex["registration"] == "REGISTERED"
    assert all(v == "CURRENT" for v in _statuses(report, "codex").values())
    assert codex["drift"] is False


def test_status_codex_missing_one_registration_is_partial_and_drifts(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "codex"]) == 0
    hooks_json = tmp_path / ".codex" / "hooks.json"
    cfg = json.loads(hooks_json.read_text())
    # drop the Stop registration; scripts on disk are still CURRENT
    cfg["hooks"]["Stop"] = []
    hooks_json.write_text(json.dumps(cfg))
    report = cli._hooks_status_report(tmp_path)
    codex = _host(report, "codex")
    assert codex["registration"] == "PARTIAL"
    assert codex["drift"] is True


def test_status_codex_no_registration_is_unregistered(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "codex"]) == 0
    hooks_json = tmp_path / ".codex" / "hooks.json"
    hooks_json.write_text(json.dumps({"hooks": {}}))
    report = cli._hooks_status_report(tmp_path)
    codex = _host(report, "codex")
    # scripts still on disk → still installed, but nothing points at them
    assert codex["installed"] is True
    assert codex["registration"] == "UNREGISTERED"
    assert codex["drift"] is True


def test_status_codex_registered_scripts_gone_dir_present_still_installed(tmp_path, monkeypatch):
    # Registration entries exist but scripts were deleted: host counts as
    # installed (issue: "installed if hooks dir OR registration entries exist"),
    # files report MISSING.
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "codex"]) == 0
    hooks_dir = tmp_path / ".codex" / "hooks"
    for p in list(hooks_dir.iterdir()):
        p.unlink()
    report = cli._hooks_status_report(tmp_path)
    codex = _host(report, "codex")
    assert codex["installed"] is True
    assert all(v == "MISSING" for v in _statuses(report, "codex").values())
    assert codex["drift"] is True


def test_status_codex_exits_one_on_registration_drift(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "codex"]) == 0
    (tmp_path / ".codex" / "hooks.json").write_text(json.dumps({"hooks": {}}))
    capsys.readouterr()
    rc = cli.main(["hooks", "status"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "UNREGISTERED" in out
    assert "daimon hooks install codex" in out


# ---- json pipe --------------------------------------------------------------


def test_status_json_shape(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "windsurf"]) == 0
    capsys.readouterr()
    assert cli.main(["hooks", "status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    ws = _host(payload, "windsurf")
    assert ws["installed"] is True
    assert {f["name"] for f in ws["files"]} == set(_WINDSURF_FILES)


# ---- daimon status one-line pointer (requirement 6) -------------------------


def test_main_status_renders_drift_pointer_when_present(capsys):
    render.render_status({
        "project": "/p/A",
        "proj": {"exists": False},
        "glob": {"exists": False},
        "last": None,
        "hook_drift": True,
    })
    out = capsys.readouterr().out
    assert "installed hooks out of date" in out
    assert "daimon hooks status" in out


def test_main_status_silent_when_no_drift(capsys):
    render.render_status({
        "project": "/p/A",
        "proj": {"exists": False},
        "glob": {"exists": False},
        "last": None,
        "hook_drift": False,
    })
    out = capsys.readouterr().out
    assert "out of date" not in out


def test_cmd_status_sets_hook_drift_when_installed_copy_stale(tmp_path, monkeypatch, capsys):
    # End to end: install, drift a file, run the top-level `status` command and
    # confirm it surfaces the pointer (cheap hash check wired into status).
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "windsurf"]) == 0
    target = tmp_path.joinpath(*_WINDSURF_DIR)
    (target / "daimon-windsurf-hooks.py").write_text("# drifted")
    capsys.readouterr()
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "installed hooks out of date" in out


def test_cmd_status_no_pointer_on_clean_machine(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    capsys.readouterr()
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "out of date" not in out


def test_hook_drift_present_never_raises(tmp_path, monkeypatch):
    # status must never crash on a weird hooks tree — the probe swallows errors.
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli._hook_drift_present() is False
