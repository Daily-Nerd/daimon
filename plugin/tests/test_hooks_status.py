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


def test_hook_drift_present_swallows_report_errors(monkeypatch):
    # If the report itself blows up (unreadable packaged copies, etc.) the status
    # probe reports no drift rather than crashing the whole `daimon status`.
    monkeypatch.setattr(cli, "_hooks_status_report",
                        lambda home: (_ for _ in ()).throw(RuntimeError("boom")))
    assert cli._hook_drift_present() is False


def test_hook_file_status_unreadable_path_is_missing(tmp_path):
    # A path that exists but cannot be read as bytes (here: a directory in the
    # file's place) reads as MISSING, not a crash.
    from importlib import resources

    pkg = resources.files("daimon_briefing._hooks")
    (tmp_path / "redact.py").mkdir()
    assert cli._hook_file_status(pkg, tmp_path, "redact.py") == "MISSING"


def test_codex_registration_status_handles_non_dict_hooks(tmp_path, monkeypatch):
    # A hooks.json whose "hooks" is not an object must not crash the audit.
    monkeypatch.setenv("HOME", str(tmp_path))
    codex = tmp_path / ".codex"
    codex.mkdir()
    (codex / "hooks.json").write_text(json.dumps({"hooks": "not-an-object"}))
    assert cli._codex_registration_status(tmp_path) == "UNREGISTERED"


def test_render_hooks_status_rich_drift_pointer(monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_status({
        "project": "/p/A",
        "proj": {"exists": False},
        "glob": {"exists": False},
        "last": None,
        "hook_drift": True,
    })
    assert "out of date" in capsys.readouterr().out


def test_render_hooks_status_empty_report(capsys):
    render.render_hooks_status([])
    assert "no packaged hook hosts" in capsys.readouterr().out


# ---- #943 slice 5: the manifest the hooks read is audited here too ---------
#
# The scripts landing is half of "the gate is in place". The other half is
# that the file those scripts read still matches this project's ledger, and a
# stale one fails open: byte-identical to a clean allow.


def _arm_a_check(project):
    import hashlib

    from daimon_briefing import refutations

    body = "#!/bin/sh\nexit 0\n"
    ruling_id = refutations.assert_ruling(
        subject="public posts", verdict="the rule for public posts",
        scope="publishing", evidence=["issue:943"], channel="cli-agent",
        check={"match": "gh pr create", "body": body, "intent": "warn"},
        project_dir=str(project))
    refutations.ratify(
        ruling_id, channel="ui",
        check_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        project_dir=str(project))
    return ruling_id


def test_hooks_status_reports_the_manifest_as_in_step(tmp_path, monkeypatch,
                                                      capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm_a_check(tmp_path)
    assert cli.main(["hooks", "status"]) == 0
    assert "checks manifest (this project): in step, 1 armed" in \
        capsys.readouterr().out


def test_hooks_status_reports_manifest_drift_and_exits_non_zero(
        tmp_path, monkeypatch, capsys):
    """Spec 10's own risk: the manifest goes stale after a forget or an
    overturn, and `hooks status` reports it the way it reports script drift."""
    from daimon_briefing import checks, config

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm_a_check(tmp_path)
    checks.sync(str(tmp_path))
    (config.checks_dir() / "manifest.json").write_text("[]\n",
                                                       encoding="utf-8")
    rc = cli.main(["hooks", "status"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "checks manifest (this project): drifted" in out
    assert "fix: daimon check sync" in out


def test_hooks_status_json_stays_a_list_of_hosts(tmp_path, monkeypatch,
                                                 capsys):
    """The --json shape is a contract: a list of host entries, nothing else.
    The manifest block is a text-surface addition, and a script that wants
    the audit has `daimon check sync --check --json`."""
    from daimon_briefing import checks, config

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm_a_check(tmp_path)
    checks.sync(str(tmp_path))
    (config.checks_dir() / "manifest.json").write_text("[]\n",
                                                       encoding="utf-8")
    rc = cli.main(["hooks", "status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    # The real hosts come first and keep their exact shape, so an iterator
    # written against the old payload still walks them unchanged.
    hosts = payload[:len(cli._HOOK_HOSTS)]
    assert {h["host"] for h in hosts} == set(cli._HOOK_HOSTS)
    assert all("manifest" not in h for h in hosts)
    # The exit code still folds manifest drift, so CI catches it either way.
    assert rc == 1


def test_hooks_status_json_explains_why_it_exited_non_zero(tmp_path,
                                                           monkeypatch,
                                                           capsys):
    """A non-zero exit a script cannot account for is worse than no audit.
    The manifest rides in the list as one more entry, shaped like a host so
    existing iterators keep working, and carrying the ids-only audit."""
    from daimon_briefing import checks, config

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    ruling_id = _arm_a_check(tmp_path)
    checks.sync(str(tmp_path))
    (config.checks_dir() / "manifest.json").write_text("[]\n",
                                                       encoding="utf-8")
    rc = cli.main(["hooks", "status", "--json"])
    entry = json.loads(capsys.readouterr().out)[-1]
    assert entry["host"] == "checks-manifest"
    assert entry["dir"] == str(config.checks_dir())
    assert entry["installed"] is True
    assert entry["registration"] is None and entry["files"] == []
    assert entry["drift"] is True and rc == 1
    assert entry["manifest"]["missing"] == [ruling_id]


def test_the_manifest_entry_reports_no_drift_when_the_manifest_is_in_step(
        tmp_path, monkeypatch, capsys):
    from daimon_briefing import checks

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm_a_check(tmp_path)
    checks.sync(str(tmp_path))
    rc = cli.main(["hooks", "status", "--json"])
    entry = json.loads(capsys.readouterr().out)[-1]
    assert entry["drift"] is False and rc == 0


def test_a_machine_with_no_manifest_says_so_rather_than_claiming_drift(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    rc = cli.main(["hooks", "status", "--json"])
    entry = json.loads(capsys.readouterr().out)[-1]
    assert entry["installed"] is False
    assert entry["drift"] is False and rc == 0


def test_manifest_drift_never_reaches_the_hook_drift_pointer(tmp_path,
                                                             monkeypatch,
                                                             capsys):
    """`_hook_drift_present` folds the same report into one `daimon status`
    line whose wording is about SCRIPT copies. A manifest entry inside that
    list would make a stale manifest print "installed hooks out of date",
    which points at the wrong repair. The entry is appended at print time,
    never inside `_hooks_status_report`."""
    from daimon_briefing import checks, config

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    _arm_a_check(tmp_path)
    checks.sync(str(tmp_path))
    (config.checks_dir() / "manifest.json").write_text("[]\n",
                                                       encoding="utf-8")
    assert cli._hook_drift_present() is False
    assert all(h["host"] != "checks-manifest"
               for h in cli._hooks_status_report(Path(tmp_path)))
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "installed hooks out of date" not in out
    assert "manifest drifted, run daimon check sync" in out


def test_a_project_with_no_checks_adds_no_manifest_noise(tmp_path,
                                                         monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(tmp_path))
    assert cli.main(["hooks", "status"]) == 0
    assert "checks manifest (this project): no manifest" in \
        capsys.readouterr().out


def test_hooks_status_survives_an_unreadable_checks_directory(
        tmp_path, monkeypatch, capsys):
    """The scripts audit is the verb's job; the manifest block is an extra,
    and losing the whole report over it would be the wrong trade."""
    from daimon_briefing import checks

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(checks, "audit",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert cli.main(["hooks", "status"]) == 0
    out = capsys.readouterr().out
    assert "NOT INSTALLED" in out and "checks manifest" not in out
