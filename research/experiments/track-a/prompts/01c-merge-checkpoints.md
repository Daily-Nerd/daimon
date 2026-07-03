# Prompt 01c — Merge Checkpoints (chunked partial checkpoints → unified checkpoint)

This is the merge pass for Arm C of the D-007 probe. Each chunk of the transcript was serialized independently to a partial checkpoint. This pass merges them into a single final checkpoint conforming to the cognitive-state schema.

---

## System / instruction

```
You are merging multiple partial cognitive-state checkpoints produced by chunk-by-chunk serialization of a long session transcript into one final checkpoint.

Output ONLY valid JSON conforming to the schema below. No prose before or after.

MERGE RULES — follow every one exactly:

1. UNION all items across all partial checkpoints. If an item appears in multiple chunks (possibly
   with slightly different wording due to chunked context), keep ONE canonical version — prefer the
   one with trust="verbatim" and a non-empty quote; otherwise prefer the fuller/more specific text.

2. DEDUPLICATE: two items are the same if they refer to the same real-world fact, decision, fix,
   or question. Minor wording differences do NOT make them distinct. Keep one. However, items that differ in SUBSTANCE — different decisions, or different uncertainties, even on the same topic — are NOT duplicates; keep them ALL. Only merge items that assert the same fact.

3. PRESERVE VERBATIM PINS (D-006): if any partial checkpoint has trust="verbatim" with a quote for
   an item, the merged output MUST also set trust="verbatim" and carry that exact quote. Never
   downgrade a verbatim item to inferred during merging. Likewise preserve any
   "external_state": true flag — it marks facts the next session must verify before trusting.

4. CHRONOLOGY: for recent_decisions and worker_queue, order items in the sequence they were made /
   appeared in the session (earliest chunk's items first). This is your best approximation; do NOT
   invent an order.

5. active_topic: pick from the LAST chunk's active_topic — it reflects where the session ended.
   If ambiguous, synthesize a brief inferred summary marked trust="inferred".

6. emotional_valence: take from the LAST chunk; if absent, synthesize as trust="inferred".

7. Do NOT invent items. If something appears only in one chunk, include it as-is. Do NOT discard
   items just because they appear in only one chunk.

8. contradictions_flagged: union across all chunks. If two chunks flag the same contradiction,
   deduplicate (keep one).

9. Output a single JSON object. No explanatory prose, no markdown fences — raw JSON only.

10. SUPERSESSION (staleness): when two partial checkpoints describe the SAME evolving fact at
    different points in the session — a number that was re-measured, a decision that was revised,
    a result that was corrected — keep ONLY the LATEST state (the one from the later chunk), and
    pin the LATEST quote. Do NOT keep the earlier value as a separate item, and do NOT pin an
    early quote to a fact whose final state changed. If the evolution itself matters, note it
    inside the surviving item's text ("X, revised from Y").

11. FINAL-STATE RECONCILIATION ACROSS CHUNKS: If a later partial's recent_decision or belief explicitly answers or supersedes an earlier partial's open_question on the same matter, DROP the open_question and keep the decision. Never the reverse — a later open_question does NOT un-settle an earlier decision unless the transcript explicitly reopened it.

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
session_id: <id>

PARTIAL CHECKPOINTS (JSON array, one per chunk, in chronological order):
<<< paste the JSON array of partial checkpoints here >>>
```

---

**Probe note:** The merge pass is tested in `--self-test` mode with synthetic checkpoints. The quality
of deduplication is what distinguishes a good merge from a lossy one — prefer the verbatim-pinned
version when two items represent the same fact.
