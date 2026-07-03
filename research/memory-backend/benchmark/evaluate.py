#!/usr/bin/env python3
"""
Context-as-Program Benchmark Evaluation Pipeline

Implements three evaluation modes:
1. Compression Evaluation — token ratios and primitive extraction tracking
2. QA Accuracy Evaluation — LLM-based question generation, answering, and grading
3. Human-Readable Report — Markdown report with statistics and failure patterns
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import random
import re
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("cap_benchmark")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm.litellm.svc.cluster.local:4000")
LITELLM_API_KEY = os.environ.get("LITELLM_VIRTUAL_KEY", os.environ.get("OPENAI_API_KEY", ""))
DEFAULT_QUESTION_MODEL = "gpt-5-via-cliproxy"
DEFAULT_ANSWER_MODEL = "kimi-k2.6"          # cheaper for answering
DEFAULT_GRADE_MODEL = "gpt-5-via-cliproxy"
RATE_LIMIT_DELAY = 1.5                        # seconds between API calls
MAX_RETRIES = 3


def _context_window() -> Optional[int]:
    """The active model's context window from LITELLM_CONTEXT_WINDOW, or None
    (gate off). A malformed value is ignored rather than crashing a run."""
    raw = os.environ.get("LITELLM_CONTEXT_WINDOW", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _is_timeout(exc: BaseException) -> bool:
    """True if exc is a read timeout (possibly wrapped in a URLError). A stalled
    model won't recover on retry, so these must fail fast like a 4xx."""
    if isinstance(exc, TimeoutError):
        return True
    return isinstance(getattr(exc, "reason", None), TimeoutError)


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

def _get_tokenizer():
    """Lazy-load tiktoken if available."""
    try:
        import tiktoken
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


_TOKENIZER = None


def count_tokens(text: str) -> int:
    """Count tokens in a string. Falls back to char-based heuristic."""
    if not text:
        return 0
    global _TOKENIZER
    if _TOKENIZER is None:
        _TOKENIZER = _get_tokenizer()
    if _TOKENIZER is not None:
        try:
            return len(_TOKENIZER.encode(text))
        except Exception:
            pass
    # Fallback: ~4 characters per token for English prose
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Per-model request tweaks
# ---------------------------------------------------------------------------

# Reasoning models burn output tokens on their trace — give them headroom so the
# answer isn't truncated. Case-insensitive substring match on the model id.
_REASONING_HINTS = ("kimi", "deepseek-r1", "r1-distill", "qwq", "ornith")

# Models whose UPSTREAM rejects any temperature other than 1 (e.g. kimi:
# "invalid temperature: only 1 is allowed for this model"). This is a per-provider
# quirk, NOT a general reasoning rule — do NOT add a model here unless its gateway
# actually rejects other temperatures, or you silently break determinism.
_TEMP_LOCKED_HINTS = ("kimi",)

_REASONING_MAX_TOKENS = 4000


def _matches(model: str, hints) -> bool:
    m = (model or "").lower()
    return any(h in m for h in hints)


def _adjust_params(model: str, temperature, max_tokens):
    """Per-model request tweaks: reasoning headroom (broad) + temperature lock (narrow).

    Returns the possibly-adjusted (temperature, max_tokens). Only ever raises
    max_tokens, never lowers it."""
    if _matches(model, _REASONING_HINTS) and max_tokens < _REASONING_MAX_TOKENS:
        max_tokens = _REASONING_MAX_TOKENS
    if _matches(model, _TEMP_LOCKED_HINTS):
        temperature = 1
    return temperature, max_tokens


# ---------------------------------------------------------------------------
# LiteLLM / OpenAI client wrapper with caching and rate limiting
# ---------------------------------------------------------------------------

class LLMClient:
    """Wrapper around OpenAI-compatible API with disk caching, retries, and rate limiting."""

    def __init__(
        self,
        base_url: str = LITELLM_BASE_URL,
        api_key: str = LITELLM_API_KEY,
        cache_dir: Optional[str] = None,
        delay: float = RATE_LIMIT_DELAY,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.delay = delay
        self._client = None
        self._last_call_time: Optional[float] = None

        if cache_dir is None:
            cache_dir = os.path.join(os.path.dirname(__file__), ".llm_cache")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_client(self):
        """No-op for urllib-based client."""
        return None

    def _chat_completion_urllib(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 2000,
    ) -> Optional[str]:
        """Call LiteLLM proxy using stdlib urllib."""
        # Reasoning models need token headroom; some providers (kimi) lock temperature.
        temperature, max_tokens = _adjust_params(model, temperature, max_tokens)
        import urllib.request
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps({
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode())
            content = data["choices"][0]["message"].get("content", "") or ""
            if not content.strip():
                content = data["choices"][0]["message"].get("reasoning_content", "") or ""
            return content

    def _cache_key(self, model: str, messages: List[Dict[str, str]], temperature: float, max_tokens: int) -> str:
        payload = json.dumps({"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _sleep_if_needed(self):
        if self._last_call_time is not None:
            elapsed = time.time() - self._last_call_time
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
        self._last_call_time = time.time()

    def chat_completion(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 2000,
        retries: int = MAX_RETRIES,
    ) -> Optional[str]:
        """Call the chat completion API with caching and retries. Returns content string or None on failure."""
        cache_key = self._cache_key(model, messages, temperature, max_tokens)
        cache_path = self._cache_path(cache_key)

        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                logger.debug("Cache hit: %s", cache_key[:16])
                return cached.get("content")
            except Exception as exc:
                logger.warning("Failed to load cache %s: %s", cache_key[:16], exc)

        # Pre-flight: if the model's window is known and the prompt (+ requested
        # completion) won't fit, skip the call entirely — it would stall, not
        # answer (the ornith failure mode). No network, no retry.
        window = _context_window()
        if window is not None:
            est = sum(count_tokens(m.get("content", "")) for m in messages)
            if est + max_tokens > window:
                logger.error(
                    "Prompt ~%d tokens + %d max_tokens exceeds context window %d "
                    "for model %s; skipping call (it would stall, not answer).",
                    est, max_tokens, window, model,
                )
                return None

        self._sleep_if_needed()

        for attempt in range(1, retries + 1):
            try:
                content = self._chat_completion_urllib(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                # Save to cache
                try:
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump({"content": content, "cached_at": datetime.utcnow().isoformat()}, f)
                except Exception as exc:
                    logger.warning("Failed to write cache: %s", exc)
                return content
            except Exception as exc:
                if _is_timeout(exc):
                    logger.error(
                        "API call timed out (model %s stalled?); not retrying — "
                        "a stall won't self-heal in another %ds.", model, 300,
                    )
                    return None
                logger.warning("API call failed (attempt %d/%d): %s", attempt, retries, exc)
                if attempt < retries:
                    time.sleep(self.delay * attempt)
                else:
                    logger.error("API call exhausted retries: %s", exc)
                    return None
        return None


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class CompressionResult:
    raw_tokens: int
    csl_tokens: int
    ratio: float
    primitives: Dict[str, int]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Question:
    id: str
    text: str
    category: str  # e.g. "fact", "preference", "relation", "unresolved"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Answer:
    text: str
    source: str  # "raw" or "csl"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Score:
    accuracy: float
    completeness: float
    tone_match: float
    overall: float
    explanation: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QAItem:
    question: Question
    raw_answer: Answer
    csl_answer: Answer
    score: Score                       # back-compat: equals csl_score
    rag_answer: Optional[Answer] = None
    raw_score: Optional[Score] = None
    csl_score: Optional[Score] = None
    rag_score: Optional[Score] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "question": self.question.to_dict(),
            "raw_answer": self.raw_answer.to_dict(),
            "csl_answer": self.csl_answer.to_dict(),
            "score": self.score.to_dict(),
        }
        if self.rag_answer is not None:
            d["rag_answer"] = self.rag_answer.to_dict()
        for name in ("raw_score", "csl_score", "rag_score"):
            val = getattr(self, name)
            if val is not None:
                d[name] = val.to_dict()
        return d


@dataclass
class ConversationResult:
    conv_id: str
    domain: str
    compression: CompressionResult
    qa_items: List[QAItem] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conv_id": self.conv_id,
            "domain": self.domain,
            "compression": self.compression.to_dict(),
            "qa_items": [q.to_dict() for q in self.qa_items],
            "errors": self.errors,
        }


@dataclass
class BenchmarkResult:
    conversations: List[ConversationResult]
    aggregate_compression: Dict[str, Any] = field(default_factory=dict)
    aggregate_qa: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversations": [c.to_dict() for c in self.conversations],
            "aggregate_compression": self.aggregate_compression,
            "aggregate_qa": self.aggregate_qa,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# 1. Compression Evaluation
# ---------------------------------------------------------------------------

PRIMITIVE_PATTERN = re.compile(
    r"\b(FACT|RELATION|PREFERENCE|EVENT|INTENT|UNRESOLVED|SUMMARY|RULE|NOTE)\s*\(")


def extract_primitives(csl_program: str) -> Dict[str, int]:
    """Count occurrences of each CSL primitive in the program string."""
    counts: Dict[str, int] = {}
    for match in PRIMITIVE_PATTERN.finditer(csl_program):
        primitive = match.group(1)
        counts[primitive] = counts.get(primitive, 0) + 1
    return counts


def measure_compression(raw_text: str, csl_program: str) -> CompressionResult:
    """Compute token counts, compression ratio, and primitive extraction stats."""
    raw_tokens = count_tokens(raw_text)
    csl_tokens = count_tokens(csl_program)
    ratio = raw_tokens / csl_tokens if csl_tokens > 0 else 0.0
    primitives = extract_primitives(csl_program)
    return CompressionResult(
        raw_tokens=raw_tokens,
        csl_tokens=csl_tokens,
        ratio=ratio,
        primitives=primitives,
    )


# ---------------------------------------------------------------------------
# 2. QA Accuracy Evaluation
# ---------------------------------------------------------------------------

QUESTION_GENERATION_PROMPT = """You are an expert evaluator. Read the following conversation carefully and generate {n} diverse, specific questions that require understanding the conversation to answer correctly.

Your questions should cover:
- Facts stated or implied in the conversation
- Preferences or opinions expressed by participants
- Relationships between people, projects, or entities
- Unresolved items, open questions, or pending decisions
- Temporal or causal relationships

For each question, output a JSON object with fields:
- "id": a short unique id like "q1"
- "text": the question text
- "category": one of "fact", "preference", "relation", "unresolved", "temporal", "causal"

Return ONLY a JSON array of these objects, with no markdown formatting or extra commentary.

CONVERSATION:
{conversation}

QUESTIONS (JSON array):"""


ANSWER_FROM_RAW_PROMPT = """You are a helpful assistant. Answer the following question using ONLY the information in the provided conversation. Be concise but complete. If the answer is not in the conversation, say "I don't know based on the conversation."

CONVERSATION:
{conversation}

QUESTION: {question}

ANSWER:"""


ANSWER_FROM_CSL_PROMPT = """You are a helpful assistant with access to a compressed memory store written in Context Script Language (CSL). Each statement encodes facts, relationships, preferences, events, intents, unresolved questions, summaries, rules, and notes.

Answer the user's question by "executing" (expanding) relevant CSL statements into a coherent, natural response. If the CSL program does not contain enough information, say so.

--- CSL MEMORY ---
{csl_program}
--- END MEMORY ---

QUESTION: {question}

ANSWER:"""


GRADING_PROMPT = """You are an expert evaluator. Compare the CSL answer to the RAW answer for the given question. Grade the CSL answer on three dimensions:

1. Accuracy (0.0–1.0): Are the facts in the CSL answer correct compared to the raw answer?
2. Completeness (0.0–1.0): Did the CSL answer capture all relevant details from the raw answer?
3. Tone match (0.0–1.0): Does the tone/perspective of the CSL answer match the raw answer?

Return ONLY a JSON object with this exact shape:
{{
  "accuracy": float,
  "completeness": float,
  "tone_match": float,
  "explanation": "string explaining the scores and any discrepancies"
}}

No markdown, no extra text.

QUESTION: {question}

RAW ANSWER:
{raw_answer}

CSL ANSWER:
{csl_answer}

EVALUATION (JSON):"""


def _safe_json_loads(text: str) -> Optional[Any]:
    """Extract and parse JSON from a string that may contain markdown or extra text."""
    if not text:
        return None
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from markdown code blocks
    code_block = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if code_block:
        try:
            return json.loads(code_block.group(1))
        except json.JSONDecodeError:
            pass
    # Try finding the first JSON array or object
    arr_start = text.find("[")
    obj_start = text.find("{")
    if arr_start != -1 and (obj_start == -1 or arr_start < obj_start):
        try:
            return json.loads(text[arr_start:])
        except json.JSONDecodeError:
            pass
    if obj_start != -1:
        try:
            # Find matching end brace by counting
            depth = 0
            for i, ch in enumerate(text[obj_start:]):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return json.loads(text[obj_start : obj_start + i + 1])
            return json.loads(text[obj_start:])
        except json.JSONDecodeError:
            pass
    return None


def generate_questions(
    conversation: str,
    n: int = 5,
    llm_client: Optional[LLMClient] = None,
    model: str = DEFAULT_QUESTION_MODEL,
) -> List[Question]:
    """Generate n diverse questions from a conversation using an LLM."""
    if llm_client is None:
        llm_client = LLMClient()

    prompt = QUESTION_GENERATION_PROMPT.format(conversation=conversation, n=n)
    messages = [{"role": "user", "content": prompt}]
    content = llm_client.chat_completion(model=model, messages=messages, temperature=0.5, max_tokens=2000)

    if content is None:
        raise RuntimeError("Failed to generate questions: API returned None")

    data = _safe_json_loads(content)
    if data is None:
        raise RuntimeError(f"Failed to parse questions JSON. Raw content:\n{content[:500]}")

    if not isinstance(data, list):
        raise RuntimeError(f"Expected JSON array for questions, got {type(data).__name__}")

    questions: List[Question] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        q = Question(
            id=str(item.get("id", f"q{len(questions)+1}")),
            text=str(item.get("text", "")),
            category=str(item.get("category", "fact")),
        )
        questions.append(q)

    # Enforce max n in case LLM returns more
    return questions[:n]


def answer_from_raw(
    question: str,
    conversation: str,
    llm_client: Optional[LLMClient] = None,
    model: str = DEFAULT_ANSWER_MODEL,
) -> Answer:
    """Answer a question using the full raw conversation."""
    if llm_client is None:
        llm_client = LLMClient()

    prompt = ANSWER_FROM_RAW_PROMPT.format(conversation=conversation, question=question)
    messages = [{"role": "user", "content": prompt}]
    content = llm_client.chat_completion(model=model, messages=messages, temperature=0.2, max_tokens=1500)

    if content is None:
        return Answer(text="[ERROR: API failure]", source="raw")
    return Answer(text=content.strip(), source="raw")


def answer_from_csl(
    question: str,
    csl_program: str,
    llm_client: Optional[LLMClient] = None,
    model: str = DEFAULT_ANSWER_MODEL,
) -> Answer:
    """Answer a question using only the CSL program."""
    if llm_client is None:
        llm_client = LLMClient()

    prompt = ANSWER_FROM_CSL_PROMPT.format(csl_program=csl_program, question=question)
    messages = [{"role": "user", "content": prompt}]
    content = llm_client.chat_completion(model=model, messages=messages, temperature=0.2, max_tokens=1500)

    if content is None:
        return Answer(text="[ERROR: API failure]", source="csl")
    return Answer(text=content.strip(), source="csl")


# ---------------------------------------------------------------------------
# 2b. RAG baseline — retrieval at an EQUAL token budget to the CSL program
# ---------------------------------------------------------------------------
#
# The whole point of this baseline: CSL is only interesting if it beats a plain
# retriever given the SAME number of context tokens. answer_from_raw (full 18K
# tokens) vs answer_from_csl (500 tokens) is a rigged comparison on the wrong
# axis. answer_from_rag fills exactly the CSL token budget with retrieved
# excerpts so the three methods are compared at equal cost.
#
# Retrieval here is lexical (keyword overlap + light IDF), dependency-free.
# That makes it a FLOOR for RAG, not a ceiling: a strong embedding retriever
# would likely do better. Read the numbers with that caveat.

_WORD_RE = re.compile(r"[a-z0-9]+")
_RETRIEVAL_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be",
    "to", "of", "in", "on", "at", "for", "with", "by", "from", "as", "that",
    "this", "it", "what", "who", "how", "did", "do", "does", "have", "has",
    "about", "which", "their", "they", "you", "your", "our", "we",
}


def _keywords(text: str) -> List[str]:
    return [w for w in _WORD_RE.findall(text.lower())
            if w not in _RETRIEVAL_STOPWORDS and len(w) > 2]


def chunk_conversation(conversation: str, max_chunk_tokens: int = 80) -> List[str]:
    """Split a conversation into retrievable chunks at line/turn boundaries.

    Non-empty lines are packed together until adding the next would exceed
    max_chunk_tokens, keeping chunks small enough that retrieval can fit a
    focused subset inside a tight budget.
    """
    lines = [ln.strip() for ln in conversation.splitlines() if ln.strip()]
    chunks: List[str] = []
    current: List[str] = []
    current_tokens = 0
    for line in lines:
        lt = count_tokens(line)
        if current and current_tokens + lt > max_chunk_tokens:
            chunks.append("\n".join(current))
            current, current_tokens = [], 0
        current.append(line)
        current_tokens += lt
    if current:
        chunks.append("\n".join(current))
    return chunks


def retrieve_context(question: str, chunks: List[str], token_budget: int) -> str:
    """Select the highest-scoring chunks for a question within token_budget.

    Scoring is keyword overlap weighted by inverse chunk frequency. Selected
    chunks are returned in chronological order (joined with an ellipsis) so the
    answering model still sees them in conversational sequence.
    """
    if not chunks or token_budget <= 0:
        return ""
    q_terms = set(_keywords(question))
    chunk_terms: List[set] = [set(_keywords(ch)) for ch in chunks]
    n = len(chunks)
    df: Dict[str, int] = {}
    for terms in chunk_terms:
        for t in terms:
            df[t] = df.get(t, 0) + 1

    scored: List[Tuple[float, int]] = []
    for idx, terms in enumerate(chunk_terms):
        overlap = q_terms & terms
        score = sum(math.log(1 + n / df[t]) for t in overlap)
        scored.append((score, idx))
    scored.sort(key=lambda x: (-x[0], x[1]))

    selected: List[int] = []
    used = 0
    for score, idx in scored:
        if score <= 0:
            break
        ct = count_tokens(chunks[idx])
        if selected and used + ct > token_budget:
            continue
        selected.append(idx)
        used += ct
        if used >= token_budget:
            break
    selected.sort()
    return "\n...\n".join(chunks[i] for i in selected)


ANSWER_FROM_RAG_PROMPT = """You are a helpful assistant. Answer the question using ONLY the retrieved excerpts from a conversation below. Be concise but complete. If the answer is not in the excerpts, say "I don't know based on the retrieved excerpts."

RETRIEVED EXCERPTS:
{context}

QUESTION: {question}

ANSWER:"""


def answer_from_rag(
    question: str,
    conversation: str,
    token_budget: int,
    llm_client: Optional[LLMClient] = None,
    model: str = DEFAULT_ANSWER_MODEL,
    max_chunk_tokens: int = 80,
) -> Answer:
    """Answer a question from chunks retrieved within an equal token budget."""
    if llm_client is None:
        llm_client = LLMClient()

    chunks = chunk_conversation(conversation, max_chunk_tokens=max_chunk_tokens)
    context = retrieve_context(question, chunks, token_budget)
    if not context:
        return Answer(text="I don't know based on the retrieved excerpts.", source="rag")

    prompt = ANSWER_FROM_RAG_PROMPT.format(context=context, question=question)
    messages = [{"role": "user", "content": prompt}]
    content = llm_client.chat_completion(model=model, messages=messages, temperature=0.2, max_tokens=1500)

    if content is None:
        return Answer(text="[ERROR: API failure]", source="rag")
    return Answer(text=content.strip(), source="rag")


# ---------------------------------------------------------------------------
# 3. Grading
# ---------------------------------------------------------------------------

def _score_from_content(content: Optional[str]) -> Score:
    """Parse a grader JSON blob into a Score, clamped to [0, 1]."""
    if content is None:
        return Score(0.0, 0.0, 0.0, 0.0, "[ERROR: Grading API failed]")

    data = _safe_json_loads(content)
    if data is None:
        logger.warning("Failed to parse grading JSON. Raw: %s", content[:500])
        return Score(0.0, 0.0, 0.0, 0.0,
                     f"[ERROR: JSON parse failure] Raw: {content[:300]}")
    try:
        accuracy = max(0.0, min(1.0, float(data.get("accuracy", 0.0))))
        completeness = max(0.0, min(1.0, float(data.get("completeness", 0.0))))
        tone_match = max(0.0, min(1.0, float(data.get("tone_match", 0.0))))
        overall = (accuracy + completeness + tone_match) / 3.0
        return Score(accuracy, completeness, tone_match, overall,
                     str(data.get("explanation", "")))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to extract grading fields: %s", exc)
        return Score(0.0, 0.0, 0.0, 0.0,
                     f"[ERROR: Field extraction failure] Raw: {content[:300]}")


BLIND_GRADE_PROMPT = """You are an expert evaluator. Using ONLY the source conversation as ground truth, grade the candidate ANSWER to the QUESTION. You are NOT told how the answer was produced — judge it on its own merits.

Grade three dimensions (0.0–1.0):
1. accuracy: Are the answer's claims factually correct according to the source conversation? Penalize anything the source does not support.
2. completeness: Does the answer cover the relevant details that the source actually contains for this question?
3. tone_match: Is the tone appropriately grounded? Penalize hallucinated confidence or invented specifics.

Return ONLY a JSON object with this exact shape:
{{
  "accuracy": float,
  "completeness": float,
  "tone_match": float,
  "explanation": "string explaining the scores, citing the source where relevant"
}}

No markdown, no extra text.

SOURCE CONVERSATION:
{conversation}

QUESTION: {question}

ANSWER:
{answer}

EVALUATION (JSON):"""


def grade_answer_against_source(
    answer: str,
    question: str,
    conversation: str,
    llm_client: Optional[LLMClient] = None,
    model: str = DEFAULT_GRADE_MODEL,
) -> Score:
    """Blindly grade a single answer against the source conversation.

    This removes the two biases in grade_answers(): the grader is not told which
    method produced the answer, and the raw conversation — not another LLM's
    answer — is the ground truth. Apply it identically to raw/CSL/RAG answers so
    they are scored on the same footing.
    """
    if llm_client is None:
        llm_client = LLMClient()
    prompt = BLIND_GRADE_PROMPT.format(
        conversation=conversation, question=question, answer=answer,
    )
    messages = [{"role": "user", "content": prompt}]
    content = llm_client.chat_completion(model=model, messages=messages, temperature=0.1, max_tokens=800)
    return _score_from_content(content)


def grade_answers(
    raw_answer: str,
    csl_answer: str,
    question: str,
    llm_client: Optional[LLMClient] = None,
    model: str = DEFAULT_GRADE_MODEL,
) -> Score:
    """Grade the CSL answer against the raw answer.

    DEPRECATED: label-aware and treats the raw LLM answer as ground truth.
    Retained for back-compat; prefer grade_answer_against_source().
    """
    if llm_client is None:
        llm_client = LLMClient()

    prompt = GRADING_PROMPT.format(
        question=question,
        raw_answer=raw_answer,
        csl_answer=csl_answer,
    )
    messages = [{"role": "user", "content": prompt}]
    content = llm_client.chat_completion(model=model, messages=messages, temperature=0.1, max_tokens=800)

    if content is None:
        return Score(
            accuracy=0.0,
            completeness=0.0,
            tone_match=0.0,
            overall=0.0,
            explanation="[ERROR: Grading API failed]",
        )

    data = _safe_json_loads(content)
    if data is None:
        logger.warning("Failed to parse grading JSON. Raw: %s", content[:500])
        return Score(
            accuracy=0.0,
            completeness=0.0,
            tone_match=0.0,
            overall=0.0,
            explanation=f"[ERROR: JSON parse failure] Raw: {content[:300]}",
        )

    try:
        accuracy = float(data.get("accuracy", 0.0))
        completeness = float(data.get("completeness", 0.0))
        tone_match = float(data.get("tone_match", 0.0))
        overall = (accuracy + completeness + tone_match) / 3.0
        explanation = str(data.get("explanation", ""))
        return Score(
            accuracy=max(0.0, min(1.0, accuracy)),
            completeness=max(0.0, min(1.0, completeness)),
            tone_match=max(0.0, min(1.0, tone_match)),
            overall=max(0.0, min(1.0, overall)),
            explanation=explanation,
        )
    except Exception as exc:
        logger.warning("Failed to extract grading fields: %s", exc)
        return Score(
            accuracy=0.0,
            completeness=0.0,
            tone_match=0.0,
            overall=0.0,
            explanation=f"[ERROR: Field extraction failure] Raw: {content[:300]}",
        )


# ---------------------------------------------------------------------------
# 3. Human-Readable Report
# ---------------------------------------------------------------------------

def generate_report(results: BenchmarkResult) -> str:
    """Generate a Markdown report from benchmark results."""
    lines: List[str] = []
    lines.append("# Context-as-Program Benchmark Report")
    lines.append("")
    lines.append(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    # --- Summary statistics ---
    lines.append("## Summary Statistics")
    lines.append("")

    agg_comp = results.aggregate_compression
    agg_qa = results.aggregate_qa

    lines.append("### Compression")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Mean compression ratio | {agg_comp.get('mean_ratio', 0):.2f}x |")
    lines.append(f"| Median compression ratio | {agg_comp.get('median_ratio', 0):.2f}x |")
    lines.append(f"| Min ratio | {agg_comp.get('min_ratio', 0):.2f}x |")
    lines.append(f"| Max ratio | {agg_comp.get('max_ratio', 0):.2f}x |")
    lines.append(f"| Total raw tokens | {agg_comp.get('total_raw_tokens', 0):,} |")
    lines.append(f"| Total CSL tokens | {agg_comp.get('total_csl_tokens', 0):,} |")
    lines.append("")

    # Primitive breakdown
    prim_totals = agg_comp.get("primitive_totals", {})
    if prim_totals:
        lines.append("#### Primitive Extraction Totals")
        lines.append("")
        lines.append("| Primitive | Count |")
        lines.append("|-----------|-------|")
        for prim, count in sorted(prim_totals.items(), key=lambda x: -x[1]):
            lines.append(f"| {prim} | {count} |")
        lines.append("")

    lines.append("### QA Accuracy")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Mean accuracy | {agg_qa.get('mean_accuracy', 0):.3f} |")
    lines.append(f"| Mean completeness | {agg_qa.get('mean_completeness', 0):.3f} |")
    lines.append(f"| Mean tone match | {agg_qa.get('mean_tone_match', 0):.3f} |")
    lines.append(f"| Mean overall | {agg_qa.get('mean_overall', 0):.3f} |")
    lines.append(f"| Questions evaluated | {agg_qa.get('total_questions', 0)} |")
    lines.append(f"| Successful gradings | {agg_qa.get('successful_gradings', 0)} |")
    lines.append("")

    # --- Method comparison at equal token budget (raw / CSL / RAG) ---
    by_method = aggregate_qa_by_method(results.conversations)
    if by_method:
        lines.append("### Method Comparison (equal token budget)")
        lines.append("")
        lines.append("RAG retrieves into the *same* token budget as the CSL program, so")
        lines.append("CSL vs RAG is the decisive comparison. Raw uses the full conversation")
        lines.append("(an upper bound, not an equal-cost baseline). All scored blind against")
        lines.append("the source conversation.")
        lines.append("")
        lines.append("| Method | Accuracy | Completeness | Tone | Overall | n |")
        lines.append("|--------|----------|--------------|------|---------|---|")
        for method in ("raw", "csl", "rag"):
            m = by_method.get(method, {})
            lines.append(
                f"| {method.upper()} | {m.get('mean_accuracy', 0):.3f} | "
                f"{m.get('mean_completeness', 0):.3f} | {m.get('mean_tone_match', 0):.3f} | "
                f"{m.get('mean_overall', 0):.3f} | {m.get('n', 0)} |"
            )
        csl_o = by_method.get("csl", {}).get("mean_overall", 0.0)
        rag_o = by_method.get("rag", {}).get("mean_overall", 0.0)
        if rag_o > 0:
            verdict = "CSL beats RAG" if csl_o > rag_o else "RAG beats CSL" if rag_o > csl_o else "CSL ties RAG"
            delta = csl_o - rag_o
            lines.append("")
            lines.append(f"**Verdict at equal budget: {verdict}** (CSL overall {csl_o:.3f} "
                         f"vs RAG overall {rag_o:.3f}, Δ={delta:+.3f}).")
        lines.append("")

    # --- Per-conversation breakdown ---
    lines.append("## Per-Conversation Breakdown")
    lines.append("")

    # Sort by overall QA score descending
    scored_convs = []
    for cr in results.conversations:
        if cr.qa_items:
            overall = sum(q.score.overall for q in cr.qa_items) / len(cr.qa_items)
        else:
            overall = 0.0
        scored_convs.append((overall, cr))

    scored_convs.sort(key=lambda x: x[0], reverse=True)

    for rank, (overall, cr) in enumerate(scored_convs, start=1):
        comp = cr.compression
        lines.append(f"### {rank}. `{cr.conv_id}` (domain: {cr.domain})")
        lines.append("")
        lines.append(f"- **Compression**: {comp.raw_tokens:,} raw → {comp.csl_tokens:,} CSL = **{comp.ratio:.2f}x**")
        prim_str = ", ".join(f"{k}={v}" for k, v in sorted(comp.primitives.items()))
        lines.append(f"- **Primitives**: {prim_str or 'none'}")
        if cr.qa_items:
            acc = sum(q.score.accuracy for q in cr.qa_items) / len(cr.qa_items)
            comp_score = sum(q.score.completeness for q in cr.qa_items) / len(cr.qa_items)
            tone = sum(q.score.tone_match for q in cr.qa_items) / len(cr.qa_items)
            lines.append(f"- **QA scores**: accuracy={acc:.3f}, completeness={comp_score:.3f}, tone={tone:.3f}, overall={overall:.3f}")
        else:
            lines.append("- **QA scores**: N/A (no questions evaluated)")
        if cr.errors:
            lines.append(f"- **Errors**: {len(cr.errors)}")
            for err in cr.errors[:3]:
                lines.append(f"  - {err}")
        lines.append("")

    # --- Best / Worst ---
    if len(scored_convs) >= 1:
        lines.append("## Best & Worst Performers")
        lines.append("")
        best = scored_convs[0][1]
        worst = scored_convs[-1][1]
        lines.append(f"- **Best**: `{best.conv_id}` — overall QA score {scored_convs[0][0]:.3f}")
        lines.append(f"- **Worst**: `{worst.conv_id}` — overall QA score {scored_convs[-1][0]:.3f}")
        lines.append("")

    # --- Failure pattern analysis ---
    lines.append("## Failure Pattern Analysis")
    lines.append("")

    # Collect low-score explanations
    low_score_explanations: List[str] = []
    for cr in results.conversations:
        for qa in cr.qa_items:
            if qa.score.overall < 0.5:
                low_score_explanations.append(qa.score.explanation.lower())

    if low_score_explanations:
        # Simple keyword-based pattern detection
        patterns = {
            "temporal": ["temporal", "time", "date", "when", "deadline", "timeline", "schedule"],
            "relational": ["relation", "relationship", "entity", "connection", "between"],
            "preference": ["preference", "prefer", "opinion", "like", "dislike", "want"],
            "unresolved": ["unresolved", "open question", "pending", "missing"],
            "fact": ["fact", "incorrect", "wrong", "detail", "specific"],
            "tone": ["tone", "style", "formal", "informal", "perspective"],
            "incomplete": ["incomplete", "missing detail", "omitted", "left out"],
        }
        pattern_counts: Dict[str, int] = {}
        for expl in low_score_explanations:
            for pattern_name, keywords in patterns.items():
                if any(kw in expl for kw in keywords):
                    pattern_counts[pattern_name] = pattern_counts.get(pattern_name, 0) + 1

        if pattern_counts:
            lines.append("Common failure themes in low-scoring answers:")
            lines.append("")
            for pattern, count in sorted(pattern_counts.items(), key=lambda x: -x[1]):
                lines.append(f"- **{pattern.capitalize()} issues**: {count} occurrence(s)")
            lines.append("")
        else:
            lines.append("Low-scoring answers were detected but no dominant patterns were identified automatically.")
            lines.append("")
    else:
        lines.append("No answers scored below 0.5 overall. Great job!")
        lines.append("")

    # --- Error summary ---
    total_errors = sum(len(cr.errors) for cr in results.conversations)
    if total_errors:
        lines.append("## Error Summary")
        lines.append("")
        lines.append(f"Total API or processing errors: {total_errors}")
        lines.append("")

    lines.append("---")
    lines.append("*End of report*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def aggregate_compression_results(conversations: List[ConversationResult]) -> Dict[str, Any]:
    ratios = [cr.compression.ratio for cr in conversations]
    total_raw = sum(cr.compression.raw_tokens for cr in conversations)
    total_csl = sum(cr.compression.csl_tokens for cr in conversations)
    primitive_totals: Dict[str, int] = {}
    for cr in conversations:
        for prim, count in cr.compression.primitives.items():
            primitive_totals[prim] = primitive_totals.get(prim, 0) + count

    return {
        "mean_ratio": sum(ratios) / len(ratios) if ratios else 0.0,
        "median_ratio": sorted(ratios)[len(ratios) // 2] if ratios else 0.0,
        "min_ratio": min(ratios) if ratios else 0.0,
        "max_ratio": max(ratios) if ratios else 0.0,
        "total_raw_tokens": total_raw,
        "total_csl_tokens": total_csl,
        "primitive_totals": primitive_totals,
    }


def aggregate_qa_results(conversations: List[ConversationResult]) -> Dict[str, Any]:
    all_scores: List[Score] = []
    for cr in conversations:
        for qa in cr.qa_items:
            all_scores.append(qa.score)

    if not all_scores:
        return {
            "mean_accuracy": 0.0,
            "mean_completeness": 0.0,
            "mean_tone_match": 0.0,
            "mean_overall": 0.0,
            "total_questions": 0,
            "successful_gradings": 0,
        }

    successful = [s for s in all_scores if not s.explanation.startswith("[ERROR:")]
    return {
        "mean_accuracy": sum(s.accuracy for s in successful) / len(successful) if successful else 0.0,
        "mean_completeness": sum(s.completeness for s in successful) / len(successful) if successful else 0.0,
        "mean_tone_match": sum(s.tone_match for s in successful) / len(successful) if successful else 0.0,
        "mean_overall": sum(s.overall for s in successful) / len(successful) if successful else 0.0,
        "total_questions": len(all_scores),
        "successful_gradings": len(successful),
    }


def _mean_method_scores(scores: List[Score]) -> Dict[str, Any]:
    successful = [s for s in scores if not s.explanation.startswith("[ERROR:")]
    if not successful:
        return {"mean_accuracy": 0.0, "mean_completeness": 0.0,
                "mean_tone_match": 0.0, "mean_overall": 0.0, "n": 0}
    k = len(successful)
    return {
        "mean_accuracy": sum(s.accuracy for s in successful) / k,
        "mean_completeness": sum(s.completeness for s in successful) / k,
        "mean_tone_match": sum(s.tone_match for s in successful) / k,
        "mean_overall": sum(s.overall for s in successful) / k,
        "n": k,
    }


def aggregate_qa_by_method(conversations: List[ConversationResult]) -> Dict[str, Dict[str, Any]]:
    """Aggregate blind scores separately for raw / CSL / RAG at equal budget.

    Reads the per-method scores (raw_score/csl_score/rag_score). Returns {} when
    no per-method scores are present (old two-way runs), so callers can branch.
    """
    buckets: Dict[str, List[Score]] = {"raw": [], "csl": [], "rag": []}
    for cr in conversations:
        for qa in cr.qa_items:
            if qa.raw_score is not None:
                buckets["raw"].append(qa.raw_score)
            if qa.csl_score is not None:
                buckets["csl"].append(qa.csl_score)
            if qa.rag_score is not None:
                buckets["rag"].append(qa.rag_score)
    if not any(buckets.values()):
        return {}
    return {method: _mean_method_scores(scores) for method, scores in buckets.items()}
