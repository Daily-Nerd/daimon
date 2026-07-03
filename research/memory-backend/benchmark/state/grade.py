"""Answering from a memory's context + DETERMINISTIC state grading.

State grading does not use an LLM judge: because scenarios are authored, the
current value of every probe is known. We check (a) the answer asserts the
current value (gold) and (b) it does NOT assert a now-stale value. For override
probes, asserting a stale value is the failure mode we care about.
"""

import re
from typing import Dict

from benchmark.state.scenarios import Probe
from benchmark.evaluate import LLMClient, DEFAULT_ANSWER_MODEL


ANSWER_PROMPT = """You are answering a question about the CURRENT state of an ongoing situation, using only the memory below. The memory may be compressed. Answer with ONLY the current value, in as few words as possible. If something changed over time, report ONLY the latest value — do NOT mention, explain, or reference any previous, superseded, or historical value, not even to note that it changed.

MEMORY:
{memory}

QUESTION: {question}

CURRENT-STATE ANSWER:"""


def answer_state(memory_context: str, question: str,
                 llm_client: LLMClient, model: str = DEFAULT_ANSWER_MODEL) -> str:
    if not memory_context.strip():
        return "(no memory available)"
    prompt = ANSWER_PROMPT.format(memory=memory_context, question=question)
    out = llm_client.chat_completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=200,
    )
    return (out or "").strip()


def _mentions(text: str, term: str) -> bool:
    """Whole-token-ish substring match, case-insensitive.

    Uses word boundaries where the term is alphanumeric so "Go" doesn't match
    inside "Google", but allows symbols like "20%" / "75mg" to match directly.

    Underscores and hyphens are normalized to spaces on BOTH sides before
    matching: structured memories leak identifier-style tokens into answers
    ("billing_revamp", "usage_based") that are semantically identical to the
    gold ("billing revamp", "usage-based") but regex \\b treats "_" as a word
    character and would miss them.
    """
    norm = lambda s: re.sub(r"[_-]", " ", s.lower())
    t = norm(text)
    term_l = norm(term).strip()
    if not term_l:
        return False
    if re.fullmatch(r"[a-z0-9]+", term_l):
        return re.search(rf"\b{re.escape(term_l)}\b", t) is not None
    return term_l in t


def grade_state(answer: str, probe: Probe) -> Dict[str, bool]:
    """Deterministic grade for one probe.

    correct  = states the current value AND does not assert any stale value.
    has_gold = states the current value (regardless of stale leakage).
    stale    = asserts at least one now-wrong value.
    """
    has_gold = any(_mentions(answer, g) for g in probe.gold_terms())
    asserts_stale = any(_mentions(answer, s) for s in probe.stale)
    return {
        "correct": bool(has_gold and not asserts_stale),
        "has_gold": bool(has_gold),
        "stale": bool(asserts_stale),
    }
