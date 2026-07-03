# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest"]
# ///
"""
Validation tests for the deterministic grounding-verdict screen (issue #38, Slice 1).

Driven by the labeled fixture (grounding_fixture.json) + the REAL session
transcripts (sessions/H3.txt, sessions/H4.txt). The screen triages a
`grounded:false` LLM-judge verdict:

  absent  -> no salient token of the claim is anywhere in the full transcript
             -> the judge's negative is RELIABLE (keep grounded:false).
  present -> salient tokens ARE in the transcript -> the judge MISSED support
             -> the negative is UNRELIABLE and must be escalated (Slice 2).

HARD SAFETY INVARIANT: every judge_error claim must screen `present`. A
judge_error that screened `absent` would silently confirm a false negative —
the exact failure mode #38 exists to catch (cf. landmine #4: LLM per-claim
grounding is noise-dominated and skips the transcript tail).

Run (from this directory):
    uv run --with pytest pytest test_grounding_screen.py
"""

import json
from pathlib import Path

import pytest

from grounding_screen import salient_tokens, screen_negative


HERE = Path(__file__).resolve().parent
SESSIONS_DIR = HERE.parent / "sessions"
FIXTURE_PATH = HERE / "grounding_fixture.json"


# --------------------------------------------------------------- fixtures

def _load_fixture() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text())


def _transcript(session: str) -> str:
    return (SESSIONS_DIR / f"{session}.txt").read_text()


FIXTURE = _load_fixture()
_TRANSCRIPT_CACHE: dict[str, str] = {}


def _screen(record: dict) -> str:
    session = record["session"]
    if session not in _TRANSCRIPT_CACHE:
        _TRANSCRIPT_CACHE[session] = _transcript(session)
    return screen_negative(record["text"], _TRANSCRIPT_CACHE[session])


def _by_id(claim_id: str) -> dict:
    for rec in FIXTURE:
        if rec["id"] == claim_id:
            return rec
    raise KeyError(claim_id)


# ----------------------------------------------------- fixture integrity

class TestFixtureIntegrity:
    def test_has_thirteen_records(self):
        assert len(FIXTURE) == 13

    def test_all_judge_grounded_false(self):
        assert all(r["judge_grounded"] is False for r in FIXTURE)

    def test_label_counts(self):
        labels = [r["true_label"] for r in FIXTURE]
        assert labels.count("judge_error") == 4
        assert labels.count("confab") == 9
        assert set(labels) == {"judge_error", "confab"}


# ------------------------------------------ HARD SAFETY INVARIANT (#38)

class TestSafetyInvariant:
    @pytest.mark.parametrize(
        "record",
        [r for r in FIXTURE if r["true_label"] == "judge_error"],
        ids=lambda r: f"{r['session']}-{r['id']}",
    )
    def test_every_judge_error_screens_present(self, record):
        # A judge_error screened `absent` would silently confirm a false
        # negative. This must NEVER happen across all 8 judge errors.
        verdict = _screen(record)
        assert verdict == "present", (
            f"{record['session']}/{record['id']} is a KNOWN judge_error "
            f"(transcript supports it) but screened {verdict!r}; the screen "
            f"would have rubber-stamped a false negative. "
            f"salient_tokens={sorted(salient_tokens(record['text']))}"
        )

    def test_no_judge_error_lands_in_absent_bucket(self):
        absent_errors = [
            r for r in FIXTURE
            if r["true_label"] == "judge_error" and _screen(r) == "absent"
        ]
        assert absent_errors == [], (
            "judge_error claims leaked into the absent bucket: "
            f"{[r['id'] for r in absent_errors]}"
        )


# ------------------------------------------ confab triage behaviour

class TestConfabTriage:
    def test_r93_lexically_absent_confab_screens_absent(self):
        # r93 invents user-trait adjectives (pragmatic/methodical/iterating/
        # rationalizing) that are nowhere in H3. The one confab the screen can
        # confidently keep as grounded:false.
        assert _screen(_by_id("r93")) == "absent"

    @pytest.mark.parametrize(
        "claim_id", ["r53", "r81", "r86", "r94", "r79", "r20", "r65", "r76"]
    )
    def test_token_reusing_confabs_screen_present(self, claim_id):
        # Real confabs that REUSE transcript vocabulary. The screen must NOT
        # auto-trust them — they go to the Slice-2 residual. The screen neither
        # confirms nor flips them; it only declines to rubber-stamp.
        assert _screen(_by_id(claim_id)) == "present"


# ------------------------------------------ bucket summary (the whole picture)

class TestBucketSummary:
    def test_buckets_partition_as_expected(self):
        absent = {r["id"] for r in FIXTURE if _screen(r) == "absent"}
        present = {r["id"] for r in FIXTURE if _screen(r) == "present"}

        # every record lands in exactly one bucket
        assert absent | present == {r["id"] for r in FIXTURE}
        assert absent & present == set()

        # THE INVARIANT, stated as a set fact: zero judge_errors in `absent`.
        errors = {r["id"] for r in FIXTURE if r["true_label"] == "judge_error"}
        assert absent & errors == set()

        # absent = exactly the one lexically-absent confab.
        assert absent == {"r93"}

        # present = 4 judge_errors + 8 token-reusing confabs = 12.
        assert present == errors | {"r53", "r81", "r86", "r94", "r79", "r20", "r65", "r76"}
        assert len(present) == 12


# ------------------------------------------ salient_tokens unit tests

class TestSalientTokens:
    def test_keeps_ip_address_whole(self):
        toks = salient_tokens("coordinator IP was 10.10.70.127 today")
        assert "10.10.70.127" in toks

    def test_keeps_numeric_range(self):
        assert "12-18" in salient_tokens("requires 12-18 months of validation")

    def test_keeps_plus_quantity(self):
        assert "6+" in salient_tokens("persistent through 6+ collision failures")

    def test_drops_grounding_framing_and_stopwords(self):
        toks = salient_tokens(
            "Whether the outcome is uncertain or possible remains unknown"
        )
        for boilerplate in ("whether", "the", "is", "uncertain", "possible", "unknown"):
            assert boilerplate not in toks

    def test_keeps_distinctive_content_words(self):
        toks = salient_tokens("The LATAM sponsor pool projections are weak")
        assert "latam" in toks
        assert "sponsor" in toks

    def test_drops_generic_vague_nouns(self):
        # generic abstract nouns carry no checkable specificity; dropping them
        # is what lets r93 ("...rationalizing constraints") screen absent even
        # though "constraints" appears in unrelated H3 context.
        toks = salient_tokens("iterating without rationalizing constraints")
        assert "constraints" not in toks
        assert "rationalizing" in toks
