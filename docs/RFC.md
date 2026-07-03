# Technical RFC: Daimon — Persistent AI Companion Infrastructure

> ## ⚠️ SUPERSEDED — read this first
>
> This RFC specs the **standalone "persistent AI companion" system**, which was retired per **[D-008](../research/DECISIONS.md)** (user-approved 2026-06-09). The current authoritative architecture is **[MVP-DREAM-BRIEFING.md](./MVP-DREAM-BRIEFING.md)** — a dream-briefing skill on hermes-agent + Honcho, with upstream contributions to Graphiti.
>
> **This file is preserved, not deleted.** Research docs reference it by section number (e.g. "RFC §5.1"). Each section below is tagged:
>
> - **🟢 LIVE** — reused as-is by the MVP.
> - **🟡 RETAINED (reframed)** — concept kept, but implemented via a Honcho/Graphiti dependency, not built here.
> - **🔴 SUPERSEDED** — dropped or out of MVP scope.
>
> | Section | Status | Note |
> |---|---|---|
> | §5.1 Cognitive State (checkpoint schema) | 🟢 LIVE | The MVP checkpoint format. Now carries D-006 trust classes (`cognitive-state.schema.json`). |
> | §5.1 Dream Sequence | 🟢 LIVE | This *is* the briefing (Track-A reconstruct output). |
> | §5.1 Worker Task / §6.3 Worker Pool | 🔴 SUPERSEDED | Background-worker model is out of MVP scope. |
> | §6.1 CRP — Serialize / Reconstruct | 🟢 LIVE | Core of the MVP; reuses the Track-A serializer + reconstruct prompts. |
> | §6.1 State Versioning / rollback | 🔴 SUPERSEDED | `findings/03`: rollback to a checkpoint from the same lossy process is a weak defense. Honcho/Graphiti carry temporal history. |
> | §6.2 Memory Core (episodic/semantic/narrative) | 🟡 RETAINED | Conceptually kept; provided by Honcho (reconciliation) + Graphiti (temporal KG), not built. |
> | §6.4 Initiative Mode (taxonomy L0–3, attention model) | 🟡 RETAINED | MVP ships **Level 0 only** (`findings/05`); L1–3 deferred. |
> | §6.5 Epistemic Graph (schema/population/query) | 🔴 SUPERSEDED | Graphiti ships the temporal-KG mechanism (D-005 retracted). Use Graphiti; do not build. |
> | §7 API Surface / §7.2 WebSocket | 🔴 SUPERSEDED | Standalone-service surface; MVP is a hermes skill + hooks, not a service. |
> | §8 Configuration (`daimon.yaml`) | 🔴 SUPERSEDED | Replaced by hermes skill/hook config. |
> | §9 Milestones (Phases 1–4) | 🔴 SUPERSEDED | See MVP-DREAM-BRIEFING.md §8 (thin vertical slices). |

**Status:** ⚠️ Superseded by D-008 / [MVP-DREAM-BRIEFING.md](./MVP-DREAM-BRIEFING.md) — preserved for reference (CRP sections still live)  
**Author:** Daimon (AI-conceived, human-refined)  
**Date:** 2026-06-09  
**Target Version:** v0.1.0 (MVP)  
**Related:** [MVP-DREAM-BRIEFING.md](./MVP-DREAM-BRIEFING.md) (current), [PITCH.md](./PITCH.md), [PROBLEM.md](./PROBLEM.md), [ARCHITECTURE.md](./ARCHITECTURE.md)

---

## 1. Summary

This RFC proposes Daimon, a self-hosted AI companion system that maintains **persistent cognitive state** across sessions, runs **autonomous background workers**, and possesses **initiative** to interrupt users with relevant insights. It is built on top of the existing Hermes agent infrastructure and targets technical users who want AI collaboration, not AI servitude.

---

## 2. Motivation

See [PROBLEM.md](./PROBLEM.md) for the full forensic analysis. The executive summary:

- Current AI assistants are stateless; every session starts from zero
- They cannot run background tasks or initiate contact
- They have no model of the user’s evolving beliefs or priorities
- This creates a 15% productivity tax on every AI-assisted workflow

Daimon eliminates this tax.

---

## 3. Goals

### 3.1 Must Have (MVP)
- [ ] Cognitive Resumption Protocol — dense state serialization and narrative reconstruction at session start
- [ ] Persistent Memory Core — vector + graph storage of conversations, facts, and beliefs
- [ ] One Background Worker Type — e.g., "monitor GitHub issues/PRs and summarize changes"
- [ ] Dream Log — read-only feed of background worker outputs
- [ ] Hermes Integration — runs as a first-class citizen in the Hermes ecosystem

### 3.2 Should Have (v0.2)
- [ ] Initiative Mode — agent can proactively message user via Slack/Discord when thresholds are met
- [ ] Epistemic Graph — explicit tracking of user beliefs and contradictions
- [ ] Tool Autonomy — agent can run tests, grep logs, draft code without explicit per-command approval
- [ ] Mood/Tone Model — adaptive communication style based on inferred user state

### 3.3 Could Have (v1.0)
- [ ] Multi-user federation — shared Daimon instances for teams with privacy partitions
- [ ] Local model fallback — background workers run on local LLMs when API budgets are tight
- [ ] Voice/ambient interface — always-on listening mode for quick captures

### 3.4 Won’t Have (Explicitly Out of Scope)
- General-purpose web browsing for the agent (security risk, low ROI)
- Autonomous code deployment to production (too dangerous for v1)
- Social media management (not our user)

---

## 4. Non-Goals

- Replacing Hermes. Daimon extends Hermes, it does not fork it.
- Being model-specific. We support any model LiteLLM can route to.
- Being cloud-hosted. Daimon is local-first; cloud is opt-in only.
- Being a no-code tool. Target user is a developer who can edit YAML.

---

## 5. Design Overview

### 5.1 Core Abstractions

> **🟢 LIVE (Cognitive State, Dream Sequence) · 🔴 SUPERSEDED (Worker Task).** The cognitive-state checkpoint schema and the dream-sequence narrative are reused directly by the MVP (the dream sequence *is* the briefing). The MVP schema additionally carries D-006 trust classes — see `cognitive-state.schema.json`. Worker Task is out of MVP scope.

#### Cognitive State
A JSON-serializable object representing the agent’s mind at a point in time:

```json
{
  "session_id": "uuid",
  "timestamp": "ISO8601",
  "working_context": {
    "active_topic": "auth service refactoring",
    "open_questions": ["should we switch to JWT?"],
    "recent_decisions": ["keep bcrypt, don’t migrate"],
    "emotional_valence": "frustrated_but_determined"
  },
  "epistemic_snapshot": {
    "strong_beliefs": ["microservices are a mistake at our scale"],
    "uncertainties": ["Rust vs Go for the new service"],
    "contradictions_flagged": []
  },
  "worker_queue": [
    {"task": "check CVE-2026-XXXX", "status": "pending", "priority": 7}
  ],
  "memory_pointers": {
    "last_vector_offset": 12450,
    "last_graph_checkpoint": "abc123"
  }
}
```

#### Dream Sequence
A narrative generated from the cognitive state, delivered to the user at session start:

```
While you were away (14 hours):
- I checked 3 new PRs. The auth refactor (#847) looks good; I left a review.
- CVE-2026-XXXX does NOT affect our deps (checked via osv.dev).
- You were debating JWT vs sessions. I found a relevant Hacker News thread — 
  linked in your dream log.

Open loops:
- You said you’d review the Terraform module “ tomorrow.” It’s tomorrow.
- The staging deploy failed last night. Logs suggest an OOM. Want me to investigate?

Ready when you are.
```

#### Worker Task
A declarative unit of background work:

```yaml
id: check-cve-nightly
trigger:
  type: cron
  schedule: "0 6 * * *"
input:
  lockfiles: ["Cargo.lock", "package-lock.json"]
action:
  type: api_call
  endpoint: "https://api.osv.dev/v1/querybatch"
  payload_template: "..."
output:
  destination: dream_log
  format: markdown
  priority_rule: "only_notify_if_match"
```

---

### 5.2 Component Interactions

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full diagram. Key flows:

**Session Start Flow:**
1. User opens chat / IDE / Slack
2. CSM loads latest cognitive state from Memory Core
3. Session Resurrector hydrates working context
4. Dream Sequencer generates narrative briefing
5. Agent delivers briefing and awaits input

**Background Worker Flow:**
1. Cron trigger or CSM enqueue fires
2. Worker Pool spawns isolated task process
3. Task executes with read-only or draft-only capabilities
4. Output written to dream log
5. CSM evaluates: is this interrupt-worthy?
6. If yes, notification queued for next session or sent proactively

---

## 6. Detailed Design

### 6.1 Cognitive Resumption Protocol (CRP)

> **🟢 LIVE — this is the core of the MVP.** Serialize at session end, reconstruct at session start. The MVP reuses the Track-A serializer and reconstruct prompts (`research/experiments/track-a/`). Note the empirical correction from `findings/03`: single-cycle reconstruction is faithful (FMR ~1%) but recall is length-driven (RR 67.3%, cliff ~1,400 lines); the recall fix lives in serialization (D-007). **State Versioning (rollback) below is 🔴 SUPERSEDED** — `findings/03` found rollback to a checkpoint produced by the same lossy process to be a weak defense against confabulation.

#### Serialization
At session end, the agent’s working state is serialized to a "cognitive checkpoint." This is not a transcript. It is a **compressed representation** of:
- What the agent was thinking about
- What it was uncertain about
- What it planned to do next
- What emotional tone the conversation had

Serialization uses a two-pass process:
1. **Extraction:** The model is prompted to summarize the session into the cognitive state schema
2. **Embedding:** The summary + key utterances are embedded and stored in the vector DB

#### Resurrection
At session start, the reverse process:
1. **Retrieval:** Fetch the latest cognitive checkpoint + recent high-salience memories
2. **Reconstruction:** Prompt the model to "resume" its prior state given the checkpoint
3. **Dream Synthesis:** Generate a natural-language briefing from the reconstructed state

**Key constraint:** The dream sequence must be **skimmable**. Users should be able to read it in <30 seconds and decide what to engage with.

#### State Versioning
Cognitive checkpoints are versioned. If a resurrection produces a degraded or confused agent, the user can "roll back" to a prior checkpoint.

---

### 6.2 Memory Core

> **🟡 RETAINED (reframed) — not built here.** The layered memory model (episodic / semantic / narrative) is conceptually retained, but the MVP does **not** build it. Honcho provides cross-session user modeling + reconciliation; Graphiti provides the temporal knowledge graph (`findings/07`). The checkpoint store is local-file in Slice 1, Honcho-backed in Slice 3 (MVP-DREAM-BRIEFING.md §2, §8).

#### Episodic Layer (Vector DB)
- Stores raw conversation segments, embedded
- Segmented by session, tagged with topics and entities
- Retrieval via hybrid search (semantic + metadata filters)

#### Semantic Layer (Graph DB)
- Nodes: entities, beliefs, decisions, people, projects
- Edges: relationships, temporal ordering, confidence levels
- Queries: "What did we decide about X?", "Show me everything related to Y"

#### Narrative Layer
- Long-form summaries of past sessions, organized by project/topic
- Generated periodically by background workers
- Serves as "long-term memory" — less precise but more coherent than raw episodes

#### Compression Strategy
- Recent sessions (<30 days): full episodic retention
- Medium-term (30-90 days): compressed to narrative summaries + key facts
- Long-term (>90 days): graph nodes only, episodic data archived

---

### 6.3 Worker Pool

> **🔴 SUPERSEDED — out of MVP scope.** The MVP has no background-worker pool. Proactive background cognition is part of the deferred initiative taxonomy (Level 1–3), not the dream-briefing MVP.

#### Worker Types

| Type | Trigger | Lifetime | Example |
|------|---------|----------|---------|
| Cron | Schedule | Recurring | Nightly CVE scan |
| Event | External webhook | One-shot | New PR opened |
| Reactive | CSM inference | One-shot | "This looks urgent, investigate" |
| Batch | User request | Bounded | "Review all open PRs" |

#### Isolation
Each worker runs in a separate process/container with:
- Its own memory context (no access to CSM state unless explicitly granted)
- Capability-scoped tool access
- Hard timeout (default: 5 minutes)
- Resource limits (CPU, memory, API call budget)

#### Output Contract
All workers produce:
- `status`: success / failure / partial
- `summary`: One-line result for the dream log
- `detail`: Full output (markdown, code, diffs)
- `confidence`: 0-10 score for the finding
- `suggested_action`: What the agent thinks should happen next

---

### 6.4 Initiative Mode

> **🟡 RETAINED (reframed) — MVP ships Level 0 only.** The initiative taxonomy survives as net-new (`findings/07`), but the MVP ships **Level 0 only**: pull at session start; nothing pings you proactively. Levels 1–3, the four-channel escalation, and the attention model are deferred (`findings/05`; MVP-DREAM-BRIEFING.md §5).

#### Interruption Taxonomy

| Level | Channel | Condition |
|-------|---------|-----------|
| 0 (Silent) | Dream log only | All background findings default here |
| 1 (Low) | Chat notification | Confidence > 7, relevance > 8, user not busy |
| 2 (Medium) | Slack DM | Confidence > 8, time-sensitive, user online |
| 3 (High) | Slack DM + mention | Confidence > 9, critical (CVE, outage, deadline) |

#### Attention Model
The CSM maintains an inferred "user attention state":
- Calendar: in meeting, focus time, off hours
- Recent activity: active in chat vs. idle for hours
- Explicit signals: "/dnd on", "focus mode", "only interrupt for P0"

The attention model gates all Level 1-3 interruptions.

---

### 6.5 Epistemic Graph

> **🔴 SUPERSEDED — depend on Graphiti, do not build.** Graphiti ships the temporal-KG validity-interval + overlap-gated contradiction mechanism verbatim in code (`resolve_edge_contradictions`); Honcho ships belief reconciliation. **D-005 is retracted as novel** (`findings/07`). The only net-new piece here is the **Claimify-style extraction gate** — absent in Graphiti — whose home is an upstream PR, not this build (MVP-DREAM-BRIEFING.md §7).

#### Schema

```
(Belief {text, confidence, created_at, updated_at, source_session})
-[:CONTRADICTS]-> (Belief)
-[:SUPPORTS]-> (Belief)
-[:SUPERSEDED_BY {superseded_at}]-> (Belief)
(Decision {text, context, reversed_at})
-[:BASED_ON]-> (Belief)
(Project {name, status})
-[:REQUIRES_BELIEF]-> (Belief)
```

#### Population
The epistemic graph is populated via:
1. Explicit extraction: The model is prompted to identify beliefs and decisions in conversation
2. Implicit inference: Background workers scan docs/commits for implicit assumptions
3. User correction: "That’s not what I believe anymore" updates the graph

#### Query Interface
- "What do I believe about X?" → Returns belief chain with confidence and history
- "Have I changed my mind about Y?" → Returns superseded beliefs
- "What decisions depend on belief Z?" → Returns downstream decisions at risk if Z changes

---

## 7. API Surface (Tentative)

> **🔴 SUPERSEDED — MVP is a skill, not a service.** Daimon ships as a hermes skill + hooks bundle, not a standalone REST/WebSocket service. There is no `daimon` server. Integration points are hermes hook events (`on_session_end`, `pre_llm_call`, …) and the Honcho SDK — see MVP-DREAM-BRIEFING.md §2.

### 7.1 REST API

```
GET    /v1/state              # Current cognitive state
POST   /v1/session/start      # Begin session, returns dream sequence
POST   /v1/session/end        # End session, triggers checkpoint save
GET    /v1/dream-log          # Read dream log entries
POST   /v1/workers/enqueue    # Submit background task
GET    /v1/workers/{id}       # Check worker status
GET    /v1/memory/query       # Query memory (vector + graph)
GET    /v1/episteme/beliefs   # List beliefs about a topic
POST   /v1/episteme/contradiction  # Report a detected contradiction
POST   /v1/initiative/level   # Set interruption level
```

### 7.2 WebSocket

Real-time channel for:
- Live dream log updates during sessions
- Proactive agent messages (initiative mode)
- Background worker progress streaming

---

## 8. Configuration

> **🔴 SUPERSEDED — replaced by hermes skill/hook config.** There is no standalone `daimon.yaml` service config in the MVP. Configuration lives in the hermes skill bundle (`SKILL.md` + hook registration) and a checkpoint-store flag (`file` | `honcho`).

### 8.1 User Config (`daimon.yaml`)

```yaml
daimon:
  identity:
    name: "Daimon"  # User can rename
    voice: "direct"  # direct, formal, playful
    verbosity: "concise"  # concise, detailed, exhaustive

  memory:
    vector_backend: "pgvector"  # pgvector, qdrant, pinecone
    graph_backend: "neo4j"      # neo4j, kuzu, rdf
    retention_days: 365
    compression_enabled: true

  workers:
    max_concurrent: 3
    default_timeout: 300
    api_budget_usd_monthly: 50.00
    local_model_fallback: true

  initiative:
    enabled: true
    default_level: 1  # 0=silent, 1=chat, 2=slack, 3=urgent
    attention_model: "calendar+activity"  # or "calendar_only", "explicit_only"

  integrations:
    github:
      repos: ["Daily-Nerd/homelab-apps", "Daily-Nerd/TripWire"]
      watch: ["issues", "prs", "actions"]
    slack:
      channel: "#daimon"
      dm_user: "@kibukx"
    email:
      draft_only: true
      allowed_domains: ["daily-nerd.io"]
```

---

## 9. Milestones

> **🔴 SUPERSEDED — see MVP-DREAM-BRIEFING.md §8.** The four-phase plan below assumed the standalone build. The MVP is sequenced as thin vertical slices (Slice 1: local-file briefing, no Honcho; Slice 2: recall fix; Slice 3: Honcho-backed; Slice 4: Claimify-gate PR). The CRP work in Phase 1 carries forward; background-worker / epistemic-graph phases do not.

### Phase 1: Cognitive Resumption (Weeks 1-4)
- [ ] Implement cognitive state schema
- [ ] Build session start/end protocol
- [ ] Integrate vector DB for episodic memory
- [ ] Deliver dream sequence on session start
- [ ] Hermes skill for Daimon session management

**Definition of Done:** User opens Hermes, gets a dream sequence, and the agent remembers the previous session’s context.

### Phase 2: Background Workers (Weeks 5-8)
- [ ] Worker Pool implementation (cron + event triggers)
- [ ] One concrete worker: GitHub PR/issue monitor
- [ ] Dream log UI/read-out
- [ ] Capability sandbox (read-only default)

**Definition of Done:** User wakes up to a dream log entry about overnight PR activity.

### Phase 3: Initiative + Episteme (Weeks 9-16)
- [ ] Attention model
- [ ] Slack/Discord bridge for proactive messages
- [ ] Epistemic graph schema + population
- [ ] Contradiction detection
- [ ] Tool autonomy (tests, lint, draft code)

**Definition of Done:** Agent interrupts user with a relevant insight, and user asks "What do I believe about X?" and gets an accurate historical answer.

### Phase 4: Hardening (Weeks 17-24)
- [ ] Security audit
- [ ] Cost optimization + local model fallbacks
- [ ] Multi-user architecture (teams)
- [ ] Documentation + community onboarding

**Definition of Done:** Daily-Nerd team runs Daimon in production for 2 weeks with no P1 incidents.

---

## 10. Open Questions

1. **Checkpoint frequency:** Save at session end only, or periodic intra-session snapshots for crash recovery?
2. **Graph DB choice:** Neo4j is powerful but heavy. Kuzu is lighter but newer. Do we start with RDFLib and migrate?
3. **Initiative safety:** How do we prevent the agent from becoming annoying? Is there a "snooze" mechanism?
4. **Memory privacy:** If Daimon remembers everything, how does the user delete specific memories? GDPR implications?
5. **Multi-modal memory:** Should Daimon eventually remember images, voice memos, or stick to text?

---

## 11. Appendix

### A. Related Work
- **MemGPT / Letta:** Memory management for LLMs. Daimon goes further with autonomy and initiative.
- **AutoGPT / BabyAGI:** Autonomous agent loops. Daimon is more focused and user-aligned, less chaotic.
- **Copilot Workspace:** AI-assisted dev environment. Daimon is IDE-agnostic and persists across all tools.

### B. Glossary
- **CSM:** Cognitive State Manager
- **CRP:** Cognitive Resumption Protocol
- **Dream Log:** Read-only feed of background worker outputs
- **Epistemic Graph:** Knowledge graph of user beliefs and decisions
- **Open Loop:** Unresolved question, task, or thread from a prior session

---

## 12. Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-06-09 | Local-first, self-hosted | Aligns with Daily-Nerd values; privacy by default |
| 2026-06-09 | Hermes-native | Existing infrastructure, reduces build time |
| 2026-06-09 | LiteLLM for routing | Model-agnostic, cost control, fallback support |
| 2026-06-09 | Read-only default for workers | Security; explicit opt-in for write actions |

---

*End of RFC.*
