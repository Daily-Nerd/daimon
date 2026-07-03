# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Deterministic grounding-verdict screen — issue #38, Slice 1 (Track A research arm).

WHY THIS EXISTS
---------------
The LLM grounding judge over-reports the false-memory rate. It marks claims
`grounded:false` that the transcript actually supports, because it misses
transcript support — especially in the tail (it chunks and routinely never
reads the end). Hand-verification of the 13 `grounded:false` H3/H4 claims found
9 judge-errors and only 4 real confabulations: a 69% false-positive rate on the
judge's NEGATIVES. (See .scars landmine #4: per-claim LLM grounding is
noise-dominated without forced verification.)

A `grounded:false` verdict is only trustworthy if the claim's salient tokens are
genuinely ABSENT from the transcript. This module enforces that deterministically.

WHAT THIS IS — AND IS NOT
-------------------------
This is a TRIAGE, not an auto-corrector. It reads the WHOLE transcript (never
chunked — the judge's tail-skip is the bug we are routing around) and sorts each
`grounded:false` verdict into one of two buckets:

  "absent"  -> NO salient token of the claim occurs anywhere in the transcript.
               The judge's negative is RELIABLE: keep `grounded:false`. This is
               the only case where a confabulation can be confirmed on the
               screen's word.

  "present" -> salient tokens DO occur in the transcript. The judge is
               UNRELIABLE here: it claimed absence, but the tokens are present,
               so the negative cannot be trusted. Escalate to Slice 2 (skeptic
               re-judge / human). The verdict must NOT be counted as a confab on
               the judge's word alone.

The screen NEVER flips a verdict to grounded:true. `present` does not mean "the
claim is supported" — token reuse is cheap and real confabs reuse vocabulary
(e.g. H3 r81/r86/r94). It means only "the judge's NEGATIVE is not safe to
trust." Separating trustworthy negatives (absent) from unreliable ones (present)
is the entire job. Confirming or flipping `present` claims is downstream work.

stdlib only. The matching primitive mirrors
research/memory-backend/benchmark/state/grade.py `_mentions`: whole-token,
case-insensitive, with `_`/`-` normalized to spaces on both sides.
"""

import re


# Common English stopwords + grounding-judge framing/hedging boilerplate +
# generic vague nouns. None of these, on their own, indicate that a transcript
# SUPPORTS a specific claim — so their presence must not rescue a negative.
#
# The generic-noun group is load-bearing for r93: the claim
# "...iterating without rationalizing constraints" reuses "constraints", which
# appears in H3 only in unrelated contexts ("Locked constraints", "FT job
# constraint"). It carries no checkable specificity, so it is not salient. The
# claim's actually-distinctive tokens (pragmatic/methodical/iterating/
# rationalizing) are nowhere in the transcript, so r93 correctly screens absent.
STOPWORDS = frozenset({
    # articles / conjunctions / prepositions / determiners
    "a", "an", "the", "and", "or", "but", "nor", "of", "to", "in", "on", "at",
    "for", "by", "with", "from", "into", "onto", "as", "than", "then", "so",
    "if", "because", "given", "due", "about", "across", "per", "via", "over",
    "under", "up", "down", "out", "off", "without", "within", "through",
    "during", "after", "before", "while", "despite", "between", "against",
    "this", "that", "these", "those", "such", "each", "any", "all", "both",
    "some", "no", "not", "only", "also", "still", "yet", "even", "just",
    # pronouns
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us",
    "them", "my", "your", "his", "its", "our", "their", "mine", "yours",
    "who", "whom", "whose", "which", "what", "where", "when", "why", "how",
    "there", "here",
    # auxiliaries / common verbs of being
    "is", "are", "was", "were", "be", "been", "being", "am", "do", "does",
    "did", "done", "has", "have", "had", "will", "would", "shall", "should",
    "can", "could", "may", "might", "must", "go", "goes", "went", "get",
    "got", "make", "made",
    # grounding-judge framing / epistemic hedging boilerplate
    "whether", "uncertain", "uncertainty", "unknown", "unresolved", "undiagnosed",
    "unconfirmed", "possible", "possibly", "probable", "probably", "likely",
    "maybe", "perhaps", "seems", "appears", "implies", "suggest", "suggests",
    "regarding", "claim", "claims", "claimed", "statement", "question",
    "questions", "open", "remains", "remain", "requires", "require", "required",
    "user", "assistant",
    # generic / vague abstract nouns — high frequency, no checkable specificity
    "thing", "things", "way", "ways", "value", "values", "status", "state",
    "cause", "causes", "issue", "issues", "presence", "constraint", "constraints",
    "matter", "case", "cases", "point", "points", "part", "parts", "aspect",
    "factor", "factors", "outcome", "outcomes", "situation", "context",
})


def _mentions(text: str, term: str) -> bool:
    """Whole-token-ish substring match, case-insensitive (grade.py `_mentions`).

    Word boundaries are enforced where the term is purely alphanumeric, so
    "Go" does not match inside "Google". Symbol-bearing terms ("6+", "20%",
    IPs like "10.10.70.127") fall back to a normalized substring test.

    `_` and `-` are normalized to spaces on BOTH sides before matching, so
    identifier-style tokens ("slzb_06m", "year-1") match their spaced forms.
    """
    norm = lambda s: re.sub(r"[_-]", " ", s.lower())
    t = norm(text)
    term_l = norm(term).strip()
    if not term_l:
        return False
    if re.fullmatch(r"[a-z0-9]+", term_l):
        return re.search(rf"\b{re.escape(term_l)}\b", t) is not None
    return term_l in t


# A token is a maximal run of alphanumerics, optionally joined by internal
# connectors (. _ - / #) to keep IPs, file names, kebab identifiers, and
# numeric ranges whole, with an optional trailing quantity marker (+ #).
#   "10.10.70.127" -> one token   "12-18" -> one token   "6+" -> "6+"
#   "configmap.yaml" -> one token "slzb-06m-zigbee-coordinator" -> one token
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[._/#-][a-z0-9]+)*[+#]?")


def salient_tokens(claim: str) -> set[str]:
    """Distinctive tokens whose presence in the source would indicate support.

    Keeps numbers/quantities (incl. forms like '12-18', '6+', IPs like
    '10.10.70.127'), identifiers, file names, and content words. Drops
    stopwords AND grounding-framing boilerplate (whether, uncertain, possible,
    the, is, a, of, ...) and generic vague nouns that carry no checkable
    specificity.
    """
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(claim.lower()):
        # Stopword test on the alphabetic core (strip connectors/markers) so
        # "constraints," / "constraints." also drop, while "year-1" survives.
        core = re.sub(r"[._/#+-]", "", raw)
        if not core:
            continue
        if raw in STOPWORDS or core in STOPWORDS:
            continue
        # Drop bare single alpha letters (e.g. stray "s"); keep single digits,
        # they can be quantities.
        if len(core) == 1 and core.isalpha():
            continue
        tokens.add(raw)
    return tokens


def screen_negative(claim: str, transcript: str) -> str:
    """Triage a `grounded:false` verdict against the FULL transcript.

    NOT chunked — the judge's tail-skip is the bug this routes around, so the
    whole transcript is searched. Returns:

      "absent"  -> NO salient token found -> judge is RELIABLE -> keep
                   grounded:false (a confabulation the screen can confirm).
      "present" -> salient tokens found -> judge UNRELIABLE -> escalate to
                   Slice 2 (skeptic / human). The verdict must NOT be counted as
                   a confab on the judge's word alone.

    This never flips a verdict to grounded:true; it only separates trustworthy
    negatives (absent) from unreliable ones (present).
    """
    for token in salient_tokens(claim):
        if _mentions(transcript, token):
            return "present"
    return "absent"
