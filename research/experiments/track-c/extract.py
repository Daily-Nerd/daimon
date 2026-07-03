# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Track C extractor — automates Stage 1 (Claimify-style belief extraction) against
a self-hosted LiteLLM gateway. Output is RAW claims; you then merge sessions,
add/verify timestamps, audit is_belief, and label gold/evolution by hand before
running pipeline/run.py. The disambiguation gate is the whole point — it must
drop hedges/hypotheticals/sarcasm rather than mis-extract them.

Setup:
    kubectl port-forward -n <namespace> svc/<litellm-svc> 4000:4000
    export LITELLM_API_KEY=sk-...
    export LITELLM_MODEL=<name>

Run (one session, assign it a timestamp = its order):
    uv run extract.py --session corpus/S1.txt --timestamp 1 --out runs/S1.claims.json
    uv run extract.py --session corpus/S3.txt --timestamp 3 --out runs/S3.claims.json

Then merge the *.claims.json into one runs/<pair>.run.json, label, and:
    uv run pipeline/run.py runs/<pair>.run.json
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))
import llm  # noqa: E402

EXTRACT_SYS = """Extract the user's STABLE beliefs/positions from this conversation. A stable belief is a position the user actually holds — NOT a hypothetical, NOT sarcasm, NOT thinking-aloud, NOT a question.

DISAMBIGUATION GATE — drop, do not extract, anything that is: hedged speculation; a hypothetical/conditional; sarcasm or a joke; a question or request; anything you cannot confidently decontextualize into a standalone claim.

For each belief that PASSES the gate, output an object:
{"subject":"<stable dotted topic key, e.g. auth.architecture>","stance":"<canonical position label; same position -> same string, changed position -> different string>","validity":{"type":"ongoing"},"is_belief":true,"quote":"<exact supporting quote>"}

Use validity {"type":"point"} for an in-the-moment assertion, or {"type":"explicit","start":N,"end":N-or-null} when the user gives an explicit time range ("I've always...", "until last week...").

RULES: subject keys must be STABLE so the same topic links across sessions. Output ONLY a JSON array of claim objects, no prose. When unsure whether something is a stable belief, DROP it. Precision over recall."""


def main() -> int:
    ap = argparse.ArgumentParser(description="Track C Stage-1 extractor")
    ap.add_argument("--session", required=True, help="path to a session transcript")
    ap.add_argument("--timestamp", type=float, required=True, help="monotonic order for this session")
    ap.add_argument("--out", required=True, help="output claims JSON path")
    args = ap.parse_args()

    transcript = Path(args.session).read_text()
    content, usage, model = llm.chat([
        {"role": "system", "content": EXTRACT_SYS},
        {"role": "user", "content": f"SESSION (timestamp {args.timestamp}):\n{transcript}"},
    ])
    try:
        claims = llm.extract_json(content)
        if not isinstance(claims, list):
            raise ValueError("expected a JSON array")
    except Exception as e:
        raw = Path(args.out).with_suffix(".raw.txt")
        raw.write_text(content)
        print(f"Extraction did not return a JSON array ({e}). Raw saved to {raw}", file=sys.stderr)
        return 1

    # stamp each claim with the session timestamp + a provisional id
    for i, c in enumerate(claims, 1):
        c.setdefault("timestamp", args.timestamp)
        c.setdefault("id", f"{Path(args.session).stem}_{i}")
        c.setdefault("is_belief", True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(claims, indent=2))
    print(f"Extracted {len(claims)} claims (model={model}, tokens={usage.get('total_tokens','?')}) -> {out}")
    print("Next: merge *.claims.json into runs/<pair>.run.json, audit is_belief, label gold/evolution, then run pipeline/run.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
