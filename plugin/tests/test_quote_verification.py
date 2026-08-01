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


def test_redacted_placeholder_mid_quote_still_matches():
    # #505: the marker sits BETWEEN real text on both sides. Deleting it joined
    # two spans that are not adjacent in the source ("...token for prod" vs
    # "...token sk-... for prod"), so the quote could never match. Treating the
    # marker as a fragment boundary — the same way ellipsis is treated — is what
    # the docstring always promised.
    hay = "assistant: please set the staging token sk_live_abc123def for prod now"
    assert serializer.quote_matches(
        "set the staging token [redacted:api-key] for prod", hay)


def test_redacted_placeholder_at_quote_start_still_matches():
    hay = "assistant: the value sk_live_abc123def was rotated yesterday"
    assert serializer.quote_matches("[redacted:api-key] was rotated yesterday", hay)


def test_redacted_fragments_must_appear_in_order():
    # A redaction boundary must not weaken the precision guard: the surviving
    # fragments still have to appear in the source IN ORDER.
    hay = "assistant: please set the staging token sk_live_abc123def for prod now"
    assert not serializer.quote_matches(
        "for prod now [redacted:api-key] set the staging token", hay)


def test_marker_only_quote_is_unverifiable():
    # Nothing survives the split -> no usable fragment -> conservative False.
    # A quote that is ENTIRELY a redacted secret carries no evidence at all.
    assert not serializer.quote_matches(
        "[redacted:api-key]", "assistant: sk_live_abc123def is the token")


def test_multiple_redactions_in_one_quote_match():
    hay = ("assistant: use api_key=sk_live_abc123def and "
           "password=hunter2secret for the staging box")
    assert serializer.quote_matches(
        "use api_key=[redacted:api-key] and password=[redacted:api-key] "
        "for the staging box", hay)


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


# ---- #359: signal ids never scope the quote check ----
#
# A tool-result signal pointer (#359) asserts "this message evidences the
# outcome", NOT "the quote lives here" — so verify_quotes must scope the
# quote check to the NON-signal cited ids only, and a scoped miss must never
# execute an innocent signal pointer for the quote-id's crime.

_SIG_MSGS = [
    {"role": "user", "content": "we adopt the D-007 prompt", "id": "u-1"},
    {"role": "assistant", "content": "understood, cache stays keyed", "id": "a-2"},
    {"role": "tool", "content": "exit code 0", "id": "t-3", "tool_result": True},
]


def test_verify_quotes_signal_only_binding_is_not_a_quote_scope():
    # The item cites ONLY the signal: there is no quote-source claim to
    # disprove. Whole-transcript scan rules, and the signal pointer survives.
    cp = _cp_with({("working_context", "recent_decisions"): [
        {"text": "d", "trust": "verbatim", "quote": "adopt the D-007 prompt",
         "source_message_ids": ["t-3"]}]})
    n = serializer.verify_quotes(
        cp, serializer._render_transcript(_SIG_MSGS), _SIG_MSGS)
    item = cp["working_context"]["recent_decisions"][0]
    assert n == 0
    assert item["quote_verified"] is True
    assert item["source_message_ids"] == ["t-3"]


def test_verify_quotes_scoped_miss_keeps_signal_ids_drops_quote_ids(caplog):
    # Quote is real but lives in u-1, not the cited a-2: the false QUOTE
    # binding dies (today's #358 behavior), the signal pointer stays.
    cp = _cp_with({("working_context", "recent_decisions"): [
        {"text": "d", "trust": "verbatim", "quote": "adopt the D-007 prompt",
         "source_message_ids": ["a-2", "t-3"]}]})
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.serializer"):
        n = serializer.verify_quotes(
            cp, serializer._render_transcript(_SIG_MSGS), _SIG_MSGS)
    item = cp["working_context"]["recent_decisions"][0]
    assert n == 0
    assert item["quote_verified"] is True
    assert item["source_message_ids"] == ["t-3"]


def test_verify_quotes_scoped_hit_keeps_quote_and_signal_ids():
    cp = _cp_with({("working_context", "recent_decisions"): [
        {"text": "d", "trust": "verbatim", "quote": "adopt the D-007 prompt",
         "source_message_ids": ["u-1", "t-3"]}]})
    n = serializer.verify_quotes(
        cp, serializer._render_transcript(_SIG_MSGS), _SIG_MSGS)
    item = cp["working_context"]["recent_decisions"][0]
    assert n == 0
    assert item["quote_verified"] is True
    assert item["source_message_ids"] == ["u-1", "t-3"]


def test_verify_quotes_full_miss_still_drops_everything():
    # A quote found NOWHERE downgrades the item; a downgraded quote is not
    # evidence and neither is its signal pointer — conservative, unchanged.
    cp = _cp_with({("working_context", "recent_decisions"): [
        {"text": "d", "trust": "verbatim",
         "quote": "this exact sentence is nowhere in the transcript at all",
         "source_message_ids": ["u-1", "t-3"]}]})
    n = serializer.verify_quotes(
        cp, serializer._render_transcript(_SIG_MSGS), _SIG_MSGS)
    item = cp["working_context"]["recent_decisions"][0]
    assert n == 1
    assert item["trust"] == "inferred"
    assert "source_message_ids" not in item


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


@pytest.fixture
def _log_dir(tmp_path):
    # The autouse fixture already points DAIMON_LOG_DIR here; expose the path.
    return tmp_path / ".daimon" / "logs"


def test_audit_quotes_records_usage(
    tmp_checkpoint_dir, _projects_dir, _log_dir, capsys
):
    """#504: the only read-side verification verb recorded nothing, so there was
    no evidence either way about whether anyone reaches for it."""
    slug = store.project_slug("/p/A")
    _write_transcript(_projects_dir, slug, "SA",
                      [("user", "alpha decision text that is quoted exactly")])
    cp = _stored_checkpoint("SA", slug, [
        {"text": "d", "trust": "verbatim",
         "quote": "alpha decision text that is quoted exactly", "id": "d-a"},
    ])
    store.write_checkpoint("SA", cp, project_dir="/p/A")

    assert cli.main(["audit-quotes", "--project", "/p/A"]) == 0
    usage = (_log_dir / "usage.log").read_text(encoding="utf-8")
    assert "audit-quotes" in usage
    # A run that paired a transcript is NOT the unpaired variant.
    assert "audit-quotes:unpaired" not in usage


def test_audit_quotes_records_unpaired_variant_when_nothing_pairs(
    tmp_checkpoint_dir, _projects_dir, _log_dir, capsys
):
    """A run that resolved no transcript at all is a materially different event
    from one that verified a corpus — and doubles as passive detection for a
    host whose transcripts live somewhere the resolver does not look."""
    slug = store.project_slug("/p/A")
    # No transcript written -> nothing pairs.
    cp = _stored_checkpoint("SNO", slug, [
        {"text": "d", "trust": "verbatim", "quote": "some quoted text here", "id": "d-c"},
    ])
    store.write_checkpoint("SNO", cp, project_dir="/p/A")

    assert cli.main(["audit-quotes", "--project", "/p/A"]) == 0
    usage = (_log_dir / "usage.log").read_text(encoding="utf-8")
    assert "audit-quotes:unpaired" in usage


def test_audit_quotes_usage_respects_kill_switch(
    tmp_checkpoint_dir, _projects_dir, _log_dir, capsys, monkeypatch
):
    """Disabled means daimon writes nothing — the audit verb is no exception."""
    slug = store.project_slug("/p/A")
    _write_transcript(_projects_dir, slug, "SA",
                      [("user", "alpha decision text that is quoted exactly")])
    cp = _stored_checkpoint("SA", slug, [
        {"text": "d", "trust": "verbatim",
         "quote": "alpha decision text that is quoted exactly", "id": "d-a"},
    ])
    store.write_checkpoint("SA", cp, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_DISABLE", "1")

    cli.main(["audit-quotes", "--project", "/p/A"])
    assert not (_log_dir / "usage.log").exists()


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


# ---- #503: audit resolves a CARRIED item against its ORIGIN transcript ----
#
# carry.merge copies `quote` and `source_message_ids` forward when an item
# survives into a later checkpoint, but never the transcript identity they
# came from. Checking a carried item against the CONTAINING checkpoint's
# transcript — the pre-#503 behavior — checks it against a transcript it was
# never in. The fix resolves per ITEM from `origin_session` (stamped at
# policy.bind_origin, carried forward by carry.merge), falling back to the
# containing checkpoint's own session when the stamp is absent or its
# transcript cannot be resolved.

def test_audit_quotes_carried_item_verifies_against_origin_transcript(
    tmp_checkpoint_dir, _projects_dir, capsys
):
    slug = store.project_slug("/p/A")
    _write_transcript(_projects_dir, slug, "SA", [
        ("user", "the origin sentence lives only in this session"),
    ])
    _write_transcript(_projects_dir, slug, "SB", [
        ("user", "completely different content in the later session"),
    ])
    cp = _stored_checkpoint("SB", slug, [
        {"text": "carried decision", "trust": "verbatim",
         "quote": "the origin sentence lives only in this session",
         "origin_session": "SA", "id": "d-carried"},
    ])
    store.write_checkpoint("SB", cp, project_dir="/p/A")

    rc = cli.main(["audit-quotes", "--project", "/p/A"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "verified: 1" in out
    assert "failed: 0" in out


def test_audit_quotes_item_without_origin_falls_back_to_containing_checkpoint(
    tmp_checkpoint_dir, _projects_dir, capsys
):
    """Pre-#268 items (and anything else missing the origin_session stamp)
    keep resolving against the containing checkpoint's own transcript —
    #503's per-item resolution must not regress this."""
    slug = store.project_slug("/p/A")
    _write_transcript(_projects_dir, slug, "SC", [
        ("user", "native sentence said in this very session"),
    ])
    cp = _stored_checkpoint("SC", slug, [
        {"text": "native decision", "trust": "verbatim",
         "quote": "native sentence said in this very session", "id": "d-native"},
    ])
    store.write_checkpoint("SC", cp, project_dir="/p/A")

    rc = cli.main(["audit-quotes", "--project", "/p/A"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "verified: 1" in out
    assert "failed: 0" in out


def test_audit_quotes_missing_origin_transcript_falls_back_without_crashing(
    tmp_checkpoint_dir, _projects_dir, capsys
):
    """origin_session names a session whose transcript is nowhere on disk
    (GC'd, never captured on this host, wrong host layout). Resolution must
    fall back to the containing checkpoint's own transcript rather than
    crash or silently drop the item."""
    slug = store.project_slug("/p/A")
    _write_transcript(_projects_dir, slug, "SB", [
        ("user", "this quote is only in the containing session after all"),
    ])
    cp = _stored_checkpoint("SB", slug, [
        {"text": "carried decision", "trust": "verbatim",
         "quote": "this quote is only in the containing session after all",
         "origin_session": "S-GHOST", "id": "d-ghost"},
    ])
    store.write_checkpoint("SB", cp, project_dir="/p/A")

    rc = cli.main(["audit-quotes", "--project", "/p/A"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "verified: 1" in out
    assert "failed: 0" in out


def test_audit_quotes_source_ids_resolve_against_origin_transcript(
    tmp_checkpoint_dir, _projects_dir, capsys
):
    """A carried item's source_message_ids name uuids in the ORIGIN
    transcript, not the containing checkpoint's own. Scoping against the
    wrong transcript would fail to resolve the id and fall through to a
    full-transcript scan of a transcript the quote was never in."""
    slug = store.project_slug("/p/A")
    _write_id_transcript(_projects_dir, slug, "SA", [
        ("user", "we adopt the D-007 prompt for the serializer", "u-111"),
    ])
    _write_id_transcript(_projects_dir, slug, "SB", [
        ("user", "totally unrelated later-session content", "u-222"),
    ])
    cp = _stored_checkpoint("SB", slug, [
        {"text": "carried bound decision", "trust": "verbatim",
         "quote": "adopt the D-007 prompt for the serializer",
         "source_message_ids": ["u-111"], "origin_session": "SA",
         "id": "d-bound-carried"},
    ])
    store.write_checkpoint("SB", cp, project_dir="/p/A")

    rc = cli.main(["audit-quotes", "--project", "/p/A"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "id-resolved: 1" in out
    assert "verified: 1" in out
    assert "failed: 0" in out


def test_audit_quotes_skips_verbatim_items_with_no_usable_quote(
    tmp_checkpoint_dir, _projects_dir, capsys
):
    """A stored item tagged verbatim but carrying no quote (legacy or
    hand-edited checkpoint) is uncheckable, not a failure — counting it as
    failed would report fabrication where there is simply nothing to check."""
    slug = store.project_slug("/p/A")
    _write_transcript(_projects_dir, slug, "SA", [
        ("user", "a real sentence that one item genuinely quotes"),
    ])
    store.write_checkpoint("SA", _stored_checkpoint("SA", slug, [
        {"text": "no quote at all", "trust": "verbatim", "id": "d-1"},
        {"text": "blank quote", "trust": "verbatim", "quote": "   ", "id": "d-2"},
        {"text": "real", "trust": "verbatim",
         "quote": "a real sentence that one item genuinely quotes", "id": "d-3"},
    ]), project_dir="/p/A")

    assert cli.main(["audit-quotes", "--project", "/p/A"]) == 0
    out = capsys.readouterr().out
    # Only the one checkable item is counted, and it verified.
    assert "verbatim quotes checked: 1" in out
    assert "verified: 1" in out
    assert "failed: 0" in out


def test_audit_quotes_reads_origin_slug_from_the_origin_checkpoint(
    tmp_checkpoint_dir, _projects_dir, capsys
):
    """The real shape: the origin session has a checkpoint of its own, and it
    is the one that knows which project slug its transcript lives under. An
    item can be carried into a checkpoint in a DIFFERENT project, so the
    containing checkpoint's slug is not usable for the lookup."""
    slug_a = store.project_slug("/p/A")
    slug_b = store.project_slug("/p/B")
    # Origin session SA belongs to project B, and its transcript lives there.
    _write_transcript(_projects_dir, slug_b, "SA", [
        ("user", "the origin sentence recorded in the other project"),
    ])
    store.write_checkpoint("SA", _stored_checkpoint("SA", slug_b, [
        {"text": "native", "trust": "verbatim",
         "quote": "the origin sentence recorded in the other project",
         "id": "d-native"},
    ]), project_dir="/p/B")
    # The carried twin now lives in project A with no transcript of its own.
    store.write_checkpoint("SB", _stored_checkpoint("SB", slug_a, [
        {"text": "carried", "trust": "verbatim",
         "quote": "the origin sentence recorded in the other project",
         "origin_session": "SA", "id": "d-carried"},
    ]), project_dir="/p/A")

    assert cli.main(["audit-quotes", "--project", "/p/A"]) == 0
    out = capsys.readouterr().out
    assert "verified: 1" in out
    assert "origin-resolved: 1" in out


def test_audit_quotes_unreadable_origin_transcript_falls_back(
    tmp_checkpoint_dir, _projects_dir, capsys, monkeypatch
):
    """A transcript that resolves to a path but cannot be READ (permissions,
    truncation mid-read) must degrade to the containing session, not crash the
    whole corpus scan."""
    slug = store.project_slug("/p/A")
    _write_transcript(_projects_dir, slug, "SA", [("user", "origin text here")])
    _write_transcript(_projects_dir, slug, "SB", [
        ("user", "the containing session also holds this quoted sentence"),
    ])
    store.write_checkpoint("SB", _stored_checkpoint("SB", slug, [
        {"text": "carried", "trust": "verbatim",
         "quote": "the containing session also holds this quoted sentence",
         "origin_session": "SA", "id": "d-1"},
    ]), project_dir="/p/A")

    real_from_file = transcript.from_file

    def exploding_from_file(path):
        if str(path).endswith("SA.jsonl"):
            raise OSError("simulated unreadable transcript")
        return real_from_file(path)
    monkeypatch.setattr(transcript, "from_file", exploding_from_file)

    assert cli.main(["audit-quotes", "--project", "/p/A"]) == 0
    out = capsys.readouterr().out
    # Fell back to SB's own transcript, which does contain the quote.
    assert "verified: 1" in out
    assert "origin-resolved: 0" in out


def test_audit_quotes_usage_is_not_unpaired_when_only_origins_resolve(
    tmp_checkpoint_dir, _projects_dir, _log_dir, capsys
):
    """#503 x #504: `audit-quotes:unpaired` means NO transcript resolved for
    anything. Once resolution is per-item, a checkpoint whose own transcript is
    gone can still verify every quote it holds through origin_session — that
    run resolved transcripts and verified quotes, so reporting it as unpaired
    would report silence where real work happened."""
    slug = store.project_slug("/p/A")
    _write_transcript(_projects_dir, slug, "SA", [
        ("user", "the shared origin sentence quoted by everyone"),
    ])
    # SB has NO transcript of its own — only the carried item's origin does.
    store.write_checkpoint("SB", _stored_checkpoint("SB", slug, [
        {"text": "carried", "trust": "verbatim",
         "quote": "the shared origin sentence quoted by everyone",
         "origin_session": "SA", "id": "d-1"},
    ]), project_dir="/p/A")

    assert cli.main(["audit-quotes", "--project", "/p/A"]) == 0
    out = capsys.readouterr().out
    assert "verified: 1" in out          # the quote really was checked
    assert "unpaired: 1" in out          # SB's own transcript is still absent
    usage = (_log_dir / "usage.log").read_text(encoding="utf-8")
    assert "audit-quotes:unpaired" not in usage
    assert "audit-quotes" in usage


def test_audit_quotes_caches_transcripts_by_session(
    tmp_checkpoint_dir, _projects_dir, capsys, monkeypatch
):
    """A corpus scan resolves each session's transcript AT MOST ONCE, even
    when many carried items across many checkpoints name it as their
    origin_session — `--all` walks the whole corpus, so re-parsing per item
    would make it quadratic."""
    slug = store.project_slug("/p/A")
    _write_transcript(_projects_dir, slug, "SA", [
        ("user", "the shared origin sentence quoted by everyone"),
    ])
    calls = []
    real_from_file = transcript.from_file

    def counting_from_file(path):
        calls.append(path)
        return real_from_file(path)
    monkeypatch.setattr(transcript, "from_file", counting_from_file)

    def carried_item(item_id):
        return {"text": item_id, "trust": "verbatim",
                "quote": "the shared origin sentence quoted by everyone",
                "origin_session": "SA", "id": item_id}

    store.write_checkpoint("SB1", _stored_checkpoint(
        "SB1", slug, [carried_item("d-1"), carried_item("d-2")]),
        project_dir="/p/A")
    store.write_checkpoint("SB2", _stored_checkpoint(
        "SB2", slug, [carried_item("d-3"), carried_item("d-4")]),
        project_dir="/p/A")

    rc = cli.main(["audit-quotes", "--project", "/p/A"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "verified: 4" in out
    assert "failed: 0" in out
    # SA's transcript is parsed exactly once, not once per carried item (4)
    # or once per checkpoint that carries it (2).
    assert len(calls) == 1


# ---- Unit G (#440): daimon's own injected output is not a witness ----
#
# The recall hook and the SessionStart briefing print INTO the transcript, and
# transcript.py flattens hook stdout byte-identically into the user turn that
# carried it. Verification therefore used to accept a quote copied out of
# daimon's own echo as though this session had witnessed it — a prior
# session's item laundered as freshly verbatim, bound to a real user turn.
# The verification haystacks (and ONLY those — the extraction prompt and the
# chunk cache keep seeing the raw text) drop injected spans first.

_ECHOED = "we agreed to freeze the verbatim pin on reconsolidation"
_RECALL = ('daimon recall: prior work — decision from S0 (2h ago): '
           f'"{_ECHOED}" [verbatim]. More: daimon recall "freeze verbatim pin"')
_BRIEF = ("DAIMON BRIEFING (checkpoint: S0, written 2h ago)\n"
          "While you were away — here's where we left off.\n"
          f"decisions:\n- {_ECHOED}")
_REMINDER = f"<system-reminder>\n{_BRIEF}\n</system-reminder>"


def _msgs(*turns):
    """(role, content, id) turns shaped like transcript.py's Claude Code rows."""
    return [{"role": r, "content": c, "id": i} for r, c, i in turns]


def _decision(**over):
    item = {"text": "freeze the pin", "trust": "verbatim", "quote": _ECHOED}
    item.update(over)
    return _cp_with({("working_context", "recent_decisions"): [item]})


def _only_decision(cp):
    return cp["working_context"]["recent_decisions"][0]


# strip_injected: the shared strip, unit-level

def test_strip_injected_removes_a_system_reminder_span():
    out = serializer.strip_injected(f"genuine before\n{_REMINDER}\ngenuine after")
    assert _ECHOED not in out
    assert "genuine before" in out
    assert "genuine after" in out


def test_strip_injected_removes_a_recall_line_but_keeps_its_neighbours():
    out = serializer.strip_injected(
        f"my own words here\n{_RECALL}\nand more of mine")
    assert _ECHOED not in out
    assert "my own words here" in out
    assert "and more of mine" in out


def test_strip_injected_truncates_from_an_unwrapped_briefing_prefix():
    out = serializer.strip_injected(f"please review the plan\n{_BRIEF}")
    assert _ECHOED not in out
    assert "please review the plan" in out


def test_strip_injected_truncates_from_the_bare_briefing_header():
    # A host that swallows the DAIMON BRIEFING line still emits the render's
    # own header, so the header alone must be enough to fire the strip.
    out = serializer.strip_injected(
        f"ok\nWhile you were away — here's where we left off.\n- {_ECHOED}")
    assert _ECHOED not in out
    assert "ok" in out


def test_strip_injected_leaves_ordinary_text_byte_identical():
    text = "we decided to adopt the D-007 prompt\n- and to ship it on Friday"
    assert serializer.strip_injected(text) == text


def test_a_contentless_row_does_not_break_verification():
    """A host row whose `content` is null flattens to None, not "" — so the
    strip runs on a non-str and must fail closed (empty haystack) instead of
    raising. Verification of the OTHER messages has to survive it: one
    malformed row must never cost the whole capture its quotes."""
    msgs = _msgs(("user", "we adopt the D-007 prompt for the serializer", "u-1"))
    msgs.append({"role": "assistant", "content": None, "id": "a-2"})
    cp = _decision(text="a real decision",
                   quote="adopt the D-007 prompt for the serializer")
    n = serializer.verify_quotes(cp, serializer._render_transcript(msgs), msgs)
    item = _only_decision(cp)
    assert n == 0
    assert item["trust"] == "verbatim"
    assert item["quote_verified"] is True


# verify_quotes: an echo never earns quote_verified

def test_quote_only_in_a_recall_line_downgrades_to_inferred():
    msgs = _msgs(("user", f"{_RECALL}\nwhat did we settle on?", "u-1"))
    cp = _decision()
    n = serializer.verify_quotes(cp, serializer._render_transcript(msgs), msgs)
    item = _only_decision(cp)
    assert n == 1
    assert item["trust"] == "inferred"
    assert item["quote_verified"] is False
    assert item["quote_echo_only"] is True


def test_quote_only_in_a_system_reminder_brief_downgrades_to_inferred():
    msgs = _msgs(("user", f"{_REMINDER}\ncarry on please", "u-1"))
    cp = _decision()
    n = serializer.verify_quotes(cp, serializer._render_transcript(msgs), msgs)
    item = _only_decision(cp)
    assert n == 1
    assert item["trust"] == "inferred"
    assert item["quote_verified"] is False
    assert item["quote_echo_only"] is True


def test_quote_only_in_an_unwrapped_briefing_block_downgrades_to_inferred():
    # The context-emitting hosts' path: hook stdout lands raw, no wrapper.
    msgs = _msgs(("user", _BRIEF, "u-1"),
                 ("user", "so where were we?", "u-2"))
    cp = _decision()
    n = serializer.verify_quotes(cp, serializer._render_transcript(msgs), msgs)
    item = _only_decision(cp)
    assert n == 1
    assert item["quote_verified"] is False
    assert item["quote_echo_only"] is True


def test_genuine_text_adjacent_to_an_injected_line_still_verifies():
    genuine = "let us cut the release once the write guard lands"
    msgs = _msgs(("user", f"{_RECALL}\n{genuine}", "u-1"))
    cp = _decision(text="cut the release", quote=genuine)
    n = serializer.verify_quotes(cp, serializer._render_transcript(msgs), msgs)
    item = _only_decision(cp)
    assert n == 0
    assert item["trust"] == "verbatim"
    assert item["quote_verified"] is True
    assert "quote_echo_only" not in item


def test_echo_quote_bound_to_the_injected_turn_loses_its_binding():
    # Whole-turn id granularity is exactly what made the laundering possible:
    # the injected span and the user's own prose share one message id.
    msgs = _msgs(("user", f"{_RECALL}\nplease continue", "u-1"),
                 ("assistant", "on it", "a-2"))
    cp = _decision(source_message_ids=["u-1"])
    serializer.verify_quotes(cp, serializer._render_transcript(msgs), msgs)
    item = _only_decision(cp)
    assert item["quote_verified"] is False
    assert item["quote_echo_only"] is True
    assert "source_message_ids" not in item


def test_quote_absent_entirely_is_not_flagged_echo_only():
    msgs = _msgs(("user", "nothing related here at all", "u-1"))
    cp = _decision(quote="this sentence is nowhere in the source transcript")
    serializer.verify_quotes(cp, serializer._render_transcript(msgs), msgs)
    item = _only_decision(cp)
    assert item["quote_verified"] is False
    assert "quote_echo_only" not in item


def test_a_corroboration_badge_quoted_out_of_a_brief_is_echo_only():
    # #268 slice 4 x #440: the badge is daimon's own render, so a quote that
    # copies a badged briefing line back out of the transcript is an echo of
    # daimon agreeing with itself — the exact self-corroboration loop the
    # namespaced ledger exists to prevent, arriving by a different door. The
    # briefing strip already swallows it; this pins that the new annotation
    # rides inside the stripped span rather than surviving as a witness.
    from daimon_briefing import briefing

    badged = briefing.mark_corroborated(
        {"working_context": {
            "active_topic": None,
            "open_questions": [],
            "recent_decisions": [{"id": "d-a1d001", "text": _ECHOED,
                                  "trust": "inferred"}]},
         "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []}},
        {"d-a1d001": {"origins": {"S-a"}, "recorded": {"S-a"},
                      "latest_demotion_ts": None}})
    rendered = briefing.render(badged)
    line = next(ln for ln in rendered.splitlines() if "corroborated" in ln)
    assert "[≈ corroborated ×2]" in line

    msgs = _msgs(("user", f"DAIMON BRIEFING (checkpoint: S0)\n{rendered}", "u-1"),
                 ("user", "so where were we?", "u-2"))
    cp = _decision(quote=line.strip())
    n = serializer.verify_quotes(cp, serializer._render_transcript(msgs), msgs)
    item = _only_decision(cp)
    assert n == 1
    assert item["trust"] == "inferred"
    assert item["quote_verified"] is False
    assert item["quote_echo_only"] is True


def test_echo_flag_is_code_owned_and_model_values_are_stripped():
    cp = _decision(quote="adopt the D-007 prompt", quote_echo_only=True)
    serializer.verify_quotes(cp, "assistant: adopt the D-007 prompt today")
    item = _only_decision(cp)
    assert item["quote_verified"] is True
    assert "quote_echo_only" not in item


def test_legacy_two_arg_call_still_strips_a_reminder_span():
    cp = _decision()
    n = serializer.verify_quotes(cp, f"user: {_REMINDER}")
    assert n == 1
    assert _only_decision(cp)["quote_echo_only"] is True


# the rejection ledger carries the distinct reason code

def test_capture_writes_the_echo_only_reason_to_the_rejection_ledger(
    tmp_checkpoint_dir, fake_chat_factory
):
    from daimon_briefing import capture
    chat = fake_chat_factory(_script([
        {"text": "freeze the pin", "trust": "verbatim", "quote": _ECHOED}]))
    msgs = _msgs(("user", f"{_RECALL}\nwhat did we settle on?", "u-1"))
    msgs += [dict(m, id=f"m-{i}") for i, m in enumerate(make_messages(12))]
    capture.run("S1", msgs, project="/p/E", chat=chat, deadline=None)
    slug = store.project_slug("/p/E")
    rows = [json.loads(line) for line in
            (tmp_checkpoint_dir / slug / "verification.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    assert [r["reason"] for r in rows] == ["echo-only"]
    assert rows[0]["check"] == "quote"


# `daimon audit` reads the same stripped haystack

def test_audit_quotes_does_not_verify_an_echoed_quote(
    tmp_checkpoint_dir, _projects_dir, capsys
):
    slug = store.project_slug("/p/A")
    _write_transcript(_projects_dir, slug, "SA", [
        ("user", f"{_RECALL}\nwhat did we settle on?"),
        ("assistant", "let me look"),
    ])
    store.write_checkpoint("SA", _stored_checkpoint("SA", slug, [
        {"text": "freeze the pin", "trust": "verbatim", "quote": _ECHOED,
         "id": "d-echo"}]), project_dir="/p/A")

    rc = cli.main(["audit-quotes", "--project", "/p/A"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "verified: 0" in out
    assert "failed: 1" in out


# ---- #480 slice 3: verify_agent_evidence reuses verify_quotes' matching ----
#
# An agent's `resolve --by agent --evidence "<quote>"` claim is byte-checked
# at serialize time against the SAME transcript, through the SAME
# normalization/matching stack (quote_matches, strip_injected) as a verbatim
# capture claim — these tests exercise that function directly, unit-level,
# the same way Unit A above exercises quote_matches directly.

def test_verify_agent_evidence_hit_returns_role_of_the_carrying_message():
    msgs = [
        {"role": "user", "content": "can you check PR #6?"},
        {"role": "assistant", "content": "the user merged PR #6 from the GitHub UI"},
    ]
    found, role = serializer.verify_agent_evidence(
        "the user merged PR #6 from the GitHub UI", msgs)
    assert found is True
    assert role == "assistant"


def test_verify_agent_evidence_miss_returns_false_and_unknown():
    msgs = [{"role": "user", "content": "totally unrelated content"}]
    found, role = serializer.verify_agent_evidence(
        "this exact sentence is nowhere in the transcript at all", msgs)
    assert found is False
    assert role == "unknown"


def test_verify_agent_evidence_reuses_tier_f_normalization():
    # Same hyphen/space fold quote_matches already proves (#208) — the
    # evidence quote meets the identical bar, not a second weaker matcher.
    msgs = [{"role": "user", "content": "retry windows are 3–5 seconds — measured on the gateway"}]
    found, role = serializer.verify_agent_evidence(
        "retry windows are 3-5 seconds - measured on the gateway", msgs)
    assert found is True
    assert role == "user"


def test_verify_agent_evidence_blank_or_nonstring_is_false():
    msgs = [{"role": "user", "content": "some real content here"}]
    assert serializer.verify_agent_evidence("", msgs) == (False, "unknown")
    assert serializer.verify_agent_evidence("   ", msgs) == (False, "unknown")
    assert serializer.verify_agent_evidence(None, msgs) == (False, "unknown")


def test_verify_agent_evidence_role_unknown_when_message_role_missing():
    # A hit whose carrying message has no usable role: found True, labeled
    # honestly as "unknown" rather than guessed (#480 design: labeling, not
    # gating — see the design doc's self-quotation section).
    msgs = [{"content": "the deploy succeeded and tests are green"}]
    found, role = serializer.verify_agent_evidence(
        "the deploy succeeded and tests are green", msgs)
    assert found is True
    assert role == "unknown"


def test_verify_agent_evidence_strips_daimon_own_injected_output():
    # #440: a quote that only appears inside daimon's OWN injected recall/
    # briefing span is an echo, not a witness — must not verify.
    msgs = [{"role": "user", "content": f"{_RECALL}\nwhat did we settle on?"}]
    found, role = serializer.verify_agent_evidence(_ECHOED, msgs)
    assert found is False
    assert role == "unknown"


def test_verify_agent_evidence_skips_non_dict_messages_in_role_scan():
    # A garbage row must not crash the role scan or steal attribution — the
    # quote's real carrier still gets the credit. Only reachable with a
    # caller-precomputed haystack: without one, stripped_transcript raises
    # on the non-dict row first (serialize_strict's contract), so the loop's
    # own guard exists precisely for the precomputed-haystack path.
    good = {"role": "user", "content": "we froze the pin"}
    haystack = serializer.stripped_transcript([good])
    found, role = serializer.verify_agent_evidence(
        "we froze the pin", ["not a dict", good], haystack=haystack)
    assert found is True
    assert role == "user"


def test_verify_agent_evidence_haystack_hit_without_single_message_is_unknown():
    # The docstring's third role-unknown case: the quote verifies against
    # the caller-provided haystack, but no single message's own text carries
    # it — found stays True (the bytes ARE in the transcript), role is the
    # honest "unknown" rather than a guessed attribution.
    found, role = serializer.verify_agent_evidence(
        "the whole quote lives only in the joined haystack",
        [{"role": "user", "content": "alpha half"}],
        haystack="the whole quote lives only in the joined haystack")
    assert found is True
    assert role == "unknown"


def test_verify_agent_evidence_accepts_a_precomputed_haystack():
    msgs = [{"role": "assistant", "content": "we shipped the manual approval step"}]
    haystack = serializer.stripped_transcript(msgs)
    found, role = serializer.verify_agent_evidence(
        "we shipped the manual approval step", msgs, haystack=haystack)
    assert found is True
    assert role == "assistant"
