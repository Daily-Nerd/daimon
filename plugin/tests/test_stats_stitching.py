"""#974 step 1: `daimon stats` reports the quote-stitching rate.

#829 made stitching a RECORD on the receipt (`quote_provenance.stitching`).
Nothing read it back, so the rate a demotion has to be judged against was
unmeasurable on the one surface an operator actually runs. This file pins the
measurement: the population, the scoping, the zero-denominator wording, and
the dedup that keeps one carried item from counting once per session it rode
through.

Fixtures are built with the SHIPPING writers — serializer.verify_quotes stamps
the receipts, policy.stamp_item_ids stamps the ids, store.write_checkpoint
persists and stamps the project slug — so no hand-shaped receipt can hide a
shape the real pipeline would have written differently.
"""
import json

import pytest

from daimon_briefing import cli, policy, provenance, serializer, store

_HASH = "a" * 64

# One clean single-message quote and one stitched cross-role quote, both
# verified against the same two-message transcript.
_MESSAGES = [
    {"role": "user", "content": "the alpha premise stands firm", "id": "u-1"},
    {"role": "assistant", "content": "the omega conclusion follows cleanly",
     "id": "a-2"},
]
_CLEAN_QUOTE = "the alpha premise stands firm"
_STITCHED_QUOTE = "alpha premise stands ... omega conclusion follows"


def _source(session):
    return {
        "version": provenance.SOURCE_REF_VERSION,
        "host": "claude-code",
        "session_id": session,
        "locator": "managed",
        "author": "alice",
    }


def _checkpoint(session, items):
    return {
        "session_id": session,
        "working_context": {
            "active_topic": {"text": "topic", "trust": "inferred"},
            "open_questions": list(items),
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }


def _item(text, quote):
    return {"text": text, "trust": "verbatim", "quote": quote}


def _write(session, items, project_dir, messages=_MESSAGES):
    """Verify, stamp ids, persist — the same order serialize_strict runs in."""
    cp = _checkpoint(session, items)
    serializer.verify_quotes(
        cp, serializer._render_transcript(messages), messages,
        source_ref=_source(session), transcript_hash=_HASH)
    policy.stamp_item_ids(cp)
    assert store.write_checkpoint(session, cp, project_dir=project_dir)
    return cp


def _stats_json(capsys):
    assert cli.main(["stats", "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def _stats_text(capsys):
    assert cli.main(["stats"]) == 0
    return capsys.readouterr().out


@pytest.fixture
def project(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(d))
    return d


# ---- the rate itself ------------------------------------------------------


def test_stats_reports_the_stitching_rate_over_measured_receipts(project,
                                                                 capsys):
    _write("S1", [_item("clean claim", _CLEAN_QUOTE),
                  _item("stitched claim", _STITCHED_QUOTE)], project)

    s = _stats_json(capsys)["stitching"]
    assert s["verified"] == 2
    assert s["measured"] == 2
    assert s["stitched"] == 1
    assert s["cross_message"] == 1
    assert s["cross_role"] == 1
    assert s["unmeasured"] == 0
    assert s["rate_pct"] == 50.0


def test_the_rate_survives_the_demotion_it_is_measured_to_justify(project,
                                                                  capsys):
    """#974 step 2 demotes a stitched quote to `inferred`. The rate reads the
    RECEIPT, never the trust tag, so it keeps measuring after the demotion
    lands. Keying it on `trust == "verbatim"` would have made the rate fall
    to zero the moment enforcement shipped, and read as a fixed problem."""
    cp = _write("S1", [_item("clean claim", _CLEAN_QUOTE),
                       _item("stitched claim", _STITCHED_QUOTE)], project)
    stitched = cp["working_context"]["open_questions"][1]
    assert stitched["trust"] == "inferred"

    s = _stats_json(capsys)["stitching"]
    assert s["measured"] == 2
    assert s["stitched"] == 1


def test_the_plain_render_names_the_rate_and_the_two_flags(project, capsys):
    _write("S1", [_item("clean claim", _CLEAN_QUOTE),
                  _item("stitched claim", _STITCHED_QUOTE)], project)

    out = _stats_text(capsys)
    assert "stitching (this project)" in out
    assert ("quote stitching: 1 of 2 measured verified quotes stitched "
            "(50.0%)" in out)
    assert "cross-message 1, cross-role 1" in out


def test_the_rich_render_carries_the_same_wording(project, capsys,
                                                  monkeypatch):
    monkeypatch.setattr("daimon_briefing.render.supports_rich", lambda: True)
    monkeypatch.setenv("COLUMNS", "240")
    _write("S1", [_item("clean claim", _CLEAN_QUOTE),
                  _item("stitched claim", _STITCHED_QUOTE)], project)

    out = _stats_text(capsys)
    assert "stitching (this project)" in out
    assert "1 of 2 measured verified quotes stitched" in out


# ---- the zero denominator, in the two shapes it comes in ------------------


def test_no_verified_quotes_renders_a_line_rather_than_dividing_by_zero(
        project, capsys):
    s = _stats_json(capsys)["stitching"]
    assert s == {"verified": 0, "measured": 0, "stitched": 0,
                 "cross_message": 0, "cross_role": 0, "unmeasured": 0,
                 "rate_pct": None}
    assert "quote stitching: no verified quotes yet" in _stats_text(capsys)


def test_a_corpus_of_only_legacy_receipts_says_so_instead_of_reporting_zero(
        project, capsys):
    """A receipt with no `stitching` member is UNKNOWN, never a clean verdict.
    `0.0%` over a denominator of legacy receipts would read as "nothing is
    stitched here" when the honest answer is "daimon cannot tell you"."""
    # The legacy shape, reached through the shipping writer: without
    # `messages` there is no per-message view, so verify_quotes stamps a
    # stitching-less receipt exactly as every pre-D-019 capture did.
    cp = _checkpoint("S1", [_item("legacy claim", _CLEAN_QUOTE)])
    serializer.verify_quotes(
        cp, serializer._render_transcript(_MESSAGES),
        source_ref=_source("S1"), transcript_hash=_HASH)
    policy.stamp_item_ids(cp)
    assert store.write_checkpoint("S1", cp, project_dir=project)

    s = _stats_json(capsys)["stitching"]
    assert s["verified"] == 1
    assert s["measured"] == 0
    assert s["unmeasured"] == 1
    assert s["rate_pct"] is None
    assert ("quote stitching: none of 1 verified quotes carry a stitching "
            "verdict" in _stats_text(capsys))


def test_a_legacy_receipt_stays_out_of_the_denominator(project, capsys):
    """Measured and unmeasured are two populations, kept apart: the rate is
    over receipts that CARRY a verdict, and the rest is reported beside it."""
    _write("S1", [_item("stitched claim", _STITCHED_QUOTE)], project)
    legacy = _checkpoint("S2", [_item("legacy claim", _CLEAN_QUOTE)])
    serializer.verify_quotes(
        legacy, serializer._render_transcript(_MESSAGES),
        source_ref=_source("S2"), transcript_hash=_HASH)
    policy.stamp_item_ids(legacy)
    assert store.write_checkpoint("S2", legacy, project_dir=project)

    s = _stats_json(capsys)["stitching"]
    assert s["verified"] == 2
    assert s["measured"] == 1
    assert s["stitched"] == 1
    assert s["unmeasured"] == 1
    assert s["rate_pct"] == 100.0
    assert "; 1 unmeasured" in _stats_text(capsys)


# ---- populations that must not leak in -----------------------------------


def test_another_projects_receipts_never_reach_this_projects_rate(project,
                                                                  tmp_path,
                                                                  capsys):
    other = tmp_path / "elsewhere"
    other.mkdir()
    _write("S-other", [_item("stitched claim", _STITCHED_QUOTE)], other)
    _write("S-mine", [_item("clean claim", _CLEAN_QUOTE)], project)

    s = _stats_json(capsys)["stitching"]
    assert s["measured"] == 1
    assert s["stitched"] == 0
    assert s["rate_pct"] == 0.0


def test_a_not_verified_receipt_is_not_a_verified_quote(project, capsys):
    """The denominator is VERIFIED quotes. A downgraded item carries a
    receipt too, and counting it would dilute the rate with claims that never
    earned a stitching verdict in the first place."""
    _write("S1", [_item("absent claim", "a sentence the transcript never said"),
                  _item("stitched claim", _STITCHED_QUOTE)], project)

    s = _stats_json(capsys)["stitching"]
    assert s["verified"] == 1
    assert s["measured"] == 1
    assert s["stitched"] == 1


def test_one_item_carried_across_sessions_counts_once(project, capsys):
    """A carried item rides into every later checkpoint with the same id and
    the same frozen receipt. Counting occurrences would let a single stitched
    quote inflate the rate once per session it survived — the same
    epoch-artifact class #562 caught in the resolution split."""
    first = _write("S1", [_item("stitched claim", _STITCHED_QUOTE)], project)
    carried = json.loads(json.dumps(
        first["working_context"]["open_questions"][0]))
    second = _checkpoint("S2", [carried])
    assert store.write_checkpoint("S2", second, project_dir=project)

    s = _stats_json(capsys)["stitching"]
    assert s["verified"] == 1
    assert s["measured"] == 1
    assert s["stitched"] == 1
    assert s["rate_pct"] == 100.0


# ---- the --json contract --------------------------------------------------


def test_stats_json_carries_stitching_at_the_tail(project, capsys):
    """Key order is part of the --json contract, so a new fact is appended."""
    payload = _stats_json(capsys)
    assert list(payload)[-2:] == ["checks", "stitching"]
    assert list(payload["stitching"]) == [
        "verified", "measured", "stitched", "cross_message", "cross_role",
        "unmeasured", "rate_pct"]


@pytest.mark.parametrize("junk", ["{not json", "[1, 2, 3]"])
def test_an_unreadable_checkpoint_never_takes_stats_down(junk, project,
                                                         capsys):
    """Two ways a `.json` file in the store is not a checkpoint: it does not
    parse at all, and it parses into something that is not an object. Both
    are skipped, and the rate over the real checkpoints is unaffected."""
    _write("S1", [_item("stitched claim", _STITCHED_QUOTE)], project)
    from daimon_briefing import config

    (config.checkpoint_dir() / "junk.json").write_text(junk, encoding="utf-8")
    s = _stats_json(capsys)["stitching"]
    assert s["measured"] == 1
    assert s["stitched"] == 1
