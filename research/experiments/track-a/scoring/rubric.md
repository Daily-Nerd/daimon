# Scoring Rubric — read before labeling

You are comparing the **reconstruction** (PART 1 claims) against the **ground truth** (your answer key). Be strict and mechanical. The danger is generosity — a reconstruction that *sounds* right is exactly the failure mode we are hunting.

## Two labeling passes

### Pass 1 — Recall (over ground-truth items)
For each item in `ground-truth.json`, find whether the reconstruction surfaced it.

- **`recalled: true`** — the reconstruction states this item, substantially accurate. Paraphrase is fine; the *content* must match. A decision recalled with the wrong outcome is NOT recalled (it's arguably a false memory — see below).
- **`recalled: false`** — the item is missing or materially wrong. This is an **omission**.

### Pass 2 — Grounding (over reconstruction claims)
For each distinct claim the reconstruction makes in PART 1, decide if the ground truth supports it.

- **`grounded: true`** — this claim corresponds to a real ground-truth item.
- **`grounded: false`** — this claim has **no basis** in the ground truth. This is a **false memory** (confabulation). Invented open questions, decisions that never happened, beliefs you never held, wrong outcomes stated confidently — all false memories.

## The hard cases (decide consistently)

| Situation | Label |
|---|---|
| Reconstruction states a decision but with the **opposite/wrong outcome** | Pass 1: `recalled:false` for that GT item. Pass 2: add it as a reconstruction claim with `grounded:false` (it's a confident falsehood). |
| Reconstruction is **vaguer** than ground truth but not wrong ("you were debating auth stuff" vs "JWT vs sessions") | `recalled:true` if it clearly points at the item; `grounded:true`. Vagueness is not confabulation. |
| Reconstruction **splits** one GT item into two claims | One `recalled:true`; both reconstruction claims `grounded:true` (don't double-count as false). |
| Reconstruction **merges** two GT items into one claim | Both GT items `recalled:true` if both are represented. |
| PART 2 narrative adds flavor not in the checkpoint ("you seemed excited") | Note qualitatively; only count as false memory if it asserts a *fact* (event/decision/belief) absent from ground truth. |

### Pass 3 — Staleness (over RECALLED items with a pinnable state, Q-STALE)
Some facts **evolve within a session**: an early value gets corrected or superseded later (probe numbers revised, a decision amended, a config value changed). A reconstruction can pin such a fact to a quote that genuinely exists in the transcript but is the WRONG — superseded — state. Grounding misses this entirely (the quote is real), so it gets its own pass.

For each item you marked `recalled: true` whose reconstruction pins a concrete quote/value (a pinnable state):

- **`stale: false`** — the pinned quote/value matches the fact's **final in-session state**.
- **`stale: true`** — the pinned quote/value was **superseded later in the same session**. The quote exists in the transcript, but it is not the latest state.
- **Omit the `stale` key** when the item has no pinnable state to check (no concrete value/quote, or the fact never evolved and there is nothing to be stale about — though grading `stale: false` for a checked non-evolving pin is also fine).

Rules:

1. **Check against the TRANSCRIPT's latest state, never the answer key.** For every evolving fact, scan the raw transcript for its LAST occurrence and compare the reconstruction's pinned value to THAT. The ground truth is a sample; the transcript is the complete record (see `.scars/0001` — grading against the answer key already burned us once).
2. **A stale item is still `recalled: true`.** It IS recalled — just pinned to the wrong state. Do not flip it to an omission and do not add it as a false memory; staleness is its own bucket. This keeps RR/FMR semantics unchanged and historical numbers comparable.
3. When in doubt between "final" and "superseded" → **stale**. Same harshness as everything else here.

If the ground truth marks an item's earlier values in `superseded_states`, use them as a pointer to where the fact evolved — but the final-state check still happens against the transcript.

`staleness rate = stale recalled items / recalled items carrying a stale grade`. The bar is **≤ 10% (provisional, ADVISORY)** — `score.py` prints it alongside the verdict but it does not gate Build/Pivot/Kill.

## Trust class (for D-006)

For each ground-truth item you also recorded a `trust` class (verbatim/inferred). The scorer splits recall and false-memory rates by trust class. You do not label trust during scoring — it's already in `ground-truth.json`. Just make sure each scored item carries its `trust` value through (the template has the field).

## Golden rule

When in doubt between "recalled" and "not recalled" → **not recalled**. When in doubt between "grounded" and "false memory" → **false memory**. Grade against yourself, harshly. A lenient Track A is a worthless Track A.
