#!/usr/bin/env python3
"""Windsurf Cascade hook probe — capture the REAL hook payload before we build.

The Windsurf adapter (#35) needs ground truth: the Cascade Hooks docs
(https://docs.windsurf.com/windsurf/cascade/hooks) are marked draft, so before
writing `daimon-windsurf-hooks.py` we capture what a real `post_cascade_response`
event actually delivers — payload shape, transcript_path location, and a sample
of the transcript's JSONL step format.

HOW TO RUN (takes ~5 minutes):

1. Save this file anywhere, e.g. `~/daimon-windsurf-probe.py`.
2. Register it as a Cascade hook (user-level hooks JSON — see the docs page
   above for the exact file location on your platform), for the
   `post_cascade_response` event, command: `python3 ~/daimon-windsurf-probe.py`.
   If your Windsurf build offers `post_cascade_response_with_transcript`,
   register that variant instead (richer payload).
3. Open Windsurf, run ONE short Cascade turn (any prompt).
4. Send back the folder `~/daimon-windsurf-probe/` (it contains the captured
   payload and a small transcript sample — a few lines, nothing sensitive
   beyond your one test prompt).

The probe is fail-open: it always exits 0 and never blocks Cascade.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

OUT_DIR = Path.home() / "daimon-windsurf-probe"
TRANSCRIPT_SAMPLE_LINES = 10


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    raw = sys.stdin.read()
    (OUT_DIR / f"payload-{stamp}.raw").write_text(raw, encoding="utf-8")

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        (OUT_DIR / f"payload-{stamp}.note").write_text(
            "stdin was not JSON — raw capture only\n", encoding="utf-8")
        return 0

    (OUT_DIR / f"payload-{stamp}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # Any *path* field that exists on disk gets a head sample — the adapter
    # needs the transcript's per-line step schema, not the whole conversation.
    if isinstance(payload, dict):
        for key, value in payload.items():
            if not (isinstance(value, str) and "path" in key.lower()):
                continue
            p = Path(value).expanduser()
            if not p.is_file():
                continue
            try:
                with p.open("r", encoding="utf-8", errors="replace") as f:
                    head = [next(f) for _ in range(TRANSCRIPT_SAMPLE_LINES)]
            except StopIteration:
                head = p.read_text(encoding="utf-8",
                                   errors="replace").splitlines(keepends=True)
            except OSError:
                continue
            (OUT_DIR / f"sample-{key}-{stamp}.txt").write_text(
                "".join(head), encoding="utf-8")

    print(f"daimon probe: captured to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — a probe must never break the host
        sys.exit(0)
