# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Skeptic re-judge + verified-grounding pipeline — issue #38, Slice 2 (Track A).

WHY THIS EXISTS
---------------
The first-pass LLM grounding judge over-reports the false-memory rate: it marks
claims `grounded:false` that the transcript actually supports, because it chunks
and routinely never reads the transcript tail (.scars landmine #4 — per-claim
grounding is noise-dominated without forced verification). Slice 1
(`grounding_screen.screen_negative`) triages those negatives deterministically:

  "absent"  -> NO salient token of the claim is anywhere in the transcript.
               The judge's negative is RELIABLE -> this is a real confabulation.
  "present" -> salient tokens ARE in the transcript -> the judge's negative is
               UNRELIABLE (it claimed absence; the tokens are present). Escalate.

Slice 2 closes the loop on the `present` residual with a SECOND, skeptical LLM
pass that is forced to read the WHOLE transcript (never chunked — the tail-skip
is the bug we route around):

  verify_negative(claim, transcript, chat) -> "grounded" | "confab"
    screen == "absent"  -> "confab"   (deterministic; MUST NOT call chat)
    screen == "present" -> skeptic_verdict re-judges:
                           grounded:true  -> "grounded" (judge_error rescued)
                           grounded:false -> "confab"   (token reuse, not support)

The `absent` short-circuit is load-bearing: it incurs ZERO gateway cost for
confidently-absent confabs, and it is the only verdict the screen can confirm on
its own word. Everything else is decided by the skeptic.

`chat(messages, **kwargs) -> str` is INJECTED so the pipeline is testable with a
mock and never reaches the network in the deterministic suite. (The real
research/experiments/lib/llm.py `chat` returns a (content, usage, model) tuple;
skeptic_verdict unwraps that defensively, but the contract is str.)

stdlib only.
"""

import json

from grounding_screen import screen_negative


# ---------------------------------------------------------------------------
# Skeptic system prompt — the crux.
# ---------------------------------------------------------------------------
# Frame the model as a SKEPTICAL verifier of the FIRST judge's negative, warn it
# about the first judge's known failure (tail-skip), and define "grounded"
# STRICTLY as the transcript ASSERTING the claim — not merely sharing tokens.
SKEPTIC_SYS = """You are a SKEPTICAL grounding verifier. A first-pass judge already \
marked the claim below `grounded:false` (it could not find support). That judge is \
UNRELIABLE on negatives: it chunks the transcript and ROUTINELY MISSES support, \
especially in the TAIL (it often never reads the end). Your job is to re-check, \
honestly and thoroughly, whether the support is actually there.

SEARCH THE WHOLE TRANSCRIPT, beginning to end. Do not stop early. The support, if \
it exists, is frequently near the end where the first judge stopped looking.

GROUNDED IS STRICT. A claim is grounded ONLY if the transcript actually ASSERTS it \
— states the fact, or clearly entails it. Merely SHARING WORDS with the transcript \
is NOT grounding. Beware these token-reuse traps, which are NOT grounded (they are \
real confabulations even though the vocabulary overlaps):
  (a) INVERSION — the claim flips a value, number, sign, or direction the \
transcript actually states.
  (b) OVER-EXTRAPOLATION — the claim asserts more than the transcript supports \
(a guess, projection, or generalization the transcript never makes).
  (c) WRONG COUNT — the claim states a quantity/count/duration that the \
transcript does not actually state (or states differently).

Decide:
  grounded: true  -> the transcript genuinely asserts (or clearly entails) the claim.
  grounded: false -> no real assertion; if tokens are present, they fall into a \
trap above (inversion / over-extrapolation / wrong count) or are otherwise unrelated.

Output ONLY strict JSON, no prose, exactly:
{"grounded": true|false, "evidence": "<short verbatim quote that asserts the claim, \
OR an explanation of why the present tokens do not support it>"}"""


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of a model reply, tolerating ```json fences and
    surrounding prose (mirrors research/experiments/lib/llm.py `extract_json`).

    stdlib only — kept local so this module depends on nothing but grounding_screen.
    """
    t = text.strip()
    # Strip a leading code fence (```json ... ``` or ``` ... ```).
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # Fall back to the widest brace span (tolerates leading/trailing prose).
    i, j = t.find("{"), t.rfind("}")
    if i != -1 and j != -1 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except json.JSONDecodeError:
            pass
    raise json.JSONDecodeError("no JSON object found in skeptic reply", t, 0)


def skeptic_verdict(claim: str, transcript: str, chat) -> dict:
    """Re-judge ONE escalated negative with a skeptical, full-transcript pass.

    `chat(messages, **kwargs) -> str` is injected. The FULL transcript is passed
    (NOT chunked — the first judge's tail-skip is the bug this routes around).
    Returns {"grounded": bool, "evidence": str}, JSON-parsed from the reply
    (tolerating ```json fences + surrounding prose).
    """
    user_content = (
        f"CLAIM (first judge marked this grounded:false):\n{claim}\n\n"
        f"FULL TRANSCRIPT (read all of it, including the tail):\n{transcript}"
    )
    reply = chat(
        [
            {"role": "system", "content": SKEPTIC_SYS},
            {"role": "user", "content": user_content},
        ]
    )
    # The real lib/llm.py chat returns (content, usage, model); the contract is
    # str. Unwrap a tuple/list defensively so either shape works.
    if isinstance(reply, (tuple, list)):
        reply = reply[0]
    raw = _extract_json(reply)
    return {
        "grounded": bool(raw.get("grounded", False)),
        "evidence": str(raw.get("evidence", "")),
    }


def verify_negative(claim: str, transcript: str, chat) -> str:
    """Verified-grounding decision for a first-pass `grounded:false` verdict.

      screen_negative == "absent"  -> "confab"  (deterministic; MUST NOT call chat)
      screen_negative == "present" -> skeptic_verdict -> "grounded" if grounded
                                      else "confab"

    Returns "grounded" | "confab".
    """
    if screen_negative(claim, transcript) == "absent":
        # The screen confirmed a real confabulation on its own word — no salient
        # token anywhere in the transcript. Zero gateway cost: do NOT call chat.
        return "confab"
    verdict = skeptic_verdict(claim, transcript, chat)
    return "grounded" if verdict["grounded"] else "confab"
