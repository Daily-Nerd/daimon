"""Deterministic synthetic-session builder. The transcript is the vehicle:
cycle k's session opens with the carried context (rendered briefing, or raw
checkpoint JSON in the carry arm), pads with screened filler turns, optionally
injects distractor work, and at FLIP_CYCLE (and ONLY there) revises the
evolving fact from V1 to V2. After the flip cycle, knowledge of V2 can reach
cycle k+1 only through the memory channel — that is the experiment."""

import seed


class VocabLeakError(RuntimeError):
    """A grading nonce leaked into wrapper text — grading would be meaningless."""


_OPENER = ("<session-context>\nWhile you were away, here is where we left "
           "off:\n{context}\n</session-context>\n\nWhere did we leave off?")
_OPENER_ACK = ("Caught up from the handoff context above; continuing from "
               "there.")

_FLIP_USER = (f"Update from the load-test rerun: the chunk threshold is "
              f"revised — {seed.V2_TOKEN} replaces {seed.V1_TOKEN} effective "
              f"immediately. The old value is obsolete.")
_FLIP_ACK = (f"Recorded: chunk threshold is now {seed.V2_TOKEN}; "
             f"{seed.V1_TOKEN} is superseded.")


def make_transcript(context_text: str, cycle: int, arm: str,
                    _extra_wrapper: str = "") -> list[dict]:
    if arm not in ("control", "distractor", "carry"):
        raise ValueError(f"unknown arm: {arm}")
    wrapper_texts = [_OPENER.replace("{context}", ""), _OPENER_ACK,
                     _extra_wrapper]
    wrapper_texts += [u + " " + a for u, a in seed.FILLER_TURNS]
    if arm == "distractor":
        wrapper_texts += seed.DISTRACTOR_POOL
    # The flip turn deliberately contains kelvane tokens: it is session
    # CONTENT (the fact evolving), not wrapper. Everything else must be clean.
    leaked = seed.vocab_screen(wrapper_texts)
    if leaked:
        raise VocabLeakError(f"grading nonces leaked into wrappers: {leaked}")

    msgs = [
        {"role": "user",
         "content": _OPENER.replace("{context}", context_text)
                    + (" " + _extra_wrapper if _extra_wrapper else "")},
        {"role": "assistant", "content": _OPENER_ACK},
    ]
    if cycle == seed.FLIP_CYCLE:
        msgs.append({"role": "user", "content": _FLIP_USER})
        msgs.append({"role": "assistant", "content": _FLIP_ACK})
    if arm == "distractor":
        # deterministic per-cycle rotation through the pool, 3 items per cycle
        for i in range(3):
            d = seed.DISTRACTOR_POOL[(cycle * 3 + i) % len(seed.DISTRACTOR_POOL)]
            msgs.append({"role": "user", "content": f"Also today: {d}"})
            msgs.append({"role": "assistant",
                         "content": f"Acknowledged and tracked: {d}"})
    for u, a in seed.FILLER_TURNS:
        msgs.append({"role": "user", "content": u})
        msgs.append({"role": "assistant", "content": a})
    return msgs
