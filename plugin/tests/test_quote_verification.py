"""Deterministic verbatim-quote verification at serialize time (#125)."""

import hashlib
import json
import logging

import pytest

from daimon_briefing import cli, serializer, store, transcript
from tests.conftest import FIXTURES, make_messages


# ---- Unit A: quote_matches (tier-f normalization) ----

def test_exact_substring_matches():
    hay = "user: we decided to adopt the D-007 prompt for the serializer"
    assert serializer.quote_matches("adopt the D-007 prompt for the serializer", hay)


def test_whitespace_fold_matches():
    hay = "assistant: the   chunk\n\tthreshold  is 1200 lines exactly"
    assert serializer.quote_matches("the chunk threshold is 1200 lines exactly", hay)


def test_markdown_bold_only_difference_matches():
    # Quote is plain; transcript keeps raw markdown emphasis.
    hay = "assistant: we will **freeze the verbatim pin** on reconsolidation"
    assert serializer.quote_matches("freeze the verbatim pin on reconsolidation", hay)


def test_markdown_backtick_only_difference_matches():
    hay = "assistant: call `serialize_strict` with the injected chat seam"
    assert serializer.quote_matches("call serialize_strict with the injected chat seam", hay)


def test_markdown_list_marker_only_difference_matches():
    hay = "assistant: the plan:\n- rotate the pointer before writing latest"
    assert serializer.quote_matches("rotate the pointer before writing latest", hay)


def test_casefold_matches():
    hay = "user: We Adopt The D-007 Prompt For The Serializer Now"
    assert serializer.quote_matches("we adopt the d-007 prompt for the serializer now", hay)


def test_redacted_placeholder_stripped_from_fragment():
    # Audit path: a STORED quote carries a redaction marker (secrets are masked
    # at write), but the raw transcript still has the real secret. Stripping the
    # placeholder from the quote lets the surviving boundary text still match.
    hay = "assistant: please set the staging token sk_live_abc123def now"
    assert serializer.quote_matches("set the staging token [redacted:api-key]", hay)


def test_ellipsis_split_in_order_passes():
    hay = ("assistant: first we rotate the pointer chain, then much later "
           "we write the new latest atomically")
    assert serializer.quote_matches(
        "first we rotate the pointer chain...write the new latest atomically", hay
    )


def test_ellipsis_split_out_of_order_fails():
    hay = ("assistant: first we rotate the pointer chain, then much later "
           "we write the new latest atomically")
    # fragments present but in the WRONG order -> must fail.
    assert not serializer.quote_matches(
        "write the new latest atomically...first we rotate the pointer chain", hay
    )


def test_short_fragments_dropped_unverifiable_is_false():
    hay = "assistant: yes ok sure fine good done now"
    # every ellipsis fragment normalizes below 8 chars -> unverifiable -> false
    assert not serializer.quote_matches("yes...ok...sure", hay)


@pytest.mark.parametrize("paraphrase", [
    "we chose D-007 because it extracts more decisions than the alternative",
    "the serializer now freezes pins so recall cannot rewrite them",
    "rotating pointers keeps a deep history well for reconstruction",
])
def test_paraphrase_set_fails_precision_guard(paraphrase):
    # Source text the paraphrases are ABOUT, but never quote verbatim.
    hay = ("assistant: we adopted the D-007 prompt. verbatim pins are frozen at "
           "capture. pointer rotation retains prev checkpoints for the well.")
    assert not serializer.quote_matches(paraphrase, hay)


def test_empty_or_nonstring_quote_is_false():
    assert not serializer.quote_matches("", "some haystack text here")
    assert not serializer.quote_matches(None, "some haystack text here")


# ---- Unicode punctuation folding + inline list markers (#208) ----

def test_curly_apostrophe_in_transcript_matches_straight_in_quote():
    # Real downgrade shape: transcript renders a curly apostrophe (U+2019),
    # the model quotes the ASCII one — otherwise byte-faithful.
    hay = "assistant: we don’t rotate the pointer before the write lands"
    assert serializer.quote_matches(
        "we don't rotate the pointer before the write lands", hay)


def test_straight_apostrophe_in_transcript_matches_curly_in_quote():
    hay = "assistant: we don't rotate the pointer before the write lands"
    assert serializer.quote_matches(
        "we don’t rotate the pointer before the write lands", hay)


def test_curly_double_quotes_fold_to_straight():
    hay = "user: call it “the pointer chain” in the docs"
    assert serializer.quote_matches('call it "the pointer chain" in the docs', hay)


def test_en_and_em_dash_fold_to_hyphen():
    hay = ("assistant: retry windows are 3–5 seconds — "
           "measured on the gateway")
    assert serializer.quote_matches(
        "retry windows are 3-5 seconds - measured on the gateway", hay)


def test_nonbreaking_space_folds_to_space():
    hay = "assistant: bump the limit to 1200 lines for chunking"
    assert serializer.quote_matches(
        "bump the limit to 1200 lines for chunking", hay)


def test_unicode_ellipsis_still_splits_before_folding():
    # U+2026 is an elision marker split on the RAW quote — punctuation folding
    # must not eat it before quote_matches sees it.
    hay = ("assistant: first we rotate the pointer chain, then much later "
           "we write the new latest atomically")
    assert serializer.quote_matches(
        "first we rotate the pointer chain…write the new latest atomically",
        hay)


def test_inline_list_marker_in_quote_matches_line_anchored_haystack():
    # Real downgrade shape: the haystack marker sits at a line start (which
    # line-anchored stripping removes) while the quote reflows the same marker
    # mid-string — stripping must be symmetric across both placements.
    hay = ("assistant: PR up: **https://github.com/x/y/pull/11**\n"
           "- Branch `feat/thing`")
    assert serializer.quote_matches(
        "PR up: **https://github.com/x/y/pull/11** - Branch `feat/thing`", hay)


def test_inline_numbered_markers_match_numbered_list():
    hay = "assistant: plan:\n1. rotate the pointer chain\n2. write the new latest"
    assert serializer.quote_matches(
        "1. rotate the pointer chain 2. write the new latest", hay)


def test_hyphenated_words_survive_marker_stripping():
    hay = "assistant: we must re-verify the foo-bar pointer chain now"
    assert serializer.quote_matches(
        "we must re-verify the foo-bar pointer chain now", hay)
    # The hyphen is load-bearing: a de-hyphenated haystack must NOT match.
    assert not serializer.quote_matches(
        "we must re-verify the foo-bar pointer chain now",
        "assistant: we must re verify the foo bar pointer chain now")


def test_decimals_survive_marker_stripping():
    hay = "assistant: the sampling constant stays at 3.14 for this run"
    assert serializer.quote_matches(
        "the sampling constant stays at 3.14 for this run", hay)
    # `3. ` is a marker-shaped token only when space-delimited; the intact
    # decimal in the quote must NOT match a haystack where it is broken up.
    assert not serializer.quote_matches(
        "the sampling constant stays at 3.14 for this run",
        "assistant: the sampling constant stays at 3. 14 for this run")


# ---- Unit B: verify_quotes (in-place mutation + logging) ----

def _cp_with(items_by_kind):
    cp = {
        "session_id": "S1",
        "working_context": {
            "active_topic": {"text": "topic", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [], "uncertainties": [], "contradictions_flagged": [],
        },
    }
    for (section, key), items in items_by_kind.items():
        cp[section][key] = items
    return cp


def test_verify_quotes_stamps_true_on_hit():
    cp = _cp_with({("working_context", "recent_decisions"): [
        {"text": "d", "trust": "verbatim", "quote": "adopt the D-007 prompt"}]})
    n = serializer.verify_quotes(cp, "assistant: adopt the D-007 prompt today")
    item = cp["working_context"]["recent_decisions"][0]
    assert n == 0
    assert item["trust"] == "verbatim"
    assert item["quote_verified"] is True


def test_verify_quotes_downgrades_on_miss(caplog):
    cp = _cp_with({("working_context", "recent_decisions"): [
        {"text": "a fabricated decision line", "trust": "verbatim",
         "quote": "this exact sentence is nowhere in the transcript at all"}]})
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.serializer"):
        n = serializer.verify_quotes(cp, "assistant: something entirely unrelated")
    item = cp["working_context"]["recent_decisions"][0]
    assert n == 1
    assert item["trust"] == "inferred"
    assert item["quote_verified"] is False
    # downgrade is visible in the log with an item-text prefix
    assert any("fabricated decision" in r.getMessage() for r in caplog.records)


def test_verify_quotes_downgrade_log_redacts_secret(caplog):
    # #141: the downgrade warning is the one verify_quotes line that carries
    # item text, and it fires PRE-redaction — a secret inside a downgraded
    # item must be scrubbed in the log line while the checkpoint item itself
    # stays raw (store redacts it at write time, ids must hash redacted text).
    secret = "AKIAIOSFODNN7EXAMPLE"
    cp = _cp_with({("working_context", "recent_decisions"): [
        {"text": f"rotate key {secret} next", "trust": "verbatim",
         "quote": "this exact sentence is nowhere in the transcript at all"}]})
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.serializer"):
        serializer.verify_quotes(cp, "assistant: something entirely unrelated")
    msgs = [r.getMessage() for r in caplog.records]
    assert not any(secret in m for m in msgs)
    assert any("[redacted:aws-key]" in m for m in msgs)
    item = cp["working_context"]["recent_decisions"][0]
    assert item["text"] == f"rotate key {secret} next"  # log-only scrub


def test_verify_quotes_leaves_inferred_items_unstamped():
    cp = _cp_with({("working_context", "recent_decisions"): [
        {"text": "d", "trust": "inferred", "quote": ""}]})
    serializer.verify_quotes(cp, "assistant: anything")
    assert "quote_verified" not in cp["working_context"]["recent_decisions"][0]


# ---- #215: last_verified stamp on a verify hit ----

def test_verify_quotes_stamps_last_verified_iso_on_hit():
    cp = _cp_with({("working_context", "recent_decisions"): [
        {"text": "d", "trust": "verbatim", "quote": "adopt the D-007 prompt"}]})
    serializer.verify_quotes(cp, "assistant: adopt the D-007 prompt today")
    item = cp["working_context"]["recent_decisions"][0]
    assert item["quote_verified"] is True
    # Parseable by the same ISO-8601 UTC stamp store.py's `created`/`ts` use.
    import datetime as dt
    parsed = dt.datetime.strptime(item["last_verified"], "%Y-%m-%dT%H:%M:%SZ")
    assert parsed.tzinfo is None  # naive per strptime; the format IS UTC (Z)


def test_verify_quotes_does_not_stamp_last_verified_on_miss():
    cp = _cp_with({("working_context", "recent_decisions"): [
        {"text": "a fabricated decision line", "trust": "verbatim",
         "quote": "this exact sentence is nowhere in the transcript at all"}]})
    serializer.verify_quotes(cp, "assistant: something entirely unrelated")
    item = cp["working_context"]["recent_decisions"][0]
    assert item["quote_verified"] is False
    assert "last_verified" not in item


def test_verify_quotes_does_not_stamp_last_verified_for_inferred_items():
    cp = _cp_with({("working_context", "recent_decisions"): [
        {"text": "d", "trust": "inferred", "quote": ""}]})
    serializer.verify_quotes(cp, "assistant: anything")
    assert "last_verified" not in cp["working_context"]["recent_decisions"][0]


# ---- #358: id-scoped verification with whole-transcript fallback ----

_ID_MSGS = [
    {"role": "user", "content": "we adopt the D-007 prompt", "id": "u-1"},
    {"role": "assistant", "content": "understood, cache stays keyed", "id": "a-2"},
]


def test_verify_quotes_id_scoped_hit_keeps_binding_and_stamps():
    cp = _cp_with({("working_context", "recent_decisions"): [
        {"text": "d", "trust": "verbatim", "quote": "adopt the D-007 prompt",
         "source_message_ids": ["u-1"]}]})
    n = serializer.verify_quotes(
        cp, serializer._render_transcript(_ID_MSGS), _ID_MSGS)
    item = cp["working_context"]["recent_decisions"][0]
    assert n == 0
    assert item["quote_verified"] is True
    assert item["source_message_ids"] == ["u-1"]  # binding survives
    assert "last_verified" in item


def test_verify_quotes_wrong_binding_falls_back_and_drops_ids(caplog):
    # Scar #10's ambiguity, disproven direction: the quote is real but it
    # lives in u-1, not the cited a-2. Verdict stays exactly today's (the
    # whole-transcript scan verifies it) but the false binding must die.
    cp = _cp_with({("working_context", "recent_decisions"): [
        {"text": "d", "trust": "verbatim", "quote": "adopt the D-007 prompt",
         "source_message_ids": ["a-2"]}]})
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.serializer"):
        n = serializer.verify_quotes(
            cp, serializer._render_transcript(_ID_MSGS), _ID_MSGS)
    item = cp["working_context"]["recent_decisions"][0]
    assert n == 0
    assert item["trust"] == "verbatim"
    assert item["quote_verified"] is True
    assert "source_message_ids" not in item
    assert any("cited message" in r.getMessage() for r in caplog.records)


def test_verify_quotes_miss_downgrades_and_drops_ids():
    cp = _cp_with({("working_context", "recent_decisions"): [
        {"text": "d", "trust": "verbatim",
         "quote": "this exact sentence is nowhere in the transcript at all",
         "source_message_ids": ["u-1"]}]})
    n = serializer.verify_quotes(
        cp, serializer._render_transcript(_ID_MSGS), _ID_MSGS)
    item = cp["working_context"]["recent_decisions"][0]
    assert n == 1
    assert item["trust"] == "inferred"
    assert "source_message_ids" not in item  # nothing left worth binding


def test_verify_quotes_unresolvable_id_falls_back_and_keeps_ids():
    # An id that does not resolve (old checkpoint against a rewritten
    # transcript, carried item from another session) is NOT disproven — the
    # whole-transcript fallback rules, today's behavior byte-for-byte, and
    # the binding is left alone for a future audit with the right transcript.
    cp = _cp_with({("working_context", "recent_decisions"): [
        {"text": "d", "trust": "verbatim", "quote": "adopt the D-007 prompt",
         "source_message_ids": ["ghost-9"]}]})
    n = serializer.verify_quotes(
        cp, serializer._render_transcript(_ID_MSGS), _ID_MSGS)
    item = cp["working_context"]["recent_decisions"][0]
    assert n == 0
    assert item["quote_verified"] is True
    assert item["source_message_ids"] == ["ghost-9"]


def test_verify_quotes_two_arg_call_ignores_bindings():
    # Without messages the function must behave exactly as before #358 —
    # bindings are neither used nor touched.
    cp = _cp_with({("working_context", "recent_decisions"): [
        {"text": "d", "trust": "verbatim", "quote": "adopt the D-007 prompt",
         "source_message_ids": ["u-1"]}]})
    n = serializer.verify_quotes(cp, "assistant: adopt the D-007 prompt today")
    item = cp["working_context"]["recent_decisions"][0]
    assert n == 0
    assert item["quote_verified"] is True
    assert item["source_message_ids"] == ["u-1"]


# ---- Unit C: serialize_strict integration ----

def _script(items):
    import json
    return json.dumps({
        "session_id": "S1",
        "working_context": {
            "active_topic": {"text": "topic", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": items,
        },
        "epistemic_snapshot": {
            "strong_beliefs": [], "uncertainties": [], "contradictions_flagged": [],
        },
    })


def test_serialize_strict_downgrades_unverifiable_quote(fake_chat_factory):
    # 20 rendered messages: "line N from user/assistant"
    chat = fake_chat_factory(_script([
        {"text": "made-up decision", "trust": "verbatim",
         "quote": "a quote that never appears anywhere in this transcript"}]))
    cp = serializer.serialize_strict("S1", make_messages(20), chat=chat)
    item = cp["working_context"]["recent_decisions"][0]
    assert item["trust"] == "inferred"
    assert item["quote_verified"] is False


def test_serialize_strict_keeps_real_quote_verbatim(fake_chat_factory):
    chat = fake_chat_factory(_script([
        {"text": "a real decision", "trust": "verbatim",
         "quote": "line 5 from assistant"}]))
    cp = serializer.serialize_strict("S1", make_messages(20), chat=chat)
    item = cp["working_context"]["recent_decisions"][0]
    assert item["trust"] == "verbatim"
    assert item["quote_verified"] is True


# ---- Unit D: transcript_hash ----

def test_file_sha256_matches_raw_bytes(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_bytes(b'{"role":"user","content":"hello"}\n')
    assert transcript.file_sha256(p) == hashlib.sha256(p.read_bytes()).hexdigest()


def test_file_sha256_missing_file_returns_none(tmp_path):
    assert transcript.file_sha256(tmp_path / "nope.jsonl") is None


def test_cli_serialize_stamps_transcript_hash(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch
):
    chat = fake_chat_factory(json.dumps({
        "session_id": "sample_transcript",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [], "recent_decisions": [],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [], "uncertainties": [], "contradictions_flagged": [],
        },
    }))
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    path = FIXTURES / "sample_transcript.md"
    rc = cli.main(["serialize", str(path)])
    assert rc == 0
    ckpt = store.read_checkpoint("sample_transcript")
    assert ckpt["transcript_hash"] == transcript.file_sha256(path)


def test_absent_transcript_hash_tolerated_by_readers(tmp_checkpoint_dir):
    # A legacy checkpoint without the field must read back cleanly.
    cp = {
        "session_id": "legacy",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [], "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }
    store.write_checkpoint("legacy", cp)
    got = store.read_checkpoint("legacy")
    assert "transcript_hash" not in got


# ---- Unit E: receipt_hash reserved slot on decision items ----

def test_receipt_hash_preserved_through_write_and_redaction(tmp_checkpoint_dir):
    # A decision item carrying receipt_hash (plus a secret in text that redaction
    # WILL rewrite) must keep receipt_hash intact after write_checkpoint.
    cp = {
        "session_id": "R1",
        "created": "2026-07-07T10:00:00Z",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [{
                "text": "use token api_key=supersecretvalue123",
                "trust": "inferred",
                "receipt_hash": "deadbeefcafe",
            }],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }
    store.write_checkpoint("R1", cp, project_dir="/p/R")
    got = store.read_checkpoint("R1")
    dec = got["working_context"]["recent_decisions"][0]
    assert dec["receipt_hash"] == "deadbeefcafe"
    assert "[redacted:api-key]" in dec["text"]  # redaction still fired around it


def test_receipt_hash_preserved_through_carry(tmp_checkpoint_dir):
    from daimon_briefing import carry
    prev = {
        "session_id": "P", "created": "2026-07-07T09:00:00Z",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [{
                "text": "ship the rotation guard before the release cut",
                "trust": "inferred", "importance": 8,
                "receipt_hash": "abc123",
            }],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }
    new = {
        "session_id": "N", "created": "2026-07-07T11:00:00Z",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [], "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }
    merged = carry.merge(new, prev, now=store._created_epoch("2026-07-07T11:00:00Z"))
    carried = merged["working_context"]["recent_decisions"]
    assert carried and carried[0]["receipt_hash"] == "abc123"


# ---- Redaction interplay: verification runs PRE-redaction ----

def test_secret_bearing_quote_verifies_before_redaction(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch
):
    # The transcript contains a secret; the LLM quotes it verbatim. Verification
    # runs BEFORE redaction, so it matches the raw rendered text and stays
    # verbatim — then write_checkpoint redacts the stored quote, verdict intact.
    secret = "sk_live_abcdefgh12345678"
    messages = [
        {"role": "user", "content": f"set the gateway key to {secret} now please"},
        {"role": "assistant", "content": "done, wired the key into the client"},
    ] * 3
    chat = fake_chat_factory(json.dumps({
        "session_id": "SEC",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [{
                "text": "set the gateway key",
                "trust": "verbatim",
                "quote": f"set the gateway key to {secret} now please",
            }],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [], "uncertainties": [], "contradictions_flagged": [],
        },
    }))
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    import tempfile
    from pathlib import Path as _P
    tf = _P(tempfile.mkdtemp()) / "sess.md"
    tf.write_text("\n\n".join(f"**{m['role']}**: {m['content']}" for m in messages),
                  encoding="utf-8")
    rc = cli.main(["serialize", str(tf), "--project", "/p/S"])
    assert rc == 0
    dec = store.read_checkpoint("sess")["working_context"]["recent_decisions"][0]
    # verified TRUE against the raw text (pre-redaction) ...
    assert dec["trust"] == "verbatim"
    assert dec["quote_verified"] is True
    # ... yet the stored quote is redacted (the secret never reached disk).
    assert secret not in dec["quote"]
    assert "[redacted:" in dec["quote"]


# ---- Unit F: audit-quotes CLI (read-only) ----

def _write_transcript(projects_dir, slug, session_id, turns):
    d = projects_dir / slug
    d.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"role": r, "content": c}) for r, c in turns]
    (d / f"{session_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _stored_checkpoint(session_id, slug, decisions):
    return {
        "session_id": session_id,
        "created": "2026-07-07T10:00:00Z",
        "project_slug": slug,
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": decisions,
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }


@pytest.fixture
def _projects_dir(tmp_path, monkeypatch):
    d = tmp_path / ".claude" / "projects"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DAIMON_CLAUDE_PROJECTS_DIR", str(d))
    return d


def test_audit_quotes_reports_verified_and_failed(
    tmp_checkpoint_dir, _projects_dir, capsys, monkeypatch
):
    slug = store.project_slug("/p/A")
    _write_transcript(_projects_dir, slug, "SA", [
        ("user", "we decided to adopt the D-007 prompt for the serializer"),
        ("assistant", "understood, wiring it now"),
    ])
    cp = _stored_checkpoint("SA", slug, [
        {"text": "real quote decision", "trust": "verbatim",
         "quote": "adopt the D-007 prompt for the serializer", "id": "d-aaa"},
        {"text": "fabricated decision", "trust": "verbatim",
         "quote": "this sentence is nowhere in the source transcript", "id": "d-bbb"},
    ])
    store.write_checkpoint("SA", cp, project_dir="/p/A")

    rc = cli.main(["audit-quotes", "--project", "/p/A"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "verified" in out.lower()
    assert "1" in out  # 1 verified, 1 failed
    assert "fabricated decision" in out  # failing item's text prefix reported


def test_audit_quotes_is_read_only(
    tmp_checkpoint_dir, _projects_dir, monkeypatch
):
    slug = store.project_slug("/p/A")
    _write_transcript(_projects_dir, slug, "SA", [
        ("user", "totally unrelated content here"),
        ("assistant", "nothing matches"),
    ])
    cp = _stored_checkpoint("SA", slug, [
        {"text": "fabricated decision", "trust": "verbatim",
         "quote": "this sentence is nowhere in the source transcript", "id": "d-bbb"},
    ])
    store.write_checkpoint("SA", cp, project_dir="/p/A")

    cli.main(["audit-quotes", "--project", "/p/A"])
    # trust tag on disk is UNCHANGED — audit reports, never rewrites.
    got = store.read_checkpoint("SA")
    assert got["working_context"]["recent_decisions"][0]["trust"] == "verbatim"


def test_audit_quotes_counts_unpaired_when_transcript_missing(
    tmp_checkpoint_dir, _projects_dir, capsys
):
    slug = store.project_slug("/p/A")
    # No transcript written -> checkpoint is unpaired.
    cp = _stored_checkpoint("SNO", slug, [
        {"text": "d", "trust": "verbatim", "quote": "some quoted text here", "id": "d-c"},
    ])
    store.write_checkpoint("SNO", cp, project_dir="/p/A")

    rc = cli.main(["audit-quotes", "--project", "/p/A"])
    out = capsys.readouterr().out.lower()
    assert rc == 0
    assert "unpaired" in out


def test_audit_quotes_all_flag_spans_projects(
    tmp_checkpoint_dir, _projects_dir, capsys
):
    slug_a = store.project_slug("/p/A")
    slug_b = store.project_slug("/p/B")
    _write_transcript(_projects_dir, slug_a, "SA",
                      [("user", "alpha decision text that is quoted exactly")])
    _write_transcript(_projects_dir, slug_b, "SB",
                      [("user", "beta decision text that is quoted exactly")])
    store.write_checkpoint("SA", _stored_checkpoint("SA", slug_a, [
        {"text": "a", "trust": "verbatim",
         "quote": "alpha decision text that is quoted exactly", "id": "d-a"}]),
        project_dir="/p/A")
    store.write_checkpoint("SB", _stored_checkpoint("SB", slug_b, [
        {"text": "b", "trust": "verbatim",
         "quote": "beta decision text that is quoted exactly", "id": "d-b"}]),
        project_dir="/p/B")

    # Default scope (project A) sees only 1 checkpoint; --all sees both.
    cli.main(["audit-quotes", "--project", "/p/A"])
    default_out = capsys.readouterr().out
    cli.main(["audit-quotes", "--project", "/p/A", "--all"])
    all_out = capsys.readouterr().out
    assert "2" in all_out  # both checkpoints scanned under --all
    # sanity: the two runs differ (default is narrower)
    assert default_out != all_out


# ---- #358: audit resolves stored message-id bindings before scanning ----


def _write_id_transcript(projects_dir, slug, session_id, turns):
    # Claude Code-shaped rows: per-message uuid rides to messages as `id`.
    d = projects_dir / slug
    d.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"role": r, "content": c, "uuid": u})
             for r, c, u in turns]
    (d / f"{session_id}.jsonl").write_text("\n".join(lines) + "\n",
                                           encoding="utf-8")


def test_audit_quotes_resolves_bound_ids(
    tmp_checkpoint_dir, _projects_dir, capsys
):
    slug = store.project_slug("/p/A")
    _write_id_transcript(_projects_dir, slug, "SA", [
        ("user", "we adopt the D-007 prompt for the serializer", "u-111"),
        ("assistant", "understood, wiring it now", "a-222"),
    ])
    cp = _stored_checkpoint("SA", slug, [
        {"text": "bound decision", "trust": "verbatim",
         "quote": "adopt the D-007 prompt for the serializer",
         "source_message_ids": ["u-111"], "id": "d-aaa"},
    ])
    store.write_checkpoint("SA", cp, project_dir="/p/A")

    rc = cli.main(["audit-quotes", "--project", "/p/A"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "id-resolved: 1" in out
    assert "verified: 1" in out


def test_audit_quotes_stale_id_falls_back_to_whole_scan(
    tmp_checkpoint_dir, _projects_dir, capsys
):
    # An id the current transcript no longer carries (moved/truncated/old
    # checkpoint) must not fail the item — the whole-transcript scan is the
    # fallback and its verdict is today's verdict.
    slug = store.project_slug("/p/A")
    _write_id_transcript(_projects_dir, slug, "SA", [
        ("user", "we adopt the D-007 prompt for the serializer", "u-111"),
    ])
    cp = _stored_checkpoint("SA", slug, [
        {"text": "stale binding decision", "trust": "verbatim",
         "quote": "adopt the D-007 prompt for the serializer",
         "source_message_ids": ["gone-999"], "id": "d-bbb"},
    ])
    store.write_checkpoint("SA", cp, project_dir="/p/A")

    rc = cli.main(["audit-quotes", "--project", "/p/A"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "id-resolved: 0" in out
    assert "verified: 1" in out
    assert "failed: 0" in out
