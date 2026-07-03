# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Track A runner — automates the AI steps (serialize -> reconstruct) against a
self-hosted LiteLLM gateway. The HUMAN steps stay manual: you still write
ground-truth.json (before running this) and score the result. That separation
is what keeps the experiment blind.

Setup:
    kubectl port-forward -n <namespace> svc/<litellm-svc> 4000:4000     # in another shell
    export LITELLM_API_KEY=sk-...        # your LiteLLM key (NOT committed)
    export LITELLM_MODEL=<name>          # `uv run ../lib/llm.py` to list

Run:
    uv run runner.py --all                       # every sessions/*.txt
    uv run runner.py --id S1                      # one session (sessions/S1.txt)

Outputs per session -> runs/<id>/checkpoint.json and runs/<id>/reconstruction.md
(both git-ignored). Then: write ground-truth, score, `uv run scoring/score.py`.
"""

import argparse
import glob
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))
import llm  # noqa: E402

SERIALIZE_SYS = """You are ending a work session and must serialize your cognitive state into a strict JSON checkpoint, so a future session can resume.

Output ONLY valid JSON conforming to the schema below. No prose before or after.

RULES — these are the point of the exercise, follow them exactly:
1. Extract only what the transcript supports. Do NOT invent open questions, decisions, beliefs, or facts that were not actually present.
2. For every item, set `trust`: "verbatim" (directly supported — you MUST include the exact `quote`) or "inferred" (paraphrase — leave `quote` empty).
3. Prefer "verbatim" wherever an explicit statement exists.
4. open_questions = genuinely unresolved at the end. recent_decisions = explicit choices. Be exhaustive on these two.
5. strong_beliefs / uncertainties = stated positions and doubts. Do NOT extract hedges, hypotheticals, sarcasm, or thinking-aloud as beliefs.
6. emotional_valence is necessarily inferred; acceptable for that single field.
7. If unsure whether something belongs, leave it out. Omission is safer than fabrication.

Schema shape:
{"session_id":"<id>","working_context":{"active_topic":{"text":"","trust":"","quote":""},"open_questions":[{"text":"","trust":"","quote":""}],"recent_decisions":[{"text":"","trust":"","quote":""}],"emotional_valence":{"text":"","trust":"inferred"}},"epistemic_snapshot":{"strong_beliefs":[{"text":"","trust":"","quote":""}],"uncertainties":[{"text":"","trust":"","quote":""}],"contradictions_flagged":[]},"worker_queue":[]}"""

RECONSTRUCT_SYS = """You are resuming a work session. Your only memory of the previous session is the cognitive checkpoint below. You do NOT have the original transcript.

Produce two parts.
PART 1 — RESUMED STATE (bullets): open questions left unresolved; decisions made; beliefs held; uncertainties; what you were working on (active topic). State each as a direct claim.
PART 2 — DREAM SEQUENCE: a <30-second skimmable "here's where we left off" briefing in natural prose.

CRITICAL: base every claim ONLY on the checkpoint. Do NOT add plausible-sounding detail not in the checkpoint. If the checkpoint is thin, your reconstruction must be thin. Do not embellish to sound coherent."""


def run_session(sid: str, session_path: Path, force: bool = False) -> str:
    """Returns one of: 'done', 'skipped', 'failed:<reason>'."""
    transcript = session_path.read_text()
    out_dir = HERE / "runs" / sid
    out_dir.mkdir(parents=True, exist_ok=True)

    if (out_dir / "reconstruction.md").exists() and not force:
        print(f"[{sid}] already done — skipping (use --force to redo)")
        return "skipped"

    # Step 1 — serialize (fresh context)
    content, usage, model = llm.chat([
        {"role": "system", "content": SERIALIZE_SYS},
        {"role": "user", "content": f"session_id: {sid}\n\nTRANSCRIPT:\n{transcript}"},
    ])
    try:
        checkpoint = llm.extract_json(content)
    except Exception:
        (out_dir / "checkpoint.raw.txt").write_text(content)
        print(f"[{sid}] serialize did not return parseable JSON — raw saved to checkpoint.raw.txt")
        return "failed:unparseable-checkpoint"
    (out_dir / "checkpoint.json").write_text(json.dumps(checkpoint, indent=2))
    print(f"[{sid}] serialized  (model={model}, tokens={usage.get('total_tokens','?')}) -> checkpoint.json")

    # Step 2 — reconstruct from checkpoint ONLY (fresh context)
    recon, usage2, _ = llm.chat([
        {"role": "system", "content": RECONSTRUCT_SYS},
        {"role": "user", "content": f"CHECKPOINT:\n{json.dumps(checkpoint, indent=2)}"},
    ])
    (out_dir / "reconstruction.md").write_text(recon)
    print(f"[{sid}] reconstructed (tokens={usage2.get('total_tokens','?')}) -> reconstruction.md")
    return "done"


def main() -> int:
    ap = argparse.ArgumentParser(description="Track A LiteLLM runner")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="run every sessions/*.txt")
    g.add_argument("--id", help="single session id (reads sessions/<id>.txt)")
    ap.add_argument("--force", action="store_true", help="re-run sessions even if already done")
    args = ap.parse_args()

    sessions_dir = HERE / "sessions"
    if args.all:
        paths = sorted(sessions_dir.glob("*.txt"))
        if not paths:
            print("No sessions/*.txt found. Drop your transcripts there first.", file=sys.stderr)
            return 2
        targets = [(p.stem, p) for p in paths]
    else:
        p = sessions_dir / f"{args.id}.txt"
        if not p.exists():
            print(f"Not found: {p}", file=sys.stderr)
            return 2
        targets = [(args.id, p)]

    print(f"Model: {os.environ.get('LITELLM_MODEL', '<unset>')}  Base: {os.environ.get('LITELLM_BASE_URL', 'http://localhost:4000')}\n")
    results = {}
    for sid, path in targets:
        try:
            results[sid] = run_session(sid, path, force=args.force)
        except llm.ChatError as e:
            print(f"[{sid}] FAILED: {e}")
            results[sid] = f"failed:{e}"
        except KeyboardInterrupt:
            print("\nInterrupted.")
            break

    done = [s for s, r in results.items() if r == "done"]
    skipped = [s for s, r in results.items() if r == "skipped"]
    failed = [s for s, r in results.items() if r.startswith("failed")]
    print(f"\nSummary: {len(done)} done, {len(skipped)} skipped, {len(failed)} failed.")
    if failed:
        print(f"  failed: {', '.join(failed)}  — re-run `uv run runner.py --all` to retry just these.")
    print("\nNext: write runs/<id>/ground-truth.json (if not yet), score per scoring/rubric.md,")
    print("then: uv run scoring/score.py runs/*/session-*.score.json")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
