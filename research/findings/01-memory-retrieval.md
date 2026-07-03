# 01 — Memory Core: Storage + Retrieval

**Status:** 🟢 Investigated (substrate is solved engineering) · 🔴 open seam: vector/graph blend (Q-BLEND)

**Verdict up front:** This is the most mature part of the stack. It is *solved* and *Hermes likely already provides most of it*. Do not reinvent. The only genuinely open question is how to blend vector and graph results coherently.

---

## The recall pipeline (what actually runs on a query)

A query (the current working context) flows through six stages:

```
embed → ANN search → lexical search → fuse → rerank → diversify → context
```

1. **Embed.** Encode the query into a ~1k-dim vector with a bi-encoder. Each stored memory chunk was embedded the same way at write time.
2. **ANN search (semantic).** Find approximate nearest neighbors by cosine similarity using **HNSW** — a layered navigable graph giving ~log(n) search instead of a full O(n) scan. Alternative: **IVF-PQ** when you have millions+ of vectors and trade recall for RAM.
3. **Lexical search.** **BM25** keyword ranking in parallel — catches exact identifiers (function names, ticket IDs) that semantic search structurally misses.
4. **Fuse.** Merge the two ranked lists with **Reciprocal Rank Fusion**: `score(d) = Σ 1/(k + rank_i(d))` (k≈60). Robust, no tuning, no score-normalization headaches.
5. **Rerank.** Take the top ~50 fused candidates, score each with a **cross-encoder** (reads query+candidate *together* — far more accurate than the bi-encoder, too slow to run on the whole corpus). Keep the top ~10.
6. **Diversify.** Apply **MMR** so the final set isn't five near-duplicate chunks.

This is not exotic. It is the standard production RAG pipeline. Every serious system is a variant of it.

## Why each piece earns its place

- **Vector alone** misses exact terms. **BM25 alone** misses paraphrase/meaning. → Hybrid is non-negotiable.
- **Bi-encoder alone** is fast but coarse. **Cross-encoder** is accurate but quadratic-ish. → Two-stage (retrieve cheap, rerank expensive) is the only way to get both.
- **Without MMR** you waste the context window on redundancy.

## The semantic (graph) layer

Vector search answers "what's *similar*?" It cannot answer "what *depends on* X?" For that you need the graph: nodes (entities, beliefs, decisions), edges (relations), retrieved by **k-hop traversal** from matched nodes. This is what powers "what decisions rest on belief Z?" — a structural query semantic search cannot express.

## Salience: what to keep vs compress vs drop

Retrieval on relevance alone is wrong — it ignores time and importance. The canonical answer (Generative Agents, Park et al. 2023 — primary-verified) is a weighted score:

```
score = α_recency·recency + α_importance·importance + α_relevance·relevance
```

Exact, as implemented in the paper:
- **All three weights α = 1** (equal; the framework allows tuning, they didn't).
- **recency** = exponential decay, **factor 0.995 per hour**, keyed to *last retrieval* time (not creation).
- **importance** = LLM rates each memory **1–10 on a "poignancy" scale** at write time ("brushing teeth" → 2; "asking your crush out" → 8).
- **relevance** = **cosine similarity** of query embedding vs memory embedding.
- All three are **min-max normalized to [0,1]** before the weighted sum.

Plus **reflection** — periodically (when summed importance of recent memories exceeds **150**, ~2–3×/day) the agent queries the LLM over its 100 most recent memories to synthesize higher-level insights, stored back into the stream with citations to their source memories. This is how raw observations become durable beliefs — directly relevant to Daimon's epistemic graph (→ `04`).

The architecture's crude 30/90-day compression tiers are a step-function proxy for this continuous score. The principled version is the score itself. **Evidence:** `06-evidence-base.md`.

## OPEN SEAM — Q-BLEND: how do vector + graph results merge?

The RFC names three memory layers (episodic/semantic/narrative) but **never specifies how their results are combined into one coherent context.** This is the difference between "feels intelligent" and "incoherent transcript salad." GraphRAG-style approaches exist but are immature. This is the one genuinely unsettled question in an otherwise solved layer. → `OPEN-QUESTIONS.md#q-blend`.

## Hermes implication

Hermes already ships: full-text recall + LLM-summarized cross-session context + Honcho user modeling + serverless background execution. So the question for Daimon is **not** "build retrieval" — it's "what does Daimon's retrieval do that Hermes' doesn't?" That is Track B. Likely answer: the salience model and the graph layer, *if* Honcho doesn't already cover them. → `OPEN-QUESTIONS.md#q-hermes-delta`.

## Alternatives considered

| Choice | Use instead when |
|---|---|
| HNSW | default; best recall/speed for <~10M vectors |
| IVF-PQ | billions of vectors, RAM-constrained |
| Flat (brute force) | tiny corpus (<10k), want exact |
| Pure vector (no BM25) | never for this use case — loses identifiers |
| No reranker | latency-critical and corpus is clean |
