"""#616: serialize.log and backend-stderr.log inside the privacy contract.

`logs/*.log` was declared exempt-no-plaintext ("no item text by
construction") while two writers under the glob falsified the claim:

  * serializer's quote-verification and outcome-grounding downgrade lines
    logged the item's OWN text (secret-shape redacted, not item-redacted),
    and the CLI routes those records into serialize.log;
  * llm._log_backend_stderr appends backend stderr/stdout, which CLI
    backends can seed with prompt fragments — transcript text (#141).

The fix keeps serialize.log's second job intact (it is the ledger `status`
parses for capture stats), so it is NOT purged wholesale like the #605
crash sink. Instead:

  * the downgrade writers log a CONTENT HASH — the same normalize.content_key
    a later `forget` of that text would tombstone — never the text;
  * `forget` scrubs LEGACY downgrade payloads by line shape (the two known
    warning prefixes), leaving ledger result lines untouched;
  * backend-stderr.log cannot be no-plaintext by construction, so it is
    declared plaintext=True and purged wholesale at forget, the crash-sink
    posture.

Canary style follows test_crash_log_privacy.py: distinctive synthetic
literals, never vendor-prefix shapes.
"""
import logging

from daimon_briefing import (cli, config, normalize, privacy, serializer,
                             store, surfaces)

PROJECT = "/p/616-logs"
CANARY = "zqxlogcanary6161 the rollout gate is held by the blue worker"
CANARY_ECHO = "zqxlogcanary6162 echoed only in daimon output"
CANARY_OUTCOME = "zqxlogcanary6163 deploy succeeded on the green cluster"
KEEPER = "an unrelated decision that must survive"


def _serialize_log_path():
    return config.log_dir() / "serialize.log"


def _backend_log_path():
    return config.log_dir() / "backend-stderr.log"


def _write_checkpoint():
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": CANARY, "trust": "inferred"},
            {"text": KEEPER, "trust": "inferred"}]},
    }, project_dir=PROJECT)


def _seed_backend_log() -> object:
    path = _backend_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "--- 2026-08-06T00:00:00Z ---\n"
        "claude exited 1\n"
        f"prompt fragment echoed by the backend: {CANARY}\n",
        encoding="utf-8")
    return path


# ---- the writers stop persisting item text --------------------------------


def test_quote_downgrade_logs_hash_never_text(caplog):
    ckpt = {"working_context": {"recent_decisions": [
        {"text": CANARY, "trust": "verbatim",
         "quote": "this quote appears nowhere"}]}}
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.serializer"):
        downgraded = serializer.verify_quotes(
            ckpt, "a transcript that contains none of the item")
    assert downgraded == 1
    assert CANARY not in caplog.text, \
        "the downgrade line is the leak #616 closes — no item text in logs"
    assert normalize.content_key(CANARY) in caplog.text, \
        "the content hash is the surviving diagnostic handle"


def test_outcome_downgrade_logs_hash_never_text(caplog):
    ckpt = {"working_context": {"recent_decisions": [
        {"text": CANARY_OUTCOME, "trust": "verbatim"}]}}
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.serializer"):
        downgraded = serializer.ground_outcomes(ckpt, {"m-signal"})
    assert downgraded == 1
    assert CANARY_OUTCOME not in caplog.text
    assert normalize.content_key(CANARY_OUTCOME) in caplog.text


# ---- the registry stops lying about the two files -------------------------


def test_registry_declares_backend_stderr_log_reachable():
    s = surfaces.match("logs/backend-stderr.log")
    assert s is not None
    assert s.plaintext is True and s.delete == "wholesale-purge"
    assert s.walker == "forget"
    # serialize.log keeps the exempt claim — true again by construction once
    # the downgrade writers log hashes (and forget scrubs the legacy lines).
    assert surfaces.match("logs/serialize.log").delete == "exempt-no-plaintext"
    assert not any(x.issue == "#616" for x in surfaces.SURFACES), \
        "#616 is closed — no entry may still cite it as an open gap"


# ---- backend-stderr.log: purge on forget ----------------------------------


def test_forget_purges_backend_stderr_log(tmp_checkpoint_dir):
    path = _seed_backend_log()
    _write_checkpoint()
    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0
    assert not path.exists(), "backend echo daimon persisted must go"


def test_forget_dry_run_never_purges_backend_log(tmp_checkpoint_dir):
    path = _seed_backend_log()
    _write_checkpoint()
    assert cli.main(["forget", CANARY, "--project", PROJECT,
                     "--dry-run"]) == 0
    assert path.exists()


def test_a_refused_forget_never_purges_backend_log(tmp_checkpoint_dir):
    path = _seed_backend_log()
    _write_checkpoint()
    assert cli.main(["forget", "no such value here",
                     "--project", PROJECT]) == 1
    assert path.exists()


def test_backend_purge_reports_count_and_never_raises(tmp_checkpoint_dir):
    _seed_backend_log()
    assert store.purge_backend_stderr_log() == (1, None)
    assert store.purge_backend_stderr_log() == (0, None)


def test_backend_purge_never_unlinks_through_a_symlink(tmp_checkpoint_dir,
                                                       tmp_path):
    outside = tmp_path / "my-notes"
    outside.mkdir()
    kept = outside / "important.log"
    kept.write_text("a user file", encoding="utf-8")
    path = _backend_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(kept)
    purged, err = store.purge_backend_stderr_log()
    assert purged == 0
    assert err is not None
    assert kept.exists(), "unlinked a file daimon did not write"


def test_forget_survives_a_failed_backend_purge(tmp_checkpoint_dir,
                                                monkeypatch, capsys):
    _seed_backend_log()
    _write_checkpoint()
    monkeypatch.setattr(store, "purge_backend_stderr_log",
                        lambda: (0, "disk on fire"))
    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0
    assert "disk on fire" in capsys.readouterr().out


# ---- serialize.log: legacy downgrade payloads scrubbed at forget ----------


LEGACY_LOG = (
    "2026-08-01T00:00:00Z WARNING daimon_briefing.serializer: "
    f"quote verification: downgraded verbatim->inferred: {CANARY}\n"
    "2026-08-01T00:00:01Z WARNING daimon_briefing.serializer: "
    "quote verification: downgraded verbatim->inferred (echo-only: quote "
    "appears only in daimon's own injected output): "
    f"{CANARY_ECHO}\n"
    "2026-08-01T00:00:02Z WARNING daimon_briefing.serializer: "
    "outcome grounding: unwitnessed outcome claim downgraded "
    f"verbatim->inferred (no signal cited): {CANARY_OUTCOME}\n"
    "2026-08-01T00:00:03Z INFO daimon_briefing.serializer: "
    "quote verification: 2 verbatim item(s) downgraded to inferred "
    "(1 echo-only)\n"
    "wrote checkpoint: /tmp/x/2026-08-01.json (took 12s)\n"
    "2026-08-02T00:00:00Z WARNING daimon_briefing.serializer: "
    "quote verification: downgraded verbatim->inferred "
    "(content hash abc123def456)\n"
)


def _seed_serialize_log(text: str = LEGACY_LOG) -> object:
    path = _serialize_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_forget_scrubs_legacy_downgrade_payloads(tmp_checkpoint_dir):
    path = _seed_serialize_log()
    _write_checkpoint()
    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0
    text = path.read_text(encoding="utf-8")
    assert CANARY not in text
    assert CANARY_ECHO not in text
    assert CANARY_OUTCOME not in text
    # the ledger job survives: result lines and counts are what status parses
    assert "wrote checkpoint: /tmp/x/2026-08-01.json (took 12s)" in text
    assert "2 verbatim item(s) downgraded to inferred" in text
    # current-format hash lines are already clean and stay byte-identical
    assert "(content hash abc123def456)" in text


def test_scrub_drops_multiline_payload_continuations(tmp_checkpoint_dir):
    _seed_serialize_log(
        "2026-08-01T00:00:00Z WARNING daimon_briefing.serializer: "
        f"quote verification: downgraded verbatim->inferred: {CANARY}\n"
        f"second line of the same item body {CANARY_ECHO}\n"
        "2026-08-01T00:00:01Z INFO daimon_briefing.serializer: "
        "quote verification: 1 verbatim item(s) downgraded to inferred "
        "(0 echo-only)\n")
    scrubbed, err = store.scrub_serialize_log()
    assert err is None and scrubbed >= 1
    text = _serialize_log_path().read_text(encoding="utf-8")
    assert CANARY not in text
    assert CANARY_ECHO not in text, \
        "a multi-line payload's continuation lines must go with it"
    assert "1 verbatim item(s) downgraded to inferred" in text


def test_scrub_is_idempotent(tmp_checkpoint_dir):
    path = _seed_serialize_log()
    assert store.scrub_serialize_log()[1] is None
    first = path.read_text(encoding="utf-8")
    scrubbed, err = store.scrub_serialize_log()
    assert err is None
    assert path.read_text(encoding="utf-8") == first


def test_scrub_missing_log_is_a_clean_zero(tmp_checkpoint_dir):
    assert store.scrub_serialize_log() == (0, None)


def test_scrub_refuses_a_symlinked_log(tmp_checkpoint_dir, tmp_path):
    outside = tmp_path / "my-notes"
    outside.mkdir()
    kept = outside / "user.log"
    kept.write_text(LEGACY_LOG, encoding="utf-8")
    path = _serialize_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(kept)
    scrubbed, err = store.scrub_serialize_log()
    assert scrubbed == 0
    assert err is not None
    assert CANARY in kept.read_text(encoding="utf-8"), \
        "rewrote a file daimon did not create"


def test_forget_survives_a_failed_scrub(tmp_checkpoint_dir, monkeypatch,
                                        capsys):
    _seed_serialize_log()
    _write_checkpoint()
    monkeypatch.setattr(store, "scrub_serialize_log",
                        lambda: (0, "scrub exploded"))
    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0
    assert "scrub exploded" in capsys.readouterr().out


# ---- the audit reports what it can prove ----------------------------------


def test_audit_reports_backend_log_store(tmp_checkpoint_dir):
    _seed_backend_log()
    _write_checkpoint()
    result = privacy.audit_project(project_dir=PROJECT)
    assert result["backend_log"]["present"] is True
    assert result["backend_log"]["age_days"] is not None


def test_audit_reports_backend_log_absent(tmp_checkpoint_dir):
    _write_checkpoint()
    result = privacy.audit_project(project_dir=PROJECT)
    assert result["backend_log"]["present"] is False
