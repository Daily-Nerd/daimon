"""#1132 2c-1: `daimon forget` reaches the trust (quarantine) ledger.

A quarantine row carries prose in `reason` and `evidence`. Forget used to
scrub every other plaintext ledger and skip this one, so a forgotten value
stayed readable in quarantine prose. The deleter REDACTS in place and never
drops a record: dropping one would lift the quarantine and let the withheld
value show again, so the hash latch (`value_key`) has to survive.
"""
from daimon_briefing import cli, ledger_census, normalize, privacy, store, trust

PROJECT = "/p/forget-trust-ledger"
CANARY = "zqxtrustcanary2c1 the staging db password rotates on fridays"
EVIDENCE_CANARY = "artifact:zqxtrustcanary2c1/rotation-notes.md"
OTHER_VALUE = "an unrelated quarantined claim that must keep its prose"
QUARANTINED = "a fabricated claim that the deploy key rotates hourly"
KEY = normalize.content_key(CANARY)
MARKER = f"[forgotten:{KEY}]"


def _checkpoint_with(*texts):
    items = [{"text": t, "trust": "inferred"} for t in texts]
    store.write_checkpoint(
        "S1", {"session_id": "S1", "created": "2026-08-01T00:00:00Z",
               "working_context": {"recent_decisions": items}},
        project_dir=PROJECT)


def _quarantine(text=QUARANTINED, reason="looks fabricated", evidence=None):
    return trust.propose(
        text=text, kind="decision", reason=reason,
        evidence=evidence or ["issue:1109"], channel="cli-tty",
        project_dir=PROJECT)


def _ledger():
    return trust._path(PROJECT)


def _forget(value):
    assert cli.main(["forget", value, "--project", PROJECT]) == 0


def _record(tid):
    return trust.get(tid, project_dir=PROJECT)


# ---- 1. a value in `reason` -------------------------------------------------


def test_forget_redacts_a_value_in_an_active_quarantine_reason(
        tmp_checkpoint_dir):
    _checkpoint_with(CANARY)
    tid = _quarantine(reason=CANARY)
    latch = trust.active_value_keys(project_dir=PROJECT)
    _forget(CANARY)
    assert CANARY not in _ledger().read_text(encoding="utf-8")
    record = _record(tid)
    assert record is not None and record["state"] == "active"
    assert record["reason"] == MARKER
    assert trust.active_value_keys(project_dir=PROJECT) == latch


# ---- 2. a value in one `evidence` entry -------------------------------------


def test_forget_redacts_only_the_matching_evidence_entry(tmp_checkpoint_dir):
    _checkpoint_with(EVIDENCE_CANARY)
    tid = _quarantine(evidence=["issue:1109", EVIDENCE_CANARY, "receipt:r1"])
    _forget(EVIDENCE_CANARY)
    assert "zqxtrustcanary2c1" not in _ledger().read_text(encoding="utf-8")
    record = _record(tid)
    assert record["evidence"] == [
        "issue:1109", f"[forgotten:{normalize.content_key(EVIDENCE_CANARY)}]",
        "receipt:r1"]
    assert record["reason"] == "looks fabricated"


# ---- 3. the quarantined value itself ----------------------------------------


def test_forgetting_the_quarantined_value_redacts_all_prose_and_keeps_latch(
        tmp_checkpoint_dir):
    _checkpoint_with(CANARY)
    tid = _quarantine(text=CANARY, reason="paraphrases the secret: " + CANARY[:20],
                      evidence=["issue:1109", "message:" + CANARY[:20]])
    latch = trust.active_value_keys(project_dir=PROJECT)
    assert latch
    _forget(CANARY)
    text = _ledger().read_text(encoding="utf-8")
    assert CANARY[:20] not in text
    record = _record(tid)
    assert record is not None and record["state"] == "active"
    assert record["reason"] == MARKER
    assert record["evidence"] == [MARKER, MARKER]
    assert record["value_key"] == KEY
    assert trust.active_value_keys(project_dir=PROJECT) == latch


# ---- 4. an unrelated quarantine is untouched --------------------------------


def test_an_unrelated_quarantine_is_byte_for_byte_unchanged(
        tmp_checkpoint_dir):
    _checkpoint_with(CANARY)
    other = _quarantine(text=OTHER_VALUE, reason="unrelated reason text")
    _quarantine(reason=CANARY)
    before = [ln for ln in _ledger().read_bytes().split(b"\n")
              if other.encode() in ln]
    assert before
    _forget(CANARY)
    after = [ln for ln in _ledger().read_bytes().split(b"\n")
             if other.encode() in ln]
    assert after == before


# ---- 5. idempotent ----------------------------------------------------------


def test_a_second_redact_changes_nothing(tmp_checkpoint_dir):
    _checkpoint_with(CANARY)
    tid = _quarantine(text=CANARY, reason=CANARY)
    _forget(CANARY)
    once = _ledger().read_bytes()
    assert CANARY.encode() not in once
    # forget already ran the deleter once; a direct re-run finds nothing.
    assert trust.redact_content_key(KEY, project_dir=PROJECT) == []
    assert _ledger().read_bytes() == once
    assert tid in once.decode()


# ---- 6. torn and non-JSON lines survive verbatim ----------------------------


def test_torn_and_non_json_lines_survive_verbatim(tmp_checkpoint_dir):
    _checkpoint_with(CANARY)
    _quarantine(reason=CANARY)
    junk = [b"not json at all", b'{"torn": "half a row', b"\xff\xfe raw bytes"]
    with _ledger().open("ab") as handle:
        for line in junk:
            handle.write(line + b"\n")
    _forget(CANARY)
    lines = _ledger().read_bytes().split(b"\n")
    for line in junk:
        assert line in lines
    assert CANARY.encode() not in _ledger().read_bytes()


# ---- 8. the audit and the census agree --------------------------------------


def test_audit_and_census_report_the_trust_ledger_clean(tmp_checkpoint_dir):
    _checkpoint_with(CANARY)
    _quarantine(reason=CANARY)
    _forget(CANARY)
    result = privacy.audit_project(project_dir=PROJECT)
    hits = [f for f in result["findings"]
            if f["content_hash"] == KEY and f["surface"] == "trust-ledger"]
    assert hits == []
    census = ledger_census.census_bucket(store.project_slug(PROJECT))
    assert census["ledgers"]["trust.jsonl"]["tombstoned_present"] == 0


# ---- 9. the unsafe deleter is gone ------------------------------------------


def test_the_trust_module_exposes_no_forget_function():
    assert [n for n in dir(trust)
            if n.startswith("forget") and callable(getattr(trust, n))] == []


# ---- value_key and forget's key agree for a secret-shaped value --------------

SECRET_SHAPED = "the deploy key is api_key=abcd1234efgh5678 for prod right now"


def test_forgetting_a_secret_shaped_quarantined_value_redacts_all_prose(
        tmp_checkpoint_dir):
    _checkpoint_with(SECRET_SHAPED)
    tid = _quarantine(text=SECRET_SHAPED, reason="the claim about the deploy key",
                      evidence=["issue:1109", "message:deploy key claim"])
    latch = trust.active_value_keys(project_dir=PROJECT)
    _forget(SECRET_SHAPED)
    record = _record(tid)
    assert record["reason"].startswith("[forgotten:")
    assert all(e.startswith("[forgotten:") for e in record["evidence"])
    assert trust.active_value_keys(project_dir=PROJECT) == latch


# ---- --dry-run previews the quarantines it would redact ----------------------


def test_dry_run_names_the_quarantines_and_writes_nothing(
        tmp_checkpoint_dir, capsys):
    _checkpoint_with(CANARY)
    tid = _quarantine(reason=CANARY)
    before = _ledger().read_bytes()
    capsys.readouterr()
    assert cli.main(["forget", CANARY, "--dry-run", "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert f"would redact prose in 1 quarantine(s) ({tid})" in out
    assert _ledger().read_bytes() == before
    assert "[forgotten:" not in out
