# 08 — CAP Asset Adoption: What Daimon Should Steal From Context-as-Program

**Status:** 🟡 Proposed (evidence from CAP's M0.3 state benchmark, 2026-06-11, [Daily-Nerd/context-as-program](https://github.com/Daily-Nerd/context-as-program) PRs #2/#3) — verdict: **adopt three CAP assets now; none require unifying the projects.**

D-008 made Honcho + Graphiti a *dependency*, which means their failure modes are now Daimon's failure modes. On 2026-06-11 CAP ran a live, instrumented integration of Graphiti (the exact engine D-008 bets on) and benchmarked it against alternatives on state-tracking with overrides. The results map directly onto two of Daimon's open loops — Q-STALE and "upstream Graphiti contributions" — and onto the D-008 risk consequence ("strategic dependency on third-party projects' roadmaps"). This finding records what transfers and why.

---

## Asset 1 — The M0.3 override/staleness harness (solves Q-STALE measurement)

**Daimon's gap:** the Q-STALE open loop ("evolving facts pin to earliest quote; serializer needs prefer-latest merge rule") has no metric. We know the failure mode exists; we cannot measure whether a fix works.

**What CAP built:** `benchmark/state/` — multi-turn scenarios with authored ground truth where values *change mid-conversation* (budget revised, deadline moved, preference reversed), then probes graded deterministically on whether memory returns the CURRENT value or a stale one. Key metrics: **override accuracy** (current value after change) and **staleness rate** — which is *literally Q-STALE*, already implemented, with a blind-grading pipeline and per-method comparison table.

**Why adopt instead of build:** the harness is ~9k lines of scenario/grading/runner code, already debugged (including a kimi-k2.6 temperature quirk and a stale-value grading fix). Daimon's adaptation cost is an input adapter: replay checkpoint → briefing → re-serialize cycles through the probe set instead of CAP's memory stores. The 2-cycle degradation test already in flight needs exactly this kind of grader.

**Transfer shape:** copy or submodule the harness; do NOT share a repo. The scenario/probe format is the interface.

---

## Asset 2 — Five Graphiti scars (de-risks the D-008 dependency)

CAP's integration spike produced five promoted scars ([context-as-program/.scars/](https://github.com/Daily-Nerd/context-as-program/tree/main/.scars), PR #3). Three are directly load-bearing for any Daimon-side Graphiti integration:

| Scar | Daimon relevance |
|---|---|
| #2 deadend — **default ontology silently drops attribute-style facts** (preferences, budgets, deadlines; no error, fact never exists) | Highest. Daimon checkpoints are dense with exactly this fact class (open loops, decision parameters, user constraints). If the Slice-3 Honcho/Graphiti integration ingests transcripts through Graphiti's default extraction, these facts silently vanish — the *silent omission* failure D-006 was designed to kill, reintroduced one layer down. |
| #5 deadend — **json_schema response_format passes through LiteLLM unenforced**; models drift off schema field names | Any structured-output call routed through the homelab proxy (Daimon's serializer included if it ever moves off direct APIs). Known-good path: json_object mode with schema in prompt. |
| #3 fence — **Graphiti `search()` returns invalidated edges**; bi-temporal stamping is bookkeeping only, consumer must filter `invalid_at` | If Daimon reads beliefs back from Graphiti for briefings, an unfiltered query surfaces superseded facts side-by-side with current ones — a staleness bug injected at read time, invisible until a briefing asserts an old value. |

(#1 embedder contract and #4 kimi temperature transfer too, but only matter if Daimon reuses CAP's exact wiring.)

**The measured cost of scar #2:** with default extraction, Graphiti captured *zero* attribute-style facts in smoke tests ("favorite color is blue → green" produced one entity, no fact edges, with both kimi-k2.6 and claude-haiku-4-5). With the mitigation below, gold recall still reached only 0.786 vs 0.929 for CAP's own extractor at equal budget. Extraction coverage — not temporal logic — is Graphiti's weak surface.

---

## Asset 3 — The `custom_extraction_instructions` mitigation (ammunition for upstream PRs)

CAP's working fix for scar #2: every `add_episode` call passes extraction instructions telling the model to treat stated values (preferences, quantities, deadlines, statuses, decisions) as first-class entities with an owner→current-value fact edge. This took Graphiti from zero attribute facts to functional (override accuracy 0.818).

This is exactly what D-008's "upstream Graphiti contributions (extraction gate)" loop needs: a reproducible failure case, a measured baseline, a working mitigation, and benchmark deltas — a complete upstream issue/PR package. Contributing it serves both projects: Daimon hardens its dependency; CAP strengthens its comparison baseline.

---

## Evidence quality — read before citing

The headline numbers (CAP hybrid 0.909 vs Graphiti 0.818 override accuracy) come from **4 scenarios, ~11 override probes — the delta is one probe**. Treat the *ranking* as unconfirmed until CAP's widened run (≥10 scenarios). The scars, by contrast, are binary observed behaviors (extraction drops facts; search returns invalidated edges) reproduced across two models — those are solid regardless of benchmark n.

## What this finding does NOT argue

Not unification. CAP is pre-validation research (compression claim unvalidated at 2.7x measured vs 50–500x claimed); Daimon is shipped and gated. The convergence trigger stays as decided: CAP becomes a candidate backend only after (1) widened benchmark preserves its edge, (2) it wins on replayed Daimon checkpoints scored on Q-STALE, (3) extraction clears ≥10x compression at ≥85% fidelity. Until then: steal assets across the interface, keep the bets separate.

## Proposed actions

- [ ] Adapt CAP's M0.3 harness as the Q-STALE grader (input adapter over checkpoint replay)
- [ ] Add scars #2/#3/#5 context to the Honcho/Graphiti integration design notes before Slice 3
- [ ] Package scar #2 + mitigation + benchmark delta as an upstream Graphiti issue/PR
- [ ] Re-evaluate this finding after CAP's widened (≥10 scenario) benchmark run
