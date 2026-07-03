# Prompt 01 — Extract (conversation → clean, temporal claims)

This is **Stage 1** of the pipeline: a Claimify-style extraction with a *disambiguation gate*. The gate is the whole point — it must DROP hedges, hypotheticals, sarcasm, and thinking-aloud rather than mis-extract them as beliefs. Evidence: raw extraction on conversational text is where precision dies ([`../../findings/04`](../../findings/04-epistemic-graph.md)).

Run per session. Output JSON claims; you then merge claims across the two sessions into one run file, add `timestamp`s, and hand-verify before scoring.

---

## System / instruction

```
Extract the user's STABLE beliefs/positions from this conversation. A stable belief is a position the user actually holds — NOT a hypothetical, NOT sarcasm, NOT thinking-aloud, NOT a question.

DISAMBIGUATION GATE — drop, do not extract, anything that is:
- hedged speculation ("maybe we could...", "I guess...")
- a hypothetical or conditional ("if we had more time, X")
- sarcasm or a joke
- a question or a request
- something you cannot confidently decontextualize into a standalone claim

For each belief that PASSES the gate, output:
{
  "subject": "<stable dotted key for the topic, e.g. auth.architecture, db.choice>",
  "stance": "<short canonical label for the position; SAME position across sessions MUST get the SAME stance string; a different position gets a different string>",
  "validity": {"type": "ongoing"},   // ongoing = held from now on; or "point" for an in-the-moment assertion; or {"type":"explicit","start":N,"end":N|null} when the user gives an explicit time range ("I've always...", "until last week...")
  "is_belief": true,
  "quote": "<exact supporting quote>"
}

RULES:
- subject keys must be STABLE: the same topic in session 1 and session 3 must share the exact same subject key, or the temporal logic cannot link them.
- stance strings encode the POSITION: same opinion -> same string; changed opinion -> different string.
- Output only JSON: a list of claim objects. No prose.
- When unsure whether something is a stable belief, DROP it. Precision over recall.
```

## User content

```
SESSION <n> (timestamp <n>):
<<< paste one session transcript here >>>
```

---

## After extraction (human, ~15 min)

1. Merge the claim lists from both sessions into one `runs/<pair>.run.json` under `extracted_claims`.
2. Add a `timestamp` to each claim (session order is enough: session-1 claims < session-3 claims; preserve within-session order).
3. Give each claim an `id` (c1, c2, …).
4. **Audit `is_belief`:** for every extracted claim, confirm it's a genuine stable belief. If the gate let noise through (a hedge/hypothetical/sarcasm), set `is_belief: false` — that counts against BEP.
5. Label `gold_contradictions` (pairs that genuinely conflict at the same time) and `evolution_pairs` (same subject, position legitimately changed over time). See [`../scoring/rubric.md`](../scoring/rubric.md).
6. Run `uv run pipeline/run.py runs/<pair>.run.json`.
