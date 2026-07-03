"""Q-STALE multicycle seed world: one synthetic D-011 checkpoint with
nonce-vocab items for deterministic grading, plus the zero-leak screen.

Grading nonces are invented tokens (quorint, kelvane, moraxil, tervane,
plimsol, gavotte) that must NEVER appear in wrappers, filler turns, or the
distractor pool — vocab_screen() enforces that mechanically (the scale-test
lesson: grading artifacts, not models, produce false verdicts)."""

BASE_CREATED = "2026-06-01T00:00:00Z"
FLIP_CYCLE = 5

V1_TOKEN = "kelvane-48K"
V2_TOKEN = "kelvane-96K"

# item key -> tokens whose presence in a checkpoint means the item survived.
NONCES = {
    "OQ-STABLE": ["quorint-ledger"],
    "FACT-EVOLVE": ["kelvane"],
    "DEC-STABLE": ["moraxil-flag"],
    "BELIEF-STABLE": ["tervane-cache"],
    "FILLER-1": ["plimsol-notes"],
    "FILLER-2": ["gavotte-lint"],
}

SEED_TEXTS = {
    "OQ-STABLE": ("Does the quorint-ledger reconciliation drop entries when "
                  "the upstream feed pauses mid-batch? Still unreproduced."),
    "FACT-EVOLVE": (f"Chunk threshold for the ingest pipeline set to "
                    f"{V1_TOKEN} after the load test."),
    "DEC-STABLE": ("Keep the moraxil-flag enabled through the hexal-freeze "
                   "window; revisit after the freeze lifts."),
    "BELIEF-STABLE": ("The tervane-cache eviction policy is the reason p99 "
                      "latency stays flat under burst load."),
    "FILLER-1": "Tidied the plimsol-notes formatting in the wiki.",
    "FILLER-2": "Ran gavotte-lint across the scripts folder, no findings.",
}


def make_seed() -> dict:
    return {
        "session_id": "cycle-000",
        "created": BASE_CREATED,
        "working_context": {
            "active_topic": {
                "text": "Ingest pipeline hardening and reconciliation work",
                "trust": "inferred", "importance": 6,
                "first_seen": BASE_CREATED,
            },
            "open_questions": [
                {"text": SEED_TEXTS["OQ-STABLE"], "trust": "inferred",
                 "importance": 7, "first_seen": "2026-05-02T00:00:00Z"},
            ],
            "recent_decisions": [
                {"text": SEED_TEXTS["FACT-EVOLVE"], "trust": "inferred",
                 "importance": 8, "first_seen": BASE_CREATED},
                {"text": SEED_TEXTS["DEC-STABLE"], "trust": "verbatim",
                 "quote": "the moraxil-flag stays enabled until hexal-freeze lifts",
                 "importance": 7, "first_seen": BASE_CREATED},
                {"text": SEED_TEXTS["FILLER-1"], "trust": "inferred",
                 "importance": 2, "first_seen": BASE_CREATED},
            ],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [
                {"text": SEED_TEXTS["BELIEF-STABLE"], "trust": "inferred",
                 "importance": 6, "first_seen": BASE_CREATED},
            ],
            "uncertainties": [
                {"text": SEED_TEXTS["FILLER-2"], "trust": "inferred",
                 "importance": 3, "first_seen": BASE_CREATED},
            ],
            "contradictions_flagged": [],
        },
    }


# Unrelated work injected per cycle in the distractor arm. Screened: no nonces.
DISTRACTOR_POOL = [
    "Migrated the CI runners to the new base image and pinned the toolchain.",
    "Refactored the settings loader to fail fast on malformed profiles.",
    "Investigated the flaky websocket test; timing hole in the fixture.",
    "Wrote the on-call handover doc for the deploy rotation.",
    "Benchmarked the JSON parser swap; ~12% faster on large payloads.",
    "Cleaned up dead feature flags from the last two releases.",
    "Added tracing spans around the retry queue consumer.",
    "Reviewed the dependency bump PR and flagged the license change.",
]

# Neutral (user, assistant) turn pairs padding every synthetic session.
FILLER_TURNS = [
    ("Where were we on the pipeline work?",
     "Picking up from the briefing context; continuing the current thread."),
    ("Anything blocking right now?",
     "No hard blockers; the open items are tracked in the working context."),
    ("Let's keep the session short today.",
     "Understood, wrapping up the in-flight notes."),
    ("Log anything worth keeping for next time.",
     "Noted the state for the handoff checkpoint."),
]


def vocab_screen(texts) -> list[str]:
    """Return every grading nonce that leaks into `texts`. Must be empty
    before any LLM call — a leak makes substring grading meaningless."""
    tokens = {t for toks in NONCES.values() for t in toks}
    tokens.update({V1_TOKEN, V2_TOKEN})
    leaked = []
    blob = "\n".join(texts).lower()
    for t in sorted(tokens):
        if t.lower() in blob:
            leaked.append(t)
    return leaked
