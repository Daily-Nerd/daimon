# 04 — Epistemic Graph: Belief Extraction + Contradiction (THE DIFFERENTIATOR / GAMBLE)

**Status:** 🟢 Investigated (evidence in hand) — feasibility verdict: **active-research problem, not solved; but there is a viable architecture**

This is the only feature no funded competitor (Letta, Mem0) has shipped — Daimon's real wedge. It is also the riskiest. The evidence below is specific and it changes the design.

---

## The headline finding (primary-verified)

> **NLI models that score 80–93% on contradiction *benchmarks* collapse to 23.94% precision on naturally occurring dialogue.** (DECODE, [arxiv 2012.13391](https://arxiv.org/abs/2012.13391))

A naive "run DeBERTa-MNLI over conversation history" pipeline fires roughly **3 false alarms per true contradiction**. For an agent that tells you "you contradicted yourself," that false-positive rate is **product-lethal** — every wrong flag erodes trust, and the user disables the feature. The contradiction *class* is structurally the weakest part of every NLI model, even on clean legal text.

This is exactly the failure the stress-test predicted (30–40% false contradictions). It is now quantified.

## Why the collapse happens

Contradictions are **rare events** in real dialogue (DECODE: 381 of 8,933 utterances; 95.7% majority-class baseline). Severe class imbalance + long/noisy conversational text destroys precision. The model catches most real contradictions (high recall) but drowns them in false positives.

## The three sub-problems (very different maturity)

The feature decomposes into three, and conflating them is the trap:

| Sub-problem | Maturity | Evidence |
|---|---|---|
| 1. Claim/belief extraction | **Nearly tractable** | Claimify: 99% of extracted claims entailed by source, *with a disambiguation gate that drops hedges/ambiguity* ([arxiv 2502.10855](https://arxiv.org/abs/2502.10855)) |
| 2. Pairwise contradiction between two *clean* claims | **Moderate** | Frontier LLMs 0.71–0.89 on clean pairs; they fail *safe* (high precision, low recall) ([arxiv 2504.00180](https://arxiv.org/abs/2504.00180)) |
| 3. Contradiction over raw, long, multi-turn conversation | **Unsolved core** | 23.94% precision (NLI); self-contradiction within context **0.006–0.456 accuracy** even for frontier models |

**The design lesson:** never operate on raw conversation. Convert it to clean claims first (sub-problem 1, which works), then reason over claims (sub-problem 2, which is OK), and *never* attempt sub-problem 3 directly.

## The temporal insight — belief CHANGE is not contradiction

The hardest conceptual point, and the heart of Daimon's pitch ("hold a mirror to intellectual evolution"): *"I believed X in March, not-X in June"* is **belief evolution, not contradiction.** A naive NLI flags every belief update as a contradiction → false-positive flood.

This is a **recognized, actively-researched problem with usable building blocks:**

- **BeliefShift** ([arxiv 2603.23848](https://arxiv.org/html/2603.23848)) — a longitudinal benchmark that *separates* belief-revision accuracy (legit change) and drift-coherence from contradiction as distinct metrics. Frontier models are mediocre at the judgment: Claude 3.5 Sonnet tops drift-coherence at **0.81** — meaning ~1 in 5 legitimate position-changes is still misjudged. Core trade-off: "models that personalize aggressively resist drift poorly, while factually-grounded models miss legitimate belief updates." RAG helps memory, **not** judgment.

- **Zep / Graphiti** ([arxiv 2501.13956](https://arxiv.org/abs/2501.13956)) — temporal knowledge graph for agent memory with **validity intervals**. When a new belief about the same subject arrives, it **invalidates (supersedes) the old edge with a timestamp** rather than flagging a contradiction. A true contradiction is *only* when two beliefs have **overlapping validity intervals**. Reported: DMR 94.8% (beats MemGPT 93.4%), LongMemEval **+18.5%**, −90% latency.

**This is the unlock.** It converts "is this a contradiction?" (semantically hard, ~24% precision) into "do these validity intervals overlap?" (mostly mechanical, high precision). The RFC §6.5 schema already has `SUPERSEDED_BY` — but the docs treat it as a graph edge, not as the *core precision mechanism*. It is the core precision mechanism.

## The viable architecture (what to actually build)

Do NOT run NLI over conversation. Build this pipeline:

```
1. Claimify-style extraction   → clean, decontextualized claims (drop hedges/sarcasm/hypotheticals at extraction)
2. Temporal KG (Zep/Graphiti)  → store each claim as an edge with a validity interval
3. Supersession-with-timestamp → new claim about same subject invalidates old edge (= evolution, NOT a flag)
4. Flag contradiction ONLY on  → overlapping validity intervals
5. (optional) symbolic/geometric conflict engine (SKG-Eval style) instead of NLI for the final check
```

This is what moves precision from ~24% toward product-viable. Building on raw NLI will not get there.

## Alternatives

| Approach | Precision on dialogue | Verdict |
|---|---|---|
| Raw NLI (DeBERTa-MNLI) over history | ~24% | **Product-lethal. Do not ship.** |
| Frontier-LLM judge over clean pairs | 0.71–0.89 | OK for step 4, fails safe (misses some) |
| Claimify extraction → temporal KG → interval-overlap | High (mechanical final check) | **The path.** |
| Symbolic/geometric engine (SKG-Eval) | Claimed higher, **UNVERIFIED** | Promising, read its tables before betting |

## Validation hook (Track C)

Track C measures Belief Extraction Precision (BEP) and False Contradiction Rate (FCR) on a real corpus. **Refined by this evidence:** Track C should test the *pipeline* (Claimify→temporal-KG→interval-overlap), not raw NLI — testing raw NLI just re-confirms the known 24%. The real question is whether the temporal-KG pipeline clears the FCR ≤ 20% bar. → update `OPEN-QUESTIONS.md#q-contra-precision` and consider revising the Track C protocol in `VALIDATION.md`.

## Feasibility verdict

**Solved? No. Buildable at product precision? Plausibly yes — but only with the temporal-KG architecture, not naive NLI.** This is simultaneously the strongest argument FOR Daimon (the moat is real because it's genuinely hard and no one's shipped it) and the biggest execution risk (get the architecture wrong and it's a pedantic noise machine). The differentiator and the bomb are the same feature — now with a blueprint for defusing it.
