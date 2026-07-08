from daimon_briefing import briefing


def test_render_none_for_empty_checkpoint():
    assert briefing.render(None) is None
    assert briefing.render({}) is None


def test_render_none_when_no_signal():
    empty = {
        "session_id": "S1",
        "working_context": {
            "active_topic": {"text": "", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }
    assert briefing.render(empty) is None


def test_external_state_items_surface_at_top(sample_checkpoint):
    text = briefing.render(sample_checkpoint)
    assert text is not None
    # The external-state / verify-before-trusting block must precede the rest.
    verify_idx = text.lower().find("verify")
    decisions_idx = text.lower().find("decision")
    assert verify_idx != -1
    assert decisions_idx != -1
    assert verify_idx < decisions_idx
    # The exact PR-merge gap item is present with its quote.
    assert "PR #6" in text


def test_open_loops_before_decisions(sample_checkpoint):
    text = briefing.render(sample_checkpoint)
    open_idx = text.lower().find("open")
    dec_idx = text.lower().find("decision")
    assert open_idx < dec_idx


def test_verify_marker_present_for_external_state(sample_checkpoint):
    text = briefing.render(sample_checkpoint)
    # The marker must explicitly tell the user to verify before trusting.
    assert "verify before trusting" in text.lower()


def test_trust_marking_in_output(sample_checkpoint):
    text = briefing.render(sample_checkpoint)
    # verbatim items should be visually marked distinctly from inferred ones.
    assert "verbatim" in text.lower() or "✓" in text or '"' in text


def test_render_without_external_state_still_works():
    ckpt = {
        "session_id": "S1",
        "working_context": {
            "active_topic": {"text": "refactor", "trust": "inferred"},
            "open_questions": [{"text": "rename or not", "trust": "inferred"}],
            "recent_decisions": [{"text": "use signals", "trust": "inferred"}],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }
    text = briefing.render(ckpt)
    assert text is not None
    assert "rename or not" in text
    assert "use signals" in text


def test_render_skips_empty_active_topic():
    # Counterpart of test_validate_allows_empty_active_topic_text: validate()
    # accepts an empty-text active_topic, render() must skip the section.
    ckpt = {
        "session_id": "S1",
        "working_context": {
            "active_topic": {"text": "  ", "trust": "inferred"},
            "open_questions": [{"text": "rename or not", "trust": "inferred"}],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }
    text = briefing.render(ckpt)
    assert text is not None
    assert "Active topic" not in text


def test_render_equals_render_plain_over_build(sample_checkpoint):
    b = briefing.build(sample_checkpoint)
    assert b is not None
    assert briefing.render(sample_checkpoint) == briefing.render_plain(b)


def test_build_partitions_external_state(sample_checkpoint):
    b = briefing.build(sample_checkpoint)
    assert any("PR #6" in i.get("text", "") for i in b["external"])
    assert all(not i.get("external_state") for i in b["open_loops"])


def test_build_returns_none_for_empty_checkpoint():
    assert briefing.build({}) is None
    assert briefing.build(None) is None


def test_llm_briefing_sends_configured_temperature(sample_checkpoint, monkeypatch):
    # Opt-in LLM rendering must not pin temperature at the call site —
    # DAIMON_LLM_TEMPERATURE flows into the request body via llm.chat.
    import io
    import json
    import urllib.request

    monkeypatch.setenv("DAIMON_LLM_BRIEFING", "1")
    monkeypatch.setenv("DAIMON_LLM_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("DAIMON_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("DAIMON_LLM_MODEL", "test-model")
    monkeypatch.setenv("DAIMON_LLM_TEMPERATURE", "1")
    captured = {}

    # The fake response must carry every verbatim quote — #30 post-validation
    # rejects an LLM render that loses one, and this test is about the
    # request body, not the validation gate.
    faithful = ('llm-rendered briefing: "I\'ll merge it myself later from the '
                'GitHub UI" / "do we chunk below 1200 lines or single-pass?" / '
                '"we adopt the D-007 prompt for the serializer"')

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        payload = {"choices": [{"message": {"content": faithful}}]}
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert briefing.render(sample_checkpoint) == faithful
    assert captured["body"]["temperature"] == 1.0


def _decs(n):
    # n chronological inferred decisions: d0 (oldest) .. d(n-1) (newest)
    return [{"text": f"d{i}", "trust": "inferred"} for i in range(n)]


def test_build_caps_decisions_to_recent_tail(monkeypatch):
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "10")
    cp = {"working_context": {"recent_decisions": _decs(18)}, "epistemic_snapshot": {}}
    b = briefing.build(cp)
    assert len(b["decisions"]) == 10
    assert b["decisions_overflow"] == 8
    # newest kept, oldest dropped
    assert [d["text"] for d in b["decisions"]] == [f"d{i}" for i in range(8, 18)]


def test_build_no_overflow_when_under_cap(monkeypatch):
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "10")
    cp = {"working_context": {"recent_decisions": _decs(3)}, "epistemic_snapshot": {}}
    b = briefing.build(cp)
    assert len(b["decisions"]) == 3
    assert b["decisions_overflow"] == 0


def test_build_zero_is_unbounded(monkeypatch):
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "0")
    cp = {"working_context": {"recent_decisions": _decs(18)}, "epistemic_snapshot": {}}
    b = briefing.build(cp)
    assert len(b["decisions"]) == 18
    assert b["decisions_overflow"] == 0


def test_overflow_note_text():
    assert briefing._overflow_note(0) is None
    assert briefing._overflow_note(-1) is None
    assert briefing._overflow_note(8) == "(+8 earlier decisions — full history in checkpoint)"
    assert briefing._overflow_note(1) == "(+1 earlier decision — full history in checkpoint)"


def test_render_plain_shows_overflow_marker(monkeypatch):
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "10")
    cp = {"working_context": {"recent_decisions": _decs(18)}, "epistemic_snapshot": {}}
    out = briefing.render_plain(briefing.build(cp))
    assert "  (+8 earlier decisions — full history in checkpoint)" in out


def test_render_plain_no_marker_when_under_cap(monkeypatch):
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "10")
    cp = {"working_context": {"recent_decisions": _decs(3)}, "epistemic_snapshot": {}}
    out = briefing.render_plain(briefing.build(cp))
    assert "earlier decision" not in out


def test_render_plain_byte_identical_when_unbounded(monkeypatch):
    # N=0: the decisions section must be exactly the item lines, no marker appended.
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "0")
    cp = {"working_context": {"recent_decisions": _decs(3)}, "epistemic_snapshot": {}}
    out = briefing.render_plain(briefing.build(cp))
    assert "earlier decision" not in out
    # decisions section ends cleanly on the last item line
    assert out.rstrip().endswith("- [~ inferred] d2")


# --- Issue #101: contradictions_flagged renders as its own section ----------


def _ckpt_with_contradictions():
    return {
        "session_id": "S1",
        "working_context": {
            "active_topic": {"text": "schema cleanup", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [],
            "uncertainties": [],
            "contradictions_flagged": [
                {
                    "text": "claimed the cache was cold, later said it was warm",
                    "trust": "verbatim",
                    "quote": "the cache was warm the whole time",
                },
                {"text": "estimate conflicts with the measured result", "trust": "inferred"},
            ],
        },
    }


def test_contradictions_render_as_own_section():
    text = briefing.render(_ckpt_with_contradictions())
    assert text is not None
    assert "Contradictions flagged:" in text
    assert "claimed the cache was cold, later said it was warm" in text
    assert '"the cache was warm the whole time"' in text  # verbatim quote carried


def test_contradictions_are_trust_tagged_like_other_sections():
    text = briefing.render(_ckpt_with_contradictions())
    # Same item format as every other section: [✓ verbatim] / [~ inferred] marks.
    assert "- [✓ verbatim] claimed the cache was cold" in text
    assert "- [~ inferred] estimate conflicts with the measured result" in text


def test_contradictions_empty_list_renders_nothing(sample_checkpoint):
    # sample_checkpoint carries contradictions_flagged: []
    text = briefing.render(sample_checkpoint)
    assert "Contradiction" not in text


def test_contradictions_missing_key_renders_nothing():
    ckpt = {
        "session_id": "S1",
        "working_context": {
            "active_topic": {"text": "refactor", "trust": "inferred"},
            "open_questions": [{"text": "rename or not", "trust": "inferred"}],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }
    text = briefing.render(ckpt)
    assert text is not None
    assert "Contradiction" not in text


def test_build_surfaces_contradictions():
    b = briefing.build(_ckpt_with_contradictions())
    assert b is not None
    assert len(b["contradictions"]) == 2


def test_build_briefs_when_only_contradictions_present():
    # A checkpoint whose ONLY signal is a flagged contradiction is still worth surfacing.
    ckpt = {
        "session_id": "S1",
        "working_context": {
            "active_topic": {"text": "", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [],
            "uncertainties": [],
            "contradictions_flagged": [{"text": "X vs not-X", "trust": "inferred"}],
        },
    }
    b = briefing.build(ckpt)
    assert b is not None
    assert briefing.render(ckpt) is not None


def test_llm_briefing_prompt_enumerates_contradictions():
    # The opt-in LLM path enumerates sections in its ordering instruction —
    # contradictions must be part of that enumeration (#101).
    assert "contradiction" in briefing._RECONSTRUCT_SYS.lower()


def test_render_plain_handles_missing_overflow_key():
    # defensive: a hand-built b without decisions_overflow must not KeyError
    b = {"external": [], "open_loops": [],
         "decisions": [{"text": "x", "trust": "inferred"}],
         "active_topic": None, "beliefs": [], "uncertainties": []}
    out = briefing.render_plain(b)
    assert "earlier decision" not in out


# ---- #78: decay/recency ordering inside briefing sections ----

import time as _time

_NOW78 = 1_800_000_000.0


def _iso78(days_ago):
    return _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(_NOW78 - days_ago * 86400))


def _ckpt78(open_qs):
    return {
        "session_id": "S78",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": open_qs,
            "recent_decisions": [
                {"text": "older decision", "trust": "inferred", "first_seen": _iso78(9)},
                {"text": "newer decision", "trust": "inferred", "first_seen": _iso78(1)},
            ],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }


def test_build_orders_open_loops_by_effective_weight():
    b = briefing.build(_ckpt78([
        {"text": "stale minor", "trust": "inferred", "first_seen": _iso78(80), "importance": 2},
        {"text": "fresh loadbearing", "trust": "inferred", "first_seen": _iso78(1), "importance": 9},
    ]), now=_NOW78)
    assert [i["text"] for i in b["open_loops"]] == ["fresh loadbearing", "stale minor"]


def test_build_keeps_decisions_chronological():
    # CHRONOLOGY is the serializer's contract and the tail-cap depends on it —
    # decisions are NEVER weight-reordered.
    b = briefing.build(_ckpt78([{"text": "q", "trust": "inferred"}]), now=_NOW78)
    assert [i["text"] for i in b["decisions"]] == ["older decision", "newer decision"]


def test_build_legacy_items_keep_original_order():
    # No stamps, no importance -> equal neutral weights -> stable sort preserves
    # serializer order (backward compatibility with pre-D-011 checkpoints).
    b = briefing.build(_ckpt78([
        {"text": "first as written", "trust": "inferred"},
        {"text": "second as written", "trust": "inferred"},
    ]), now=_NOW78)
    assert [i["text"] for i in b["open_loops"]] == ["first as written", "second as written"]


# ---- #79: token budget — section-preserving truncation ----


def test_estimate_tokens_is_len_over_four():
    assert briefing.estimate_tokens("x" * 400) == 100
    assert briefing.estimate_tokens("") == 0


def test_truncate_short_text_unchanged():
    assert briefing.truncate_preserving_sections("short", 200) == "short"


def test_truncate_preserves_labeled_sections():
    text = ("preamble filler " * 40
            + "\n**Root Cause:** the cache pinned bad responses\nmore detail\n"
            + "filler again " * 40
            + "\n**Fix:** nonce on retry bypasses the cache\ntrailing detail\n"
            + "outro filler " * 40)
    out = briefing.truncate_preserving_sections(text, 300)
    assert len(out) <= 300
    assert "Root Cause" in out and "cache pinned" in out
    assert "Fix" in out and "nonce" in out
    assert "preamble filler" not in out
    assert "…" in out or "..." in out or "truncated" in out


def test_truncate_without_sections_head_cuts_with_marker():
    text = "no labels here just words " * 50
    out = briefing.truncate_preserving_sections(text, 200)
    assert len(out) <= 200
    assert out.startswith("no labels here")
    assert "…" in out or "..." in out or "truncated" in out


def _fat_checkpoint(n_beliefs=40, n_loops=6):
    def item(i, imp, kind):
        return {"text": f"{kind} number {i} " + "padding words " * 30,
                "trust": "inferred", "importance": imp,
                "first_seen": "2026-07-01T00:00:00Z"}
    return {
        "session_id": "S-fat",
        "working_context": {
            "active_topic": {"text": "the active topic", "trust": "inferred"},
            "open_questions": (
                [{"text": "verify the deploy state", "trust": "inferred",
                  "external_state": True}]
                + [item(i, 9, "loop") for i in range(n_loops)]),
            "recent_decisions": [item(i, 5, "decision") for i in range(10)],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [item(i, 2, "belief") for i in range(n_beliefs)],
            "uncertainties": [item(i, 3, "doubt") for i in range(10)],
            "contradictions_flagged": [],
        },
    }


def test_render_plain_respects_token_budget(monkeypatch):
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "800")
    b = briefing.build(_fat_checkpoint(), now=1_800_000_000.0)
    out = briefing.render_plain(b)
    assert briefing.estimate_tokens(out) <= 800
    # the load-bearing skeleton survives every cut
    assert "VERIFY BEFORE TRUSTING" in out and "verify the deploy state" in out
    assert "Active topic" in out
    assert "trimmed" in out  # dropped content is announced, never silent


def test_render_plain_budget_zero_is_unbounded(monkeypatch):
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "0")
    b = briefing.build(_fat_checkpoint(), now=1_800_000_000.0)
    out = briefing.render_plain(b)
    assert briefing.estimate_tokens(out) > 800  # nothing dropped
    assert "trimmed" not in out


def test_render_plain_drops_low_weight_background_before_open_loops(monkeypatch):
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "800")
    b = briefing.build(_fat_checkpoint(), now=1_800_000_000.0)
    out = briefing.render_plain(b)
    # importance-9 open loops survive; the importance-2 belief wall goes first
    assert "loop number 0" in out
    assert "belief number 39" not in out


def test_render_plain_under_budget_untouched(monkeypatch, sample_checkpoint):
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "3000")
    b = briefing.build(sample_checkpoint, now=1_800_000_000.0)
    out = briefing.render_plain(b)
    assert "trimmed" not in out and "PR #6" in out


def test_line_marks_carried_items():
    carried = {"text": "old loop", "trust": "inferred", "carried_from": "S-0"}
    native = {"text": "fresh loop", "trust": "inferred"}
    assert "[carried]" in briefing._line(carried)
    assert "[carried]" not in briefing._line(native)


def test_line_carried_marker_precedes_quote():
    item = {"text": "old decision", "trust": "verbatim",
            "quote": "the exact words", "carried_from": "S-0"}
    line = briefing._line(item)
    assert line.index("[carried]") < line.index("the exact words")


# ---- #134: null text/quote must render, not crash (torn/legacy checkpoint) ----


def test_line_tolerates_null_text_and_quote():
    # dict.get returns the stored None for a present-but-null key, so the old
    # .get("text", "").strip() raised AttributeError. Must not raise now.
    assert isinstance(briefing._line({"text": None, "trust": "inferred"}), str)
    assert isinstance(
        briefing._line({"text": None, "trust": "verbatim", "quote": None}), str)


def test_nonempty_tolerates_null_text():
    assert briefing._nonempty({"text": None, "trust": "inferred"}) is False


def test_build_drops_null_text_item_and_renders_good_ones():
    # End-to-end: one null-text item among good items must not take down the
    # whole render — the bad item drops, the good items still show.
    cp = {"working_context": {"recent_decisions": [
              {"text": None, "trust": "inferred"},
              {"text": "shipped the fix", "trust": "inferred"}]},
          "epistemic_snapshot": {}}
    out = briefing.render_plain(briefing.build(cp))
    assert "shipped the fix" in out


# ---- #30: trust-class integrity — the differentiator's own guarantees ----


def _llm_briefing_env(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BRIEFING", "1")
    monkeypatch.setenv("DAIMON_LLM_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("DAIMON_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("DAIMON_LLM_MODEL", "test-model")


def test_llm_render_rejected_when_verbatim_quote_missing(sample_checkpoint, monkeypatch):
    # The LLM path used to print whatever came back — a verbatim pin could be
    # silently dropped or reworded. A render that loses any verbatim quote is
    # rejected and the deterministic render takes over (#30).
    _llm_briefing_env(monkeypatch)
    from daimon_briefing import llm
    monkeypatch.setattr(llm, "chat",
                        lambda *a, **k: "a fluent briefing that quotes nothing")
    out = briefing.render(sample_checkpoint)
    # deterministic fallback: every verbatim quote is present, mechanically
    assert "I'll merge it myself later from the GitHub UI" in out
    assert "do we chunk below 1200 lines or single-pass?" in out


def test_llm_render_accepted_when_quotes_survive(sample_checkpoint, monkeypatch):
    _llm_briefing_env(monkeypatch)
    from daimon_briefing import llm
    faithful = (
        "While you were away: verify PR #6 first — "
        '"I\'ll merge it myself later from the GitHub UI". '
        'Open: "do we chunk below 1200 lines or single-pass?". '
        'Decided: "we adopt the D-007 prompt for the serializer".'
    )
    monkeypatch.setattr(llm, "chat", lambda *a, **k: faithful)
    assert briefing.render(sample_checkpoint) == faithful


def test_llm_render_tolerates_rewrapped_whitespace(sample_checkpoint, monkeypatch):
    # LLMs re-wrap lines; a quote split across a newline is still intact.
    _llm_briefing_env(monkeypatch)
    from daimon_briefing import llm
    rewrapped = (
        'Verify: "I\'ll merge it myself\nlater from the GitHub UI".\n'
        'Open: "do we chunk below 1200\nlines or single-pass?".\n'
        'Decided: "we adopt the D-007\nprompt for the serializer".'
    )
    monkeypatch.setattr(llm, "chat", lambda *a, **k: rewrapped)
    assert briefing.render(sample_checkpoint) == rewrapped


def test_budget_truncation_never_rewrites_verbatim_text(monkeypatch):
    # #23 froze verbatim text in carry; render must honor the same rule —
    # budget pressure truncates INFERRED items first, verbatim text stays
    # byte-intact (#30).
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "400")
    long_verbatim = "the exact verbatim wording " + "alpha bravo " * 60
    long_inferred = "an inferred summary " + "charlie delta " * 60
    cp = {
        "session_id": "S-v",
        "working_context": {
            "active_topic": {"text": "topic", "trust": "inferred"},
            "open_questions": [
                {"text": long_verbatim, "trust": "verbatim", "importance": 9,
                 "first_seen": "2026-07-01T00:00:00Z"},
                {"text": long_inferred, "trust": "inferred", "importance": 9,
                 "first_seen": "2026-07-01T00:00:00Z"},
            ],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }
    b = briefing.build(cp, now=1_800_000_000.0)
    out = briefing.render_plain(b)
    # Verbatim: either byte-intact (render strips trailing whitespace) or
    # dropped whole — a rewritten-in-place verbatim text is the violation.
    assert (long_verbatim.strip() in out
            or "the exact verbatim wording" not in out), \
        "verbatim text was truncated in place — rewritten, not dropped"
    # Budget pressure DID land on the inferred item (proves the exemption is
    # doing work, not that the budget never fired).
    assert long_inferred.strip() not in out


def test_mark_untagged_trust_is_not_inferred():
    # An item with no trust class must not be presented as a confident
    # "~ inferred" — that is a classification it never earned (#30). The
    # recall CLI already says "untagged"; the briefing must agree.
    assert briefing._mark({"text": "x"}) == "? untagged"
    assert briefing._mark({"text": "x", "trust": ""}) == "? untagged"
    assert briefing._mark({"text": "x", "trust": "inferred"}) == "~ inferred"
    assert briefing._mark({"text": "x", "trust": "verbatim"}) == "✓ verbatim"


# ---- #103: withhold event-resolved items at render time ----


def _res_evt(ref, status="resolved", text=""):
    e = {"ts": "2026-07-07T00:00:00Z", "kind": "resolution",
         "item_ref": ref, "status": status}
    if text:
        e["item_text"] = text
    return e


def test_withhold_by_exact_id():
    cp = {"working_context": {"open_questions": [
        {"text": "is the gateway stable", "id": "o-aaa"},
        {"text": "does carry hold", "id": "o-bbb"}]}}
    filtered, withheld, candidates = briefing.withhold(cp, {"o-aaa": _res_evt("o-aaa")})
    texts = [i["text"] for i in filtered["working_context"]["open_questions"]]
    assert texts == ["does carry hold"]
    assert withheld[0][1]["id"] == "o-aaa"
    assert candidates == []
    assert len(cp["working_context"]["open_questions"]) == 2  # input untouched


def test_reopen_event_does_not_withhold():
    cp = {"working_context": {"open_questions": [{"text": "x y z", "id": "o-aaa"}]}}
    filtered, withheld, candidates = briefing.withhold(
        cp, {"o-aaa": _res_evt("o-aaa", status="reopened")})
    assert withheld == []
    assert candidates == []
    assert filtered["working_context"]["open_questions"]


def test_legacy_idless_item_withheld_by_item_text_fuzzy():
    cp = {"working_context": {"open_questions": [
        {"text": "release pipeline approval step still awaiting manual gate"}]}}  # no id
    ev = _res_evt("o-old01", text="release pipeline manual approval gate awaiting")
    filtered, withheld, candidates = briefing.withhold(cp, {"o-old01": ev})
    assert filtered["working_context"]["open_questions"] == []
    assert len(withheld) == 1
    assert candidates == []


def test_id_bearing_item_never_fuzzy_withheld():
    cp = {"working_context": {"open_questions": [
        {"text": "release pipeline manual approval gate awaiting", "id": "o-live1"}]}}
    ev = _res_evt("o-old01", text="release pipeline manual approval gate awaiting")
    filtered, withheld, candidates = briefing.withhold(cp, {"o-old01": ev})
    assert withheld == []  # exact text match but id-bearing: never fuzzy-bound
    assert candidates == []


def test_no_resolved_events_returns_input_unchanged():
    cp = {"working_context": {"open_questions": [{"text": "x", "id": "o-a"}]}}
    filtered, withheld, candidates = briefing.withhold(cp, {})
    assert filtered is cp and withheld == [] and candidates == []


def test_withhold_covers_strong_beliefs():
    # #103 I2: withhold used to iterate only carry._CARRIED_KINDS (3 of 5
    # item kinds), so a resolved strong_beliefs id never suppressed — even
    # though `daimon resolve` accepts it. withhold must cover all five
    # store._ITEM_LISTS kinds; carry's own 3-kind carry policy is untouched.
    cp = {"epistemic_snapshot": {"strong_beliefs": [
        {"text": "extractive pinning prevents silent fact loss", "id": "b-aaa"}]}}
    filtered, withheld, candidates = briefing.withhold(cp, {"b-aaa": _res_evt("b-aaa")})
    assert filtered["epistemic_snapshot"]["strong_beliefs"] == []
    assert withheld[0][1]["id"] == "b-aaa"
    assert candidates == []


def test_withhold_covers_contradictions_flagged():
    cp = {"epistemic_snapshot": {"contradictions_flagged": [
        {"text": "conflicting claims about the gateway", "id": "c-aaa"}]}}
    filtered, withheld, candidates = briefing.withhold(cp, {"c-aaa": _res_evt("c-aaa")})
    assert filtered["epistemic_snapshot"]["contradictions_flagged"] == []
    assert withheld[0][1]["id"] == "c-aaa"
    assert candidates == []


# ---- #14: withhold's third outcome — supersede-candidate is a live SUGGESTION ----


def test_withhold_candidate_kept_and_stamped():
    cp = {"working_context": {"recent_decisions": [
        {"text": "use the old gateway timeout", "id": "r-old"}]}}
    resolutions = {"r-old": _res_evt("r-old", status="supersede-candidate:r-9f3a2b")}
    filtered, withheld, candidates = briefing.withhold(cp, resolutions)
    # item PRESENT in filtered, and stamped on the returned copy only.
    kept = filtered["working_context"]["recent_decisions"]
    assert len(kept) == 1
    assert kept[0]["id"] == "r-old"
    assert kept[0]["_supersede_candidate"] == "r-9f3a2b"
    assert withheld == []
    assert len(candidates) == 1
    assert candidates[0][0] == "recent_decisions"
    assert candidates[0][1]["id"] == "r-old"
    assert candidates[0][1]["_supersede_candidate"] == "r-9f3a2b"
    assert candidates[0][2]["status"] == "supersede-candidate:r-9f3a2b"
    # input checkpoint is never mutated — no transient field, ever.
    assert "_supersede_candidate" not in cp["working_context"]["recent_decisions"][0]


def test_withhold_candidate_malformed_new_id_never_stamped():
    # The status field is free-form by design, so a candidate's payload can
    # carry arbitrary text ("supersede-candidate:o-new1a; echo pwned"). That
    # text would ride verbatim into the rendered confirm-command suggestion
    # AND the hook-injected LLM context — an injection surface. Only an
    # id-shaped payload earns a stamp; a malformed machine claim earns no
    # surface at all: no stamp, no candidates entry, item stays live and
    # renders normally.
    cp = {"working_context": {"open_questions": [
        {"text": "is the gateway stable", "id": "o-aaa"}]}}
    filtered, withheld, candidates = briefing.withhold(
        cp, {"o-aaa": _res_evt(
            "o-aaa", status="supersede-candidate:o-new1a; echo pwned")})
    assert withheld == []
    assert candidates == []
    kept = filtered["working_context"]["open_questions"]
    assert len(kept) == 1
    assert "_supersede_candidate" not in kept[0]
    assert "pwned" not in briefing._line(kept[0])


def test_withhold_candidate_conforming_id_still_stamps():
    # Regression pair for the shape gate: a real serializer-shaped id
    # (kind initial + hex slice, optional counter suffix) must still stamp.
    cp = {"working_context": {"open_questions": [
        {"text": "is the gateway stable", "id": "o-aaa"}]}}
    filtered, withheld, candidates = briefing.withhold(
        cp, {"o-aaa": _res_evt("o-aaa", status="supersede-candidate:o-1a2b3c-2")})
    assert withheld == []
    assert len(candidates) == 1
    assert filtered["working_context"]["open_questions"][0][
        "_supersede_candidate"] == "o-1a2b3c-2"


def test_withhold_hard_still_drops():
    # Regression: a hard "superseded-by" (cli) resolution still drops the
    # item exactly as before, and candidates comes back empty.
    cp = {"working_context": {"open_questions": [
        {"text": "is the gateway stable", "id": "o-aaa"}]}}
    filtered, withheld, candidates = briefing.withhold(
        cp, {"o-aaa": _res_evt("o-aaa", status="superseded-by:o-new")})
    assert filtered["working_context"]["open_questions"] == []
    assert len(withheld) == 1
    assert candidates == []


def test_line_renders_candidate_annotation():
    item = {"text": "use the old gateway timeout", "id": "r-old",
            "_supersede_candidate": "r-new"}
    line = briefing._line(item)
    assert "likely superseded by r-new" in line
    assert "daimon resolve r-old --status superseded-by:r-new" in line


def test_line_renders_candidate_reject_hint():
    # #111: a human who disagrees with the machine's guess needs a printed
    # path too — the reject command rides alongside the confirm command.
    item = {"text": "use the old gateway timeout", "id": "r-old",
            "_supersede_candidate": "r-new"}
    line = briefing._line(item)
    assert "reject: daimon reverify r-old" in line


def test_reopen_clears_candidate_flag():
    # A reopen event as the LATEST event means the item is neither resolved
    # nor a candidate — no drop, no stamp, no annotation.
    cp = {"working_context": {"recent_decisions": [
        {"text": "use the old gateway timeout", "id": "r-old"}]}}
    filtered, withheld, candidates = briefing.withhold(
        cp, {"r-old": _res_evt("r-old", status="reopened")})
    assert withheld == []
    assert candidates == []
    kept = filtered["working_context"]["recent_decisions"]
    assert "_supersede_candidate" not in kept[0]
