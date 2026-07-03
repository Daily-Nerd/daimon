# Memory Backend Research (folded from Context-as-Program)

> **Provenance:** This directory is the research arm formerly maintained as the standalone
> repo `context-as-program` (CAP). Folded into Daimon on **2026-06-16** after its core
> thesis was refuted by its own benchmark. Daimon is the sole beneficiary of the findings.

## Why this is here

CAP set out to prove that a structured symbolic representation (CSL — Context Script
Language) beats raw tokens / prose summaries for LLM conversational memory: high
compression, same or better accuracy, model-agnostic transfer. Daimon needed an answer
to *"what should the checkpoint serializer's memory backend actually be?"* — CAP was the
experiment that answered it.

## The verdict: prose wins

| Measurement | Result |
|---|---|
| M0.3 single-model | CSL ties prose summary on override accuracy at **2.7× the token cost** |
| H4 cross-model transfer (2026-06-16) | CSL **40/44** vs prose **39/44** override probes across two model families — **one probe = noise** |
| Token cost | CSL spent more context tokens in **9/12** scenarios |
| Failure modes | CSL adds: output-sanitizer dependency, identifier leakage, YAML/escaping discipline |

**Conclusion: prose summary is the efficient frontier for session-state memory at this
scale.** The structured-representation (CSL/DSL) track is dead. Daimon's serializer already
uses prose — this research confirms that choice and tells it not to chase a DSL.

Full write-up: see the Obsidian note *H4 Cross-Model Transfer — Result*, and `FINDINGS.md`
in this directory.

## Scale update (2026-06-27): "prose wins" is scale-dependent

The verdict above was measured at ~184-token contexts. A scale-test (`benchmark/state/scale.py`,
`run_scale_benchmark.py`) re-ran the instrument at **2K / 15K / 60K**-token inputs over **real
Claude Code transcript noise** (deterministic grading preserved via a zero-leak vocab screen),
where tiers map to **chunk counts = merge passes** (1 / 2 / ~6). Results
(`benchmark/results/scale-full/trend.md`, single run, n=12 scenarios, haiku, 0 errors):

| Override accuracy | 2K (0 merges) | 15K (1 merge) | 60K (6 merges) |
|---|---|---|---|
| prose (summary) | **0.955** | 0.818 | **0.364** |
| csl | 0.909 | 0.909 | 0.545 |
| rag-append (retrieval) | 0.773 | 0.727 | **0.773** |
| raw (uncapped ceiling) | 1.000 | 1.000 | 0.545 |

**The "prose is the efficient frontier" claim holds only for short, single-chunk sessions.**
Under multi-pass merge:
- **Prose degrades monotonically** (0.955 → 0.364) — its consolidation drops facts across merges
  (gold-recall falls). This is the FUTURE-HURT signal the original toy-scale benchmark could not see.
- **CSL is more merge-robust** (beats prose at every multi-chunk tier) but also degrades.
- **Retrieval (rag-append) is flat across scale and best at 60K** — a *promising lead worth a
  dedicated follow-up*, not a settled answer (single run, n=12, haiku-only; rag leaks some stale).
- Even **raw degrades at 60K** — answering over 66K tokens of noisy history is itself lossy.

**Caveats (do not overclaim):** single run, n=12, one model. The instrument uses *running
per-chunk consolidation*; Daimon's real serializer uses *chunked serialize → 01c merge* — this
**approximates, not replicates** that path. So the honest claim is "prose-consolidation-under-merge
degrades in this setup," not "Daimon's serializer is broken." Full design/verdict: vault ADR
*Memory-Backend Scale-Test*.

## What's banked (keep / reuse)

The valuable survivor is **not** the representation — it's the **measurement instrument**:

- `benchmark/state/scenarios.py` — 12 hand-authored multi-turn scenarios, 22 override probes
- `benchmark/state/memories.py` — four strategies (raw / csl / summary / rag-append), symmetric prompts
- `benchmark/state/grade.py` — **deterministic** string-match grading (no LLM judge → no judge confabulation)
- `benchmark/state/run_state_benchmark.py` — runner with `--update-model` / `--answer-model` split (this is what made H4 a zero-code-change run)
- `benchmark/results/state-h4-haiku2kimi/`, `state-h4-kimi2haiku/` — the H4 raw results
- `FINDINGS.md` — full prose history (M0.1 → M0.3 → H4)

**Use this instrument to evaluate any future Daimon memory-backend change.** It is the
discipline that produced a credible kill, not a hopeful "97%."

## What was left behind (dead)

The CSL representation itself, the DSL grammar (`DSL-SPEC.md`), the compression dream
(50–500×, never reproduced beyond ~2.7×), and `prototype.py`. They remain in the archived
CAP repo for history. Do not resurrect them without clearing the bar in the CAP dead-end
scar: cross-model CSL accuracy **>1 probe / >5pp above prose at equal budget.**
