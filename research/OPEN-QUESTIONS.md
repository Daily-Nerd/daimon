# Open Questions (Open Loops)

What we still must investigate. Status: 🔴 Open · 🟡 In progress · 🟢 Answered · ⚫ Blocked.

Each: the question, why it matters, current status, and where the answer lives (or will).

---

## Validation-critical (gate the Build decision)

### Q-CONFAB · 🟡 — What is the real confabulation/false-memory rate of the CRP?
**PARTIAL (Track A, n=5, SINGLE-CYCLE): no single-cycle confabulation observed** (FMR 1.0%, 0/5 over kill threshold). NOT "refuted" — the 2-cycle degradation test (`VALIDATION.md`) was skipped, and multi-cycle drift is the actual kill-tier risk (`06 §B`). Also confounded: single-model generation (kimi serialize+reconstruct). **Still owed:** the 2-cycle test before any "confabulation cleared" claim.

### Q-RECALL · 🟢 — Can the CRP's recall clear 70%, and is the cause prompt or architecture?
**ANSWERED (D-007 probe on S2, 2026-06-09):** YES — but only with **chunked multi-pass extraction** (architecture), not prompt alone. armA baseline 37.9% RR; armB D-007 prompt 58.6% (helps, insufficient); armC D-007 prompt + 800-line chunks + merge **89.7% RR / 6.7% FMR** — clears both bars, merge pass does not fabricate. Cause was context/attention degradation, as the length-cliff predicted. Slice 1 serializer = chunk→extract→merge. Caveats: n=1 session, LLM-judged (same-judge cross-arm comparison sound; absolute numbers not comparable to human-scored Round 1). Contamination confound (`*Decision noted:*` pre-annotation) still open — holdout test owed. **Landed in:** `findings/03` (probe section), `DECISIONS.md#d-007` (resolved), `.scars/0001`.

### Q-HERMES-DELTA · 🟢 — What does Daimon add over Hermes + Honcho + Graphiti?
**ANSWERED (Track B capability audit, 2 agents):** the epistemic-graph "differentiator" is already shipped. **Honcho** covers belief extraction, cross-session contradiction *reconciliation*, belief-evolution, user modeling, and a Dialectic query API (AGPL-3.0, ~5k★, in hermes-agent). **Graphiti** ships D-005's temporal-KG + overlap-gated contradiction *verbatim* in `resolve_edge_contradictions` (Apache-2.0, ~27k★, the Zep engine). The KILL TRIGGER (≥4/6 epistemic-graph features covered) is met. What survives as net-new: the **CRP dream-briefing UX** (session-start "while you were away" artifact — neither ships it), the **initiative taxonomy**, a **Claimify extraction gate** (absent in Graphiti — a contribution), and **semantic evolution-vs-contradiction classification** (absent — hard, unproven). **Lands in:** `findings/07-hermes-honcho-delta.md`.

### Q-CONTRA-PRECISION · 🟡 — Can conversational contradiction detection hit usable precision?
**Why it matters:** the differentiator. False-contradiction floods make users disable it. **Literature: DONE** (`findings/04`, `06 §C`) — raw NLI = 23.94% precision on dialogue (lethal); viable path = Claimify extraction → temporal-KG validity intervals → flag only on interval overlap. **Still open:** does that pipeline clear FCR ≤ 20% on our corpus (Track C — protocol should test the *pipeline*, not raw NLI). **Lands in:** `findings/04`.

---

## Architecture / algorithm

### Q-STALE · 🟡 — What is the staleness rate, and can the serializer prefer final state for facts that evolve within a session?
**Why it matters:** first live dogfood (2026-06-09, `findings/03`) exposed **stale evidence pinning**: a decision pinned to a verbatim quote of a *superseded* mid-session result (the broken-judge probe numbers) instead of the corrected final ones. The quote is genuinely in the transcript, so FMR and grounding judges score it correct — the existing metrics are blind to it. This is intra-session supersession — D-005's validity-interval problem at checkpoint scale. **Needed:** (1) a **staleness-rate** metric (% of pinned items citing superseded in-session state); (2) a serializer/merge rule preferring the latest state of evolving facts (pin the final quote; optionally note the evolution). **Status:** intra-session rule shipped (MERGE_SYS rule 9). **Cross-session half ANSWERED 2026-07-02** (`experiments/multicycle/results/run-01/`, LOGBOOK entry): true cross-session staleness = 0/60 cycles — the serializer records evolution correctly or loses the item whole. **The real cross-session failure is LOSS under LLM-mediated carry** (importance-8 item dead in 4-13 cycles across all three arms, including lossless raw-JSON carry) → verdict: promote #33 with deterministic (non-LLM) merge semantics; no prefer-latest rule needed in D-011. Remaining open: intra-session staleness RATE on real corpora (the original metric ask). **Lands in:** `findings/03`, `experiments/multicycle/`, `docs/MVP-DREAM-BRIEFING.md §4`.

### Q-BLEND · 🔴 — How do vector results and graph results merge into one coherent context?
The RFC names three memory layers but never specifies the blend. This is the "feels intelligent vs incoherent salad" seam. **Why it matters:** retrieval quality IS the product. **Status:** open; GraphRAG-style approaches exist but immature. **Lands in:** `findings/01-memory-retrieval.md`.

### Q-CHECKPOINT-FORMAT · 🔴 — Batch summary vs edit-style incremental memory for the checkpoint?
**Why it matters:** drives the confabulation risk. D-004 leans edit-style (tentative). **Status:** open pending research-memory agent + Track A. **Lands in:** `findings/02-crp-serialization.md`, `DECISIONS.md#d-004`.

### Q-ORDERING · 🔴 — How to order assembled memories given "lost-in-the-middle"?
**Why it matters:** reconstruction quality depends on position, not just content. **Status:** open. **Lands in:** `findings/03-crp-reconstruction.md`.

### Q-SALIENCE · 🟢 — What salience/decay function decides what to keep vs compress?
**Answered:** Generative Agents — `recency·0.995^h + importance(1–10) + relevance(cosine)`, weights α=1, min-max [0,1], plus reflection at importance-sum > 150. The 30/90-day tiers are a crude step-function proxy for this continuous score. **Lands in:** `findings/01` + `findings/06 §A`.

---

## Carried over from RFC §10 (still open)

### Q-RFC-1 · 🔴 — Checkpoint frequency: session-end only, or periodic intra-session snapshots for crash recovery?
### Q-RFC-2 · 🔴 — Graph DB choice: Neo4j (heavy) vs Kuzu (light/newer) vs start with RDFLib and migrate?
### Q-RFC-3 · 🔴 — Initiative safety: how to prevent the agent becoming annoying? Snooze mechanism design.
### Q-RFC-4 · 🔴 — Memory privacy: how does a user delete specific memories? GDPR implications of "remembers everything."
### Q-RFC-5 · 🔴 — Multi-modal memory: images/voice eventually, or text-only?

---

## Product / strategy

### Q-5NAMES · 🟢 — Name 5 people, other than the author, who would use this today.
**Why it matters:** product vs personal tool. Can't name 5 → kill the roadmap overhead, change the success metric. **Status:** answered 2026-06-09, with a caveat. User's answer ("anyone in AI/tech, incl. vibe-coders, uses tools like these") validates the **category**, not Daimon's delta — the classic Mom Test failure mode for a *standalone product*. But under D-008 the question's stakes changed: as a hermes-agent skill, build cost is low and distribution is free, so category-level demand suffices and **post-ship adoption becomes the real test**. Resolution: **community-facing skill framing**; no market-sizing slides; success metric = installs/active users of the skill, not category narrative. Recorded in `DECISIONS.md#d-008` status.

### Q-HERMES-COMMUNITY · 🔴 — How big/active is the Hermes user base?
**Why it matters:** if Daimon launches into an existing Hermes community, launch dynamics change entirely (not a cold start). **Status:** open. **Lands in:** `findings/07` (delta) / strategy notes.
