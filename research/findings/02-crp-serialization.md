# 02 — CRP Write Path: Serialization (Checkpointing)

**Status:** 🟢 Investigated — Mem0 mechanics + numbers verified (primary source)

**The job:** at session end, compress a ~20k-token conversation into a small, faithful state object (the cognitive checkpoint, RFC §5.1).

---

## The naive algorithm (what the RFC specifies)

Hierarchical / recursive summarization:
1. **Chunk** the transcript (by turn, or by semantic-boundary segmentation via embedding distance).
2. **Summarize each chunk** → leaf summaries.
3. **Summarize the summaries** → root state.
4. **Extract structured fields** (`open_questions`, `recent_decisions`, etc.) with **constrained decoding** — JSON schema enforced at generation so output always parses.

**What it accomplishes:** lossy compression with *controlled* loss — no single LLM call must hold everything.

**The fatal property:** every level is lossy AND generative. The model doesn't just drop detail — it invents plausible connective tissue. That is the seed of confabulation, and recursive summarization may compound it (summary-of-summary drift). → quantified in `06-evidence-base.md`.

## The better algorithm (2026 state of the art) — D-004

**Edit-style incremental memory** (Mem0 / A-MEM / SimpleMem): instead of regenerating a fresh summary each session, maintain a persistent set of atomic memory notes and apply **insert / update / delete** operations as new information arrives. The old state is *edited*, not *rewritten*.

**Why it reduces drift:** batch summarization re-derives everything from scratch each time (every cycle is a fresh chance to lose a fact — and per `03`, silent omission is the *dominant* confabulation mechanism). Edit-style only touches what changed — stable facts are preserved verbatim, so they can't be silently dropped or re-paraphrased. This is a *structural* defense against the actual measured failure mode.

### Mem0 — the verified mechanism ([arxiv 2504.19413](https://arxiv.org/abs/2504.19413))

Two-phase pipeline, not batch summarization:
1. **Extraction:** an LLM takes (a) a stored conversation summary, (b) the **last 10 messages**, (c) the current exchange → extracts salient candidate facts.
2. **Update:** for each fact, retrieve the **top 10 semantically similar** existing memories, then the LLM picks **one operation**: **ADD** (no equivalent exists), **UPDATE** (augment), **DELETE** (contradicted), or **NOOP**. Fact-granular, not narrative-granular.

**Numbers (LOCOMO benchmark, LLM-as-judge):**
- Overall **66.88** (Mem0) / **68.44** (Mem0 graph variant) vs **52.90** (OpenAI memory) → **+26% relative**.
- **Temporal reasoning: 55.51 vs OpenAI's 21.71** — the biggest gap, directly relevant to belief-evolution (→ `04`).
- **91% lower p95 latency** (1.44s vs 17.1s full-context); **>90% fewer tokens** (~7k vs ~26k).

**Honest caveat (correcting an easy overclaim):** full-context *still scores higher on raw accuracy* (**72.90** vs Mem0's 66.88). Mem0's win is **competitive accuracy at a fraction of the cost/latency** — NOT beating full-context on accuracy. For Daimon: edit-style is the right architecture for drift-resistance and cost, but don't claim it's more *accurate* than just stuffing everything in context (when everything fits). It wins when everything *doesn't* fit — which, over a long relationship, is always.

→ Decision `D-004` is now better-supported but the caveat refines it: edit-style for drift/cost/scale, not for peak accuracy on short histories.

## Alternatives (honest ranking)

| Approach | Confabulation risk | Coherence | Cost | Notes |
|---|---|---|---|---|
| Recursive summarization (RFC) | High (generative each cycle) | High | Med | The naive default |
| **Edit-style incremental** (Mem0/A-MEM) | **Lower** (only edits deltas) | High | Med | 2026 SOTA; D-004 |
| Extractive only (verbatim quotes) | Very low (can't invent facts) | Low (choppy, loses "why") | Low | Safe but cold |
| Hybrid: extractive facts + generative narrative | Low-Med | High | Med | Facts pinned verbatim, narrative generated around them — promising |
| Raw transcript + retrieval (no summary) | None | N/A | High tokens | Defeats the purpose (back to "paste it all") |

**Leading candidate:** hybrid — pin decisions/open-questions as *verbatim extracted* items (no confabulation possible on the load-bearing facts), generate only the connective narrative. Confine the generative risk to the parts where a small error is harmless.

## What this means for the checkpoint schema

The RFC §5.1 schema mixes facts (`recent_decisions`) with vibes (`emotional_valence`). The facts must be extractive/verifiable; the vibes can be generative. The schema should mark each field's *trust class* (verbatim vs inferred) so reconstruction (→ `03`) knows what to trust. **New idea to validate.** → consider for `OPEN-QUESTIONS.md`.

## Validation hook

Track A of the validation plan tests exactly this: serialize 5 real sessions, reconstruct, measure False-Memory Rate. The serialization method (batch vs edit vs hybrid) is the variable most worth A/B-ing inside Track A.

