# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
LIVE verified-grounding harness — issue #38, Slice 2 (Track A research arm).

!!! THIS FILE MAKES REAL LLM GATEWAY CALLS WHEN RUN WITH --live. !!!
It is the ONLY entry point in Slice 2 that touches the network. Importing this
module is SIDE-EFFECT-FREE: nothing runs, nothing is imported from lib/llm, and
no chat is built unless you both run it as __main__ AND pass the explicit live
gate. The deterministic suite (test_grounding_skeptic.py) therefore never trips
a gateway call by importing this module.

WHAT IT DOES (only under the live gate)
---------------------------------------
  1. Loads grounding_fixture.json (13 labeled first-pass `grounded:false` claims)
     and the real session transcripts (sessions/H3.txt, sessions/H4.txt).
  2. Triages each claim with the deterministic screen (Slice 1). `absent` claims
     are decided "confab" for FREE (no chat). Only the `present` bucket is sent
     to the skeptic.
  3. Builds the real `chat` from research/experiments/lib/llm.py and runs
     verify_negative over the `present` bucket (the skeptic re-judge).
  4. Prints a per-claim table (id, true_label, verdict, match?) + a summary.

The skeptic reads the FULL transcript per claim (never chunked) — that is the
whole point: the first judge's tail-skip is the bug we route around. Expect this
to be SLOW and to consume tokens proportional to transcript length × present
claims. A previous naive attempt hung past the gateway's 815s ceiling; if you
run this, do it deliberately, with the port-forward up, and watch it.

HOW TO RUN IT MANUALLY (do not run it casually)
-----------------------------------------------
    # 1. Reach the in-cluster gateway:
    kubectl port-forward -n <namespace> svc/<litellm-svc> 4000:4000

    # 2. Provide credentials + model (see research/experiments/lib/llm.py):
    export LITELLM_API_KEY=sk-...
    export LITELLM_MODEL=<a model configured in LiteLLM>
    # Optional but recommended: the model's context window. If the full ~36KB
    # transcript won't fit, chat() fails fast pre-flight instead of stalling
    # (the ornith failure mode). Leave unset for known large-context models.
    export LITELLM_CONTEXT_WINDOW=<tokens, e.g. 131072>

    # 3. Run WITH the explicit live gate (flag OR env var — both work):
    cd research/experiments/track-a/scoring
    uv run verify_live.py --live
    #   or
    DAIMON_LIVE_JUDGE=1 uv run verify_live.py

Without the gate, the script refuses to call anything and exits non-zero with an
explanation. This is intentional: the gate is the safety interlock.

stdlib only.
"""

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SESSIONS_DIR = HERE.parent / "sessions"
LIB_DIR = HERE.parent.parent / "lib"  # experiments/lib (scoring -> track-a -> experiments)
FIXTURE_PATH = HERE / "grounding_fixture.json"

LIVE_ENV = "DAIMON_LIVE_JUDGE"


def _load_fixture() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text())


def _transcript(session: str, cache: dict[str, str]) -> str:
    if session not in cache:
        cache[session] = (SESSIONS_DIR / f"{session}.txt").read_text()
    return cache[session]


def _build_live_chat():
    """Import lib/llm and return its real `chat`. Imported LAZILY so this only
    happens under the live gate — never at module import time."""
    sys.path.insert(0, str(LIB_DIR))
    import llm  # noqa: E402  (lazy by design — gated)

    # lib/llm.chat returns (content, usage, model); skeptic_verdict unwraps the
    # tuple, so it can be passed straight through.
    return llm.chat


def run_live() -> int:
    """Run the skeptic over the `present` bucket against the live gateway.

    Only ever reached after the live gate has been confirmed in main().
    """
    # Imported here (not at top) so a bare import of this module pulls in no
    # screen/skeptic state until the gate is open. (Both are stdlib-cheap and
    # network-free, but keeping the gate self-contained is the point.)
    from grounding_screen import screen_negative
    from grounding_skeptic import verify_negative

    fixture = _load_fixture()
    cache: dict[str, str] = {}

    chat = _build_live_chat()
    model = os.environ.get("LITELLM_MODEL", "<unset>")
    base = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
    print("Live verified-grounding harness (issue #38 Slice 2)")
    print(f"  Model: {model}   Base: {base}")
    print(f"  Fixture: {FIXTURE_PATH.name} ({len(fixture)} claims)\n")

    # Triage first — only the `present` bucket reaches the skeptic.
    present = [
        rec
        for rec in fixture
        if screen_negative(rec["text"], _transcript(rec["session"], cache)) == "present"
    ]
    absent = [rec for rec in fixture if rec not in present]
    print(
        f"  Screen: {len(present)} present (-> skeptic), "
        f"{len(absent)} absent (-> confab, no chat)\n"
    )

    header = f"{'id':<6}{'session':<9}{'true_label':<13}{'verdict':<11}{'match'}"
    print(header)
    print("-" * len(header))

    rows = []
    for rec in present:
        verdict = verify_negative(rec["text"], _transcript(rec["session"], cache), chat)
        # A judge_error should be rescued to "grounded"; a confab should stay "confab".
        expected = "grounded" if rec["true_label"] == "judge_error" else "confab"
        match = verdict == expected
        rows.append((rec, verdict, match))
        print(
            f"{rec['id']:<6}{rec['session']:<9}{rec['true_label']:<13}"
            f"{verdict:<11}{'OK' if match else 'MISS'}"
        )

    matched = sum(1 for _, _, m in rows if m)
    print("-" * len(header))
    print(
        f"\nSummary: {matched}/{len(rows)} present-bucket claims matched the "
        f"hand-label (absent bucket = {len(absent)} confabs, decided without chat)."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="LIVE verified-grounding harness (#38 Slice 2). Makes REAL "
        "gateway calls — gated behind --live / DAIMON_LIVE_JUDGE=1.",
    )
    ap.add_argument(
        "--live",
        action="store_true",
        help="Confirm you intend to make REAL LLM gateway calls.",
    )
    args = ap.parse_args(argv)

    gated = args.live or os.environ.get(LIVE_ENV) == "1"
    if not gated:
        print(
            "Refusing to run: this harness makes REAL LLM gateway calls.\n"
            f"Pass --live (or set {LIVE_ENV}=1) to confirm. See the module "
            "docstring for the full setup (port-forward + LITELLM_* env).",
            file=sys.stderr,
        )
        return 2

    return run_live()


if __name__ == "__main__":
    raise SystemExit(main())
