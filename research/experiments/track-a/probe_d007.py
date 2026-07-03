# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
D-007 Serializer Probe — Track A Round 2
=========================================
Answers: is the Track A recall cliff a PROMPT problem (D-007 richer extraction)
or an ARCHITECTURE problem (chunked multi-pass extraction)?

Three arms, one session (default S2):
  Arm A — baseline prompt  (01-serialize.md), single-pass
  Arm B — D-007 prompt     (01b-serialize-d007.md), single-pass
  Arm C — D-007 prompt + chunked multi-pass (01c-merge-checkpoints.md merge)

Usage:
    # Real run (needs LiteLLM):
    export LITELLM_API_KEY=sk-...
    export LITELLM_MODEL=<name>
    uv run probe_d007.py --session S2

    # Idempotent re-run (skip arms whose score.json already exists):
    uv run probe_d007.py --session S2

    # Force re-run everything:
    uv run probe_d007.py --session S2 --force

    # Self-test (mock LLM, no network):
    uv run probe_d007.py --self-test

    # Re-run ONLY the judge passes on existing checkpoint+reconstruction (cheap):
    uv run probe_d007.py --session S2 --rejudge

Scoring (two-pass judge):
    Pass 1 (recall):    GT items vs reconstruction -> `recalled` flags (unchanged).
    Pass 2 (grounding): each reconstruction claim vs the ORIGINAL TRANSCRIPT
                        (chunked; grounded if ANY chunk supports it) -> `grounded`.
    FMR is measured against the transcript, NOT the GT item list — a rich
    reconstruction with true-but-not-in-GT detail must NOT count as confabulation.

Outputs:
    runs/<session>/probe-d007/armA/  checkpoint.json  reconstruction.md  score.json  meta.json
    runs/<session>/probe-d007/armB/  ...
    runs/<session>/probe-d007/armC/  ...  (also: chunk-*.json for partial checkpoints)
"""

import argparse
import json
import os
import re
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROMPTS_DIR = HERE / "prompts"
SESSIONS_DIR = HERE / "sessions"
RUNS_DIR = HERE / "runs"
SCORING_DIR = HERE / "scoring"

ARM_LABELS = ("armA", "armB", "armC")

# Score thresholds (VALIDATION.md Track A)
RR_PASS = 0.70
FMR_PASS = 0.10
MEANINGFUL_DELTA_PP = 0.05  # 5 pp

# ---------------------------------------------------------------------------
# Prompt text loading (read from .md files, extract the ```...``` blocks)
# ---------------------------------------------------------------------------

def _extract_code_block(md_text: str, nth: int = 0) -> str:
    """Extract the nth ```...``` block from markdown text (0-indexed)."""
    parts = md_text.split("```")
    # Parts at odd indices are inside code fences: [before, block, after, block, ...]
    # A ``` block can optionally start with a language tag (e.g. 'json\n...')
    blocks = []
    for i in range(1, len(parts), 2):
        raw = parts[i]
        # Strip leading language hint (e.g. "json\n")
        if "\n" in raw:
            first_line, rest = raw.split("\n", 1)
            if first_line.strip() and not first_line.strip()[0] in "{[( \t":
                raw = rest
        blocks.append(raw.strip())
    if nth >= len(blocks):
        raise ValueError(f"Expected at least {nth + 1} code block(s), found {len(blocks)}")
    return blocks[nth]


def load_prompt_sys(filename: str) -> str:
    """Load system prompt text from a prompts/*.md file (first code block)."""
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return _extract_code_block(path.read_text())


# ---------------------------------------------------------------------------
# Mock LLM for --self-test (no network required)
# ---------------------------------------------------------------------------

class MockLLM:
    """Deterministic stub — returns plausible JSON/text based on input keywords."""

    call_count: int = 0

    @staticmethod
    def chat(messages, **kwargs):
        MockLLM.call_count += 1
        user_content = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
        )

        # Merge pass: user_content contains PARTIAL CHECKPOINTS
        if "PARTIAL CHECKPOINTS" in user_content:
            try:
                idx = user_content.index("[")
                partials = json.loads(user_content[idx:])
            except (ValueError, json.JSONDecodeError):
                partials = []
            merged = _mock_merge(partials)
            return json.dumps(merged), {"total_tokens": 99}, "mock-model"

        # Recall-judge pass (GT vs reconstruction)
        if "GROUND TRUTH ITEMS" in user_content:
            return _mock_recall_judge(user_content), {"total_tokens": 50}, "mock-model"

        # Grounding-judge pass (claims vs transcript chunk)
        if "TRANSCRIPT CHUNK" in user_content:
            return _mock_grounding_judge(user_content), {"total_tokens": 40}, "mock-model"

        # Reconstruct pass
        if "CHECKPOINT:" in user_content:
            return (
                "PART 1 — RESUMED STATE\n"
                "- Working on: fixing provider regression bugs A and B\n"
                "- Open: verify rebuild produces clean plan\n"
                "- Decision: hold release-please PR #51\n\n"
                "PART 2 — DREAM SEQUENCE\n"
                "We were deep in provider debugging when the session ended. "
                "Two regressions surfaced — mirrored_ports null-vs-empty (Bug A) "
                "and stale tag IDs on override-off ports (Bug B). Fixes are in place "
                "but unverified. Your next step: rebuild the binary, run plan, paste it.",
                {"total_tokens": 120},
                "mock-model",
            )

        # Serialize pass (single-pass or chunk)
        session_id = "S_test"
        for line in user_content.splitlines():
            if line.startswith("session_id:"):
                session_id = line.split(":", 1)[1].strip()
                break

        checkpoint = {
            "session_id": session_id,
            "working_context": {
                "active_topic": {
                    "text": "Fixing provider regression bugs in terraform-provider-acme",
                    "trust": "inferred",
                    "quote": "",
                },
                "open_questions": [
                    {
                        "text": "Verify rebuild produces clean plan",
                        "trust": "verbatim",
                        "quote": "Rebuild the provider binary + plan — paste it and I'll confirm before you apply.",
                    },
                    {
                        "text": "Provider PR #62 still needs to be merged when CI is green",
                        "trust": "verbatim",
                        "quote": "Provider PR #62 — CI should be running; merge when green.",
                    },
                ],
                "recent_decisions": [
                    {
                        "text": "Hold release-please PR #51 (v1.0.0)",
                        "trust": "verbatim",
                        "quote": "Hold. We need to continue on higher-priority project work first",
                    },
                    {
                        "text": "[Fix] Bug A: mirrored_ports returned empty set instead of null for non-mirroring ports",
                        "trust": "verbatim",
                        "quote": "root cause: read-mapping returned empty set instead of null",
                    },
                ],
                "emotional_valence": {
                    "text": "Focused and determined, some frustration with provider churn",
                    "trust": "inferred",
                    "quote": "",
                },
            },
            "epistemic_snapshot": {
                "strong_beliefs": [
                    {
                        "text": "dhcp_enriched=0 is NOT a bug",
                        "trust": "verbatim",
                        "quote": "dhcp_enriched > 0 happened 94 times in 24h. Enrichment works.",
                    }
                ],
                "uncertainties": [
                    {
                        "text": "Whether Bug A / Bug B fixes work end-to-end is unverified",
                        "trust": "inferred",
                        "quote": "",
                    }
                ],
                "contradictions_flagged": [],
            },
            "worker_queue": [],
        }
        return json.dumps(checkpoint), {"total_tokens": 200}, "mock-model"

    @staticmethod
    def extract_json(text: str):
        return json.loads(text)


def _mock_merge(partials: list[dict]) -> dict:
    """Union partial checkpoints — dedup by text prefix, prefer verbatim."""
    if not partials:
        return {
            "session_id": "merged",
            "working_context": {
                "active_topic": {"text": "", "trust": "inferred", "quote": ""},
                "open_questions": [],
                "recent_decisions": [],
                "emotional_valence": {"text": "", "trust": "inferred", "quote": ""},
            },
            "epistemic_snapshot": {
                "strong_beliefs": [],
                "uncertainties": [],
                "contradictions_flagged": [],
            },
            "worker_queue": [],
        }

    def dedup_items(lists: list[list]) -> list:
        seen: dict[str, dict] = {}
        for lst in lists:
            for item in lst:
                key = item.get("text", "")[:60].lower()
                if key not in seen or item.get("trust") == "verbatim":
                    seen[key] = item
        return list(seen.values())

    last = partials[-1]
    merged: dict[str, Any] = {
        "session_id": last.get("session_id", "merged"),
        "working_context": {
            "active_topic": last.get("working_context", {}).get("active_topic", {"text": "", "trust": "inferred", "quote": ""}),
            "open_questions": dedup_items([p.get("working_context", {}).get("open_questions", []) for p in partials]),
            "recent_decisions": dedup_items([p.get("working_context", {}).get("recent_decisions", []) for p in partials]),
            "emotional_valence": last.get("working_context", {}).get("emotional_valence", {"text": "", "trust": "inferred", "quote": ""}),
        },
        "epistemic_snapshot": {
            "strong_beliefs": dedup_items([p.get("epistemic_snapshot", {}).get("strong_beliefs", []) for p in partials]),
            "uncertainties": dedup_items([p.get("epistemic_snapshot", {}).get("uncertainties", []) for p in partials]),
            "contradictions_flagged": dedup_items([p.get("epistemic_snapshot", {}).get("contradictions_flagged", []) for p in partials]),
        },
        "worker_queue": dedup_items([p.get("worker_queue", []) for p in partials]),
    }
    return merged


def _mock_recall_judge(user_content: str) -> str:
    """Mock recall judge: recalled flags + claim ENUMERATION (no grounding here)."""
    try:
        gt_start = user_content.index("GROUND TRUTH ITEMS (JSON):")
        gt_end = user_content.index("\n\nRECONSTRUCTION TEXT:")
        gt_json = user_content[gt_start + len("GROUND TRUTH ITEMS (JSON):"):gt_end].strip()
        gt_items = json.loads(gt_json)
    except (ValueError, json.JSONDecodeError):
        gt_items = []

    try:
        recon_start = user_content.index("RECONSTRUCTION TEXT:") + len("RECONSTRUCTION TEXT:")
        recon_text = user_content[recon_start:].strip()
    except ValueError:
        recon_text = ""

    # Simple heuristic: mark recalled=true if any word from item text appears in reconstruction
    scored_gt = []
    for item in gt_items:
        words = set(item.get("text", "").lower().split())
        hit = bool(words & set(recon_text.lower().split()))
        scored_gt.append({**item, "recalled": hit})

    # Enumerate claims from reconstruction bullets — grounding judged in pass 2
    claims = []
    for line in recon_text.splitlines():
        line = line.strip()
        if line.startswith("- "):
            claims.append({"id": f"r{len(claims) + 1}", "text": line[2:]})

    return json.dumps({"scored_gt": scored_gt, "reconstruction_claims": claims})


def _mock_grounding_judge(user_content: str) -> str:
    """Mock grounding judge: claim supported iff >=50% of its words (len>=4) occur in the chunk."""
    try:
        ci = user_content.index("CLAIMS (JSON):") + len("CLAIMS (JSON):")
        ti = user_content.index("TRANSCRIPT CHUNK")
        claims = json.loads(user_content[ci:ti].strip())
        chunk_text = user_content[user_content.index(":", ti) + 1:]
    except (ValueError, json.JSONDecodeError):
        claims, chunk_text = [], ""

    chunk_words = set(re.findall(r"[a-z0-9_#]+", chunk_text.lower()))
    out = []
    for c in claims:
        words = [w for w in re.findall(r"[a-z0-9_#]+", c.get("text", "").lower()) if len(w) >= 4]
        frac = (sum(1 for w in words if w in chunk_words) / len(words)) if words else 0.0
        out.append({"id": c.get("id"), "supported": frac >= 0.5})
    return json.dumps({"claims": out})


# ---------------------------------------------------------------------------
# Real LLM import (lazy — only when not in self-test mode)
# ---------------------------------------------------------------------------

_llm_module = None


def get_llm(mock: bool = False):
    global _llm_module
    if mock:
        return MockLLM
    if _llm_module is None:
        import llm as _llm
        _llm_module = _llm
    return _llm_module


# ---------------------------------------------------------------------------
# Chunking utilities
# ---------------------------------------------------------------------------

def chunk_transcript(text: str, chunk_lines: int, overlap_lines: int) -> list[str]:
    """Split transcript into overlapping line-based chunks."""
    lines = text.splitlines()
    chunks = []
    step = max(1, chunk_lines - overlap_lines)
    i = 0
    while i < len(lines):
        end = min(i + chunk_lines, len(lines))
        chunks.append("\n".join(lines[i:end]))
        if end >= len(lines):
            break
        i += step
    return chunks


# ---------------------------------------------------------------------------
# Scoring — import score_session from scoring/score.py via sys.path injection
# ---------------------------------------------------------------------------

_score_module = None


def get_score_session():
    global _score_module
    if _score_module is None:
        sys.path.insert(0, str(SCORING_DIR))
        import score as _score  # noqa: E402
        _score_module = _score
    return _score_module.score_session


# ---------------------------------------------------------------------------
# Auto-scoring via LLM judge (populates recalled/grounded flags)
# ---------------------------------------------------------------------------

RECALL_JUDGE_SYS = """You are a precision recall judge for a cognitive-state reconstruction experiment.

Given:
1. A list of GROUND TRUTH ITEMS (each has an id, type, and text).
2. A RECONSTRUCTION TEXT (plain prose + bullets).

TASK 1 — RECALL. For each ground truth item, decide:
  recalled: true  — the reconstruction text conveys this fact, even if paraphrased
  recalled: false — the reconstruction text omits or contradicts this fact

TASK 2 — CLAIM ENUMERATION. Break the reconstruction text into atomic factual claims
(one checkable assertion each). Do NOT judge their truth or grounding here — just enumerate.

Output ONLY a JSON object with this shape (no prose):
{
  "scored_gt": [
    {"id": "<gt_id>", "type": "<type>", "trust": "<trust>", "recalled": true|false},
    ...
  ],
  "reconstruction_claims": [
    {"id": "r1", "text": "<atomic claim>"},
    ...
  ]
}"""

GROUNDING_JUDGE_SYS = """You are a grounding judge measuring confabulation (false memories).

Given:
1. CLAIMS (JSON list of {id, text}) extracted from a session reconstruction.
2. A TRANSCRIPT CHUNK — a contiguous slice of the ORIGINAL raw session transcript
   (possibly partial; other chunks exist and will be judged separately).

For each claim, decide:
  supported: true  — THIS chunk contains evidence for the claim (verbatim or clear paraphrase)
  supported: false — THIS chunk contains no such evidence. The claim may still be supported
                     by another chunk; that is fine — judge ONLY against this chunk.

Be strict: supported only if the chunk actually contains the fact.
Do NOT mark supported because a claim merely sounds plausible.

Output ONLY a JSON object (no prose):
{"claims": [{"id": "r1", "supported": true|false}, ...]}"""


def llm_recall_judge(
    gt_items: list[dict],
    reconstruction_text: str,
    llm_mod,
    verbose: bool = False,
) -> dict:
    """Pass 1: GT items vs reconstruction -> recalled flags + claim enumeration."""
    user_content = (
        f"GROUND TRUTH ITEMS (JSON):\n{json.dumps(gt_items, indent=2)}\n\n"
        f"RECONSTRUCTION TEXT:\n{reconstruction_text}"
    )
    content, usage, model = llm_mod.chat(
        [
            {"role": "system", "content": RECALL_JUDGE_SYS},
            {"role": "user", "content": user_content},
        ]
    )
    if verbose:
        print(f"    [recall-judge] tokens={usage.get('total_tokens', '?')} model={model}")
    raw = llm_mod.extract_json(content)
    if "scored_gt" in raw:
        return raw
    return {"scored_gt": gt_items, "reconstruction_claims": []}


def llm_grounding_judge(
    claims: list[dict],
    transcript: str,
    llm_mod,
    judge_chunk_lines: int = 1200,
    judge_overlap_lines: int = 100,
    verbose: bool = False,
) -> tuple[list[dict], int]:
    """Pass 2: each claim vs the ORIGINAL TRANSCRIPT (chunked).

    A claim is grounded if ANY chunk supports it; ungrounded only if NO chunk does.
    Returns ([{id, text, grounded}], n_chunks). Already-supported claims are not
    re-sent to later chunks (token saver)."""
    claims = [{"id": c.get("id") or f"r{i + 1}", "text": c.get("text", "")} for i, c in enumerate(claims)]
    if not claims:
        return [], 0

    chunks = chunk_transcript(transcript, judge_chunk_lines, judge_overlap_lines)
    supported: set[str] = set()
    for i, chunk in enumerate(chunks):
        pending = [c for c in claims if c["id"] not in supported]
        if not pending:
            break
        user_content = (
            f"CLAIMS (JSON):\n{json.dumps(pending, indent=2)}\n\n"
            f"TRANSCRIPT CHUNK ({i + 1} of {len(chunks)}):\n{chunk}"
        )
        content, usage, model = llm_mod.chat([
            {"role": "system", "content": GROUNDING_JUDGE_SYS},
            {"role": "user", "content": user_content},
        ])
        raw = llm_mod.extract_json(content)
        items = raw.get("claims", raw) if isinstance(raw, dict) else raw
        n_hit = 0
        for it in items or []:
            if it.get("supported"):
                supported.add(it.get("id"))
                n_hit += 1
        if verbose:
            print(f"    [ground-judge {i + 1}/{len(chunks)}] {n_hit}/{len(pending)} newly supported "
                  f"tokens={usage.get('total_tokens', '?')}")

    graded = [{"id": c["id"], "text": c["text"], "grounded": c["id"] in supported} for c in claims]
    return graded, len(chunks)


# ---------------------------------------------------------------------------
# Core arm execution
# ---------------------------------------------------------------------------

def _run_serialize_single(
    sid: str,
    transcript: str,
    sys_prompt: str,
    llm_mod,
    verbose: bool = True,
) -> tuple[dict, dict]:
    """Single-pass serialization. Returns (checkpoint_dict, usage_info)."""
    content, usage, model = llm_mod.chat([
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"session_id: {sid}\n\nTRANSCRIPT:\n{transcript}"},
    ])
    checkpoint = llm_mod.extract_json(content)
    if verbose:
        print(f"    [serialize] model={model} tokens={usage.get('total_tokens', '?')}")
    return checkpoint, {"model": model, "tokens": usage.get("total_tokens", "?")}


def _run_serialize_chunked(
    sid: str,
    transcript: str,
    serialize_sys: str,
    merge_sys: str,
    llm_mod,
    chunk_lines: int,
    overlap_lines: int,
    arm_dir: Path,
    verbose: bool = True,
) -> tuple[dict, dict]:
    """Chunked multi-pass serialization. Returns (final_checkpoint, usage_info)."""
    chunks = chunk_transcript(transcript, chunk_lines, overlap_lines)
    total_tokens = 0
    model_used = "?"

    partial_checkpoints = []
    for i, chunk_text in enumerate(chunks):
        chunk_sid = f"{sid}_chunk{i + 1}of{len(chunks)}"
        content, usage, model = llm_mod.chat([
            {"role": "system", "content": serialize_sys},
            {"role": "user", "content": f"session_id: {chunk_sid}\n\nTRANSCRIPT (chunk {i + 1} of {len(chunks)}):\n{chunk_text}"},
        ])
        partial = llm_mod.extract_json(content)
        partial_checkpoints.append(partial)
        total_tokens += usage.get("total_tokens", 0) if isinstance(usage.get("total_tokens"), int) else 0
        model_used = model
        chunk_path = arm_dir / f"chunk-{i + 1:02d}.json"
        chunk_path.write_text(json.dumps(partial, indent=2))
        if verbose:
            print(f"    [chunk {i + 1}/{len(chunks)}] lines={len(chunk_text.splitlines())} tokens={usage.get('total_tokens', '?')}")

    # Merge pass
    merge_user = (
        f"session_id: {sid}\n\n"
        f"PARTIAL CHECKPOINTS (JSON array, one per chunk, in chronological order):\n"
        f"{json.dumps(partial_checkpoints, indent=2)}"
    )
    merge_content, merge_usage, merge_model = llm_mod.chat([
        {"role": "system", "content": merge_sys},
        {"role": "user", "content": merge_user},
    ])
    final_checkpoint = llm_mod.extract_json(merge_content)
    merge_tokens = merge_usage.get("total_tokens", 0) if isinstance(merge_usage.get("total_tokens"), int) else 0
    total_tokens += merge_tokens
    if verbose:
        print(f"    [merge] chunks={len(chunks)} merge_tokens={merge_usage.get('total_tokens', '?')} total_tokens~={total_tokens}")

    usage_info = {
        "model": model_used,
        "merge_model": merge_model,
        "chunk_count": len(chunks),
        "chunk_lines": chunk_lines,
        "overlap_lines": overlap_lines,
        "total_tokens_approx": total_tokens,
    }
    return final_checkpoint, usage_info


def _run_reconstruct(checkpoint: dict, llm_mod, verbose: bool = True) -> tuple[str, dict]:
    """Run the reconstruct step. Returns (reconstruction_text, usage_info)."""
    reconstruct_sys = load_prompt_sys("02-reconstruct.md")
    content, usage, model = llm_mod.chat([
        {"role": "system", "content": reconstruct_sys},
        {"role": "user", "content": f"CHECKPOINT:\n{json.dumps(checkpoint, indent=2)}"},
    ])
    if verbose:
        print(f"    [reconstruct] model={model} tokens={usage.get('total_tokens', '?')}")
    return content, {"model": model, "tokens": usage.get("total_tokens", "?")}


def _build_score_doc(
    sid: str,
    gt_items: list[dict],
    reconstruction_text: str,
    transcript: str,
    llm_mod,
    judge_chunk_lines: int = 1200,
    judge_overlap_lines: int = 100,
    verbose: bool = True,
) -> tuple[dict, dict]:
    """Two-pass scoring. Returns (score_doc, judge_meta).

    Pass 1 (recall):    GT items vs reconstruction -> recalled flags + claim enumeration.
    Pass 2 (grounding): each claim vs the TRANSCRIPT (chunked, ANY-chunk union) -> grounded.
    FMR is measured against the transcript, NOT the GT list (Track A definition)."""
    judged = llm_recall_judge(gt_items, reconstruction_text, llm_mod, verbose=verbose)

    # Merge trust/type back into scored_gt items (judge may have trimmed fields)
    gt_by_id = {item["id"]: item for item in gt_items}
    scored_gt = []
    for judged_item in judged.get("scored_gt", []):
        original = gt_by_id.get(judged_item.get("id", ""), {})
        scored_gt.append({
            "id": judged_item.get("id", original.get("id", "?")),
            "type": judged_item.get("type", original.get("type", "?")),
            "trust": judged_item.get("trust", original.get("trust", "?")),
            "recalled": judged_item.get("recalled", False),
        })

    # Ensure all GT items are represented (judge may have dropped some)
    judged_ids = {item["id"] for item in scored_gt}
    for item in gt_items:
        if item["id"] not in judged_ids:
            scored_gt.append({
                "id": item["id"],
                "type": item.get("type", "?"),
                "trust": item.get("trust", "?"),
                "recalled": False,  # conservative: assume missed if judge dropped it
            })

    # Pass 2 — grounding against the TRANSCRIPT (not GT): chunked, ANY-chunk union
    claims = judged.get("reconstruction_claims", [])
    graded_claims, n_judge_chunks = llm_grounding_judge(
        claims, transcript, llm_mod,
        judge_chunk_lines=judge_chunk_lines,
        judge_overlap_lines=judge_overlap_lines,
        verbose=verbose,
    )

    score_doc = {
        "session_id": sid,
        "ground_truth_items": scored_gt,
        "reconstruction_claims": graded_claims,
    }
    judge_meta = {
        "judge_passes": ["recall:GT-vs-reconstruction", "grounding:claims-vs-transcript"],
        "grounding_reference": "transcript",
        "grounding_rule": "grounded if ANY transcript chunk supports the claim; ungrounded only if NO chunk does",
        "judge_chunk_lines": judge_chunk_lines,
        "judge_overlap_lines": judge_overlap_lines,
        "n_judge_chunks": n_judge_chunks,
    }
    return score_doc, judge_meta


# ---------------------------------------------------------------------------
# Arm runner
# ---------------------------------------------------------------------------

@dataclass
class ArmResult:
    arm: str
    status: str  # "done" | "skipped" | "failed:<reason>"
    rr: float | None = None
    fmr: float | None = None
    omr: float | None = None
    error: str = ""


def run_arm(
    arm_name: str,
    sid: str,
    transcript: str,
    gt_items: list[dict],
    arm_dir: Path,
    serialize_prompt_file: str,
    llm_mod,
    chunked: bool = False,
    merge_prompt_file: str | None = None,
    chunk_lines: int = 800,
    overlap_lines: int = 100,
    force: bool = False,
    rejudge: bool = False,
    judge_chunk_lines: int = 1200,
    judge_overlap_lines: int = 100,
    transcript_ref: str = "",
    verbose: bool = True,
) -> ArmResult:
    """Run a single probe arm end-to-end. Returns an ArmResult.

    rejudge=True: skip serialize/reconstruct entirely — reuse existing
    checkpoint.json + reconstruction.md and re-run ONLY the two judge passes."""
    arm_dir.mkdir(parents=True, exist_ok=True)
    score_path = arm_dir / "score.json"
    checkpoint_path = arm_dir / "checkpoint.json"
    recon_path = arm_dir / "reconstruction.md"

    if score_path.exists() and not force and not rejudge:
        if verbose:
            print(f"  [{arm_name}] already scored — skipping (--force to redo, --rejudge to re-judge only)")
        # Load existing score to report metrics
        try:
            doc = json.loads(score_path.read_text())
            get_ss = get_score_session()
            ss = get_ss(doc)
            return ArmResult(arm=arm_name, status="skipped", rr=ss.rr, fmr=ss.fmr, omr=ss.omr)
        except Exception as e:
            return ArmResult(arm=arm_name, status="skipped", error=str(e))

    if rejudge:
        # --- rejudge: reuse existing artifacts, only re-run the judge passes ---
        if not checkpoint_path.exists() or not recon_path.exists():
            msg = f"missing checkpoint.json/reconstruction.md in {arm_dir}"
            return ArmResult(arm=arm_name, status=f"failed:rejudge:{msg}", error=msg)
        if verbose:
            print(f"  [{arm_name}] rejudge — reusing existing checkpoint.json + reconstruction.md")
        checkpoint = json.loads(checkpoint_path.read_text())
        recon_text = recon_path.read_text()
        old_meta: dict = {}
        if (arm_dir / "meta.json").exists():
            try:
                old_meta = json.loads((arm_dir / "meta.json").read_text())
            except Exception:
                old_meta = {}
        serialize_meta = old_meta.get("serialize_meta", {"note": "rejudge: original serialize meta unavailable"})
        reconstruct_meta = old_meta.get("reconstruct_meta", {"note": "rejudge: original reconstruct meta unavailable"})
        return _judge_and_finalize(
            arm_name=arm_name, sid=sid, gt_items=gt_items, recon_text=recon_text,
            transcript=transcript, llm_mod=llm_mod, arm_dir=arm_dir,
            serialize_prompt_file=serialize_prompt_file, merge_prompt_file=merge_prompt_file,
            chunked=chunked, chunk_lines=chunk_lines, overlap_lines=overlap_lines,
            serialize_meta=serialize_meta, reconstruct_meta=reconstruct_meta,
            judge_chunk_lines=judge_chunk_lines, judge_overlap_lines=judge_overlap_lines,
            transcript_ref=transcript_ref, rejudged=True, verbose=verbose,
        )

    # --- serialize ---
    try:
        if chunked:
            if merge_prompt_file is None:
                raise ValueError("merge_prompt_file required for chunked arm")
            # Arm A baseline reuse: only reuse from canonical runs/<sid> dir, not probe dir
            serialize_sys = load_prompt_sys(serialize_prompt_file)
            merge_sys = load_prompt_sys(merge_prompt_file)
            checkpoint, serialize_meta = _run_serialize_chunked(
                sid=sid,
                transcript=transcript,
                serialize_sys=serialize_sys,
                merge_sys=merge_sys,
                llm_mod=llm_mod,
                chunk_lines=chunk_lines,
                overlap_lines=overlap_lines,
                arm_dir=arm_dir,
                verbose=verbose,
            )
        else:
            # Arm A: reuse existing checkpoint if it was already produced by the canonical runner
            canonical_checkpoint = RUNS_DIR / sid / "checkpoint.json"
            if arm_name == "armA" and canonical_checkpoint.exists() and not force:
                if verbose:
                    print(f"  [armA] reusing canonical checkpoint from runs/{sid}/checkpoint.json")
                checkpoint = json.loads(canonical_checkpoint.read_text())
                serialize_meta = {"model": "reused-from-canonical", "tokens": 0, "note": "reused existing checkpoint"}
            else:
                serialize_sys = load_prompt_sys(serialize_prompt_file)
                checkpoint, serialize_meta = _run_serialize_single(
                    sid=sid,
                    transcript=transcript,
                    sys_prompt=serialize_sys,
                    llm_mod=llm_mod,
                    verbose=verbose,
                )
        checkpoint_path.write_text(json.dumps(checkpoint, indent=2))
    except Exception as e:
        return ArmResult(arm=arm_name, status=f"failed:serialize:{e}", error=str(e))

    # --- reconstruct ---
    try:
        recon_text, reconstruct_meta = _run_reconstruct(checkpoint, llm_mod, verbose=verbose)
        recon_path.write_text(recon_text)
    except Exception as e:
        return ArmResult(arm=arm_name, status=f"failed:reconstruct:{e}", error=str(e))

    return _judge_and_finalize(
        arm_name=arm_name, sid=sid, gt_items=gt_items, recon_text=recon_text,
        transcript=transcript, llm_mod=llm_mod, arm_dir=arm_dir,
        serialize_prompt_file=serialize_prompt_file, merge_prompt_file=merge_prompt_file,
        chunked=chunked, chunk_lines=chunk_lines, overlap_lines=overlap_lines,
        serialize_meta=serialize_meta, reconstruct_meta=reconstruct_meta,
        judge_chunk_lines=judge_chunk_lines, judge_overlap_lines=judge_overlap_lines,
        transcript_ref=transcript_ref, rejudged=False, verbose=verbose,
    )


def _judge_and_finalize(
    arm_name: str,
    sid: str,
    gt_items: list[dict],
    recon_text: str,
    transcript: str,
    llm_mod,
    arm_dir: Path,
    serialize_prompt_file: str,
    merge_prompt_file: str | None,
    chunked: bool,
    chunk_lines: int,
    overlap_lines: int,
    serialize_meta: dict,
    reconstruct_meta: dict,
    judge_chunk_lines: int,
    judge_overlap_lines: int,
    transcript_ref: str,
    rejudged: bool,
    verbose: bool,
) -> ArmResult:
    """Two-pass judge + metrics + score.json/meta.json writing (shared by run and rejudge)."""
    # --- two-pass LLM judge (recall vs GT; grounding vs TRANSCRIPT) ---
    try:
        score_doc, judge_meta = _build_score_doc(
            sid=sid,
            gt_items=gt_items,
            reconstruction_text=recon_text,
            transcript=transcript,
            llm_mod=llm_mod,
            judge_chunk_lines=judge_chunk_lines,
            judge_overlap_lines=judge_overlap_lines,
            verbose=verbose,
        )
    except Exception as e:
        return ArmResult(arm=arm_name, status=f"failed:judge:{e}", error=str(e))

    # --- compute metrics ---
    try:
        get_ss = get_score_session()
        ss = get_ss(score_doc)
    except Exception as e:
        return ArmResult(arm=arm_name, status=f"failed:score:{e}", error=str(e))

    # --- write outputs ---
    # score.json: score document (compatible with score.py's expected session-*.score.json shape)
    # Provenance lives in the sidecar meta.json — score doc structure stays score.py-compatible
    (arm_dir / "score.json").write_text(json.dumps(score_doc, indent=2))

    meta = {
        "arm": arm_name,
        "session_id": sid,
        "timestamp": _iso_now(),
        "serialize_prompt": serialize_prompt_file,
        "merge_prompt": merge_prompt_file,
        "chunked": chunked,
        "chunk_lines": chunk_lines if chunked else None,
        "overlap_lines": overlap_lines if chunked else None,
        "model_env": os.environ.get("LITELLM_MODEL", "mock"),
        "base_url_env": os.environ.get("LITELLM_BASE_URL", "http://localhost:4000"),
        "serialize_meta": serialize_meta,
        "reconstruct_meta": reconstruct_meta,
        "judge": {**judge_meta, "transcript_ref": transcript_ref},
        "rejudged": rejudged,
        "rr": ss.rr,
        "fmr": ss.fmr,
        "omr": ss.omr,
        "n_gt": ss.n_gt,
        "n_claims": ss.n_claims,
        "n_false": ss.n_false,
    }
    (arm_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    status = "rejudged" if rejudged else "done"
    if verbose:
        print(f"  [{arm_name}] {status} — RR={ss.rr * 100:.1f}% FMR={ss.fmr * 100:.1f}% OR={ss.omr * 100:.1f}%")

    return ArmResult(arm=arm_name, status=status, rr=ss.rr, fmr=ss.fmr, omr=ss.omr)


# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------

def compute_verdict(results: dict[str, ArmResult]) -> str:
    """D-007 verdict: which arm answers the prompt-vs-architecture question.

    An arm 'clears the bar' only if RR >= 0.70 AND FMR <= 0.10 (Track A pass bars)."""
    b = results.get("armB")
    c = results.get("armC")

    def ok(arm: ArmResult | None) -> bool:
        return arm is not None and arm.rr is not None and not arm.status.startswith("failed")

    def clears(arm: ArmResult | None) -> bool:
        return ok(arm) and arm.rr >= RR_PASS and arm.fmr is not None and arm.fmr <= FMR_PASS

    # Note arms whose RR clears but FMR doesn't — that nuance matters for the report
    notes = []
    for arm in (b, c):
        if ok(arm) and arm.rr >= RR_PASS and not clears(arm):
            fmr_s = "n/a" if arm.fmr is None else f"{arm.fmr * 100:.1f}%"
            notes.append(f"{arm.arm} meets RR bar but fails FMR bar ({fmr_s} > 10%)")
    suffix = ("  [" + "; ".join(notes) + "]") if notes else ""

    if not ok(b) and not ok(c):
        return "INCONCLUSIVE — both B and C failed to produce scores"
    if clears(b):
        return "PROMPT FIX SUFFICIENT — arm B clears RR>=70% AND FMR<=10%; D-007 prompt change is enough" + suffix
    if clears(c):
        return "CHUNKING REQUIRED (ARCHITECTURAL) — arm B did not clear the bars but arm C did" + suffix
    if ok(b) and ok(c):
        delta = c.rr - b.rr
        if delta > MEANINGFUL_DELTA_PP:
            return (f"CHUNKING HELPS, NEITHER CLEARS BAR — C > B by {delta * 100:.1f} pp "
                    f"(architectural change needed but more work required)") + suffix
        return (f"NEITHER — arms B and C near-equal (delta {delta * 100:.1f} pp); "
                f"deeper problem (may be length/attention fundamental)") + suffix
    if ok(b):
        return f"C FAILED — arm B only: RR {b.rr * 100:.1f}% FMR {_pct(b.fmr)}{' (clears bars)' if clears(b) else ' (does not clear bars)'}"
    if ok(c):
        return f"B FAILED — arm C only: RR {c.rr * 100:.1f}% FMR {_pct(c.fmr)}{' (clears bars)' if clears(c) else ' (does not clear bars)'}"
    return "INCONCLUSIVE"


# ---------------------------------------------------------------------------
# Comparison table
# ---------------------------------------------------------------------------

def print_comparison_table(results: dict[str, ArmResult], verdict: str) -> None:
    arm_cfg = {
        "armA": "baseline (01-serialize)",
        "armB": "D-007 prompt (01b-serialize-d007)",
        "armC": "D-007 + chunked (01c-merge)",
    }

    header = f"{'Arm':<8}{'Config':<42}{'RR':>7}{'FMR':>7}{'OR':>7}  {'Status'}"
    sep = "-" * len(header)
    print(f"\n{'=' * len(header)}")
    print("D-007 Serializer Probe — Comparison Table")
    print(sep)
    print(header)
    print(sep)
    for arm in ARM_LABELS:
        r = results.get(arm)
        cfg = arm_cfg.get(arm, "?")
        if r is None:
            print(f"{arm:<8}{cfg:<42}{'n/a':>7}{'n/a':>7}{'n/a':>7}  not run")
        elif r.rr is None:
            status = r.status if r.status != "skipped" else "skipped (no metrics)"
            print(f"{arm:<8}{cfg:<42}{'n/a':>7}{'n/a':>7}{'n/a':>7}  {status}")
        else:
            rr_s = f"{r.rr * 100:.1f}%"
            fmr_s = f"{r.fmr * 100:.1f}%"
            or_s = f"{r.omr * 100:.1f}%"
            print(f"{arm:<8}{cfg:<42}{rr_s:>7}{fmr_s:>7}{or_s:>7}  {r.status}")
    print(sep)
    print(f"\nVERDICT: {verdict}")
    print(f"{'=' * len(header)}\n")


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

SYNTHETIC_TRANSCRIPT = """\
User: Let's fix the authentication bug in the login flow.
Assistant: I found the root cause: the JWT expiry check was using server time instead of UTC. Fix: normalize to UTC in validate_token().
User: Good. Should we add a unit test?
Assistant: Yes — I'll add test_validate_token_utc_normalization(). Decision noted: use freezegun for time mocking in auth tests.
User: What's left?
Assistant: Open: we need to verify the fix works with the load balancer's clock skew. Not tested yet. Also: PR #44 is open, merge when CI is green.
User: I'll check tomorrow.
"""

SYNTHETIC_GT = [
    {"id": "gt1", "type": "decision", "trust": "verbatim", "text": "Fix JWT expiry check to normalize to UTC in validate_token()"},
    {"id": "gt2", "type": "decision", "trust": "verbatim", "text": "Use freezegun for time mocking in auth tests"},
    {"id": "gt3", "type": "open_question", "trust": "verbatim", "text": "Verify fix works with load balancer clock skew"},
    {"id": "gt4", "type": "open_question", "trust": "verbatim", "text": "PR #44 open, merge when CI is green"},
]


def run_self_test(chunk_lines: int = 4, overlap_lines: int = 1) -> int:
    """Self-test: exercises all plumbing with a mock LLM and synthetic data. Returns exit code."""
    print("=== D-007 self-test (mock LLM, no network) ===\n")
    mock = MockLLM

    # --- Test 1: chunking ---
    print("[1] Chunking utility...")
    chunks = chunk_transcript(SYNTHETIC_TRANSCRIPT, chunk_lines=4, overlap_lines=1)
    assert len(chunks) >= 2, f"Expected >=2 chunks, got {len(chunks)}"
    # Check overlap: last line of chunk N should be first line(s) of chunk N+1 (within overlap window)
    for i in range(len(chunks) - 1):
        lines_a = chunks[i].splitlines()
        lines_b = chunks[i + 1].splitlines()
        overlap_found = any(l in lines_b[:overlap_lines + 2] for l in lines_a[-(overlap_lines + 2):] if l.strip())
        assert overlap_found, f"No overlap detected between chunk {i} and {i + 1}"
    print(f"    chunks={len(chunks)}, overlap verified OK")

    # --- Test 2: mock serialize single-pass ---
    print("[2] Single-pass serialize (mock)...")
    sys_prompt = load_prompt_sys("01-serialize.md")
    cp, _ = _run_serialize_single("self_test", SYNTHETIC_TRANSCRIPT, sys_prompt, mock, verbose=False)
    assert "session_id" in cp, "checkpoint missing session_id"
    assert "working_context" in cp, "checkpoint missing working_context"
    print(f"    OK — checkpoint has {len(cp['working_context'].get('recent_decisions', []))} decisions, "
          f"{len(cp['working_context'].get('open_questions', []))} open_questions")

    # --- Test 3: mock serialize chunked + merge ---
    print("[3] Chunked serialize + merge (mock)...")
    # Write outputs to a temp self-test arm dir
    self_test_dir = RUNS_DIR / "_self_test_d007" / "armC"
    self_test_dir.mkdir(parents=True, exist_ok=True)
    serialize_sys = load_prompt_sys("01b-serialize-d007.md")
    merge_sys = load_prompt_sys("01c-merge-checkpoints.md")
    merged_cp, usage = _run_serialize_chunked(
        sid="self_test",
        transcript=SYNTHETIC_TRANSCRIPT,
        serialize_sys=serialize_sys,
        merge_sys=merge_sys,
        llm_mod=mock,
        chunk_lines=4,
        overlap_lines=1,
        arm_dir=self_test_dir,
        verbose=True,
    )
    assert "session_id" in merged_cp, "merged checkpoint missing session_id"
    chunk_files = sorted(self_test_dir.glob("chunk-*.json"))
    assert len(chunk_files) >= 2, f"Expected >=2 chunk files, found {len(chunk_files)}"
    print(f"    OK — merged checkpoint, {len(chunk_files)} chunk files written")

    # --- Test 4: reconstruct ---
    print("[4] Reconstruct (mock)...")
    recon_text, _ = _run_reconstruct(cp, mock, verbose=False)
    assert "PART 1" in recon_text and "PART 2" in recon_text, "reconstruction missing PART 1/2 structure"
    print(f"    OK — reconstruction text {len(recon_text)} chars")

    # --- Test 5: two-pass judge (recall + grounding) ---
    print("[5] Two-pass judge — recall vs GT, grounding vs TRANSCRIPT (mock)...")
    score_doc, judge_meta = _build_score_doc(
        "self_test", SYNTHETIC_GT, recon_text, SYNTHETIC_TRANSCRIPT, mock,
        judge_chunk_lines=4, judge_overlap_lines=1, verbose=False,
    )
    assert "ground_truth_items" in score_doc, "score doc missing ground_truth_items"
    assert "reconstruction_claims" in score_doc, "score doc missing reconstruction_claims"
    # All GT items must be present
    assert len(score_doc["ground_truth_items"]) == len(SYNTHETIC_GT), \
        f"Expected {len(SYNTHETIC_GT)} GT items, got {len(score_doc['ground_truth_items'])}"
    # All GT items must have recalled field; all claims must have grounded field
    for item in score_doc["ground_truth_items"]:
        assert "recalled" in item, f"GT item {item.get('id')} missing recalled field"
    for claim in score_doc["reconstruction_claims"]:
        assert "grounded" in claim, f"claim {claim.get('id')} missing grounded field"
    assert judge_meta["grounding_reference"] == "transcript", "grounding must reference the transcript"
    assert judge_meta["n_judge_chunks"] >= 1, "grounding judge must have chunked the transcript"
    print(f"    OK — {len(score_doc['ground_truth_items'])} GT recalled-judged, "
          f"{len(score_doc['reconstruction_claims'])} claims transcript-grounded "
          f"({judge_meta['n_judge_chunks']} judge chunks)")

    # --- Test 6: grounding union across chunks (ANY-chunk semantics) ---
    print("[6] Grounding union — claim grounded iff ANY chunk supports it...")
    union_claims = [
        {"id": "c1", "text": "Root cause: the JWT expiry check used server time instead of UTC"},  # chunk 1 only
        {"id": "c2", "text": "PR #44 is open; merge when CI is green"},                            # chunk 2 only
        {"id": "c3", "text": "Switched the database engine to PostgreSQL"},                        # nowhere — confabulated
    ]
    graded, n_chunks = llm_grounding_judge(
        union_claims, SYNTHETIC_TRANSCRIPT, mock,
        judge_chunk_lines=4, judge_overlap_lines=1, verbose=False,
    )
    g = {c["id"]: c["grounded"] for c in graded}
    assert n_chunks == 2, f"Expected 2 judge chunks, got {n_chunks}"
    assert g["c1"] is True, "c1 should be grounded (supported by chunk 1)"
    assert g["c2"] is True, "c2 should be grounded (supported ONLY by chunk 2 — union failed)"
    assert g["c3"] is False, "c3 should be ungrounded (no chunk supports it)"
    print(f"    OK — chunk1-only grounded, chunk2-only grounded (union), fabricated claim ungrounded")

    # --- Test 7: score_session integration ---
    print("[7] score_session() integration...")
    get_ss = get_score_session()
    ss = get_ss(score_doc)
    assert 0.0 <= ss.rr <= 1.0, f"RR out of range: {ss.rr}"
    assert 0.0 <= ss.fmr <= 1.0, f"FMR out of range: {ss.fmr}"
    print(f"    OK — RR={ss.rr * 100:.1f}% FMR={ss.fmr * 100:.1f}%")

    # --- Test 8: verdict logic (RR AND FMR bars) ---
    print("[8] Verdict logic (clears bar = RR>=70% AND FMR<=10%)...")
    results = {
        "armA": ArmResult("armA", "done", rr=0.517, fmr=0.0, omr=0.483),
        "armB": ArmResult("armB", "done", rr=0.72, fmr=0.05, omr=0.28),
        "armC": ArmResult("armC", "done", rr=0.65, fmr=0.03, omr=0.35),
    }
    v = compute_verdict(results)
    assert "PROMPT FIX" in v, f"Expected 'PROMPT FIX' verdict, got: {v}"

    results2 = {
        "armA": ArmResult("armA", "done", rr=0.517, fmr=0.0, omr=0.483),
        "armB": ArmResult("armB", "done", rr=0.60, fmr=0.05, omr=0.40),
        "armC": ArmResult("armC", "done", rr=0.75, fmr=0.03, omr=0.25),
    }
    v2 = compute_verdict(results2)
    assert "CHUNKING REQUIRED" in v2, f"Expected 'CHUNKING REQUIRED' verdict, got: {v2}"

    results3 = {
        "armA": ArmResult("armA", "done", rr=0.517, fmr=0.0, omr=0.483),
        "armB": ArmResult("armB", "done", rr=0.55, fmr=0.05, omr=0.45),
        "armC": ArmResult("armC", "done", rr=0.62, fmr=0.03, omr=0.38),
    }
    v3 = compute_verdict(results3)
    assert "CHUNKING HELPS" in v3, f"Expected 'CHUNKING HELPS' verdict, got: {v3}"

    # NEW: high RR but failing FMR must NOT clear the bar
    results4 = {
        "armA": ArmResult("armA", "done", rr=0.517, fmr=0.0, omr=0.483),
        "armB": ArmResult("armB", "done", rr=0.72, fmr=0.35, omr=0.28),  # RR clears, FMR fails
        "armC": ArmResult("armC", "done", rr=0.65, fmr=0.03, omr=0.35),
    }
    v4 = compute_verdict(results4)
    assert "PROMPT FIX" not in v4, f"FMR 35% must not clear the bar, got: {v4}"
    assert "FMR" in v4, f"Verdict should note the FMR failure, got: {v4}"
    print(f"    OK — 4 verdict branches incl. FMR-gate: [{v4[:60]}...]")

    # --- Test 9: idempotency (score.json already exists) ---
    print("[9] Idempotency — skip if score.json exists...")
    # Write a minimal score.json to the self-test arm dir
    dummy_score = {"session_id": "self_test", "ground_truth_items": list(SYNTHETIC_GT), "reconstruction_claims": []}
    for item in dummy_score["ground_truth_items"]:
        item["recalled"] = True
    (self_test_dir / "score.json").write_text(json.dumps(dummy_score))
    # Run again — should skip
    result = run_arm(
        arm_name="armC",
        sid="self_test",
        transcript=SYNTHETIC_TRANSCRIPT,
        gt_items=SYNTHETIC_GT,
        arm_dir=self_test_dir,
        serialize_prompt_file="01b-serialize-d007.md",
        llm_mod=mock,
        chunked=True,
        merge_prompt_file="01c-merge-checkpoints.md",
        force=False,
        verbose=False,
    )
    assert result.status == "skipped", f"Expected 'skipped', got: {result.status}"
    print(f"    OK — arm skipped as expected (status={result.status})")

    # --- Test 10: --force overrides idempotency ---
    print("[10] --force overrides idempotency...")
    result_forced = run_arm(
        arm_name="armC",
        sid="self_test",
        transcript=SYNTHETIC_TRANSCRIPT,
        gt_items=SYNTHETIC_GT,
        arm_dir=self_test_dir,
        serialize_prompt_file="01b-serialize-d007.md",
        llm_mod=mock,
        chunked=True,
        merge_prompt_file="01c-merge-checkpoints.md",
        force=True,
        judge_chunk_lines=4,
        judge_overlap_lines=1,
        transcript_ref="self-test:SYNTHETIC_TRANSCRIPT",
        verbose=False,
    )
    assert result_forced.status == "done", f"Expected 'done' with --force, got: {result_forced.status}"
    print(f"    OK — arm re-ran with force (status={result_forced.status})")

    # --- Test 11: --rejudge re-runs ONLY judge passes on existing artifacts ---
    print("[11] --rejudge — judge-only re-run on existing checkpoint+reconstruction...")
    calls_before = MockLLM.call_count
    result_rejudge = run_arm(
        arm_name="armC",
        sid="self_test",
        transcript=SYNTHETIC_TRANSCRIPT,
        gt_items=SYNTHETIC_GT,
        arm_dir=self_test_dir,
        serialize_prompt_file="01b-serialize-d007.md",
        llm_mod=mock,
        chunked=True,
        merge_prompt_file="01c-merge-checkpoints.md",
        rejudge=True,
        judge_chunk_lines=4,
        judge_overlap_lines=1,
        transcript_ref="self-test:SYNTHETIC_TRANSCRIPT",
        verbose=False,
    )
    calls_used = MockLLM.call_count - calls_before
    assert result_rejudge.status == "rejudged", f"Expected 'rejudged', got: {result_rejudge.status}"
    # judge-only = 1 recall call + <=2 grounding chunk calls; serialize+merge+reconstruct would add 4+
    assert calls_used <= 3, f"rejudge made {calls_used} LLM calls — looks like it re-serialized"
    rejudged_score = json.loads((self_test_dir / "score.json").read_text())
    for claim in rejudged_score["reconstruction_claims"]:
        assert "grounded" in claim, "rejudged score.json claims missing grounded field"
    rejudged_meta = json.loads((self_test_dir / "meta.json").read_text())
    assert rejudged_meta["rejudged"] is True, "meta.json must record rejudged=True"
    assert rejudged_meta["judge"]["grounding_reference"] == "transcript", \
        "meta.json must record grounding reference = transcript"
    print(f"    OK — rejudged with {calls_used} LLM calls (no re-serialize), provenance updated")

    # --- Test 12: --rejudge fails cleanly when artifacts are missing ---
    print("[12] --rejudge with missing artifacts fails per-arm...")
    empty_dir = RUNS_DIR / "_self_test_d007" / "armEmpty"
    empty_dir.mkdir(parents=True, exist_ok=True)
    result_missing = run_arm(
        arm_name="armEmpty",
        sid="self_test",
        transcript=SYNTHETIC_TRANSCRIPT,
        gt_items=SYNTHETIC_GT,
        arm_dir=empty_dir,
        serialize_prompt_file="01b-serialize-d007.md",
        llm_mod=mock,
        rejudge=True,
        verbose=False,
    )
    assert result_missing.status.startswith("failed:rejudge"), \
        f"Expected failed:rejudge, got: {result_missing.status}"
    print(f"    OK — missing artifacts reported as arm failure (status={result_missing.status[:40]}...)")

    # --- Test 13: per-arm failure isolation ---
    print("[13] Per-arm failure isolation...")

    class BrokenLLM:
        @staticmethod
        def chat(messages, **kwargs):
            raise RuntimeError("simulated LLM failure")

        @staticmethod
        def extract_json(text):
            return {}

    broken_dir = RUNS_DIR / "_self_test_d007" / "armBroken"
    broken_dir.mkdir(parents=True, exist_ok=True)
    broken_result = run_arm(
        arm_name="armBroken",
        sid="self_test",
        transcript=SYNTHETIC_TRANSCRIPT,
        gt_items=SYNTHETIC_GT,
        arm_dir=broken_dir,
        serialize_prompt_file="01b-serialize-d007.md",
        llm_mod=BrokenLLM,
        chunked=False,
        force=True,
        verbose=False,
    )
    assert broken_result.status.startswith("failed"), \
        f"Expected failed status, got: {broken_result.status}"
    print(f"    OK — broken arm reported failed without killing probe (status={broken_result.status})")

    # --- Done ---
    print(f"\n=== self-test PASSED ({MockLLM.call_count} mock LLM calls) ===")
    print(f"    Artifacts in: {RUNS_DIR / '_self_test_d007'}")
    return 0


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _pct(x: float | None) -> str:
    return " n/a" if x is None else f"{x * 100:.1f}%"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="D-007 Serializer Probe — compares prompt-fix vs. chunking on recall cliff",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              uv run probe_d007.py --self-test
              uv run probe_d007.py --session S2
              uv run probe_d007.py --session S2 --force
              uv run probe_d007.py --session S2 --rejudge
              uv run probe_d007.py --session S1 --chunk-lines 600 --overlap-lines 80
        """),
    )
    ap.add_argument("--session", default="S2", help="Session ID (default: S2)")
    ap.add_argument("--force", action="store_true", help="Re-run arms even if score.json already exists")
    ap.add_argument("--rejudge", action="store_true",
                    help="Re-run ONLY the judge passes on existing checkpoint.json + reconstruction.md "
                         "(no re-serialize/reconstruct); recomputes score.json + verdict")
    ap.add_argument("--chunk-lines", type=int, default=800, help="Lines per chunk for arm C serialization (default: 800)")
    ap.add_argument("--overlap-lines", type=int, default=100, help="Overlap lines between chunks (default: 100)")
    ap.add_argument("--judge-chunk-lines", type=int, default=1200,
                    help="Transcript lines per grounding-judge chunk (default: 1200)")
    ap.add_argument("--judge-overlap-lines", type=int, default=100,
                    help="Overlap lines between grounding-judge chunks (default: 100)")
    ap.add_argument("--self-test", action="store_true", help="Run self-test with mock LLM (no network)")
    args = ap.parse_args()

    if args.self_test:
        return run_self_test(chunk_lines=args.chunk_lines, overlap_lines=args.overlap_lines)

    # --- Real run ---
    sid = args.session

    # Load transcript
    transcript_path = SESSIONS_DIR / f"{sid}.txt"
    if not transcript_path.exists():
        print(f"ERROR: transcript not found: {transcript_path}", file=sys.stderr)
        print(f"  Drop your transcript at {transcript_path} first.", file=sys.stderr)
        return 2

    # Load ground truth
    gt_path = RUNS_DIR / sid / "ground-truth.json"
    if not gt_path.exists():
        print(f"ERROR: ground-truth not found: {gt_path}", file=sys.stderr)
        print(f"  Write {gt_path} before running the probe.", file=sys.stderr)
        return 2

    try:
        gt_doc = json.loads(gt_path.read_text())
        gt_items = gt_doc.get("ground_truth_items", gt_doc) if isinstance(gt_doc, dict) else gt_doc
    except Exception as e:
        print(f"ERROR: failed to load ground-truth.json: {e}", file=sys.stderr)
        return 2

    transcript = transcript_path.read_text()
    n_lines = len(transcript.splitlines())

    # Confirm LiteLLM env
    llm_mod = get_llm(mock=False)
    model_name = os.environ.get("LITELLM_MODEL", "<unset>")
    base_url = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
    print(f"D-007 Serializer Probe")
    print(f"  Session:  {sid} ({n_lines} lines, {len(gt_items)} GT items)")
    print(f"  Model:    {model_name}  Base: {base_url}")
    print(f"  Chunks:   {args.chunk_lines} lines / {args.overlap_lines} overlap (arm C)")
    print(f"  Judge:    grounding vs transcript, {args.judge_chunk_lines} lines / {args.judge_overlap_lines} overlap")
    print(f"  Mode:     {'REJUDGE (judge passes only)' if args.rejudge else 'full'}  Force: {args.force}")
    print()

    probe_dir = RUNS_DIR / sid / "probe-d007"
    probe_dir.mkdir(parents=True, exist_ok=True)

    arm_configs = [
        dict(
            arm_name="armA",
            arm_dir=probe_dir / "armA",
            serialize_prompt_file="01-serialize.md",
            chunked=False,
        ),
        dict(
            arm_name="armB",
            arm_dir=probe_dir / "armB",
            serialize_prompt_file="01b-serialize-d007.md",
            chunked=False,
        ),
        dict(
            arm_name="armC",
            arm_dir=probe_dir / "armC",
            serialize_prompt_file="01b-serialize-d007.md",
            chunked=True,
            merge_prompt_file="01c-merge-checkpoints.md",
        ),
    ]

    results: dict[str, ArmResult] = {}
    for cfg in arm_configs:
        arm_name = cfg["arm_name"]
        print(f"--- {arm_name} ---")
        try:
            result = run_arm(
                sid=sid,
                transcript=transcript,
                gt_items=gt_items,
                llm_mod=llm_mod,
                chunk_lines=args.chunk_lines,
                overlap_lines=args.overlap_lines,
                force=args.force,
                rejudge=args.rejudge,
                judge_chunk_lines=args.judge_chunk_lines,
                judge_overlap_lines=args.judge_overlap_lines,
                transcript_ref=f"sessions/{sid}.txt",
                **cfg,
            )
            results[arm_name] = result
        except Exception as e:
            print(f"  [{arm_name}] UNEXPECTED ERROR: {e}")
            results[arm_name] = ArmResult(arm=arm_name, status=f"failed:unexpected:{e}", error=str(e))

    verdict = compute_verdict(results)
    print_comparison_table(results, verdict)

    # Summary
    failed = [r for r in results.values() if r.status.startswith("failed")]
    if failed:
        print(f"WARNING: {len(failed)} arm(s) failed:")
        for r in failed:
            print(f"  {r.arm}: {r.status}")

    print(f"Outputs: {probe_dir}/")
    print(f"  armA/  armB/  armC/  — each: checkpoint.json, reconstruction.md, score.json, meta.json")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
