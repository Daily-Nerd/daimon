"""Durable quote receipts and strict host source resolution (#594)."""

import json
from pathlib import Path

from daimon_briefing import provenance, serializer, store


_HASH = "a" * 64


def _source(session="S-origin", *, host="claude-code", author="alice"):
    return {
        "version": provenance.SOURCE_REF_VERSION,
        "host": host,
        "session_id": session,
        "locator": "managed",
        "author": author,
    }


def _receipt(session="S-origin", *, author="alice", checked_at="2026-08-05T10:00:00Z"):
    return provenance.quote_receipt(
        _source(session, author=author),
        {"algorithm": "sha256", "scope": "raw-file", "value": _HASH},
        outcome="verified", checked_at=checked_at,
        binding_mode="message-ids", message_ids=["msg-1"])


def _checkpoint(session, item, created):
    return {
        "session_id": session,
        "created": created,
        "working_context": {
            "active_topic": {"text": "topic", "trust": "inferred"},
            "open_questions": [item],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [],
            "uncertainties": [],
        },
    }


def test_capture_source_ref_infers_registered_hosts(tmp_path):
    claude = tmp_path / ".claude" / "projects"
    codex = tmp_path / ".codex"
    home = tmp_path

    c = provenance.capture_source_ref(
        "S-claude", claude / "slug" / "S-claude.jsonl",
        home=home, codex_home=codex, claude_projects=claude, author="alice")
    x = provenance.capture_source_ref(
        "S-codex", codex / "sessions" / "2026" / "S-codex.jsonl",
        home=home, codex_home=codex, claude_projects=claude, author="alice")
    w = provenance.capture_source_ref(
        "S-wind", home / ".windsurf" / "transcripts" / "S-wind.jsonl",
        home=home, codex_home=codex, claude_projects=claude, author="alice")

    assert (c["host"], c["locator"]) == ("claude-code", "managed")
    assert (x["host"], x["locator"]) == ("codex", "managed")
    assert (w["host"], w["locator"]) == ("windsurf", "managed")


def test_capture_source_ref_rejects_unsafe_session_id(tmp_path):
    for value in ("", "../escape", "a/b", "a*b", "a?b", "a[0]", "x" * 201):
        assert provenance.capture_source_ref(value, tmp_path / "x") is None


def test_capture_source_ref_marks_hermes_host_api_without_transcript():
    source = provenance.capture_source_ref(
        "S-hermes", host_hint="hermes", author="alice")

    assert source["host"] == "hermes"
    assert source["locator"] == "host-api"


def test_source_ref_rejects_unknown_host_and_unsafe_session():
    assert not provenance.valid_source_ref(dict(_source(), host="other"))
    assert not provenance.valid_source_ref(dict(_source(), session_id="../escape"))


def test_quote_receipt_is_complete_valid_and_bounded():
    receipt = _receipt()
    assert provenance.valid_quote_receipt(receipt)
    assert receipt["source"]["session_id"] == "S-origin"
    assert receipt["digest"]["value"] == _HASH
    assert receipt["verifier"] == {"id": "tier-f", "version": 1}
    assert receipt["outcome"] == "verified"
    assert receipt["binding"]["message_ids"] == ["msg-1"]
    # Representative receipt measured 394 bytes compact / 519 bytes with the
    # store's indent=2 formatting. Keep a generous compact bound so accidental
    # growth is reviewed instead of silently multiplying across every item.
    assert len(json.dumps(receipt, separators=(",", ":"))) < 600


def test_quote_receipt_rejects_malformed_time_and_unbounded_bindings():
    bad_time = json.loads(json.dumps(_receipt()))
    bad_time["checked_at"] = "sometime-Z"
    assert not provenance.valid_quote_receipt(bad_time)

    too_many = json.loads(json.dumps(_receipt()))
    too_many["binding"]["message_ids"] = [f"m-{i}" for i in range(65)]
    assert not provenance.valid_quote_receipt(too_many)


def test_quote_receipt_rejects_invalid_constructor_binding_mode():
    assert provenance.quote_receipt(
        _source(),
        {"algorithm": "sha256", "scope": "raw-file", "value": _HASH},
        outcome="verified", checked_at="2026-08-05T10:00:00Z",
        binding_mode="other") is None


def test_quote_receipt_rejects_every_malformed_evidence_component():
    mutations = (
        ("source", None),
        ("digest", None),
        ("digest.algorithm", "md5"),
        ("verifier", None),
        ("verifier.id", "other"),
        ("outcome", "unknown"),
        ("checked_at", None),
        ("binding", None),
        ("binding.message_ids", ["msg-1", "msg-1"]),
    )

    for dotted, value in mutations:
        receipt = json.loads(json.dumps(_receipt()))
        target = receipt
        parts = dotted.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
        assert not provenance.valid_quote_receipt(receipt), dotted


def test_binding_message_ids_rejects_invalid_receipt():
    assert provenance.binding_message_ids({}) == []


def test_verify_quotes_stamps_receipt_and_compatibility_mirrors():
    item = {
        "text": "decision", "trust": "verbatim",
        "quote": "the durable sentence", "source_message_ids": ["u-1"],
    }
    cp = _checkpoint("S-new", item, "2026-08-05T10:00:00Z")
    messages = [{"role": "user", "content": "the durable sentence", "id": "u-1"}]

    serializer.verify_quotes(
        cp, "user: the durable sentence", messages,
        source_ref=_source("S-new"), transcript_hash=_HASH)

    assert item["quote_verified"] is True
    assert item["last_verified"] == item["quote_provenance"]["checked_at"]
    assert item["quote_provenance"]["outcome"] == "verified"
    assert item["quote_provenance"]["binding"] == {
        "mode": "message-ids", "message_ids": ["u-1"]}


def test_verify_quotes_miss_keeps_complete_negative_receipt():
    item = {"text": "decision", "trust": "verbatim", "quote": "not present"}
    cp = _checkpoint("S-new", item, "2026-08-05T10:00:00Z")

    serializer.verify_quotes(
        cp, "user: different text", source_ref=_source("S-new"),
        transcript_hash=_HASH)

    assert item["trust"] == "inferred"
    assert item["quote_verified"] is False
    assert "last_verified" not in item
    assert item["quote_provenance"]["outcome"] == "not-verified"
    assert item["quote_provenance"]["binding"] == {
        "mode": "transcript-scan", "message_ids": []}


def test_model_cannot_self_issue_source_or_quote_receipt():
    receipt = _receipt()
    item = {"text": "claim", "trust": "inferred", "quote_provenance": receipt}
    cp = _checkpoint("S-new", item, "2026-08-05T10:00:00Z")
    cp["source_ref"] = _source("S-new")

    serializer.strip_code_owned_keys(cp)

    assert "source_ref" not in cp
    assert "quote_provenance" not in item


def test_resolver_unique_ambiguous_absent_remote_and_unsupported(tmp_path):
    claude = tmp_path / ".claude" / "projects"
    one = claude / "one"
    one.mkdir(parents=True)
    target = one / "S-one.jsonl"
    target.write_text("{}\n", encoding="utf-8")
    two = claude / "two"
    two.mkdir()
    duplicate = two / "S-duplicate.jsonl"
    duplicate.write_text("{}\n", encoding="utf-8")
    (one / "S-duplicate.jsonl").write_text("{}\n", encoding="utf-8")
    resolver = provenance.SourceResolver(
        home=tmp_path, codex_home=tmp_path / ".codex",
        claude_projects=claude, current_author="alice")

    found = resolver.resolve(_source("S-one"))
    assert found.state == "resolved" and found.path == target

    assert resolver.resolve(_source("S-duplicate")).state == "ambiguous"

    assert resolver.resolve(_source("S-none")).state == "absent-local"
    assert resolver.resolve(_source("S-none", author="bob")).state == "remote-author"
    assert resolver.resolve(_source("S-none", author="unknown")).state == "absent-local"
    manual = dict(_source("S-manual"), host="manual", locator="unsupported")
    assert resolver.resolve(manual).state == "unsupported"
    assert resolver.resolve({"source": "../../escape"}).state == "unsupported"


def test_resolver_local_candidate_wins_over_different_author(tmp_path):
    claude = tmp_path / ".claude" / "projects" / "slug"
    claude.mkdir(parents=True)
    target = claude / "S-team.jsonl"
    target.write_text("{}\n", encoding="utf-8")
    resolver = provenance.SourceResolver(
        home=tmp_path, claude_projects=claude.parent,
        current_author="alice")

    result = resolver.resolve(_source("S-team", author="bob"))
    assert result.state == "resolved"


def test_resolver_supports_codex_recursive_sessions(tmp_path):
    codex = tmp_path / ".codex"
    target = codex / "sessions" / "2026" / "08" / "S-codex.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text("{}\n", encoding="utf-8")
    resolver = provenance.SourceResolver(home=tmp_path, codex_home=codex)

    result = resolver.resolve(_source("S-codex", host="codex"))
    assert result.state == "resolved" and result.path == target


def test_resolver_supports_windsurf_roots_and_skips_missing_candidate(tmp_path):
    target = tmp_path / ".windsurf" / "transcripts" / "S-wind.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text("{}\n", encoding="utf-8")
    resolver = provenance.SourceResolver(home=tmp_path)

    result = resolver.resolve(_source("S-wind", host="windsurf"))

    assert result.state == "resolved" and result.path == target


def test_resolver_rejects_managed_host_without_registered_roots(tmp_path):
    resolver = provenance.SourceResolver(home=tmp_path)
    source = dict(_source("S-gemini", host="gemini"), locator="managed")

    assert resolver._roots("gemini") is None
    assert resolver.resolve(source).state == "unsupported"


def test_resolver_treats_index_scan_oserror_as_absent(tmp_path, monkeypatch):
    claude = tmp_path / ".claude" / "projects"
    resolver = provenance.SourceResolver(home=tmp_path, claude_projects=claude)

    def denied_glob(path, pattern):
        if path == claude:
            raise OSError("denied")
        return ()

    monkeypatch.setattr(Path, "glob", denied_glob)

    assert resolver.resolve(_source("S-denied")).state == "absent-local"


def test_resolver_skips_candidate_path_runtime_errors(tmp_path, monkeypatch):
    resolver = provenance.SourceResolver(home=tmp_path)

    def broken_is_file(path):
        raise RuntimeError(f"cannot inspect {path}")

    monkeypatch.setattr(Path, "is_file", broken_is_file)

    assert resolver.resolve(
        _source("S-broken", host="windsurf")).state == "absent-local"


def test_resolver_reports_unreadable_without_fallback(tmp_path, monkeypatch):
    claude = tmp_path / ".claude" / "projects" / "slug"
    claude.mkdir(parents=True)
    target = claude / "S-denied.jsonl"
    target.write_text("{}\n", encoding="utf-8")
    real_open = Path.open

    def denied(path, *args, **kwargs):
        if path == target:
            raise PermissionError("denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    resolver = provenance.SourceResolver(
        home=tmp_path, claude_projects=claude.parent)
    assert resolver.resolve(_source("S-denied")).state == "unreadable"


def test_resolver_rejects_symlink_escape_from_managed_root(tmp_path):
    root = tmp_path / ".claude" / "projects"
    bucket = root / "slug"
    bucket.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    (bucket / "S-link.jsonl").symlink_to(outside)
    resolver = provenance.SourceResolver(home=tmp_path, claude_projects=root)

    assert resolver.resolve(_source("S-link")).state == "absent-local"


def test_carried_receipt_survives_source_checkpoint_gc(
    tmp_path, tmp_checkpoint_dir, monkeypatch
):
    monkeypatch.setenv("DAIMON_CHECKPOINT_KEEP", "1")
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    claude = tmp_path / ".claude" / "projects" / "slug"
    claude.mkdir(parents=True)
    transcript = claude / "S-origin.jsonl"
    transcript.write_text('{"role":"user","content":"durable quote"}\n',
                          encoding="utf-8")
    receipt = _receipt()
    origin_item = {
        "text": "durable claim", "trust": "verbatim", "quote": "durable quote",
        "quote_provenance": receipt,
    }
    store.write_checkpoint(
        "S-origin", _checkpoint("S-origin", origin_item,
                                "2026-08-05T10:00:00Z"), project_dir="/p/A")

    carried = dict(origin_item, carried_from="S-origin")
    store.write_checkpoint(
        "S-current", _checkpoint("S-current", carried,
                                 "2026-08-05T11:00:00Z"), project_dir="/p/A")

    assert store.read_checkpoint("S-origin") is None
    current = store.read_checkpoint("S-current")
    kept = current["working_context"]["open_questions"][0]
    assert provenance.valid_quote_receipt(kept["quote_provenance"])
    resolver = provenance.SourceResolver(
        home=tmp_path, claude_projects=claude.parent, current_author="alice")
    assert resolver.resolve(kept["quote_provenance"]["source"]).state == "resolved"
