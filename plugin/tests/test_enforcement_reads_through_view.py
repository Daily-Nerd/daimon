"""Enforcement is built only from values the reader may see (#1208).

The armed-check manifest and `refute guard` used to read raw ledger rows, so a
value a person retired (forgotten by another project, or quarantined) kept
enforcing. A ruling whose enforcement value is withheld arms nothing and is
counted, and the guard matches only the anchors and subjects a reader may see.
"""

import ast
import hashlib
from pathlib import Path

from daimon_briefing import checks_sync, cli, config, refutation_guard, refutations
from daimon_briefing.surfaces import Writer
from tests import _masking as m

BODY = "#!/bin/sh\nexit 0\n"
LINE = "checks: 1 ruling(s) not as ratified, not armed; see daimon ruling list"


def _sha(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _arm(*, match="gh pr create", body=BODY, subject="public posts"):
    rid = refutations.assert_ruling(
        subject=subject, verdict=m.VISIBLE, scope="publishing",
        evidence=["issue:943"], channel="cli-agent",
        check={"match": match, "body": body, "intent": "warn"},
        project_dir=m.PROJECT)
    refutations.ratify(rid, channel="ui", check_sha256=_sha(body),
                       project_dir=m.PROJECT)
    return rid


def _manifest_text():
    path = config.checks_dir() / "manifest.json"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _all_checks_bytes():
    base = config.checks_dir()
    return "".join(p.read_text(encoding="utf-8", errors="replace")
                   for p in sorted(base.glob("*")) if p.is_file())


def test_a_forgotten_check_match_arms_nothing(tmp_checkpoint_dir):
    _arm(match=m.FORGOTTEN)
    m.forget()
    report = checks_sync.sync(m.PROJECT, writer=Writer.HUMAN)
    assert report.ok and report.armed == 0
    assert report.not_as_ratified == 1
    assert "SECRET-F" not in _all_checks_bytes()


def test_a_forgotten_check_body_arms_nothing(tmp_checkpoint_dir):
    body = m.FORGOTTEN
    _arm(body=body)
    m.forget()
    report = checks_sync.sync(m.PROJECT, writer=Writer.HUMAN)
    assert report.ok and report.armed == 0
    assert report.not_as_ratified == 1
    assert "SECRET-F" not in _all_checks_bytes()


def test_a_forgotten_body_leaves_no_executable_behind(tmp_checkpoint_dir):
    body = m.FORGOTTEN
    _arm(body=body)
    checks_sync.sync(m.PROJECT, writer=Writer.HUMAN)
    assert "SECRET-F" in _all_checks_bytes()
    m.forget()
    checks_sync.sync(m.PROJECT, writer=Writer.HUMAN)
    assert "SECRET-F" not in _all_checks_bytes()


def test_a_quarantined_check_match_arms_nothing(tmp_checkpoint_dir):
    _arm(match=m.QUARANTINED.casefold())
    m.quarantine()
    report = checks_sync.sync(m.PROJECT, writer=Writer.HUMAN)
    assert report.ok and report.armed == 0
    assert report.not_as_ratified == 1
    assert "secret-q" not in _all_checks_bytes().lower()


def test_a_clean_armed_ruling_is_still_armed(tmp_checkpoint_dir):
    _arm()
    report = checks_sync.sync(m.PROJECT, writer=Writer.HUMAN)
    assert report.ok and report.armed == 1 and report.not_as_ratified == 0
    assert "gh pr create" in _manifest_text()


def test_only_the_withheld_ruling_is_dropped(tmp_checkpoint_dir):
    clean = _arm()
    _arm(match=m.FORGOTTEN, subject="second")
    m.forget()
    report = checks_sync.sync(m.PROJECT, writer=Writer.HUMAN)
    assert report.armed == 1 and report.not_as_ratified == 1
    assert clean in _manifest_text()


def test_the_audit_and_the_writer_agree(tmp_checkpoint_dir):
    _arm(match=m.FORGOTTEN)
    m.forget()
    checks_sync.sync(m.PROJECT, writer=Writer.HUMAN)
    audit = checks_sync.audit(m.PROJECT)
    assert audit.wanted == [] and audit.drift is False


def test_the_audit_flags_a_manifest_that_still_holds_a_retired_value(
        tmp_checkpoint_dir):
    rid = _arm(match=m.FORGOTTEN)
    checks_sync.sync(m.PROJECT, writer=Writer.HUMAN)
    m.forget()
    audit = checks_sync.audit(m.PROJECT)
    assert audit.wanted == [] and audit.stale == [rid] and audit.drift


def test_check_sync_says_how_many_rulings_were_skipped(
        tmp_checkpoint_dir, capsys):
    _arm(match=m.FORGOTTEN)
    m.forget()
    rc, out, _ = m.run(capsys, "check", "sync")
    assert rc == 0
    assert LINE in out
    assert "SECRET" not in out


def test_check_sync_is_silent_when_nothing_was_skipped(
        tmp_checkpoint_dir, capsys):
    _arm()
    rc, out, _ = m.run(capsys, "check", "sync")
    assert rc == 0 and "not as ratified" not in out


def test_hooks_install_says_how_many_rulings_were_skipped(
        tmp_checkpoint_dir, tmp_path, monkeypatch, capsys):
    _arm(match=m.FORGOTTEN)
    m.forget()
    monkeypatch.setenv("HOME", str(tmp_path))
    capsys.readouterr()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_resolve_project", lambda _arg: m.PROJECT)
    rc = cli.main(["hooks", "install", "codex"])
    out = capsys.readouterr().out
    assert rc == 0 and LINE in out and "SECRET" not in out


def test_guard_does_not_match_a_forgotten_anchor(tmp_checkpoint_dir):
    refutations.ratify(refutations.assert_refutation(
        subject="a plain subject", verdict=m.VISIBLE, scope="census",
        evidence=["measurement:replay"], channel="cli-agent",
        anchors=[m.FORGOTTEN.casefold()], project_dir=m.PROJECT),
        channel="cli-tty", project_dir=m.PROJECT)
    assert refutation_guard.guard("x", anchors=[m.FORGOTTEN],
                             project_dir=m.PROJECT)
    m.forget()
    assert refutation_guard.guard("x", anchors=[m.FORGOTTEN],
                             project_dir=m.PROJECT) == []


def test_guard_does_not_match_a_forgotten_subject(tmp_checkpoint_dir):
    refutations.ratify(refutations.assert_refutation(
        subject=m.FORGOTTEN, verdict=m.VISIBLE, scope="census",
        evidence=["measurement:replay"], channel="cli-agent",
        project_dir=m.PROJECT), channel="cli-tty", project_dir=m.PROJECT)
    assert refutation_guard.guard(f"about {m.FORGOTTEN} here",
                             project_dir=m.PROJECT)
    m.forget()
    assert refutation_guard.guard(f"about {m.FORGOTTEN} here",
                             project_dir=m.PROJECT) == []


def test_guard_does_not_match_a_quarantined_anchor(tmp_checkpoint_dir):
    refutations.ratify(refutations.assert_refutation(
        subject="a plain subject", verdict=m.VISIBLE, scope="census",
        evidence=["measurement:replay"], channel="cli-agent",
        anchors=[m.QUARANTINED.casefold()], project_dir=m.PROJECT),
        channel="cli-tty", project_dir=m.PROJECT)
    m.quarantine()
    assert refutation_guard.guard("x", anchors=[m.QUARANTINED],
                             project_dir=m.PROJECT) == []


def test_guard_still_matches_a_visible_anchor(tmp_checkpoint_dir):
    rid = refutations.assert_refutation(
        subject="a plain subject", verdict=m.VISIBLE, scope="census",
        evidence=["measurement:replay"], channel="cli-agent",
        anchors=["issue:7", m.FORGOTTEN.casefold()], project_dir=m.PROJECT)
    refutations.ratify(rid, channel="cli-tty", project_dir=m.PROJECT)
    m.forget()
    hits = refutation_guard.guard("x", anchors=["issue:7"], project_dir=m.PROJECT)
    assert [h["refutation_id"] for h in hits] == [rid]
    assert hits[0]["guard_match"]["anchors"] == ["issue:7"]


def _imports_view(module) -> bool:
    """Whether a module imports `view` anywhere, in the module body or inside
    a function, in any spelling (`from . import view`, `from .view import x`,
    `import daimon_briefing.view`)."""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module and node.module.split(".")[0] == "view":
                return True
            if node.level == 1 and not node.module and any(
                    alias.name == "view" for alias in node.names):
                return True
            if node.level == 0 and (node.module or "").split(".")[:2] == [
                    "daimon_briefing", "view"]:
                return True
        elif isinstance(node, ast.Import):
            if any(alias.name.split(".")[:2] == ["daimon_briefing", "view"]
                   for alias in node.names):
                return True
    return False


def test_the_ledger_modules_never_import_the_view():
    """The ledger holds the raw rows and the view judges them, so a ledger
    module that imports the view is reading what a reader must not see, or
    judging it itself (#1208). The enforcement readers live above the view
    (`checks_sync`, `refutation_guard`) and these two must stay below it."""
    from daimon_briefing import checks
    assert not _imports_view(checks)
    assert not _imports_view(refutations)


def test_the_enforcement_readers_do_import_the_view():
    """Anti-vacuity: the scan sees the import where it lives."""
    assert _imports_view(checks_sync)
    assert _imports_view(refutation_guard)
