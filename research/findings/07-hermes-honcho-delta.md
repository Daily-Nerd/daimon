# 07 — Track B: The Hermes + Honcho + Graphiti Delta

**Status:** 🟢 Investigated (2 adversarial capability audits, primary-source/code-level) — verdict: **the stated differentiator is already shipped. Kill the standalone-product framing; the kernel survives as a layer/contribution.**

This is the question that decides whether Daimon is a product. The deep-dive critique was right to demand it before any serializer Round 2.

---

## What already exists

### Honcho ([plastic-labs/honcho](https://github.com/plastic-labs/honcho)) — AGPL-3.0, ~5k★, production, in hermes-agent
Memory infrastructure that models "changing people… over time." LLM-driven deriver extracts deductive/inductive/abductive conclusions into a per-peer **representation**; a **Dreaming** agent fills gaps async; a **Dialectic API** (`.chat()`) answers natural-language queries over the user model. Verbatim from their docs: *"when new information conflicts with old conclusions, it reconciles them instead of just accumulating more data."* Benchmarks ~89.9% LoCoMo at ~5% context. Storage is vector-embedded observation docs (not a graph), linked by `source_ids`.

### Graphiti ([getzep/graphiti](https://github.com/getzep/graphiti)) — Apache-2.0, ~27k★, the Zep engine, prod
Bi-temporal knowledge graph for agent memory. Edges carry `valid_at` / `invalid_at` / `created_at` / `expired_at`. On a changing fact it **invalidates, not deletes**. `resolve_edge_contradictions` gates on temporal overlap: non-overlapping = sequential validity (evolution), overlapping = contradiction → sets `invalid_at`. Paper: arXiv 2501.13956.

---

## Delta map — Daimon MVP feature → existing coverage

| Daimon feature | Honcho | Graphiti | Net-new to Daimon? |
|---|---|---|---|
| Belief/fact extraction from conversation | ✅ Covered | ✅ Covered | No |
| Contradiction detection across sessions | ✅ Covered (reconciles) | ✅ Covered (invalidates) | No — only the *flag-vs-resolve stance* differs |
| Belief-evolution tracking | ✅ Covered | ✅ Covered (temporal) | No |
| Cross-session user/preference modeling | ✅ Covered | 🟡 partial | No |
| Temporal-KG validity intervals (**D-005**) | — | ✅ **Verbatim in shipped code** | No — D-005 *is* Graphiti |
| Query API over the user model | ✅ Dialectic API | 🟡 retrieval | No |
| Hybrid retrieval (semantic+graph) | ✅ | ✅ | No |
| **CRP dream-briefing** (session-start "while you were away") | ❌ Absent | ❌ Absent | **YES** |
| **Initiative taxonomy** (proactive, attention-gated) | ❌ | ❌ | **YES** (Hermes has crude "nudges") |
| **Claimify extraction gate** (high-confidence, decontextualized) | 🟡 reflexion-only | ❌ Absent | **YES** (a contribution to Graphiti) |
| **Semantic evolution-vs-contradiction classification** (kind of change) | 🟡 reconciles, doesn't classify | ❌ temporal-only | **YES** (hard, unproven) |
| Checkpoint versioning / rollback | ❌ | 🟡 (temporal history) | minor |

**Score: ~7 of the ~9 epistemic-graph / memory features are already covered.** The kill trigger (≥4/6 covered → kill standalone) is comfortably met.

---

## Verdict

**The "epistemic graph" is not a differentiator. It is a dependency.** Honcho already delivers the *outcomes* (belief revision, contradiction reconciliation, user modeling); Graphiti already delivers the *mechanism* D-005 describes, verbatim, in code. Building either from scratch is reinventing a funded, shipped, production product — and the substrate (Hermes) that Daimon sits on already integrates Honcho. The PITCH.md "empty quadrant" is false; the moat ("glue") is glue that Honcho/Graphiti/Hermes already poured.

**D-005 is retracted as novel.** It should become "depend on Graphiti" — see decision update.

---

## What actually survives (the kernel)

Four things are genuinely net-new, in descending order of defensibility:

1. **The CRP dream-briefing UX.** A session-*start* artifact — "while you were away: you merged PR #6, the staging deploy OOM'd, you said you'd review the Terraform module — it's tomorrow." Neither Honcho (which answers *queries*) nor Graphiti (a memory store) ships this packaged, skimmable, proactive briefing. This is the one piece the deep-dive independently flagged as valuable, and it was just demonstrated live (the agent lost track of a PR merge — exactly the gap this fills). **Highest-value kernel.**
2. **Initiative taxonomy** — attention-gated proactive interruption (Level 0–3). Hermes has crude periodic nudges; nobody ships the confidence×relevance×attention gating as designed. Tractable (decision theory), `findings/05`.
3. **Claimify-style extraction gate** — genuinely absent from Graphiti (confirmed in `node_operations.py`: no confidence/verification/decontextualization). A real quality improvement — but its natural home is a **PR to Graphiti/Honcho**, not a standalone product.
4. **Semantic evolution-vs-contradiction classification** — Graphiti distinguishes the two only by interval overlap; classifying the *kind* of change (corrected misconception vs. real-world state change vs. refinement) is reasoning neither does. Hardest to copy, but unproven and narrow.

---

## Recommended path (for the user to decide)

Not a standalone product competing with 5k–27k-star incumbents. Instead:

- **Build the dream-briefing as a hermes-agent skill / a thin layer on Honcho.** Ride existing distribution. The CRP work (Track A) feeds this directly — and Track A already proved reconstruction is faithful (the serializer is the part to fix).
- **Contribute the Claimify gate + evolution-classification upstream to Graphiti/Honcho** rather than reimplementing their temporal store.
- **Drop the epistemic-graph differentiator and the market-sizing pitch.** Answer Q-5NAMES; if it's a personal tool, that's fine — but stop writing category-defining slides.

→ proposed `D-008` (strategic pivot) for the user. Track C (the contradiction pipeline) is now largely moot as an original build — it's Graphiti's job; what remains testable is only the Claimify-gate lift, as a contribution.

---

## Caveats / unverified
- Star counts/versions are time-sensitive (Honcho ~5k, Graphiti ~27k @ v0.29.2, 2026-06-08).
- Honcho's *queryable belief-revision history* (vs. silent reconciliation into current state) was the one under-documented seam — the agent flagged it as the only place to probe for a Honcho gap. Verify before claiming it.
- Graphiti code quotes are from WebFetch of `edge_operations.py`/`node_operations.py` @ v0.29.1 — read directly before citing line numbers.
