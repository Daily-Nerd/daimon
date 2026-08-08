"""The refutation ledger inside the privacy contract (#645, #647).

The ledger is a second plaintext store, so it owes the same two things every
other plaintext surface owes: it must be DECLARED in the surface registry and
SCANNED by `daimon audit privacy` (#645), and free text on its way to disk must
cross the secret scrubber before any transform that could defeat it (#647).
"""
import pytest

from daimon_briefing import (cli, normalize, privacy, refutations, store,
                             surfaces)


PROJECT = "/p/refutation-privacy"
CANARY = "zqxrefcanary8812 rotate the signing key before the next deploy"
KEEPER = "an unrelated refutation that must survive the audit"
# redact.py's aws-key shape, the same synthetic literal the other privacy
# suites use. Uppercase-dependent BY CONSTRUCTION, which is exactly why
# casefolding an anchor before the scrub used to defeat it.
SECRET = "AKIAIOSFODNN7EXAMPLE"


@pytest.fixture(autouse=True)
def _interactive(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)


def _checkpoint(text="an ordinary decision about logging"):
    store.write_checkpoint("S1", {
        "session_id": "S1",
        "created": "2026-08-08T00:00:00Z",
        "working_context": {
            "recent_decisions": [{"text": text, "trust": "inferred"}]},
    }, project_dir=PROJECT)


def _refute(**overrides):
    values = {
        "subject": KEEPER,
        "verdict": "the measurement does not support the claim",
        "scope": "refutation ledger privacy",
        "evidence": ["measurement:run-1"],
        "anchors": ["#645"],
        "channel": "cli-agent",
        "project_dir": PROJECT,
    }
    values.update(overrides)
    return refutations.assert_refutation(**values)


def _ledger_path():
    return refutations._path(PROJECT)


def _tombstone(value):
    """Tombstone WITHOUT scrubbing, so the plaintext demonstrably remains and
    the audit has to find it — the same device the checkpoint suites use."""
    key = normalize.content_key(value)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    return key


# -- #645: the ledger is declared and scanned --------------------------------

def test_the_refutation_ledger_shape_is_declared():
    surface = surfaces.match("checkpoints/{slug}/refutations.jsonl")
    assert surface is not None, "the refutation ledger is an undeclared surface"
    assert surface.plaintext is True
    assert surface.audit_exempt is False, \
        "a plaintext ledger must never claim an audit exemption"


def test_an_empty_ledger_does_not_pin_the_audit_at_cannot_prove(
        tmp_checkpoint_dir):
    """The trigger was the FIRST `refute add` in a project, not anything the
    user recorded: an undeclared file landed in `unknown`, then `unscannable`,
    and the audit returned exit 3 for that project permanently. A zero-byte
    file was enough."""
    _checkpoint()
    assert cli.main(["audit", "privacy", "--project", PROJECT]) == 0

    _ledger_path().parent.mkdir(parents=True, exist_ok=True)
    _ledger_path().write_text("", encoding="utf-8")

    result = privacy.audit_project(project_dir=PROJECT)
    assert result["unscannable"] == []
    assert cli.main(["audit", "privacy", "--project", PROJECT]) == 0


def test_a_written_ledger_does_not_pin_the_audit_at_cannot_prove(
        tmp_checkpoint_dir):
    _checkpoint()
    _refute()

    result = privacy.audit_project(project_dir=PROJECT)
    assert result["unscannable"] == []
    assert cli.main(["audit", "privacy", "--project", PROJECT]) == 0


def test_forgotten_plaintext_surviving_in_the_ledger_is_reported(
        tmp_checkpoint_dir):
    _checkpoint()
    _refute(subject=CANARY)
    key = _tombstone(CANARY)

    result = privacy.audit_project(project_dir=PROJECT)
    hits = [f for f in result["findings"] if f["content_hash"] == key]
    assert hits, "the ledger was scanned but its residue was not reported"
    assert all(f["surface"] == "refutation-ledger" for f in hits)
    assert any(f["path"] == str(_ledger_path()) for f in hits)
    assert cli.main(["audit", "privacy", "--project", PROJECT]) == 1


def test_every_plaintext_ledger_field_is_scanned_not_only_the_subject(
        tmp_checkpoint_dir):
    """The scanner and the deleter must agree on WHICH fields hold plaintext.
    A field the audit reports but forget cannot reach is a permanent exit 1;
    a field forget reaches but the audit ignores is a silent exit 0 over live
    plaintext. Both sides read refutations._PLAINTEXT_FIELDS."""
    _checkpoint()
    _refute(verdict=CANARY)
    key = _tombstone(CANARY)

    result = privacy.audit_project(project_dir=PROJECT)
    assert [f for f in result["findings"] if f["content_hash"] == key]
    assert refutations.forget_content_key(key, project_dir=PROJECT)
    assert privacy.audit_project(project_dir=PROJECT)["findings"] == []


def test_an_unreadable_ledger_is_cannot_prove_never_clean(
        tmp_checkpoint_dir):
    _checkpoint()
    _refute()
    _ledger_path().write_bytes(b"\xff\xfe not utf-8 at all")

    result = privacy.audit_project(project_dir=PROJECT)
    assert str(_ledger_path()) in result["unscannable"]
    assert cli.main(["audit", "privacy", "--project", PROJECT]) == 3


# -- #647: anchors cross the scrubber before they are casefolded -------------

def test_anchors_are_redacted_before_they_are_canonicalized(
        tmp_checkpoint_dir):
    """`canonical_anchor` casefolds, and redact.py's table is case-SENSITIVE
    for several pattern classes, so scrubbing after the transform silently
    stopped matching. Ordering, not either function."""
    _refute(anchors=[f"anchor-{SECRET}"])

    text = _ledger_path().read_text(encoding="utf-8")
    assert SECRET not in text
    assert SECRET.casefold() not in text
    assert "[redacted:aws-key]" in text


def test_a_revised_anchor_set_is_redacted_too(tmp_checkpoint_dir):
    ref_id = _refute()
    refutations.revise(ref_id, channel="cli-tty",
                       evidence=["measurement:run-2"],
                       anchors=[f"anchor-{SECRET}"], project_dir=PROJECT)

    assert SECRET.casefold() not in _ledger_path().read_text(encoding="utf-8")


def test_the_redacted_anchor_is_still_the_one_guard_matches(
        tmp_checkpoint_dir):
    """The scrub happens on BOTH sides of the comparison, so an anchor a user
    can type is still an anchor that matches what was stored."""
    ref_id = _refute(anchors=[f"anchor-{SECRET}"], channel="cli-tty",
                     ratified=True)

    rows = refutations.guard("unrelated prompt text",
                             anchors=[f"anchor-{SECRET}"],
                             project_dir=PROJECT)
    assert [r["refutation_id"] for r in rows] == [ref_id]


def test_redaction_of_an_ordinary_anchor_changes_nothing(tmp_checkpoint_dir):
    ref_id = _refute(anchors=["#645", "command:daimon-why"])
    record = refutations.get(ref_id, project_dir=PROJECT)

    assert record["anchors"] == ["issue:645", "command:daimon-why"]
