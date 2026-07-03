"""Four memory strategies, compared at an equal token budget.

Interface (duck-typed):
    observe(turn: str) -> None       # consume one conversation turn
    context(query: str) -> str       # the text injected at answer time
    tokens() -> int                  # current size of the injected context

Fairness design:
- `CslMemory` and `SummaryMemory` both consolidate via the SAME model with
  SYMMETRIC prompts at the SAME budget. The only difference is representation
  (structured CSL vs prose). That isolates "does structure help state tracking".
- `RagAppendMemory` is the naive-retrieval baseline: it never consolidates, it
  retrieves at query time within the budget. It is expected to leak stale values
  on overrides — that is a real (not strawman) property of vanilla RAG.
- `RawMemory` is the uncapped ceiling.
"""

import re
from typing import List

from benchmark.evaluate import (
    LLMClient,
    count_tokens,
    chunk_conversation,
    retrieve_context,
    DEFAULT_ANSWER_MODEL,
)


# Symmetric update prompts — identical instructions, only the representation word
# ("CSL memory" vs "summary") and format line differ.
_CSL_UPDATE_PROMPT = """You maintain a compact memory written in Context Script Language (CSL): typed statements like FACT(...), PREFERENCE(...), RELATION(...), INTENT(...), EVENT(...), RULE(...).

Below is the CURRENT memory and a NEW conversation turn. Output the UPDATED memory.

RULES:
- Merge new information from the turn.
- When a value CHANGES (a preference flips, a fact is revised, a decision is reversed), REPLACE the old value. The memory must reflect the CURRENT state only — do NOT keep superseded values as if still true.
- Keep the memory under {budget} tokens. Drop the least important details if over budget.
- Output ONLY CSL statements, one per line. No prose, no commentary.

CURRENT MEMORY:
{memory}

NEW TURN:
{turn}

UPDATED MEMORY (CSL):"""

_SUMMARY_UPDATE_PROMPT = """You maintain a compact running summary of a conversation.

Below is the CURRENT summary and a NEW conversation turn. Output the UPDATED summary.

RULES:
- Merge new information from the turn.
- When a value CHANGES (a preference flips, a fact is revised, a decision is reversed), REPLACE the old value. The summary must reflect the CURRENT state only — do NOT keep superseded values as if still true.
- Keep the summary under {budget} tokens. Drop the least important details if over budget.
- Output ONLY the summary prose. No commentary, no preamble.

CURRENT SUMMARY:
{memory}

NEW TURN:
{turn}

UPDATED SUMMARY:"""


class RawMemory:
    """Uncapped ceiling: keep every turn verbatim."""
    name = "raw"

    def __init__(self):
        self._turns: List[str] = []

    def observe(self, turn: str) -> None:
        self._turns.append(turn)

    def context(self, query: str) -> str:  # noqa: ARG002 (query unused by design)
        return "\n".join(self._turns)

    def tokens(self) -> int:
        return count_tokens(self.context(""))


class _LLMConsolidatingMemory:
    """Shared base for CSL/Summary: an LLM rewrites the memory each turn."""
    name = "base"
    _prompt = ""

    def __init__(self, llm_client: LLMClient, model: str = DEFAULT_ANSWER_MODEL, budget: int = 300):
        self.llm = llm_client
        self.model = model
        self.budget = budget
        self.memory = ""

    def observe(self, turn: str) -> None:
        prompt = self._prompt.format(budget=self.budget, memory=self.memory or "(empty)", turn=turn)
        out = self.llm.chat_completion(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=max(256, self.budget * 2),
        )
        if out is not None and out.strip():
            cleaned = self._sanitize(out.strip())
            if cleaned:
                self.memory = cleaned
            # else: treat as a failed update and keep the previous memory,
            # same policy as an empty response.

    def _sanitize(self, out: str) -> str:
        """Hook for representation-specific output validation. Identity here."""
        return out

    def context(self, query: str) -> str:  # noqa: ARG002
        return self.memory

    def tokens(self) -> int:
        return count_tokens(self.memory)


# A CSL statement line: TYPE(...), optionally behind a list bullet. Models that
# leak chain-of-thought (kimi-k2.6 does, intermittently) wrap statements in
# prose; everything that isn't a statement line is dropped.
_CSL_STATEMENT_RE = re.compile(r"^\s*(?:[-*•]\s*)?([A-Z][A-Z_]*\(.*\))\s*$")


class CslMemory(_LLMConsolidatingMemory):
    name = "csl"
    _prompt = _CSL_UPDATE_PROMPT

    def _sanitize(self, out: str) -> str:
        """Keep only parseable CSL statement lines.

        The update prompt demands "ONLY CSL statements, one per line"; this
        enforces that contract. Reasoning prose stored as memory blew the
        token budget 6.4x in the 2026-06-12 wide run. Validatability is the
        structured representation's advantage — the summary arm has no
        equivalent check because any prose is a valid summary.
        """
        lines = []
        for raw_line in out.splitlines():
            m = _CSL_STATEMENT_RE.match(raw_line)
            if m:
                lines.append(m.group(1))
        return "\n".join(lines)


class SummaryMemory(_LLMConsolidatingMemory):
    name = "summary"
    _prompt = _SUMMARY_UPDATE_PROMPT


class RagAppendMemory:
    """Naive retrieval: append turns, retrieve top chunks within budget at query time.

    No consolidation — this is the baseline that should leak stale values on
    overrides, because it retrieves both the old and new statements and cannot
    know which is current.
    """
    name = "rag-append"

    def __init__(self, budget: int = 300, max_chunk_tokens: int = 40):
        self.budget = budget
        self.max_chunk_tokens = max_chunk_tokens
        self._chunks: List[str] = []

    def observe(self, turn: str) -> None:
        self._chunks.extend(chunk_conversation(turn, max_chunk_tokens=self.max_chunk_tokens))

    def context(self, query: str) -> str:
        return retrieve_context(query, self._chunks, token_budget=self.budget)

    def tokens(self) -> int:
        # Budget-bounded by construction; report the cap as the effective size.
        return self.budget


def build_memories(llm_client: LLMClient, model: str, budget: int) -> List[object]:
    """Construct one instance of each strategy for a scenario run."""
    return [
        RawMemory(),
        CslMemory(llm_client, model=model, budget=budget),
        SummaryMemory(llm_client, model=model, budget=budget),
        RagAppendMemory(budget=budget),
    ]
