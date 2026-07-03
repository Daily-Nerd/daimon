"""Build Daimon-scale inputs for the state-tracking instrument.

Extends the small authored scenarios to 2K/15K/60K tokens by interleaving
their override spine into real Claude Code transcript noise, while keeping
deterministic grading airtight (no gold/stale token survives in the noise).
"""

import json
from dataclasses import replace
from typing import List, Set, Tuple, Dict

from benchmark.state.scenarios import Scenario
from benchmark.state.grade import _mentions
from benchmark.evaluate import count_tokens


def _blocks_text(content) -> List[str]:
    """Pull human-readable text out of a message.content (str or block list)."""
    if isinstance(content, str):
        return [content]
    out: List[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and isinstance(block.get("text"), str):
                out.append(block["text"])
            elif btype == "tool_result":
                c = block.get("content")
                if isinstance(c, str):
                    out.append(c)
                else:
                    out.extend(_blocks_text(c))
            # tool_use inputs are skipped: huge JSON, not conversational noise
    return out


def extract_noise_turns(jsonl_paths: List[str]) -> List[str]:
    turns: List[str] = []
    for path in jsonl_paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") not in ("user", "assistant"):
                    continue
                msg = rec.get("message") or {}
                for text in _blocks_text(msg.get("content")):
                    text = text.strip()
                    if text:
                        turns.append(text)
    return turns


def scenario_blocklist(scenario: Scenario) -> Set[str]:
    """Collect every gold term, alias, and stale token from a scenario's probes.

    Returns a set containing all tokens that should be blocked when screening
    turns for this scenario. This ensures the scaled scenario carries no
    gold/stale tokens that would contaminate noise.
    """
    bl: Set[str] = set()
    for probe in scenario.probes:
        bl.update(probe.gold_terms())
        bl.update(probe.stale)
    return {t for t in bl if t}


def screen_turns(turns: List[str], blocklist: Set[str]) -> Tuple[List[str], int]:
    """Filter turns, removing any that mention a blocked token.

    Guarantees: no kept turn will mention any token in the blocklist, as
    verified by _mentions(turn, token) for all blocked tokens.

    Args:
        turns: List of turn strings to screen.
        blocklist: Set of tokens to block.

    Returns:
        (kept_turns, dropped_count): Turns that passed screening and count
        of dropped turns.
    """
    kept: List[str] = []
    dropped = 0
    for t in turns:
        if any(_mentions(t, tok) for tok in blocklist):
            dropped += 1
        else:
            kept.append(t)
    return kept, dropped


def build_noise_bed(turns: List[str], target_tokens: int) -> List[str]:
    """Accumulate turns until reaching target token count.

    Accumulates turns (in order) until the running token total first reaches
    target_tokens. Returns that prefix. If turns is exhausted first, returns
    all of them.

    Args:
        turns: List of turn strings to accumulate.
        target_tokens: Target token count to reach.

    Returns:
        List of turns forming the noise bed prefix.
    """
    bed: List[str] = []
    total = 0
    for t in turns:
        bed.append(t)
        total += count_tokens(t)
        if total >= target_tokens:
            break
    return bed


def chunk_windows(turns: List[str], chunk_lines: int = 1200,
                  overlap: int = 100) -> List[str]:
    """Split turns into overlapping line windows.

    Joins turns to text, splits into overlapping line windows
    (step = chunk_lines - overlap), returns one string per window.
    Mirrors plugin/daimon_briefing/serializer.py::chunk_transcript.
    If the text has <= chunk_lines lines, returns a single-element list.

    Args:
        turns: List of turn strings to chunk.
        chunk_lines: Number of lines per window (default 1200).
        overlap: Number of overlapping lines between windows (default 100).

    Returns:
        List of strings, each representing a window.
    """
    text = "\n".join(turns)
    lines = text.splitlines()
    if len(lines) <= chunk_lines:
        return [text]
    step = max(1, chunk_lines - overlap)
    windows: List[str] = []
    i = 0
    while i < len(lines):
        end = min(i + chunk_lines, len(lines))
        windows.append("\n".join(lines[i:end]))
        i += step
        if i >= len(lines):
            break
    return windows


def build_scaled_scenario(scenario: Scenario, noise_turns: List[str],
                          target_tokens: int, chunk_lines: int = 1200,
                          overlap: int = 100) -> Tuple[Scenario, Dict]:
    """Build a scaled scenario by interleaving spine turns into noise chunks.

    Screens noise to remove all blocklisted tokens, builds a noise bed up to
    target_tokens, chunks it into overlapping windows, then interleaves the
    original scenario's spine turns evenly across the chunk sequence.

    Args:
        scenario: Original Scenario to scale.
        noise_turns: Raw noise turns to screen and chunk.
        target_tokens: Target token count for the noise bed.
        chunk_lines: Lines per chunk window (default 1200).
        overlap: Overlapping lines between windows (default 100).

    Returns:
        (scaled_scenario, metadata): A new Scenario with interleaved turns
        (probes unchanged), and metadata dict with keys:
        - n_noise_chunks: Number of noise chunks created.
        - n_spine_turns: Number of original spine turns.
        - noise_tokens: Token count of the noise bed.
        - dropped_noise_turns: Count of noise turns removed by screening.
        - shortfall: True if noise_tokens < target_tokens.
    """
    blocklist = scenario_blocklist(scenario)
    screened, dropped = screen_turns(noise_turns, blocklist)
    bed = build_noise_bed(screened, target_tokens)
    shortfall = count_tokens("\n".join(bed)) < target_tokens
    chunks = chunk_windows(bed, chunk_lines=chunk_lines, overlap=overlap)

    spine = scenario.turns
    # Interleave: spread spine turns evenly across the chunk sequence.
    interleaved: List[str] = []
    n_chunks, n_spine = len(chunks), len(spine)
    ci = 0
    for si, turn in enumerate(spine):
        # how many chunks before this spine turn
        target_ci = round((si + 1) * n_chunks / (n_spine + 1))
        while ci < target_ci and ci < n_chunks:
            interleaved.append(chunks[ci])
            ci += 1
        interleaved.append(turn)
    while ci < n_chunks:
        interleaved.append(chunks[ci])
        ci += 1

    scaled = replace(scenario, turns=interleaved)
    meta: Dict = {
        "n_noise_chunks": n_chunks,
        "n_spine_turns": n_spine,
        "noise_tokens": count_tokens("\n".join(bed)),
        "dropped_noise_turns": dropped,
        "shortfall": shortfall,
    }
    return scaled, meta
