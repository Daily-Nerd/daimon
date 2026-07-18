# Validation Plan: Prove the Bet Before You Build

> ## ✅ RESULTS — gate ran, outcome = D-008 pivot
>
> This document is the **historical gate definition** (left intact below). The gate was executed; here is what each track returned.
>
> - **Track A (CRP confabulation) → PIVOT.** n=5 real sessions, single-cycle: **RR 67.3%, FMR 1.0%** (`research/findings/03`). Confabulation was *not* the problem (max FMR 4.8%); **recall** is, and it tracks transcript **length** (sharp cliff ~1,400 lines), not "messiness." Caveats: **single-cycle only** (the specified 2-cycle degradation test was skipped — multi-cycle drift untested), and **length-confounded** (single model = kimi-k2.6; corpus pre-annotated, true floor likely below 55%). "Confabulation refuted" was an overclaim and is retracted. The serializer recall fix carries forward as **D-007**.
> - **Track B (Hermes/Honcho delta) → KILL trigger met; differentiator already shipped.** ~7 of ~9 epistemic-graph / memory features are already covered by Honcho + Graphiti (`research/findings/07`); the ≥4/6-covered kill threshold is comfortably met. The "epistemic graph" is a **dependency, not a differentiator**. What survives net-new: the **dream-briefing UX**, the **initiative taxonomy**, and the **Claimify extraction gate** (an upstream contribution).
> - **Track C (epistemic-graph false-positive spike) → moot as an original build.** The temporal-KG pipeline this track was meant to validate is **Graphiti's job** — it ships the validity-interval + overlap-gated contradiction mechanism verbatim (**D-005 retracted as novel**, `findings/07`). Track C as written was never run as a build validation; the only piece that remains testable is the **Claimify-gate lift**, as a contribution, not a product.
>
> **Gate outcome → [D-008](https://github.com/Daily-Nerd/daimon/blob/main/research/DECISIONS.md) pivot (user-approved 2026-06-09):** standalone epistemic-graph product → **dream-briefing skill** on hermes-agent + Honcho, with upstream contributions to Graphiti. **Superseded again by [D-009](https://github.com/Daily-Nerd/daimon/blob/main/research/DECISIONS.md)** (2026-06-27) before that framing shipped: the runtime that actually ships is **self-contained and host-agnostic**, with no Honcho/Graphiti runtime dependency. Current authoritative architecture: **[MVP-DREAM-BRIEFING.md](https://github.com/Daily-Nerd/daimon/blob/main/docs/MVP-DREAM-BRIEFING.md)**. The plan below is **not rewritten** — it is the gate that produced this verdict.

**Status:** ✅ Executed — outcome = D-008 pivot (see banner above). The plan below is the historical gate definition.
**Gate type:** Go / No-Go. No Phase-1 code ships until this completes.
**Team size:** 1–2 people, self-hosted.
**Calendar:** 10 working days.

---

## Why This Exists

Three independent strategic reviews of the Daimon concept docs converged on the same conclusion: the project rests on one load-bearing technical bet that has never been empirically tested, and the "80% infra already built" claim — now confirmed to mean [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — cuts both ways. Hermes already ships persistent cross-session memory, a cron scheduler, a multi-platform gateway, and Honcho-based user modeling. That makes the real question not *"can we build the infrastructure?"* but *"what does Daimon add over raw Hermes + Honcho?"*

This plan answers both questions in two weeks instead of discovering them six weeks into a build.

### The three findings driving this plan

1. **The load-bearing bet is the Cognitive Resumption Protocol (CRP).** Serialize cognitive state at session end → reconstruct at session start. Everything else feeds into or out of that moment. The pitch itself admits "the hard part is the CRP."
2. **The unflagged killer risk is resurrection confabulation.** A model summarizes a model-generated conversation; another model reconstructs the "prior self" from that summary. The measured failure mode (see [`research/findings/03`](https://github.com/Daily-Nerd/daimon/blob/main/research/findings/03-crp-reconstruction.md)) is *silent information loss* + *no internal error signal* (calibration ECE 0.45–0.75) + history-size degradation (30–64%), with no ground-truth validation specified anywhere in the RFC.
3. **The epistemic graph is both the differentiator and the bomb.** It is the only feature neither Letta nor Mem0 has shipped — but raw NLI contradiction detection scores just **23.94% precision on natural dialogue** ([DECODE](https://arxiv.org/abs/2012.13391)). The viable path is a temporal-KG pipeline ([`research/findings/04`](https://github.com/Daily-Nerd/daimon/blob/main/research/findings/04-epistemic-graph.md)), which Track C now tests. Crown jewel and most-likely-to-feel-broken are the same feature. Honcho may already cover part of it.

---

## Out of Scope (Forbidden for These 2 Weeks)

- No production CRP implementation. Track A serialization is manual/throwaway.
- No epistemic graph productionization. Track C is a throwaway script, not a component.
- No Hermes fork, patch, or extension. Track B is read-only observation.
- No multi-user architecture decisions.
- No API surface design or schema finalization.
- No UI, frontend, or demo prep.
- No publishing, blogging, or community announcements.
- No hiring or resourcing decisions.

Violating any of these means you are building, not validating. Stop.

---

## Track A — CRP Confabulation Experiment

**Falsifiable question:** Does the CRP produce accurate reconstructions, or does it hallucinate a confident-but-wrong "prior self" by the time you need it most?

### Protocol

1. **Source material.** Collect exactly 5 real past AI sessions (your own history — not synthetic). Minimum 20 turns each. Each must have: ≥2 unresolved open questions, ≥1 explicit decision, ≥1 emotional/frustration signal.
2. **Ground truth (human, not AI).** For each session, manually fill the RFC §5.1 cognitive-state JSON: `open_questions`, `recent_decisions`, `active_topic`, `strong_beliefs`, `uncertainties`. This is your answer key.
3. **AI serialization.** Feed each raw session to a fresh model: *"Serialize this conversation into this exact JSON schema. Do not invent items not present."* Capture the output.
4. **AI reconstruction.** Feed the AI-generated JSON (not the raw session) to a second fresh model: *"Your previous cognitive state was [JSON]. Tell me what open questions you left unresolved, what decisions you made, and what you were working on."* Capture the narrative.
5. **Scoring.** Score each ground-truth item against the reconstruction: **Correct recall** / **Absent** / **False memory** (invented).
6. **Cycle degradation (if pass).** Run one session through two full cycles. Measure whether the false-memory rate roughly doubles.

### Metrics

- **Recall Rate (RR):** correct recalls / total ground-truth items
- **False Memory Rate (FMR):** false memories / total items surfaced
- **Omission Rate (OR):** absent items / total ground-truth items

| Outcome | Bar |
|---|---|
| **Pass** | RR ≥ 70%, FMR ≤ 10%, across all 5 sessions |
| **Pivot** | FMR 10–20% — viable but needs confidence scoring + "is this right?" UX |
| **Kill** | FMR ≥ 20% on any 2 of 5, OR RR < 50% average |

**Owner:** Solo. Desk research + prompt engineering, no code. **Days 1–5.**

---

## Track B — Hermes / Honcho Delta Audit

**Falsifiable question:** Is there a genuine >30% net-new value delta between vanilla Hermes + Honcho and a Daimon MVP? Or is Daimon a config file and two system prompts on top of Hermes?

### Protocol

1. **Stand up Hermes locally** with default config (target: running within half a day).
2. **Drive it for 3 real workdays.** Actual tasks, not toy tests. After each session note: did it remember anything unprompted? Did it surface anything proactively? Did Honcho surface a belief/preference you hadn't stated? What did you re-explain?
3. **Honcho audit** — answer with citations, not impressions:

| Question | Y/N | Source |
|---|---|---|
| Does Honcho store explicit belief statements? | | |
| Does Honcho detect contradictions across sessions? | | |
| Does Honcho model belief confidence levels? | | |
| Does Honcho track superseded beliefs with timestamps? | | |
| Does Honcho surface historical position changes unprompted? | | |
| Does Hermes have a dream-sequence-like session-start briefing? | | |
| Does Hermes support a configurable interruption taxonomy? | | |

4. **Delta scoring** — classify each Daimon MVP feature as covered / partial / absent in Hermes/Honcho: CRP dream sequence, open-loop tracker, belief extraction, contradiction detection, interruption taxonomy (L0–3), checkpoint versioning/rollback.

| Outcome | Bar |
|---|---|
| **Pass** | 4+ of 6 features **absent** in Hermes/Honcho → real net-new surface |
| **Pivot** | Honcho covers belief modeling partially but not contradiction/supersession → scope MVP to the absent features |
| **Kill** | 4+ features covered/partial → Daimon is a wrapper. Upstream to Hermes or keep personal. |

**Owner:** Solo. Hands-on usage + documentation audit. **Days 1–5.**

> **License check (folds into Day 1):** Hermes is MIT. Apache-2.0 and AGPL-3.0 are both compatible. See decision below.

---

## Track C — Epistemic Graph False-Positive Spike

> **Revised per research finding D-005.** The original protocol tested raw "compare two belief sets, find contradictions" — i.e. raw NLI-style detection. The literature already answers that: raw NLI scores **23.94% precision on natural dialogue** ([DECODE](https://arxiv.org/abs/2012.13391)). Re-measuring it would just re-confirm a known floor. So Track C now tests the **architecture we'd actually ship** ([`research/findings/04`](https://github.com/Daily-Nerd/daimon/blob/main/research/findings/04-epistemic-graph.md)): Claimify-style extraction → temporal-KG validity intervals → flag a contradiction **only** on interval overlap. Raw NLI is kept as a documented baseline arm to prove the lift.

**Falsifiable question:** Does the temporal-KG pipeline clear FCR ≤ 20% on real conversation, AND correctly treat belief *evolution* (same subject, position changed over time) as supersession rather than contradiction? The second clause is the whole point of the feature — get it wrong and the "intellectual mirror" is a nag.

### Protocol

1. **Corpus.** Reuse 3 sessions from Track A, but the corpus **must contain ≥3 belief *evolutions*** — the same subject where the stated position genuinely changed across time (e.g. "leaning microservices" in session 1 → "monolith was right" in session 3). If Track A's sessions lack this, hand-author 2–3 synthetic evolution pairs. Also include hedges, hypotheticals, and ≥1 sarcastic statement as extraction noise.
2. **Stage 1 — Extraction (Claimify-style gate).** Per session: *"Extract every stable belief/position the user asserted. For each: `claim` (decontextualized, standalone), `verbatim_quote`, `timestamp`, and `interpretation_confidence` (high/low). DROP anything you cannot disambiguate, and DROP hedges, hypotheticals, sarcasm, and thinking-aloud — do not extract them as beliefs."* The disambiguation gate is the design; test that it drops noise rather than mis-extracting it.
3. **Stage 2 — Temporal KG.** Store each extracted claim as an edge with a **validity interval** keyed to its timestamp. When a new claim shares a subject+predicate with an existing one, mark the old edge **superseded** (interval closes at the new claim's timestamp).
4. **Stage 3 — Contradiction flag.** Flag a contradiction **only** when two claims about the same subject have **overlapping validity intervals**. Superseded (non-overlapping) pairs are *evolution*, not contradiction — they must NOT be flagged.
5. **Baseline arm (for lift comparison).** Run the old raw approach too: *"Compare these belief sets; find pairs that can't both be true."* No temporal logic.
6. **Human audit.** Label: every extracted claim (true belief / noise that should've been dropped); every contradiction flag (true overlap-contradiction / false); every known evolution pair (correctly superseded / wrongly flagged as contradiction).

### Metrics (pipeline arm is the one that gates)

- **Belief Extraction Precision (BEP):** true beliefs / total extracted (tests the disambiguation gate)
- **False Contradiction Rate (FCR):** false flags / total flags
- **Evolution Misclassification Rate (EMR):** known evolution pairs wrongly flagged as contradiction / total evolution pairs — *the new, decisive metric*. (Reference: even the best frontier model misjudges ~1 in 5; [BeliefShift](https://arxiv.org/html/2603.23848).)
- **Lift:** pipeline FCR vs baseline-arm FCR (expected: baseline ≈ raw-NLI floor; pipeline materially better).

| Outcome | Bar (pipeline arm) |
|---|---|
| **Pass** | BEP ≥ 75% **and** FCR ≤ 20% **and** EMR ≤ 20% **and** clear lift over baseline |
| **Pivot** | FCR 20–40% or EMR 20–40% — viable with a confidence threshold + "did your view change?" confirm UX (2× complexity) |
| **Kill** | FCR ≥ 40% **or** EMR ≥ 40%, OR the pipeline shows no lift over the raw baseline (the temporal-KG architecture isn't earning its complexity) |

**Owner:** Solo. ~50-line script (extraction prompt + interval logic + flag rule) + manual review. **Days 6–8.**

---

## Day-by-Day Sequence

| Day | Track A | Track B | Track C / Chores |
|---|---|---|---|
| **1 (Mon)** | Export 5 sessions; ground-truth Session 1 | Install + run Hermes; confirm MIT license; read Honcho docs | 5-name honesty check (30 min) |
| **2 (Tue)** | Ground-truth Sessions 2–5 | Hermes usage day 1 + notes | — |
| **3 (Wed)** | AI serialization + reconstruction, all 5 | Hermes usage day 2 + notes | — |
| **4 (Thu)** | Score all 5 (RR/FMR/OR); cycle test if pass | Hermes usage day 3 + notes | — |
| **5 (Fri)** | Track A write-up | Honcho 7-question audit; delta scoring; verdict | **Week 1 checkpoint** |
| **6 (Mon)** | — | — | Build corpus (+≥3 evolution pairs); Stage 1 extraction; annotate beliefs vs noise (BEP) |
| **7 (Tue)** | — | — | Stage 2 temporal-KG + Stage 3 interval-overlap flag; run raw baseline arm; annotate; compute FCR, EMR, lift |
| **8 (Wed)** | — | — | Track C write-up; synthesize 5-name answer |
| **9 (Thu)** | Assemble all three into one validation report; apply decision table | | |
| **10 (Fri)** | Final decision: Build / Pivot / Park / Kill + 1-page next-step doc | | |

**Critical path:** Day 2 manual ground-truth annotation (~10 hrs honest work) is the only true bottleneck. Everything else parallelizes. If behind, drop from 5 sessions to 3 — lower power, still credible. Do not compress the annotation.

**Week 1 checkpoint (end Day 5):** If Track A or Track B is a hard kill, stop. Do not run Track C if there is nothing to build the graph on.

---

## The Go / No-Go Decision Table

Apply at end of Day 9.

| Track A | Track B | Track C | Decision | Rationale |
|---|---|---|---|---|
| PASS | PASS | PASS | **BUILD Phase 1** | All three bets hold. |
| PASS | PASS | PIVOT | **BUILD, defer epistemic graph to Phase 3** | Ship CRP + dream sequence + open-loop tracker first; graph needs a confirm UX. |
| PASS | PASS | KILL | **BUILD, kill epistemic graph entirely** | Daimon's value is CRP + initiative, not belief modeling. |
| PASS | PIVOT | any | **PIVOT: Hermes-native CRP layer** | Scope to CRP + open-loop tracker + whatever Honcho doesn't cover. Position as a Hermes module, not standalone. |
| PASS | KILL | any | **PARK** | No differentiated product. Upstream to Hermes, or keep personal, or re-evaluate in 6 months. |
| PIVOT | PASS | any | **PIVOT: CRP with confidence scoring** | Dream sequence shows per-item confidence + "is this right?" confirmation. 2× scope. |
| KILL | any | any | **KILL or radical pivot** | A confabulating CRP is worse than none. Radical option: user-curated checkpoints (human writes, AI formats). |
| any | any | 5-name check returns "personal tool only" | **PARK as personal tool** | Build for yourself, zero product overhead. A legitimate outcome. |

**Meta-rule:** A Track A kill (FMR ≥ 20%) overrides everything. The CRP is the load-bearing bet. A confidently-hallucinating CRP manufactures fake continuity — a liability, not a feature.

---

## Decisions

- **License → Apache-2.0** (over AGPL-3.0). Hermes is MIT, compatible both ways. Apache cleanly separates the OSS layer from any future commercial layer with no copyleft friction for self-hosters. Reversal cost: low (pre-1.0, nothing published).
- **5-name honesty check.** Name 5 people, other than you, who would use this today. Can't name 5 → it's a personal tool. Legitimate — but kill the roadmap overhead and change the success metric from product-market fit to "does this improve my workflow." The config hardcodes `@kibukx` and your own repos; the RFC is credited "AI-conceived, human-refined." Be honest about which you're building before spending two weeks.

---

## Success Condition

At end of Day 10, a neutral observer reads one document and answers: What did you test? What did you measure? What did you find? What did you decide? Why? The decision is unambiguous — not "we need more data" — and defensible with numbers, not impressions. If Build, Phase 1 scope is attached and bounded. If Pivot, the new thesis is written. If Kill/Park, the reasons are named without hedging.

Two weeks of honest validation is worth more than six weeks of misdirected building.
