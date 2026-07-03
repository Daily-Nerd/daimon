"""Q-STALE multicycle driver: cycle a seed checkpoint through the REAL
daimon pipeline (briefing render -> synthetic session -> serialize_strict ->
store.write_checkpoint) N times per arm, grade every cycle deterministically.

Resumable: run_dir/<arm>/cycle-NN.json caches each cycle's checkpoint; a
rerun re-runs only missing cycles. Token guard: CountingChat aborts the run
past the budget (default 600K estimated tokens, len//4)."""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugin"))

import seed
import synth
import grade

_BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)


class BudgetExceeded(RuntimeError):
    pass


class CountingChat:
    """Wrap a chat callable with a hard estimated-token budget (len//4,
    matching briefing.estimate_tokens). Abort BEFORE the call that would
    have been wasted spend past the ceiling."""

    def __init__(self, chat, budget=600_000):
        self._chat = chat
        self.budget = budget
        self.spent = 0

    def __call__(self, messages, **kwargs):
        if self.spent >= self.budget:
            raise BudgetExceeded(f"{self.spent} >= {self.budget} tokens")
        self.spent += sum(len(str(m.get("content", ""))) for m in messages) // 4
        out = self._chat(messages, **kwargs)
        self.spent += len(out) // 4
        return out


def _created_for(cycle: int) -> str:
    return (_BASE + timedelta(days=cycle)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _context_for(arm: str, cp: dict, now_epoch: float) -> str:
    """The carry channel under test: rendered briefing text (control /
    distractor, the production path) or raw checkpoint JSON (carry — the
    lossless upper bound standing in for #33 merged history). The simulated
    clock (now_epoch) is passed into briefing.build so #78 decay scoring runs
    in simulation time, not wall time — the carry branch ignores it since it
    injects the raw checkpoint verbatim."""
    from daimon_briefing import briefing
    if arm == "carry":
        return json.dumps(cp, ensure_ascii=False, indent=2)
    b = briefing.build(cp, now=now_epoch)
    return briefing.render_plain(b) if b else "(no briefing rendered)"


def run_arm(arm: str, cycles: int, chat, run_dir) -> list[dict]:
    run_dir = Path(run_dir)
    arm_dir = run_dir / arm
    arm_dir.mkdir(parents=True, exist_ok=True)
    # Isolated store per arm: write_checkpoint must never see ~/.daimon,
    # and _stamp_first_seen's carry-over needs a per-arm pointer chain.
    os.environ["DAIMON_CHECKPOINT_DIR"] = str(arm_dir / "store")
    project_dir = f"/experiment/{arm}"

    from daimon_briefing import carry, config as dconfig, serializer, store

    cp = seed.make_seed()
    rows = list(grade.grade_checkpoint(cp, 0, arm))
    store.write_checkpoint(cp["session_id"], dict(cp), project_dir=project_dir)

    for k in range(1, cycles + 1):
        cache = arm_dir / f"cycle-{k:02d}.json"
        if cache.exists():
            cp = json.loads(cache.read_text(encoding="utf-8"))
        else:
            now_epoch = store._created_epoch(_created_for(k))
            context = _context_for(arm, cp, now_epoch)
            msgs = synth.make_transcript(context, cycle=k, arm=arm)
            sid = f"{arm}-cycle-{k:03d}"
            out = serializer.serialize_strict(sid, msgs, chat=chat)
            # serialize_strict echoes the LLM's own session_id from the
            # checkpoint body it emitted — the driver owns identity.
            out["session_id"] = sid
            out["created"] = _created_for(k)
            if dconfig.carry_enabled():
                # Mirror cli.py's serialize wiring: prev read before the write
                # rotates it; clock = the cycle's simulated stamp (scar:
                # simulated-clock-must-thread-into-every-now-consumer).
                prev_latest = store.read_latest(project_dir)
                out = carry.merge(out, prev_latest,
                                  store._created_epoch(out["created"]),
                                  floor=dconfig.carry_floor(),
                                  cap=dconfig.carry_max())
            store.write_checkpoint(sid, out, project_dir=project_dir)
            cache.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                             encoding="utf-8")
            cp = out
        rows.extend(grade.grade_checkpoint(cp, k, arm))
    (run_dir / f"results-{arm}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Q-STALE multicycle experiment")
    ap.add_argument("--arms", default="control,distractor,carry")
    ap.add_argument("--cycles", type=int, default=20)
    ap.add_argument("--run-dir", default="results/run-01")
    ap.add_argument("--budget", type=int, default=600_000)
    ap.add_argument("--dry-run", action="store_true",
                    help="fake chat (echoes seed) — no LLM, no cost")
    args = ap.parse_args(argv)

    if args.dry_run:
        def base_chat(messages, **kwargs):
            return json.dumps(seed.make_seed())
    else:
        from daimon_briefing import llm
        base_chat = llm.chat
    chat = CountingChat(base_chat, budget=args.budget)

    all_rows = []
    for arm in args.arms.split(","):
        print(f"=== arm {arm} ===", flush=True)
        try:
            all_rows.extend(run_arm(arm.strip(), args.cycles, chat,
                                    args.run_dir))
        except BudgetExceeded as exc:
            print(f"BUDGET ABORT in arm {arm}: {exc}", flush=True)
            break
        print(f"tokens spent so far: {chat.spent}", flush=True)
    report = grade.summarize(all_rows)
    Path(args.run_dir, "summary.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
