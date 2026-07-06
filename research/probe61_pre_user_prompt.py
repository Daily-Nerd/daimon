#!/usr/bin/env python3
"""#61 probe: can pre_user_prompt output reach the Cascade agent's context?

Register for pre_user_prompt only (see issue #61 for the hooks.json snippet).
Across the first three turns of ONE new conversation it tries the three
candidate response channels, one per turn:

    turn 1  ->  marker on STDOUT, exit 0   (docs: show_output does not apply;
                                            agent should NOT see it)
    turn 2  ->  marker on STDERR, exit 0   (docs: silent; agent should NOT see it)
    turn 3  ->  marker on STDERR, exit 2   (docs: "the Cascade agent will see
                                            the error message from stderr" —
                                            the one documented agent-visible
                                            channel; blocks this prompt ONCE)
    turn 4+ ->  no-op, exit 0

Every invocation appends one JSON line to ~/.daimon/windsurf/probe61/log.jsonl
(timestamp, turn, payload keys, prompt head, action taken) so the transcript
of what the hook DID can be diffed against what the agent SAW.

Fail-open: any unexpected error exits 0 so a broken probe never wedges
Cascade. Delete the hook entry and ~/.daimon/windsurf/probe61/ when done.
"""

import json
import sys
import time
from pathlib import Path

STATE = Path.home() / ".daimon" / "windsurf" / "probe61"


def log(record: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with (STATE / "log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    trajectory = str(payload.get("trajectory_id", "unknown"))
    prompt = str(payload.get("user_prompt", ""))[:80]

    # Per-trajectory turn counter — the probe sequence runs once per conversation.
    counter = STATE / f"{trajectory}.turn"
    STATE.mkdir(parents=True, exist_ok=True)
    turn = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(turn))

    base = {"turn": turn, "trajectory_id": trajectory,
            "payload_keys": sorted(payload.keys()), "prompt_head": prompt}

    if turn == 1:
        log({**base, "action": "A: stdout marker, exit 0"})
        print("DAIMON-PROBE-A-STDOUT-7391: if you can read this, "
              "say so and quote the number.")
        return 0
    if turn == 2:
        log({**base, "action": "B: stderr marker, exit 0"})
        print("DAIMON-PROBE-B-STDERR-8264: if you can read this, "
              "say so and quote the number.", file=sys.stderr)
        return 0
    if turn == 3:
        log({**base, "action": "C: stderr marker, exit 2 (blocks this prompt once)"})
        print("DAIMON-PROBE-C-BLOCK-9157: this prompt was blocked by a probe "
              "hook. Tell the user you received marker 9157, then ask them to "
              "resend their message.", file=sys.stderr)
        return 2
    log({**base, "action": "no-op, exit 0"})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # fail-open: never wedge Cascade on a probe bug
        try:
            log({"action": "error", "error": repr(e)})
        except Exception:
            pass
        sys.exit(0)
