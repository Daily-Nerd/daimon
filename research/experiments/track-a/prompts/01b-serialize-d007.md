# Prompt 01b — Serialize D-008 (raw session → cognitive checkpoint, enhanced extraction)

This is the D-008 revision of `01-serialize.md`. It extends the baseline serializer to capture the full assistant-side execution layer — fixes, diagnoses, implementation decisions — which Track A showed were silently dropped on long sessions (recall cliff at ~1,400 lines).

**Do not use this prompt until the baseline (01) run exists for comparison.**

---

## System / instruction

```
You are ending a work session and must serialize your cognitive state into a strict JSON checkpoint, so a future session can resume.

Output ONLY valid JSON conforming to the schema below. No prose before or after.

RULES — follow every one exactly; this is the point of the exercise:

1. Extract only what the transcript supports. Do NOT invent open questions, decisions, beliefs, or facts not actually present.

2. For every item, set `trust`:
   - "verbatim" → directly supported by an explicit statement. You MUST include the exact `quote` from the transcript.
   - "inferred" → you are paraphrasing or synthesizing. Leave `quote` empty.
   Prefer "verbatim" wherever an explicit statement exists.

3. open_questions = things left genuinely unresolved at end. recent_decisions = explicit choices made.
   Be exhaustive on BOTH — they are load-bearing.

4. strong_beliefs / uncertainties = stated positions and stated doubts. Do NOT extract hedges, hypotheticals, sarcasm, or thinking-aloud as beliefs.

5. emotional_valence is necessarily inferred; acceptable for that single field.

6. If unsure whether something belongs, leave it out. Omission is safer than fabrication.

--- D-007 EXTRACTION TARGETS (new) ---

7. ASSISTANT-SIDE FIXES & DIAGNOSES: When the assistant diagnosed a bug, root-caused a failure, or
   applied a fix, extract these as recent_decisions and/or beliefs — even if the USER never explicitly
   stated them. Label clearly: use the prefix "[Fix]" or "[Diagnosis]" in the text.
   Include: what was broken, what the root cause was, and what fix was applied.
   Quote the most direct statement from the transcript (the AI's own diagnosis line if present).

8. IMPLEMENTATION-LEVEL DECISIONS: Extract decisions that were made DURING implementation —
   function names, data structures chosen, algorithmic approaches, library choices, test strategy
   (e.g. "used UseStateForUnknown on six attrs", "AlertSuppressor keyed on (rule_id, device_id, dst_ip)").
   These appear in assistant turns, not just user-stated choices. Include them in recent_decisions.

9. OPEN END-OF-SESSION QUESTIONS & LOOSE THREADS: Beyond explicit user questions, scan for:
   - Things the assistant said it would do "next" or "after"
   - Verifications that did not happen (e.g. "pending the user rebuilds and pastes the plan")
   - Optional follow-ups explicitly flagged (e.g. "optional, separate")
   - Anything left ambiguous or deferred to the next session
   Add these to open_questions with trust="verbatim" if quoted, "inferred" if synthesized.

10. PRESERVE D-006 EXTRACTIVE PINNING: For every decision, fix, and open question that has a
    direct quote, you MUST set trust="verbatim" and include that exact quote in the `quote` field.
    Never paraphrase when a direct quote exists.

11. EXTERNAL-STATE FLAG: For any open_question whose answer could have changed OUTSIDE this
    session (a PR the user said they'd merge, a deploy, a file edited elsewhere, an action the
    user took in another tool), add `"external_state": true` to that item. This marks facts the
    next session MUST verify before trusting.

12. FINAL-STATE RESOLUTION: Classify every item by its LAST state in the transcript, not its first. If something raised as an open question earlier is explicitly answered or chosen later — INCLUDING by a terse user ratification ("yes", "go with X", "do it", "sounds good") that covers one or more proposals — record it as a recent_decision, NOT an open_question. Do NOT invent a resolution: promote to a decision only when the transcript explicitly settles it; if it was merely discussed and left hanging, it stays an open_question.

13. DISTINCT ITEMS — DO NOT MERGE: Two decisions, or two uncertainties, that differ in substance are SEPARATE items even when they share a topic. One dropped product idea is not another dropped product idea; a platform you skipped is not an unresolved API-approval for that platform. Extract each distinct choice or doubt as its own item; never collapse several into one summary line.

14. EXACT QUANTITIES & IDENTIFIERS: Copy counts, file ranges, version numbers, commit hashes, ports, and identifiers EXACTLY as the transcript states them (17 files / docs 01-17 / commit 2e1d78b / port 6638 — never "about 15" or "several"). Never round, approximate, or drop a precise quantity the transcript states.

Schema shape:
{
  "session_id": "<id>",
  "working_context": {
    "active_topic": {"text": "", "trust": "", "quote": ""},
    "open_questions": [{"text": "", "trust": "", "quote": "", "external_state": false}],
    "recent_decisions": [{"text": "", "trust": "", "quote": ""}],
    "emotional_valence": {"text": "", "trust": "inferred"}
  },
  "epistemic_snapshot": {
    "strong_beliefs": [{"text": "", "trust": "", "quote": ""}],
    "uncertainties": [{"text": "", "trust": "", "quote": ""}],
    "contradictions_flagged": []
  },
  "worker_queue": []
}
```

## User content

```
session_id: S2

TRANSCRIPT:
<<< paste the full raw session here >>>
```

---

**D-007 note:** The key test is whether explicit assistant-side fix/diagnosis lines (e.g. "Root cause: ...", "The fix: ...", "I'll commit once you paste the plan") get promoted into `recent_decisions` and `open_questions` — not dropped as narrative filler. Compare arm B's checkpoint against arm A to see which GT items are newly captured.
