#!/usr/bin/env python3
"""
Context-as-Program: LLM-based CSL Extractor

Extracts structured CSL (Context Script Language) from raw conversation text
using an LLM via the LiteLLM proxy.

Usage:
    from benchmark.extractor import CSLExtractor
    extractor = CSLExtractor(model="gpt-5-via-cliproxy")
    program = extractor.extract(conversation_text)
    print(program.to_csl())
"""

import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("cap_benchmark.extractor")

# ---------------------------------------------------------------------------
# LiteLLM configuration
# ---------------------------------------------------------------------------

LITELLM_BASE_URL = os.environ.get(
    "LITELLM_BASE_URL", "http://litellm.litellm.svc.cluster.local:4000"
)
LITELLM_API_KEY = os.environ.get("LITELLM_VIRTUAL_KEY", "")
DEFAULT_MODEL = "gpt-5-via-cliproxy"
RATE_LIMIT_DELAY = 1.5
MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# CSL Data Model (mirrors prototype.py for standalone use)
# ---------------------------------------------------------------------------

@dataclass
class CSLStatement:
    stmt_type: str
    fields: Dict[str, Any]

    def to_csl(self) -> str:
        field_strs = []
        for k, v in self.fields.items():
            if isinstance(v, list):
                inner = ", ".join(
                    f'"{x}"' if isinstance(x, str) else str(x) for x in v
                )
                v_str = f"[{inner}]"
            elif isinstance(v, str):
                v_str = f'"{v}"'
            elif isinstance(v, bool):
                v_str = str(v).lower()
            else:
                v_str = str(v)
            field_strs.append(f"{k}={v_str}")
        return f"{self.stmt_type}({', '.join(field_strs)})"

    def token_estimate(self) -> int:
        return int(len(self.to_csl().split()) / 0.75)


class CSLProgram:
    def __init__(self):
        self.statements: List[CSLStatement] = []
        self._index: Dict[str, List[CSLStatement]] = {}

    def add(self, stmt: CSLStatement) -> None:
        self.statements.append(stmt)
        self._index.setdefault(stmt.stmt_type, []).append(stmt)

    def to_csl(self) -> str:
        lines = ["// === Context Script Language Program ===", ""]
        for stmt_type in [
            "PREFERENCE", "RELATION", "FACT", "EVENT", "INTENT",
            "UNRESOLVED", "SUMMARY", "RULE", "NOTE",
        ]:
            stmts = self._index.get(stmt_type, [])
            if stmts:
                lines.append(f"// === {stmt_type.upper()}S ===")
                for stmt in stmts:
                    lines.append(stmt.to_csl())
                lines.append("")
        return "\n".join(lines)

    def token_count(self) -> int:
        return sum(s.token_estimate() for s in self.statements)

    def __len__(self) -> int:
        return len(self.statements)


# ---------------------------------------------------------------------------
# HTTP client (uses stdlib urllib, no external dependencies)
# ---------------------------------------------------------------------------


def _chat_completion(model: str, messages: list, temperature: float = 0.2, max_tokens: int = 4096):
    """Call LiteLLM proxy chat completions endpoint."""
    import urllib.request
    req = urllib.request.Request(
        f"{LITELLM_BASE_URL}/v1/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode(),
        headers={
            "Authorization": f"Bearer {LITELLM_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------------------
# Extraction prompt with few-shot examples
# ---------------------------------------------------------------------------

DEFAULT_EXTRACTION_PROMPT = """You are an expert information extraction system. Your task is to read a conversation and extract the most important information into a structured format called Context Script Language (CSL).

CSL is a domain-specific language with these primitives:

- FACT(id, subject, predicate, object, confidence, timestamp, source) → ground truth assertions
- RELATION(entity1, type, entity2, strength, evidence) → connections between entities
- PREFERENCE(actor, domain, value, scope, volatility, evidence) → stable behavioral patterns
- EVENT(id, timestamp, type, participants, topics, outcome, sentiment) → discrete occurrences
- INTENT(actor, goal, motivation, urgency, blockers, progress) → goals and objectives
- UNRESOLVED(question, priority, owner, constraints, deadline) → open questions
- SUMMARY(scope, theme, key_points, confidence) → high-level patterns
- RULE(trigger, action, condition, priority, exceptions) → behavioral instructions
- NOTE(content, scope, author) → free-form observations

RULES:
1. Extract BOTH explicit facts AND implicit information (inferred preferences, relationships, intentions).
2. Use high confidence (0.9–1.0) for explicitly stated facts. Use lower confidence (0.5–0.8) for inferred information.
3. Each statement should encode what would take many sentences of text to express.
4. Be concise. One CSL statement = ~10–20 tokens. Raw conversation might be 10,000 tokens.
5. Include provenance: use evidence fields and source fields to track where information came from.
6. For preferences, note volatility: low = stable trait, medium = context-dependent, high = fleeting.
7. Capture unresolved questions with constraints and deadlines when mentioned.
8. If the user expresses skepticism, frustration, enthusiasm, or other strong sentiment toward something, capture it as a RELATION or PREFERENCE.

OUTPUT FORMAT:
Output ONLY valid CSL statements, one per line. No markdown, no explanations, no preamble. If you cannot extract any meaningful information, output a single NOTE statement.

--- FEW-SHOT EXAMPLES ---

Conversation:
User: Hey, I wanted to talk about Project Alpha. We're under a lot of pressure from the CFO to cut costs.
Assistant: I understand. What's the current budget situation?
User: We spend about $2.4M per year on infrastructure, and the CFO wants us to cut 30% within 6 months. It's basically impossible without major changes. I'm thinking we need to get rid of Kubernetes — it's overkill for our scale and the team is drowning in complexity.

CSL:
FACT(id="F1", subject="Project Alpha", predicate="budget_pressure", object="cut_30pc", confidence=0.95, timestamp="2024-03-15T14:00:00Z")
FACT(id="F2", subject="Current infrastructure", predicate="annual_cost", object="2.4M_USD", confidence=0.9)
RELATION(entity1="User", type="skeptical_of", entity2="Kubernetes", strength=0.85, evidence=["calls_it_overkill", "team_drowning_in_complexity"])
PREFERENCE(actor="User", domain="technical_approach", value="prefers_simplicity_over_completeness", scope="Project Alpha", volatility="medium")
INTENT(actor="User", goal="reduce_infra_cost_30pc", motivation="CFO_pressure", urgency="high", blockers=["major_changes_required"], progress=0.05)
UNRESOLVED(question="How to cut infrastructure costs by 30% without major disruption?", priority="high", owner="User", constraints=["6_month_timeline"])

--- END EXAMPLES ---

Now extract CSL from the following conversation:

{conversation}

CSL:"""


# ---------------------------------------------------------------------------
# CSL Parser
# ---------------------------------------------------------------------------

def parse_csl(text: str) -> CSLProgram:
    """Parse CSL text into a CSLProgram."""
    prog = CSLProgram()
    pattern = r"(\w+)\((.*?)\)"
    for match in re.finditer(pattern, text, re.DOTALL):
        stmt_type = match.group(1)
        fields_text = match.group(2)
        fields = _parse_fields(fields_text)
        if fields:
            prog.add(CSLStatement(stmt_type, fields))
    return prog


def _parse_fields(text: str) -> Dict[str, Any]:
    """Parse key=value pairs from CSL field text."""
    fields = {}
    field_pairs = _split_top_level(text)
    for pair in field_pairs:
        if "=" not in pair:
            continue
        key, val = pair.split("=", 1)
        key = key.strip()
        val = val.strip()

        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1]
            items = [x.strip().strip('"') for x in _split_list(inner)]
            fields[key] = items
        elif val.startswith('"') and val.endswith('"'):
            fields[key] = val[1:-1]
        elif val.lower() == "true":
            fields[key] = True
        elif val.lower() == "false":
            fields[key] = False
        else:
            try:
                if "." in val:
                    fields[key] = float(val)
                else:
                    fields[key] = int(val)
            except ValueError:
                fields[key] = val
    return fields


def _split_top_level(text: str) -> List[str]:
    """Split by comma at top level (not inside brackets)."""
    parts = []
    depth = 0
    current = []
    for char in text:
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def _split_list(text: str) -> List[str]:
    """Split list items by comma."""
    return [x.strip() for x in text.split(",") if x.strip()]


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

@dataclass
class ExtractionResult:
    program: CSLProgram
    raw_output: str
    model: str
    input_tokens: int
    output_tokens: int
    duration_ms: float
    retries: int
    error: Optional[str] = None

    @property
    def cost_usd(self) -> float:
        """Rough cost estimate."""
        # Approximate pricing for models through LiteLLM proxy
        rates = {
            "gpt-5-via-cliproxy": (0.0015, 0.006),  # per 1K tokens
            "kimi-k2.6": (0.001, 0.003),
            "claude-haiku-4-5-via-meridian": (0.00025, 0.00125),
        }
        in_rate, out_rate = rates.get(self.model, (0.001, 0.003))
        return (self.input_tokens / 1000 * in_rate) + (
            self.output_tokens / 1000 * out_rate
        )


class CSLExtractor:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        prompt_template: str = DEFAULT_EXTRACTION_PROMPT,
        rate_limit_delay: float = RATE_LIMIT_DELAY,
        max_retries: int = MAX_RETRIES,
    ):
        self.model = model
        self.prompt_template = prompt_template
        self.rate_limit_delay = rate_limit_delay
        self.max_retries = max_retries

    def extract(self, conversation: str) -> ExtractionResult:
        """Extract CSL from a single conversation."""
        prompt = self.prompt_template.format(conversation=conversation)
        start = time.time()
        retries = 0
        error = None
        raw_output = ""

        for attempt in range(self.max_retries + 1):
            try:
                if attempt > 0:
                    # Add error feedback for retry
                    prompt = self._build_retry_prompt(
                        conversation, raw_output, error or "unknown error"
                    )
                    retries += 1

                response = _chat_completion(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=8000 if "kimi" in self.model else 4096,
                )
                raw_output = response["choices"][0]["message"].get("content", "") or ""
                # Some reasoning models (o1, kimi-k2.6) output CSL in reasoning_content
                if not raw_output.strip():
                    raw_output = response["choices"][0]["message"].get("reasoning_content", "") or ""
                    if raw_output.strip():
                        logger.info("Using reasoning_content as output (%d chars)", len(raw_output))

                # Clean up output
                raw_output = self._clean_output(raw_output)

                # Parse CSL
                program = parse_csl(raw_output)

                # Validate: must have at least one statement
                if len(program) == 0:
                    raise ValueError("No CSL statements parsed from output")

                usage = response.get("usage", {})
                duration = (time.time() - start) * 1000

                return ExtractionResult(
                    program=program,
                    raw_output=raw_output,
                    model=self.model,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                    duration_ms=duration,
                    retries=retries,
                )

            except Exception as e:
                error = str(e)
                logger.warning(
                    f"Extraction attempt {attempt + 1} failed: {error}"
                )
                if attempt < self.max_retries:
                    time.sleep(self.rate_limit_delay * (attempt + 1))
                else:
                    break

        duration = (time.time() - start) * 1000
        return ExtractionResult(
            program=CSLProgram(),
            raw_output=raw_output,
            model=self.model,
            input_tokens=0,
            output_tokens=0,
            duration_ms=duration,
            retries=retries,
            error=error,
        )

    def extract_batch(
        self, conversations: List[Tuple[str, str]], delay: Optional[float] = None
    ) -> List[Tuple[str, ExtractionResult]]:
        """
        Extract CSL from multiple conversations.

        Args:
            conversations: List of (id, conversation_text) tuples
            delay: Delay between API calls (defaults to self.rate_limit_delay)

        Returns:
            List of (id, ExtractionResult) tuples
        """
        if delay is None:
            delay = self.rate_limit_delay

        results = []
        for idx, (conv_id, text) in enumerate(conversations):
            logger.info(
                f"[{idx + 1}/{len(conversations)}] Extracting {conv_id}..."
            )
            result = self.extract(text)
            results.append((conv_id, result))
            if idx < len(conversations) - 1:
                time.sleep(delay)
        return results

    def _clean_output(self, text: str) -> str:
        """Clean LLM output to extract just the CSL statements."""
        # Remove markdown code blocks
        text = re.sub(r"```\w*\n?", "", text)
        text = re.sub(r"```", "", text)
        # Remove preamble like "CSL:" or "Here is the extraction:"
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith(("csl:", "here is", "extraction:", "output:")):
                continue
            cleaned.append(line)
        return "\n".join(cleaned)

    def _build_retry_prompt(
        self, conversation: str, last_output: str, error: str
    ) -> str:
        """Build a retry prompt with error feedback."""
        return f"""You are an expert information extraction system. Your previous extraction had an error. Please fix it and output valid CSL.

Error: {error}

Previous (possibly malformed) output:
{last_output}

Original conversation:
{conversation}

Please output ONLY valid CSL statements, one per line. No markdown, no explanations.

CSL:"""


# ---------------------------------------------------------------------------
# CLI / standalone test
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="CSL Extractor")
    parser.add_argument("--input", "-i", required=True, help="Input text file")
    parser.add_argument("--output", "-o", default="-", help="Output file (- for stdout)")
    parser.add_argument("--model", "-m", default=DEFAULT_MODEL, help="Model to use")
    parser.add_argument("--delay", "-d", type=float, default=RATE_LIMIT_DELAY, help="Rate limit delay")
    args = parser.parse_args()

    with open(args.input) as f:
        text = f.read()

    extractor = CSLExtractor(model=args.model, rate_limit_delay=args.delay)
    result = extractor.extract(text)

    if result.error:
        print(f"ERROR: {result.error}", file=sys.stderr)
        sys.exit(1)

    output = f"""// Extraction result
// Model: {result.model}
// Input tokens: {result.input_tokens}
// Output tokens: {result.output_tokens}
// Duration: {result.duration_ms:.0f}ms
// Retries: {result.retries}
// Estimated cost: ${result.cost_usd:.4f}

{result.program.to_csl()}
"""
    if args.output == "-":
        print(output)
    else:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Wrote {len(result.program)} statements to {args.output}")
        print(f"Compression: {len(text.split()) / max(result.program.token_count(), 1):.1f}x (words)")


if __name__ == "__main__":
    main()
