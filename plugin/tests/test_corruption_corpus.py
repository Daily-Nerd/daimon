"""Corruption regression corpus (#409).

Four fixture variants driven through the real serializer/store validation
gates. Exactly ONE — the well-formed, provenance-bearing item — survives
intact. The three malformed variants are rejected or downgraded; none is ever
admitted as-is.

The failure mode that matters is *silent admission* of a corrupt item, so
every test here asserts on REJECTION (the malformed variant does not survive),
not merely that the well-formed one passes. Pure fixture tests over the shipped
gates — zero model quota.

Variant -> gate:
  1. well-formed, provenance-bearing   -> validate + verify_quotes + ground_outcomes  (SURVIVES)
  2. unknown enum (bad trust class)    -> validate / _valid_item                       (REJECTED)
  3. stripped evidence                 -> validate (no quote) + ground_outcomes (no signal)  (REJECTED / DOWNGRADED)
  4. dropped target (dangling link)    -> carry.bind_links                             (NOT BOUND)
"""

import json
import logging

import pytest

from daimon_briefing import carry, serializer
from tests.conftest import make_messages

# A tool-result signal id present in the session — grounding cites it.
_SIGNAL = "uuid-tool"


def _cp(decision):
    """A minimal valid checkpoint carrying one recent_decision — the shape the
    gate tests elsewhere use (`_cp_one_decision` in test_serializer,
    `_cp_with` in test_quote_verification)."""
    return {
        "session_id": "S1",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [decision],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [],
            "uncertainties": [],
            "contradictions_flagged": [],
        },
    }


# ---- Variant 1: well-formed, provenance-bearing — must SURVIVE intact ----

def test_corpus_v1_wellformed_provenance_survives():
    item = {
        "text": "deploy succeeded",
        "trust": "verbatim",
        "quote": "deploy succeeded",
        "source_message_ids": [_SIGNAL],
    }
    cp = _cp(item)
    assert serializer.validate(cp) is True
    # quote is present in the transcript -> stays verbatim, zero downgrades
    assert serializer.verify_quotes(cp, "assistant: deploy succeeded now") == 0
    # cites a real tool-result signal -> grounded, zero downgrades
    assert serializer.ground_outcomes(cp, {_SIGNAL}) == 0
    survivor = cp["working_context"]["recent_decisions"][0]
    assert survivor["trust"] == "verbatim"        # trust class intact
    assert survivor["quote_verified"] is True     # quote pinned to transcript
    assert survivor["grounded"] is True           # provenance honoured


# ---- Variant 2: unknown enum (invalid trust class) — must be REJECTED ----

def test_corpus_v2_unknown_trust_class_rejected_by_gate():
    item = {"text": "d", "trust": "quantum-verified", "quote": "q"}
    # The gate refuses an out-of-vocabulary trust class outright.
    assert serializer._valid_item(item) is False
    assert serializer.validate(_cp(item)) is False


def test_corpus_v2_unknown_trust_class_never_leaves_the_boundary(fake_chat_factory):
    # End-to-end: an extractor emitting a bogus trust class yields no
    # checkpoint at all — the malformed item is never stored as-is.
    item = {"text": "d", "trust": "quantum-verified", "quote": "q"}
    chat = fake_chat_factory(json.dumps(_cp(item)))
    assert serializer.serialize("S1", make_messages(20), chat=chat) is None


# ---- Variant 3: stripped evidence — must be REJECTED / DOWNGRADED ----

def test_corpus_v3_verbatim_without_quote_rejected():
    # A `verbatim` claim with no quote is an unpinned claim (D-006) — the
    # schema gate refuses it before it can reach disk.
    item = {"text": "we chose X over Y", "trust": "verbatim"}
    assert serializer._valid_item(item) is False
    assert serializer.validate(_cp(item)) is False


def test_corpus_v3_outcome_claim_without_signal_downgraded(caplog):
    # An OUTCOME claim citing no tool-result signal, in a signal-bearing
    # session, is downgraded verbatim->inferred: the transcription stays
    # honest but the *outcome* is unwitnessed, so it is never stored as a
    # trusted outcome.
    item = {"text": "deploy succeeded", "trust": "verbatim", "quote": "deploy succeeded"}
    cp = _cp(item)
    with caplog.at_level(logging.WARNING, logger="daimon_briefing.serializer"):
        n = serializer.ground_outcomes(cp, {_SIGNAL})
    out = cp["working_context"]["recent_decisions"][0]
    assert n == 1
    assert out["trust"] == "inferred"             # downgraded, not stored as-is
    assert out["grounded"] is False
    assert out["quote"] == "deploy succeeded"     # transcription stays honest
    assert any("outcome" in r.getMessage().lower() for r in caplog.records)


# ---- Variant 4: dropped target (dangling supersedes) — must NOT be bound ----

def test_corpus_v4_dangling_supersedes_target_not_silently_bound():
    # A `supersedes` link whose target names no existing item must NOT be
    # resolved into a fabricated provenance edge: no supersession event, and
    # the target text is left untouched (never rewritten to some prev id).
    merged = _cp({
        "text": "we now cache responses in memory",
        "trust": "inferred",
        "links": [{"type": "supersedes",
                   "target": "a topic that was never recorded anywhere"}],
    })
    prev = _cp({
        "text": "an unrelated prior choice about logging verbosity",
        "trust": "inferred",
        "id": "d-a1b2c3",
    })
    events = carry.bind_links(merged, prev)
    assert events == []  # no supersession event fabricated
    link = merged["working_context"]["recent_decisions"][0]["links"][0]
    # target left as free text, not silently rebound to the unrelated prev id
    assert link["target"] == "a topic that was never recorded anywhere"


# ---- Corpus property: EXACTLY ONE of the four survives ----
#
# Each runner applies the real gate(s) and returns True iff the input was
# ADMITTED as a well-formed, witnessed artifact. Corruption must yield False.

def _run_v1_wellformed() -> bool:
    cp = _cp({"text": "deploy succeeded", "trust": "verbatim",
              "quote": "deploy succeeded", "source_message_ids": [_SIGNAL]})
    admitted = serializer.validate(cp)
    downgraded_q = serializer.verify_quotes(cp, "assistant: deploy succeeded now")
    downgraded_o = serializer.ground_outcomes(cp, {_SIGNAL})
    d = cp["working_context"]["recent_decisions"][0]
    return bool(admitted) and downgraded_q == 0 and downgraded_o == 0 \
        and d["trust"] == "verbatim" and d.get("grounded") is True


def _run_v2_unknown_enum() -> bool:
    # admitted iff validation passes — it must not.
    return bool(serializer.validate(
        _cp({"text": "d", "trust": "quantum-verified", "quote": "q"})))


def _run_v3_stripped_evidence() -> bool:
    # admitted-as-verbatim iff grounding leaves it verbatim — it must not.
    cp = _cp({"text": "deploy succeeded", "trust": "verbatim", "quote": "deploy succeeded"})
    serializer.ground_outcomes(cp, {_SIGNAL})
    return cp["working_context"]["recent_decisions"][0]["trust"] == "verbatim"


def _run_v4_dropped_target() -> bool:
    # "admitted" here means a provenance edge was fabricated — it must not be.
    merged = _cp({"text": "we now cache responses in memory", "trust": "inferred",
                  "links": [{"type": "supersedes",
                             "target": "a topic that was never recorded anywhere"}]})
    prev = _cp({"text": "an unrelated prior choice about logging verbosity",
                "trust": "inferred", "id": "d-a1b2c3"})
    return bool(carry.bind_links(merged, prev))


_CORPUS = [
    ("v1_wellformed", _run_v1_wellformed, True),
    ("v2_unknown_enum", _run_v2_unknown_enum, False),
    ("v3_stripped_evidence", _run_v3_stripped_evidence, False),
    ("v4_dropped_target", _run_v4_dropped_target, False),
]


@pytest.mark.parametrize("name,runner,expected_admitted", _CORPUS,
                         ids=[c[0] for c in _CORPUS])
def test_corpus_variant_disposition(name, runner, expected_admitted):
    assert runner() is expected_admitted


def test_corpus_exactly_one_survives():
    survivors = [name for name, runner, _ in _CORPUS if runner()]
    assert survivors == ["v1_wellformed"]
