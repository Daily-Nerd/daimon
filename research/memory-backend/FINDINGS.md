# Findings & Results

## Prototype Run (2026-05-30)

### Setup
- **Compressor:** Heuristic-based (regex + keyword matching) — intentionally dumb to establish a baseline
- **Test data:** Two synthetic conversation segments about infrastructure migration (~514 raw tokens total)
- **Query engine:** Keyword extraction + JSON field matching

### Results

#### Compression
| Metric | Value |
|--------|-------|
| Raw conversation tokens | ~514 |
| CSL program tokens | ~194 |
| **Compression ratio** | **2.7x** |
| Statements extracted | 19 (after merge) |

**Important caveat:** This is with a naive heuristic compressor. A real LLM-based extractor should achieve **10–500x** compression by inferring implicit relationships, collapsing redundant statements, and using higher-level abstractions.

#### Information Captured
The CSL program successfully encoded:
- **2 Preferences:** avoids Python, prefers Go
- **5 Facts:** $2.4M infra cost, 30% budget cut, 6-month timeline, 12-person team, CTO supportive
- **1 Relation:** User skeptical of Kubernetes
- **1 Intent:** Migrate off Kubernetes (15% progress, 3 blockers)
- **1 Event:** Tense meeting with CFO/CTO, approved 30% cut
- **2 Unresolveds:** Orchestrator choice, team retraining
- **1 Summary:** Q1 theme = budget pressure + migration
- **1 Rule:** Suggest Go/Rust before Python

#### Query Accuracy
| Question | Relevant Statements Found | Correct? |
|----------|--------------------------|----------|
| "What does the user think about Kubernetes?" | RELATION (skeptical), INTENT (migrate) | ✅ |
| "What is Project Alpha's budget situation?" | FACT (30pc cut), FACT (6mo), EVENT (meeting) | ✅ |
| "What programming language does the user prefer?" | PREFERENCE (avoids Python, prefers Go), RULE | ✅ |
| "What happened in the meeting?" | EVENT (tense meeting), FACT (budget cut) | ✅ |
| "What are the current unresolved questions?" | SUMMARY (open questions mentioned) | ⚠️ Partial — didn't return UNRESOLVEDs directly |

**Hit rate:** 4/5 questions returned relevant statements. The fifth missed because "unresolved" as a keyword matched SUMMARY but not the UNRESOLVED statements themselves (the word "unresolved" doesn't appear in their fields).

#### Merge Behavior
Merging two conversation segments:
- **Before merge:** 15 + 7 = 22 statements
- **After merge:** 19 statements (3 deduplicated)
- **Deduplication:** Basic subject+predicate matching for FACTs worked. But duplicate RELATIONs, EVENTs, and SUMMARYs were not caught.

**Gap:** The merge engine needs similarity scoring, not just exact matching.

---

## M0.3 State-Tracking Benchmark (2026-06-11 / 2026-06-12)

### Setup
- **Scenarios:** 12 hand-authored multi-turn conversations with known state evolution (widened from 4 on 2026-06-12), 22 override probes across diverse shapes: double-override chains, reverts (A→B→A), entity swaps, late overrides, scoped sibling-preserving changes, numeric drift, status flips, rename+ownership transfers (`benchmark/state/scenarios.py`)
- **Arms:** `raw` (uncapped ceiling), `csl` (structured DSL consolidation), `summary` (prose consolidation, same model/budget — isolates structure vs prose), `rag-append` (naive retrieval), `graphiti` (temporal-KG adoption arm: Zep engine, bi-temporal edge invalidation, FalkorDB backend)
- **Models:** kimi-k2.6 for update, extraction, and answering. Budget 300 tokens for consolidating arms
- **Grading:** deterministic against authored ground truth; `_`/`-` normalized to spaces (structured memories leak identifier tokens like `billing_revamp` into answers — semantically gold, regex `\b` missed them). Regrades are offline via `benchmark/state/regrade.py`

### Results (regraded, `benchmark/results/state-kimi-wide/report-regraded.md`)

| Method | Overall acc | Override acc | Staleness | Gold recall | ~ctx tok |
|--------|-------------|--------------|-----------|-------------|----------|
| raw | 1.000 | 1.000 | 0.000 | 1.000 | 184 |
| **summary** | **1.000** | **1.000** | **0.000** | **1.000** | **59** |
| csl | 0.974 | 0.955 | 0.000 | 0.974 | 1922 ⚠️ |
| graphiti | 0.789 | 0.864 | 0.045 | 0.789 | 57 |
| rag-append | 0.868 | 0.818 | 0.136 | 0.895 | 300 |

### Verdicts

1. **Prose summary beat CSL — and everything else.** Perfect override accuracy at 59 tokens. The simplest consolidation baseline won outright. At 4 scenarios (2026-06-11 run) CSL and summary tied 0.909; widening to 22 probes separated them decisively.
2. **CSL beats Graphiti on overrides** (0.955 vs 0.864) — the adoption-arm verdict survived widening (was a 1-probe gap, now 2). Graphiti's weakness is extraction coverage (gold recall 0.789: facts never enter the graph), not its temporal invalidation, which works.
3. **CSL blew its token budget 6.4×** (1922 vs 300) on 5/12 scenarios. Root cause: kimi-k2.6 intermittently prefixes chain-of-thought to its completion; prose summary absorbs that harmlessly (any prose is a valid summary) but the CSL pipeline stores the whole blob — reasoning is not valid DSL and nothing strips it.

### CSL-specific fragilities found (prose doesn't pay these costs)

1. **Reasoning-leak blowup** — invalid DSL has no graceful degradation. Fix candidate: sanitize consolidation output to statement lines only.
2. **Identifier leakage** — answer models parrot DSL tokens (`Whitaker_Trust`) instead of natural language. Grading now compensates; real consumers would see it.

### Follow-up: format validation closes the gap (2026-06-12, `state-kimi-wide-sanitized/`)

`CslMemory` now keeps only parseable `TYPE(...)` statement lines from each
consolidation — enforcing the contract the prompt already states. This is
structure's genuine advantage: DSL output is validatable, prose is not.
Rerun (graphiti arm unchanged, omitted):

| Method | Override acc | Gold recall | ~ctx tok |
|--------|--------------|-------------|----------|
| raw | 1.000 | 1.000 | 184 |
| csl (sanitized) | 1.000 | 1.000 | 161 |
| summary | 1.000 | 1.000 | 59 |

The verdict softens from "prose beats CSL" to "**CSL ties prose on accuracy
at 2.7× the token cost**" — and CSL's 161 tokens barely undercut raw's 184 at
these scenario lengths, so the compression story is empty here too. One
scenario (vendor-selection) still ignored the budget instruction with 1099
tokens of valid statements. Structure no longer loses, but buys nothing prose
doesn't; H4 cross-model transfer is the deciding experiment.

### Operational notes
- One flaky extraction (model echoing the JSON schema back) killed an entire 2h run; `GraphitiMemory.observe()` now retries with cache-busting instruction suffixes (the LiteLLM proxy caches by prompt, so plain retries replay the same bad response) and skips the turn on exhaustion.
- Full run is ~2h cold, ~25 min proxy-cache-warm.

---

## Key Insights

### What Worked
1. **Structured primitives are expressive.** 15 statements captured the essence of a long, nuanced conversation.
2. **Queryability is real.** Unlike raw text where you hope attention lands in the right place, CSL statements are addressable.
3. **Merge is conceptually sound.** Differential updates to memory without full rewrites.
4. **Human readability.** A human can read the CSL and understand what the model "knows."

### What Didn't Work
1. **Heuristic extraction misses nuance.** The mock compressor only catches explicit statements. It misses:
   - Implicit preferences ("I'm tired of X" → prefers_not_X)
   - Emotional undertones
   - Multi-hop inferences ("CTO supportive" + "CFO pushed back" → political tension)
2. **Merge is too naive.** Duplicates pollute memory. Need learned similarity scoring.
3. **Query matching is brittle.** Keyword-based retrieval fails when questions don't match field names.
4. **No reconstruction validation.** We can retrieve statements, but we haven't proven an LLM can generate *good answers* from CSL alone.

---

## Hypotheses to Test

### H1: LLM extraction achieves >10x compression with >85% fidelity
**Test:** Feed 50 long conversations to GPT-4o/Claude, extract CSL, measure token reduction.  
**Status:** Untested. High confidence based on LLM capability.

### H2: LLM answers from CSL are comparable to answers from raw context
**Test:** Blind QA benchmark. Grade answers from (a) raw context vs. (b) CSL only.  
**Status:** Partially tested via M0.3 (2026-06-12): CSL answers reached 0.974 overall vs raw's 1.000 — but only by exceeding its token budget 6.4×, and prose summary matched raw exactly at 59 tokens. The comparison that matters is no longer CSL-vs-raw; it's CSL-vs-prose, and CSL lost.

### H3: CSL is incrementally updatable without degradation
**Test:** Simulate 10 conversation turns. Compress each, merge into memory, query after each.  
**Status:** Tested via M0.3 (12 scenarios, 9–13 turns each, per-turn consolidation). Accuracy held (0.955 override) but token budget did NOT — reasoning-leak blowup on 5/12 scenarios. Incremental update degrades cost, not accuracy.

### H4: CSL enables cross-model memory transfer
**Test:** Extract CSL with Claude, load into GPT-4o, verify it answers correctly.  
**Status:** Untested. If true, this is a major differentiator — and after M0.3 it is the main surviving reason to prefer structure over prose. Note the test must include a prose-summary transfer arm: if prose transfers equally well, structure has no remaining accuracy case.

---

## Comparison to Related Work

| Work | Approach | Similarity to CaP |
|------|----------|-------------------|
| **LLMLingua** (Jiang et al., 2023) | Compress prompts with smaller LLM | Different: still outputs text tokens |
| **H2O** (Zhang et al., 2023) | Heavy-hitter KV cache eviction | Different: drops info blindly |
| **MemGPT** (Packer et al., 2023) | OS-inspired memory management | Similar: structured memory, but stores raw text |
| **RWKV/Mamba** | Fixed-size recurrent state | Different: opaque vector, not interpretable |
| **Neural Turing Machine** (Graves et al., 2014) | Differentiable external memory | Similar spirit, but continuous memory, not symbolic |
| **Gist Tokens** (Mu et al., 2024) | Learned soft prompt compression | Similar: compressed representation, but not structured/interpretable |

**Novelty claim:** Context-as-Program is the first to propose a **structured, symbolic, executable DSL** as a general-purpose context compression mechanism for LLMs. Previous work either compresses text-to-text (LLMLingua), drops information (H2O), or uses opaque vectors (Mamba, Gist).

---

## Open Questions

1. **What is the fidelity floor?** At what compression ratio does critical nuance get lost? Is it task-dependent?
2. **Can the DSL be learned rather than hand-designed?** A learned latent DSL might be more expressive but less interpretable.
3. **How does CSL interact with tool use?** If the model calls tools, should tool outputs be stored as CSL or raw text?
4. **What about code contexts?** A codebase has different structure than conversation. Would CSL primitives need to change?
5. **Security implications.** If CSL is interpretable, it's auditable. But can it be poisoned more easily than embeddings?

---

*Last updated: 2026-06-12 | Next update after H4 cross-model transfer benchmark*
