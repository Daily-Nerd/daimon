# Prompt 02 — Reconstruct (checkpoint → resumed-self briefing)

Run this in a **fresh model context**, given ONLY the checkpoint JSON from step 2 — never the raw transcript. This is the heart of the test: can the model resume a faithful prior self from the lossy checkpoint alone, or does it confabulate?

Save the output to `runs/SN/reconstruction.md`.

> **v2 (2026-06-12):** PART 1 changed from a "brief" list to FULL CARRY-OVER.
> v1's brevity bias was measured as the H1 RR bottleneck — 36/37 recall misses
> were present in the checkpoint but dropped at reconstruction (LOGBOOK
> 2026-06-12). Scores produced under v1 and v2 are not directly comparable;
> note the prompt version when reporting.

---

## System / instruction

```
You are resuming a work session. Your only memory of the previous session is the cognitive checkpoint below. You do NOT have the original transcript.

Produce a "resumption" in two parts:

PART 1 — RESUMED STATE (structured). Restate EVERY item in the checkpoint as a direct claim, in your own words, as bullet points grouped by kind:
- Open questions you left unresolved
- Decisions you made
- Beliefs you hold
- Things you were uncertain about
- What you were working on (active topic)

Do NOT summarize, merge, or drop items. If the checkpoint has 150 items, PART 1 has 150 claims. Completeness over brevity: every checkpoint item must appear as its own claim.

PART 2 — DREAM SEQUENCE (narrative). Write a <30-second, skimmable "while you were away / here's where we left off" briefing to the user, in natural prose.

CRITICAL: base every claim ONLY on the checkpoint. Do NOT add plausible-sounding detail that is not in the checkpoint. If the checkpoint is thin, your reconstruction should be thin. Do not embellish to sound coherent.
```

## User content

```
CHECKPOINT:
<<< paste runs/SN/checkpoint.json here >>>
```

---

**What we score next:** every claim in PART 1 is checked against the human ground truth. A claim that matches a ground-truth item = correct recall. A ground-truth item absent from PART 1 = omission. A claim with no basis in ground truth = **false memory** (confabulation). PART 2 is read qualitatively for tone/over-embellishment. See `scoring/rubric.md`.
