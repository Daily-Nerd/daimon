# 06 — Evidence Base

**Status:** 🟢 Investigated (3 parallel research agents, primary-source-verified, gaps flagged)

This is the consolidated evidence dossier. Every number is from a primary source unless **flagged**. The discipline: separate *proven substrate* from *measured risk* from *unsolved differentiator* — conflating them is how you fool yourself.

---

## A. PROVEN — the substrate works and is shipping

### Salience-weighted memory produces believable continuity
**Generative Agents** (Park et al. 2023, [arxiv 2304.03442](https://arxiv.org/abs/2304.03442)) — the canonical evidence for the dream-sequence concept.
- Retrieval = `recency·0.995^hours + importance(1–10) + relevance(cosine)`, weights α=1, min-max normalized to [0,1].
- Reflection: when summed importance of recent memories > **150** (~2–3×/day), synthesize higher-level insights with citations.
- Ablation (from survey 2603.07670): **remove reflection → agents degenerate from coherent multi-day planning to repetitive context-free responses within 48 simulated hours.** Salience + reflection are load-bearing, not decorative.

### Retrieval-augmented memory beats fixed context on consistency
**MemGPT/Letta** (Packer et al. 2023, [arxiv 2310.08560](https://arxiv.org/abs/2310.08560)) — LLM-as-OS, virtual context paging (70% warning / 100% flush thresholds, recursive summary on eviction).
- Deep Memory Retrieval accuracy: GPT-4 **32.1% → 92.5%** with MemGPT; GPT-4-Turbo 35.3% → 93.4%.
- Conversation-opener engagement SIM-1 **0.857 > human baseline 0.800**.

### Edit-style memory: competitive accuracy at a fraction of cost
**Mem0** (2025, [arxiv 2504.19413](https://arxiv.org/abs/2504.19413)) — ADD/UPDATE/DELETE/NOOP per fact.
- LOCOMO overall **66.88** vs OpenAI memory 52.90 (**+26% rel**); temporal **55.51 vs 21.71**.
- **91% lower p95 latency** (1.44s vs 17.1s), **>90% fewer tokens** (~7k vs ~26k).
- ⚠️ **Honest caveat:** full-context still leads raw accuracy (**72.90 > 66.88**). Mem0 wins cost/latency/scale, not peak accuracy on short histories.

### ANN retrieval is solved engineering
**HNSW** (Malkov & Yashunin, [arxiv 1603.09320](https://arxiv.org/abs/1603.09320)) — layered navigable graph, `l = floor(-ln(U(0,1))·mL)`, `mL = 1/ln(M)`, M 5–48, O(log N) search. Higher recall-at-speed than IVF-PQ (which wins on memory). Operating range ~80% recall @1ms to ~100% @50ms (⚠️ range from Faiss/Pinecone characterization, not the paper's tables).

---

## B. MEASURED RISK — confabulation is real; its *shape* is loss + miscalibration

This is the load-bearing bet's risk profile. See `03-crp-reconstruction.md` for the design implications.

### History size degrades reconstruction 30–64%
**LongMemEval** ([arxiv 2410.10813](https://arxiv.org/html/2410.10813)) — reading full accumulated history vs a clean oracle slice:
- GPT-4o **0.918 → 0.577** (37% drop); Llama-3.1-70B 0.744 → 0.334 (55% drop); commercial memory tools up to **64% drop**.

### Compounding is real — at the task-dependency level
- **MemoryArena** ([arxiv 2602.16313](https://arxiv.org/abs/2602.16313)): multi-session success decays *monotonically* with cross-session dependency depth; "no method maintains a flat region." Passive-recall scores do NOT predict active multi-session reliability.
- **Survey 2603.07670**: models near-perfect on LoCoMo **plummet to 40–60%** on MemoryArena.
- **AMA-Bench** ([arxiv 2602.22769](https://arxiv.org/abs/2602.22769)): best purpose-built memory system ~**57%** on long-horizon recall; **"state abstraction" (summarized state) is the weakest dimension at 0.47** — directly relevant to summary-based reconstruction.

### Recursive summarization compounds via SILENT LOSS, not exploding fabrication
- Multi-doc summarization ([arxiv 2410.13961](https://arxiv.org/html/2410.13961)): hallucination *rate* roughly flat (±5%) as inputs scale, but **recall drops up to 33%**. Up to 45% (news) / 75% (conversation) of content can be hallucinated; models fail to abstain on non-existent subtopics 79% of the time.
- Survey example: a safety instruction ("never call the production DB") **lost after 3 summary cycles** (⚠️ illustrative, not a measured rate).
- ⚠️ **Genuine evidence gap:** a clean "factual-error-per-summarization-round" drift rate is *unmeasured* in the literature (the survey itself confirms this). "Confidently wrong by session 50" is a plausible hypothesis, not an established number → that's what Track A measures.

### Lost-in-the-middle: mid-context state is worse than no state
**Liu et al. 2023** (verified, [ACL PDF](https://aclanthology.org/2024.tacl-1.9.pdf)) — GPT-3.5, 20 docs: start **79.2%**, middle **56.0%**, end 40.2%, **closed-book 56.1%**. Middle < closed-book → buried prior state actively misleads.

### No internal error signal — confident fabrication is the default
**Calibration** ([arxiv 2502.11028](https://arxiv.org/html/2502.11028v1)): ECE **0.45–0.75** on open-ended tasks; self-assessment AUROC **~62.7%** (near 50% random). The model narrates a fabricated session as fluently as a true one. (⚠️ the 62.7% AUROC is secondary-sourced.)

---

## C. UNSOLVED — the differentiator (conversational contradiction)

See `04-epistemic-graph.md` for the architecture. Verdict: **active-research, not solved — but a viable pipeline exists.**

### The precision wall
- **DECODE** ([arxiv 2012.13391](https://arxiv.org/abs/2012.13391)): NLI at 80–93% on benchmark **collapses to 23.94% precision on natural dialogue** (high recall 74%, but ~3 false alarms per true hit).
- **Self-contradiction within a single context: 0.006–0.456 accuracy** even for frontier models ([arxiv 2504.00180](https://arxiv.org/abs/2504.00180)) — the single hardest category.
- ContractNLI: contradiction-class F1 **0.357** vs entailment 0.834, even on clean legal text.

### Extraction is the tractable half
- **Claimify** ([arxiv 2502.10855](https://arxiv.org/abs/2502.10855)): **99%** of extracted claims entailed by source, *with a disambiguation gate that drops hedges/ambiguity.* This is the front-end that converts noisy conversation into clean claims. (⚠️ no paper reports a clean precision number for rejecting sarcasm/thinking-aloud specifically.)

### The temporal unlock — evolution ≠ contradiction
- **BeliefShift** ([arxiv 2603.23848](https://arxiv.org/html/2603.23848)): belief-revision accuracy and drift-coherence are *separate metrics* from contradiction. Best (Claude 3.5) drift-coherence **0.81** → ~1 in 5 legit position-changes still misjudged. RAG helps memory, not judgment.
- **Zep/Graphiti** ([arxiv 2501.13956](https://arxiv.org/abs/2501.13956)): temporal KG with **validity intervals**; new belief *supersedes* old edge with a timestamp; contradiction flagged *only* on **overlapping** intervals. DMR **94.8%**, LongMemEval **+18.5%**, −90% latency. **This converts a 24%-precision semantic problem into a mostly-mechanical interval-overlap check.**
- ⚠️ **SKG-Eval** ([arxiv 2605.16650](https://arxiv.org/abs/2605.16650)) symbolic/geometric conflict engine — architecturally aligned but its precision/recall tables were **unverifiable** (truncated). Read Section 5 before betting on it.

---

## D. Verification gaps (what we do NOT know)

Honest ledger — do not cite these as established:
- "Factual error per summarization round" drift rate — **genuinely unmeasured** in the literature.
- Per-session-count accuracy curves for AMA-Bench / PERMA / MemoryArena — described as figures, exact tabular numbers not extracted.
- SKG-Eval precision/recall — truncated in all fetches.
- Calibration ECE from older papers (2306.13063, 2405.02917) and the 62.7% AUROC — secondary-sourced.
- A-MEM mechanism (arxiv 2502.12110) and "SimpleMem" — **not primary-verified**; SimpleMem may not exist under that exact name. Do not cite with numbers.
- Claimify precision against sarcasm/hypotheticals *as a category* — only the 99% source-entailment figure is confirmed.

---

## E. The synthesis (what the evidence says about Daimon)

| Bet | Evidence verdict | Implication |
|---|---|---|
| Memory storage/retrieval | **Proven, shipping** | Don't reinvent; likely Hermes already has it (Track B) |
| CRP serialization | **Edit-style proven for cost/scale** | Use Mem0-style ops, not batch summary (D-004) |
| CRP reconstruction | **Risk real; shape = loss + miscalibration** | Pin facts extractively; order for edges; MEASURE (Track A) |
| Epistemic graph | **Unsolved on raw NLI; viable via temporal-KG** | Build Claimify→temporal-KG→interval-overlap, NOT NLI; MEASURE (Track C) |
| Initiative | **Mature decision theory** | Lowest risk; EV gate → bandit |

**The pattern holds, now with numbers:** everything except the epistemic graph is assembled from proven parts. The novelty — and the risk — concentrate in exactly the two boxes the validation plan measures (A and C). The evidence didn't soften the verdict. It sharpened it and handed us the architectures (extractive-pinning, temporal-KG validity intervals) that give those two bets a real chance.
