"""Enforcement is built only from values the reader may see (#1208).

The armed-check manifest and `refute guard` used to read raw ledger rows, so a
value a person retired (forgotten by another project, or quarantined) kept
enforcing. A ruling whose enforcement value is withheld arms nothing and is
counted, and the guard matches only the anchors and subjects a reader may see.
"""

import ast
import hashlib
from pathlib import Path

from daimon_briefing import checks, cli, config, refutations
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
    report = checks.sync(m.PROJECT, writer=Writer.HUMAN)
    assert report.ok and report.armed == 0
    assert report.not_as_ratified == 1
    assert "SECRET-F" not in _all_checks_bytes()


def test_a_forgotten_check_body_arms_nothing(tmp_checkpoint_dir):
    body = f"#!/bin/sh\n# {m.FORGOTTEN}\nexit 0\n"
    _arm(body=body)
    m.forget()
    report = checks.sync(m.PROJECT, writer=Writer.HUMAN)
    assert report.ok and report.armed == 0
    assert report.not_as_ratified == 1
    assert "SECRET-F" not in _all_checks_bytes()


def test_a_forgotten_body_leaves_no_executable_behind(tmp_checkpoint_dir):
    body = f"#!/bin/sh\n# {m.FORGOTTEN}\nexit 0\n"
    _arm(body=body)
    checks.sync(m.PROJECT, writer=Writer.HUMAN)
    assert "SECRET-F" in _all_checks_bytes()
    m.forget()
    checks.sync(m.PROJECT, writer=Writer.HUMAN)
    assert "SECRET-F" not in _all_checks_bytes()


def test_a_quarantined_check_match_arms_nothing(tmp_checkpoint_dir):
    _arm(match=m.QUARANTINED.casefold())
    m.quarantine()
    report = checks.sync(m.PROJECT, writer=Writer.HUMAN)
    assert report.ok and report.armed == 0
    assert report.not_as_ratified == 1
    assert "secret-q" not in _all_checks_bytes().lower()


def test_a_clean_armed_ruling_is_still_armed(tmp_checkpoint_dir):
    _arm()
    report = checks.sync(m.PROJECT, writer=Writer.HUMAN)
    assert report.ok and report.armed == 1 and report.not_as_ratified == 0
    assert "gh pr create" in _manifest_text()


def test_only_the_withheld_ruling_is_dropped(tmp_checkpoint_dir):
    clean = _arm()
    _arm(match=m.FORGOTTEN, subject="second")
    m.forget()
    report = checks.sync(m.PROJECT, writer=Writer.HUMAN)
    assert report.armed == 1 and report.not_as_ratified == 1
    assert clean in _manifest_text()


def test_the_audit_and_the_writer_agree(tmp_checkpoint_dir):
    _arm(match=m.FORGOTTEN)
    m.forget()
    checks.sync(m.PROJECT, writer=Writer.HUMAN)
    audit = checks.audit(m.PROJECT)
    assert audit.wanted == [] and audit.drift is False


def test_the_audit_flags_a_manifest_that_still_holds_a_retired_value(
        tmp_checkpoint_dir):
    rid = _arm(match=m.FORGOTTEN)
    checks.sync(m.PROJECT, writer=Writer.HUMAN)
    m.forget()
    audit = checks.audit(m.PROJECT)
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
    assert refutations.guard("x", anchors=[m.FORGOTTEN],
                             project_dir=m.PROJECT)
    m.forget()
    assert refutations.guard("x", anchors=[m.FORGOTTEN],
                             project_dir=m.PROJECT) == []


def test_guard_does_not_match_a_forgotten_subject(tmp_checkpoint_dir):
    refutations.ratify(refutations.assert_refutation(
        subject=m.FORGOTTEN, verdict=m.VISIBLE, scope="census",
        evidence=["measurement:replay"], channel="cli-agent",
        project_dir=m.PROJECT), channel="cli-tty", project_dir=m.PROJECT)
    assert refutations.guard(f"about {m.FORGOTTEN} here",
                             project_dir=m.PROJECT)
    m.forget()
    assert refutations.guard(f"about {m.FORGOTTEN} here",
                             project_dir=m.PROJECT) == []


def test_guard_does_not_match_a_quarantined_anchor(tmp_checkpoint_dir):
    refutations.ratify(refutations.assert_refutation(
        subject="a plain subject", verdict=m.VISIBLE, scope="census",
        evidence=["measurement:replay"], channel="cli-agent",
        anchors=[m.QUARANTINED.casefold()], project_dir=m.PROJECT),
        channel="cli-tty", project_dir=m.PROJECT)
    m.quarantine()
    assert refutations.guard("x", anchors=[m.QUARANTINED],
                             project_dir=m.PROJECT) == []


def test_guard_still_matches_a_visible_anchor(tmp_checkpoint_dir):
    rid = refutations.assert_refutation(
        subject="a plain subject", verdict=m.VISIBLE, scope="census",
        evidence=["measurement:replay"], channel="cli-agent",
        anchors=["issue:7", m.FORGOTTEN.casefold()], project_dir=m.PROJECT)
    refutations.ratify(rid, channel="cli-tty", project_dir=m.PROJECT)
    m.forget()
    hits = refutations.guard("x", anchors=["issue:7"], project_dir=m.PROJECT)
    assert [h["refutation_id"] for h in hits] == [rid]
    assert hits[0]["guard_match"]["anchors"] == ["issue:7"]


def test_refutations_has_no_module_level_view_import():
    """`view` imports `amendments` and `requests`, both of which import
    `refutations`, so a module-level import here is an import cycle."""
    tree = ast.parse(Path(refutations.__file__).read_text(encoding="utf-8"))
    top = {alias.name for node in tree.body
           if isinstance(node, ast.ImportFrom) and node.level == 1
           for alias in node.names}
    assert "view" not in top
