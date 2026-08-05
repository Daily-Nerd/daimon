#!/usr/bin/env python3
"""Measure whether accepted refutations survive into later checkpoints.

This is a read-only retrospective.  Private per-record matches are written to
the experiment's gitignored local output; committed results contain aggregates
only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "plugin"))

from daimon_briefing import carry, schema, store  # noqa: E402


CUTOFF = "2026-08-05T02:06:37Z"


def _checkpoint_items(checkpoint: dict):
    for field in schema.ITEM_FIELDS:
        block = checkpoint.get(field.section)
        if not isinstance(block, dict):
            continue
        value = block.get(field.key)
        values = [value] if field.singleton else value
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, str):
                text = item
                metadata = {}
            elif isinstance(item, dict):
                text = item.get("text")
                metadata = item
            else:
                continue
            if isinstance(text, str) and text.strip():
                yield field.kind, text.strip(), metadata


def _load_checkpoints(root: Path, project_slug: str) -> list[dict]:
    checkpoints = []
    for path in root.glob("*.json"):
        try:
            checkpoint = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(checkpoint, dict):
            continue
        created = checkpoint.get("created")
        session_id = checkpoint.get("session_id")
        if (checkpoint.get("project_slug") != project_slug
                or path.stem != session_id
                or not isinstance(created, str)
                or created > CUTOFF):
            continue
        checkpoint["_path"] = str(path)
        checkpoints.append(checkpoint)
    return sorted(checkpoints, key=lambda cp: (cp["created"], cp["session_id"]))


def _match(source_texts: list[str], checkpoint: dict) -> dict | None:
    for kind, target, metadata in _checkpoint_items(checkpoint):
        for source in source_texts:
            if source.casefold() == target.casefold():
                return {
                    "match": "exact",
                    "kind": kind,
                    "text": target,
                    "carried_from": metadata.get("carried_from"),
                    "origin_session": metadata.get("origin_session"),
                }
            if carry._same_item(source, target):
                return {
                    "match": "fuzzy",
                    "kind": kind,
                    "text": target,
                    "carried_from": metadata.get("carried_from"),
                    "origin_session": metadata.get("origin_session"),
                }
    return None


def main() -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", type=Path,
                        default=here / "labels.local.json")
    parser.add_argument("--corpus", type=Path,
                        default=here / "corpus.local.jsonl")
    parser.add_argument("--checkpoint-root", type=Path,
                        default=Path.home() / ".daimon" / "checkpoints")
    parser.add_argument(
        "--project-dir", type=Path, default=REPO,
        help="repository root used to derive the checkpoint project bucket")
    parser.add_argument("--out", type=Path,
                        default=here / "survival.local.json")
    args = parser.parse_args()

    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    corpus = {
        row["candidate_id"]: row
        for row in (
            json.loads(line)
            for line in args.corpus.read_text(encoding="utf-8").splitlines()
        )
    }
    project_slug = store.project_slug(args.project_dir)
    checkpoints = _load_checkpoints(args.checkpoint_root, project_slug)

    rows = []
    for label in labels:
        source_texts = [
            corpus[candidate_id]["text"]
            for candidate_id in label["candidate_ids"]
        ]
        later = [
            checkpoint for checkpoint in checkpoints
            if checkpoint["created"] > label["origin_created"]
            and checkpoint["session_id"] != label["origin_session"]
        ]
        timeline = []
        for checkpoint in later:
            match = _match(source_texts, checkpoint)
            timeline.append({
                "created": checkpoint["created"],
                "session_id": checkpoint["session_id"],
                "present": match is not None,
                "match": match,
            })
        rows.append({
            "refutation_id": label["id"],
            "origin_created": label["origin_created"],
            "later_checkpoints": len(timeline),
            "next_checkpoint_present": (
                timeline[0]["present"] if timeline else None),
            "latest_checkpoint_present": (
                timeline[-1]["present"] if timeline else None),
            "survived_checkpoints": sum(row["present"] for row in timeline),
            "timeline": timeline,
        })

    args.out.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    eligible = [row for row in rows if row["later_checkpoints"]]
    print(json.dumps({
        "accepted_refutations": len(rows),
        "with_later_checkpoint": len(eligible),
        "present_in_next": sum(bool(row["next_checkpoint_present"])
                               for row in eligible),
        "present_at_cutoff": sum(bool(row["latest_checkpoint_present"])
                                 for row in eligible),
        "out": str(args.out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
