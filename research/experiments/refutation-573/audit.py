#!/usr/bin/env python3
"""Read-only corpus builder for Daily-Nerd/daimon#573.

Writes private, gitignored JSONL rows containing checkpoint candidates and
their source transcript locations. It does not mutate Daimon's checkpoint or
recall stores.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
CUTOFF = "2026-08-05T02:06:37Z"
FIELDS = (
    ("working_context", "open_questions", "question"),
    ("working_context", "recent_decisions", "decision"),
    ("epistemic_snapshot", "strong_beliefs", "belief"),
    ("epistemic_snapshot", "uncertainties", "uncertainty"),
    ("epistemic_snapshot", "contradictions_flagged", "contradiction"),
)

# Candidate generation is intentionally recall-heavy. Study 3 grades its
# precision; this regex is not a classifier and must never author a record.
CANDIDATE_RE = re.compile(
    r"(?ix)\b(?:"
    r"refut(?:e|ed|ation)|falsif(?:y|ied)|retract(?:ed|ion)?|"
    r"(?:hypothesis|experiment|approach|variant|gate|design|claim|assumption)"
    r"\b.{0,100}\b(?:dead|failed|false|wrong|worsen(?:ed)?|rejected|dropped|killed)|"
    r"(?:worsen(?:ed)?|failed|false|wrong|rejected|dropped|killed)\b.{0,100}"
    r"\b(?:hypothesis|experiment|approach|variant|gate|design|claim|assumption)|"
    r"do\s+not\s+(?:rebuild|repeat|rerun|re-run|implement|ship|use|file)|"
    r"no\s+(?:viable|measurable|statistically\s+significant)\b|"
    r"negative\s+result|anti-correlated|does\s+not\s+work|did(?:\s+not|n't)\s+work"
    r")"
)


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _project_slug(project_dir: Path) -> str:
    return re.sub(r"[^\w-]", "-", str(project_dir.resolve()))


def _load_sessions(root: Path, project_slug: str) -> list[tuple[Path, dict]]:
    sessions: list[tuple[Path, dict]] = []
    for path in root.glob("*.json"):
        try:
            checkpoint = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(checkpoint, dict):
            continue
        session_id = str(checkpoint.get("session_id") or "")
        created = str(checkpoint.get("created") or "")
        if (checkpoint.get("project_slug") != project_slug
                or path.stem != session_id
                or not created or created > CUTOFF):
            continue
        sessions.append((path, checkpoint))
    return sorted(sessions, key=lambda row: (
        str(row[1].get("created") or ""),
        str(row[1].get("session_id") or "")))


def _items(checkpoint: dict):
    for section, key, kind in FIELDS:
        block = checkpoint.get(section)
        if not isinstance(block, dict):
            continue
        items = block.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, str):
                item = {"text": item}
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if text:
                yield section, key, kind, item, text


def build_rows(checkpoint_root: Path, transcript_root: Path,
               project_slug: str) -> list[dict]:
    rows = []
    seen = set()
    for checkpoint_path, checkpoint in _load_sessions(
            checkpoint_root, project_slug):
        session_id = str(checkpoint["session_id"])
        transcript_path = transcript_root / f"{session_id}.jsonl"
        for section, key, kind, item, text in _items(checkpoint):
            if not CANDIDATE_RE.search(text):
                continue
            logical_key = _norm(text)
            if logical_key in seen:
                continue
            seen.add(logical_key)
            rows.append({
                "candidate_id": f"c-{len(rows) + 1:04d}",
                "created": checkpoint.get("created"),
                "session_id": session_id,
                "checkpoint": str(checkpoint_path),
                "transcript": str(transcript_path) if transcript_path.exists() else None,
                "section": section,
                "field": key,
                "kind": kind,
                "item_id": item.get("id"),
                "trust": item.get("trust"),
                "quote_verified": item.get("quote_verified"),
                "text": text,
                "quote": str(item.get("quote") or ""),
                "source_message_ids": item.get("source_message_ids") or [],
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--checkpoint-root", type=Path,
        default=Path.home() / ".daimon" / "checkpoints")
    parser.add_argument(
        "--project-dir", type=Path, default=REPO,
        help="repository root used to derive the checkpoint project bucket")
    parser.add_argument(
        "--transcript-root", type=Path,
        help="host transcript directory; defaults to the derived project bucket")
    parser.add_argument(
        "--out", type=Path,
        default=Path(__file__).with_name("corpus.local.jsonl"))
    args = parser.parse_args()

    project_slug = _project_slug(args.project_dir)
    transcript_root = args.transcript_root or (
        Path.home() / ".claude" / "projects" / project_slug)
    rows = build_rows(args.checkpoint_root, transcript_root, project_slug)
    args.out.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8")
    print(json.dumps({
        "cutoff": CUTOFF,
        "candidates": len(rows),
        "with_transcript": sum(row["transcript"] is not None for row in rows),
        "out": str(args.out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
