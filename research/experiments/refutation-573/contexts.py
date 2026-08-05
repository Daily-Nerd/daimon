#!/usr/bin/env python3
"""Find later transcript contexts for manually accepted logical refutations."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "plugin"))

from daimon_briefing import config, serializer, store, transcript  # noqa: E402


CUTOFF = "2026-08-05T02:06:37Z"


def _snippet(text: str, hit: re.Match, width: int = 500) -> str:
    start = max(0, hit.start() - width // 2)
    end = min(len(text), hit.end() + width // 2)
    body = " ".join(text[start:end].split())
    return ("…" if start else "") + body + ("…" if end < len(text) else "")


def main() -> int:
    parser = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    parser.add_argument("--labels", type=Path,
                        default=here / "labels.local.json")
    parser.add_argument(
        "--project-dir", type=Path, default=REPO,
        help="repository root used to derive the transcript project bucket")
    parser.add_argument("--transcript-root", type=Path,
                        help="host transcript directory; defaults to the derived project bucket")
    parser.add_argument("--out", type=Path,
                        default=here / "contexts.local.jsonl")
    args = parser.parse_args()

    transcript_root = args.transcript_root
    if transcript_root is None:
        project_slug = store.project_slug(args.project_dir)
        transcript_root = config.claude_projects_dir() / project_slug

    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    sessions = []
    for path in transcript_root.glob("*.jsonl"):
        stamp = transcript.last_timestamp(path)
        if stamp and stamp <= CUTOFF:
            sessions.append((stamp, path))
    sessions.sort()

    rows = []
    for label in labels:
        patterns = [re.compile(re.escape(p), re.IGNORECASE)
                    for p in label["patterns"]]
        for stamp, path in sessions:
            if (stamp <= label["origin_created"]
                    or path.stem == label["origin_session"]):
                continue
            try:
                messages = transcript.from_file(path)
            except OSError:
                continue
            for message_index, message in enumerate(messages):
                if message.get("role") not in ("user", "assistant"):
                    continue
                text = serializer.strip_injected(
                    str(message.get("content") or ""))
                hit = None
                for pattern in patterns:
                    hit = pattern.search(text)
                    if hit is not None:
                        break
                if hit is None:
                    continue
                rows.append({
                    "refutation_id": label["id"],
                    "subject": label["subject"],
                    "session_id": path.stem,
                    "session_end": stamp,
                    "message_index": message_index,
                    "role": message.get("role"),
                    "snippet": _snippet(text, hit),
                    "grade": None,
                    "reason": None,
                })

    args.out.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8")
    print(json.dumps({
        "logical_refutations": len(labels),
        "later_contexts": len(rows),
        "subjects_with_contexts": len({row["refutation_id"] for row in rows}),
        "out": str(args.out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
