# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest"]
# ///
"""
Tests for the skeptic re-judge + verified-grounding pipeline (issue #38, Slice 2).

Slice 1 (grounding_screen) triages a `grounded:false` LLM-judge verdict into
`absent` (judge reliable -> confab) vs `present` (judge missed support ->
escalate). Slice 2 closes the loop:

  verify_negative(claim, transcript, chat) -> "grounded" | "confab"
    absent  -> "confab"   (deterministic; MUST NOT touch the LLM at all)
    present -> skeptic_verdict re-judges against the FULL transcript:
               grounded:true  -> "grounded"
               grounded:false -> "confab"  (real confab; token reuse, not support)

EVERY test here uses a MOCKED/injected `chat`. There is NO live LLM call in this
suite — a `chat` stub that raises on invocation guards the `absent` short-circuit,
and a scripted (deterministic) `chat` drives the full 13-claim pipeline
reproduction of the hand-grep (8 judge_errors -> grounded, 5 confabs -> confab).

Run (from this directory):
    uv run --with pytest pytest test_grounding_skeptic.py
"""

import json
from pathlib import Path

import pytest

from grounding_skeptic import skeptic_verdict, verify_negative


HERE = Path(__file__).resolve().parent
SESSIONS_DIR = HERE.parent / "sessions"
FIXTURE_PATH = HERE / "grounding_fixture.json"


# --------------------------------------------------------------- fixtures

def _load_fixture() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text())


FIXTURE = _load_fixture()
_TRANSCRIPT_CACHE: dict[str, str] = {}


def _transcript(session: str) -> str:
    if session not in _TRANSCRIPT_CACHE:
        _TRANSCRIPT_CACHE[session] = (SESSIONS_DIR / f"{session}.txt").read_text()
    return _TRANSCRIPT_CACHE[session]


def _by_id(claim_id: str) -> dict:
    for rec in FIXTURE:
        if rec["id"] == claim_id:
            return rec
    raise KeyError(claim_id)


# --------------------------------------------------------------- chat stubs

class ExplodingChat:
    """A chat that records nothing but FAILS LOUDLY if ever invoked.

    Used to prove the `absent` short-circuit never reaches the gateway — the
    entire point of Slice 1 triage is zero LLM cost for confidently-absent
    confabs.
    """

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, messages, **kwargs):
        self.calls += 1
        raise AssertionError(
            "chat was invoked for an `absent` claim — the deterministic "
            "short-circuit was bypassed (gateway cost incurred)."
        )


def fixed_chat(grounded: bool, evidence: str = "stub"):
    """A chat that always returns one fixed grounded verdict as a JSON string."""
    payload = json.dumps({"grounded": grounded, "evidence": evidence})

    def chat(messages, **kwargs):
        return payload

    return chat


def scripted_chat(verdict_by_text: dict[str, bool]):
    """Deterministic chat keyed on the claim text embedded in the user message.

    Returns grounded:<bool> per the map. Raises if asked about a claim NOT in
    the map — which is how the pipeline test proves `absent` claims (r93) never
    reach chat.
    """
    calls: list[str] = []

    def chat(messages, **kwargs):
        user = next(
            m["content"] for m in reversed(messages) if m.get("role") == "user"
        )
        for text, grounded in verdict_by_text.items():
            if text in user:
                calls.append(text)
                return json.dumps(
                    {"grounded": grounded, "evidence": "scripted per-claim verdict"}
                )
        raise AssertionError(
            f"scripted chat received an unmapped claim (should never happen): "
            f"{user[:120]!r}"
        )

    chat.calls = calls
    return chat


# ------------------------------------------ absent short-circuit (zero LLM)

class TestAbsentShortCircuit:
    def test_r93_absent_returns_confab_without_calling_chat(self):
        # r93 screens `absent` in H3 (Slice 1). verify_negative MUST decide
        # "confab" deterministically and NEVER touch the injected chat.
        rec = _by_id("r93")
        chat = ExplodingChat()
        verdict = verify_negative(rec["text"], _transcript(rec["session"]), chat)
        assert verdict == "confab"
        assert chat.calls == 0, "absent short-circuit must not invoke chat"


# ------------------------------------------ present -> skeptic re-judge

class TestPresentReJudge:
    def test_present_claim_grounded_true_returns_grounded(self):
        # r2 is a judge_error: salient tokens ARE in H4 (screens present) and
        # the transcript genuinely supports it. Skeptic says grounded:true.
        rec = _by_id("r2")
        verdict = verify_negative(
            rec["text"], _transcript(rec["session"]), fixed_chat(True)
        )
        assert verdict == "grounded"

    def test_present_claim_grounded_false_returns_confab(self):
        # r81 is a token-reusing confab: screens present, but the skeptic finds
        # the tokens don't actually assert the claim. grounded:false -> confab.
        rec = _by_id("r81")
        verdict = verify_negative(
            rec["text"], _transcript(rec["session"]), fixed_chat(False)
        )
        assert verdict == "confab"


# ------------------------------------------ skeptic_verdict JSON robustness

class TestSkepticVerdictParsing:
    DUMMY_TRANSCRIPT = "irrelevant transcript text"
    CLAIM = "some claim under skeptical review"

    def test_parses_bare_json(self):
        chat = lambda m, **k: '{"grounded": true, "evidence": "line 42 quote"}'
        out = skeptic_verdict(self.CLAIM, self.DUMMY_TRANSCRIPT, chat)
        assert out == {"grounded": True, "evidence": "line 42 quote"}

    def test_parses_json_fenced(self):
        chat = lambda m, **k: (
            "```json\n{\"grounded\": false, \"evidence\": \"tokens present but "
            "claim inverts the value\"}\n```"
        )
        out = skeptic_verdict(self.CLAIM, self.DUMMY_TRANSCRIPT, chat)
        assert out["grounded"] is False
        assert "inverts" in out["evidence"]

    def test_parses_json_with_leading_prose(self):
        chat = lambda m, **k: (
            "After re-reading the whole transcript including the tail, here is "
            "my verdict:\n{\"grounded\": true, \"evidence\": \"assistant states "
            "it verbatim near the end\"}"
        )
        out = skeptic_verdict(self.CLAIM, self.DUMMY_TRANSCRIPT, chat)
        assert out["grounded"] is True
        assert "verbatim" in out["evidence"]

    def test_tolerates_tuple_reply_like_real_llm_chat(self):
        # research/experiments/lib/llm.py `chat` returns (content, usage, model).
        # skeptic_verdict must unwrap the content rather than choke on the tuple.
        chat = lambda m, **k: (
            '{"grounded": true, "evidence": "ok"}',
            {"total_tokens": 7},
            "mock-model",
        )
        out = skeptic_verdict(self.CLAIM, self.DUMMY_TRANSCRIPT, chat)
        assert out == {"grounded": True, "evidence": "ok"}

    def test_returns_normalized_bool_and_str(self):
        chat = lambda m, **k: '{"grounded": false, "evidence": "no support"}'
        out = skeptic_verdict(self.CLAIM, self.DUMMY_TRANSCRIPT, chat)
        assert isinstance(out["grounded"], bool)
        assert isinstance(out["evidence"], str)
        assert set(out.keys()) == {"grounded", "evidence"}


# ------------------------------------------ full 13-claim pipeline reproduction

class TestPipelineReproduction:
    """Run all 13 fixture claims through verify_negative with a deterministic,
    scripted chat. The output must match the hand-grep:
      4 judge_errors  -> "grounded"
      9 confabs (r53/r79/r81/r86/r93/r94/r20/r65/r76) -> "confab"
    AND r93 (absent) must never reach the scripted chat."""

    def _verdict_map(self) -> dict[str, bool]:
        # grounded:true for every judge_error; grounded:false for the confabs.
        # r93 is deliberately EXCLUDED: it screens absent and must never be
        # sent to chat. The scripted chat raises on any unmapped claim, so a
        # leak would fail the run loudly.
        m: dict[str, bool] = {}
        for rec in FIXTURE:
            if rec["id"] == "r93":
                continue
            m[rec["text"]] = rec["true_label"] == "judge_error"
        return m

    def test_pipeline_matches_hand_grep(self):
        chat = scripted_chat(self._verdict_map())
        verdicts: dict[str, str] = {}
        for rec in FIXTURE:
            verdicts[rec["id"]] = verify_negative(
                rec["text"], _transcript(rec["session"]), chat
            )

        grounded_ids = {cid for cid, v in verdicts.items() if v == "grounded"}
        confab_ids = {cid for cid, v in verdicts.items() if v == "confab"}

        expected_grounded = {
            r["id"] for r in FIXTURE if r["true_label"] == "judge_error"
        }
        expected_confab = {
            "r53", "r81", "r86", "r93", "r94", "r79", "r20", "r65", "r76"
        }

        # every claim resolved to exactly one of the two verdicts
        assert grounded_ids | confab_ids == {r["id"] for r in FIXTURE}
        assert grounded_ids & confab_ids == set()

        assert grounded_ids == expected_grounded
        assert len(grounded_ids) == 4
        assert confab_ids == expected_confab
        assert len(confab_ids) == 9

        # r93 short-circuited on `absent` and NEVER reached chat
        assert _by_id("r93")["text"] not in chat.calls
        # exactly the 12 `present` claims reached the skeptic
        assert len(chat.calls) == 12
