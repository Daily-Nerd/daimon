# Architecture Overview

> ## ⚠️ SUPERSEDED — read this first
>
> This document describes the **standalone-system architecture**, retired per **[D-008](../research/DECISIONS.md)** (user-approved 2026-06-09). The current authoritative architecture is **[MVP-DREAM-BRIEFING.md](./MVP-DREAM-BRIEFING.md)** — a dream-briefing skill (hooks + `SKILL.md`) on hermes-agent + Honcho, with upstream contributions to Graphiti.
>
> **Preserved, not deleted** (research docs reference it). Per-component status:
>
> - **🟡 RETAINED (reframed):** the **four-/three-layer memory model** (episodic / semantic / working + narrative) is conceptually retained but **implemented via Honcho + Graphiti dependencies, not built** — Honcho for reconciliation + user modeling, Graphiti for the temporal knowledge graph (`research/findings/07`).
> - **🟢 LIVE:** the **Session Resurrector / Dream Sequencer** flow (reconstruct checkpoint → briefing at session start) is the MVP's core, reused from Track A.
> - **🔴 SUPERSEDED:** the **Epistemic Graph Engine** (Graphiti ships it; D-005 retracted), the **Worker Pool**, the multi-surface **UI layer** (chat/IDE/Slack), the **REST/WebSocket service shape**, and **Initiative Levels 1–3** (MVP ships Level 0 only). The MVP is a hermes skill + hooks, not a CSM service.

## Design Principles

1. **Local-first, self-hosted** — Your data, your infrastructure, your agent
2. **Model-agnostic** — Swap LLMs via LiteLLM without rewriting logic
3. **Event-driven** — React to changes in your environment, not just user prompts
4. **Transparent** — Every background action is logged and inspectable
5. **Reversible** — Any change the agent makes can be undone

---

## System Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    User Interface Layer                      │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌──────────────┐  │
│  │  Chat   │  │  IDE    │  │  Slack  │  │  Dream Log   │  │
│  │  (Web)  │  │  Plugin │  │ Bridge  │  │  (Read-only) │  │
│  └────┬────┘  └────┬────┘  └────┬────┘  └──────┬───────┘  │
│       └─────────────┴─────────────┴──────────────┘          │
│                          │                                  │
└──────────────────────────┼──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│              Cognitive State Manager (CSM)                   │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │  Session    │  │   Dream     │  │   Epistemic Graph   │ │
│  │  Resurrector│  │  Sequencer  │  │      Engine         │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │  Priority   │  │   Mood /    │  │   Open Loop         │ │
│  │  Inference  │  │  Tone Model │  │   Tracker           │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
└──────────────────────────┬──────────────────────────────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
┌─────────────────┐ ┌─────────────┐ ┌─────────────────┐
│   Memory Core   │ │ Worker Pool │ │   Tool Router   │
│  (Vector + Graph│ │ (Cron +     │ │ (Git, APIs,     │
│   + Narrative)  │ │  Kanban)    │ │  Shell, etc.)   │
└─────────────────┘ └─────────────┘ └─────────────────┘
```

---

## Component Details

### 1. Cognitive State Manager (CSM)

The CSM is the "brain stem" of Daimon. It manages the agent’s internal state across sessions.

**Session Resurrector** — 🟢 *LIVE: this flow is the MVP's core (reconstruct checkpoint → briefing), reused from Track A and run at the first `pre_llm_call` of a session (MVP-DREAM-BRIEFING.md §2).*
- Reads the last session’s dense state vector
- Reconstructs working context (open questions, active hypotheses, emotional tone)
- Generates the "dream sequence" — a narrative briefing delivered at session start

**Dream Sequencer**
- Consumes outputs from background workers
- Synthesizes them into a coherent "what happened while you were away" narrative
- Prioritizes by relevance to the user’s current focus

**Epistemic Graph Engine** — 🔴 *SUPERSEDED: this is Graphiti's job (D-005 retracted, `findings/07`). Depend on Graphiti's bi-temporal KG + `resolve_edge_contradictions`; do not build. Only net-new piece is the Claimify extraction gate, contributed upstream.*
- Maintains a graph of user beliefs, claims, and confidence levels
- Detects contradictions between current proposals and historical positions
- Surfaces intellectual evolution over time

**Priority Inference**
- Infers what the user cares about *right now* based on recent activity
- Ranks background findings by predicted relevance
- Filters interruptive notifications by a learned attention model

**Mood / Tone Model**
- Tracks the user’s communication style and current stress level
- Adapts agent verbosity, formality, and initiative accordingly
- Prevents tone-deaf interruptions during crunch time

**Open Loop Tracker**
- Explicitly tracks questions, promises, and threads that were not resolved
- Ensures nothing falls through the cracks between sessions
- Generates "nag" items for stale loops

---

### 2. Memory Core

> 🟡 **RETAINED (reframed) — not built here.** The layered model is conceptually kept, but provided by **Honcho** (cross-session user modeling + reconciliation) and **Graphiti** (temporal KG), not implemented as a Daimon-owned vector+graph stack. MVP checkpoint store: local file (Slice 1) → Honcho (Slice 3). See `findings/07`, MVP-DREAM-BRIEFING.md §2.

Three-layer memory system:

| Layer | Technology | Purpose | Retention |
|-------|-----------|---------|-----------|
| Episodic | Vector DB (pgvector/Qdrant) | Raw conversation history, embeddings | Indefinite |
| Semantic | Knowledge graph (Neo4j/RDFLib) | Beliefs, entities, relationships | Indefinite |
| Working | In-memory cache + prompt context | Current session’s active context | Session |

**Narrative Compression**
- Old episodes are not deleted; they are **compressed** into summary nodes
- Compression preserves key facts, decisions, and emotional valence
- The graph maintains pointers to raw episodes for drill-down

---

### 3. Worker Pool

> 🔴 **SUPERSEDED — out of MVP scope.** No background-worker pool in the dream-briefing MVP. Proactive background cognition belongs to the deferred initiative taxonomy (Level 1–3).

Background cognition runs via two mechanisms:

**Cron Workers**
- Scheduled tasks (e.g., "check CVEs every 6 hours")
- Implemented via Hermes cron jobs
- Write findings to the dream log

**Kanban Workers**
- On-demand background tasks (e.g., "review this PR", "draft this email")
- Spawned when the CSM detects a task that can proceed asynchronously
- Report completion or blockers to the CSM

**Worker Discipline**
- All workers are **read-only by default**
- Write actions require explicit authorization or pre-approved capability scopes
- Every action is logged with before/after snapshots

---

### 4. Tool Router

A unified interface for the agent to interact with the outside world:

| Tool | Capability | Safety Level |
|------|-----------|--------------|
| Git | Read repos, diff branches, blame lines | Read-only default |
| GitHub API | Read issues, PRs, actions | Read-only default |
| Shell | Run tests, lint, grep logs | Sandboxed, whitelisted commands |
| Email | Draft responses, read threads | Draft-only; send requires approval |
| Calendar | Read schedule, suggest blocks | Read-only |
| Web | Fetch docs, check advisories | URL allowlist |

---

## Data Flow

### Session Start

```
User opens chat
  → CSM loads dense state from Memory Core
  → Session Resurrector reconstructs working context
  → Dream Sequencer generates briefing
  → Agent delivers: "While you were away, I noticed X, Y, Z. You were debugging the auth service. Shall I resume?"
```

### Background Discovery

```
Cron worker detects new CVE in dependency
  → Worker queries if user’s project is affected
  → If yes: writes finding to dream log, sets priority
  → CSM evaluates: is this interrupt-worthy?
  → If yes: queues notification for next session or sends proactive message
```

### User Request

```
User asks: "What should I work on today?"
  → CSM queries Open Loop Tracker for stale items
  → CSM queries Epistemic Graph for recently changed beliefs
  → CSM queries Worker Pool for completed background tasks
  → Agent synthesizes prioritized agenda with rationale
```

---

## Technology Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Orchestration | Hermes (cron + kanban) | Already built, model-agnostic, self-hosted |
| LLM Routing | LiteLLM | Swap models, load balance, cost control |
| Vector DB | pgvector or Qdrant | Self-hostable, performant, good Hermes integration |
| Graph DB | Neo4j or RDFLib | Epistemic graph queries, relationship traversal |
| Cache | Redis | Working memory, session state, rate limiting |
| Message Bus | NATS or Redis Pub/Sub | Worker coordination, event streaming |
| API Layer | FastAPI | Async-native, typed, OpenAPI自动生成 |
| Frontend | TBD (likely Terminal-first, web second) | Power users first |

---

## Security Model

```
┌─────────────────────────────────────────┐
│           Capability Sandbox             │
│  ┌─────────┐ ┌─────────┐ ┌──────────┐ │
│  │  Read   │ │  Draft  │ │ Execute  │ │
│  │  Realm  │ │  Realm  │ │  Realm   │ │
│  │ (open)  │ │ (logged)│ │ (gated)  │ │
│  └─────────┘ └─────────┘ └──────────┘ │
│                                         │
│  Gatekeeper: explicit auth or           │
│  pre-approved capability scopes         │
└─────────────────────────────────────────┘
```

- **Read Realm:** Unrestricted access to user data, repos, calendars
- **Draft Realm:** Can generate content (code, emails, docs) but not commit/send
- **Execute Realm:** Can modify state (git push, email send, deploy) only with explicit user authorization or pre-configured safe scopes

---

## Scalability Considerations

- **Single-user first, multi-user later** — The architecture supports one Daimon per user. Federation comes in v2.
- **Background task budgeting** — Monthly API spend caps, local model fallbacks for cheap workers
- **Memory pruning** — Episodic memory compresses over time; only high-salience episodes retained verbatim
- **Worker concurrency limits** — Max N simultaneous background workers to prevent cost spirals
