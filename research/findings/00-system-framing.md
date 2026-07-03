# 00 — System Framing: What Daimon Actually Is

**Status:** 🟢 Investigated

## The one-sentence truth

Strip the mythology (dreams, Greek daimon, "dignified existence for the intelligence inside it"): **Daimon is a memory-augmented LLM agent — a bounded context window wrapped in a read/write loop over external storage.**

The "mind" is the context window. Everything else is plumbing that decides *what goes in the window right now and what stays on disk.*

## The governing constraint

> Context is finite. History is infinite.

Every algorithm in this research exists to answer one question: given a fixed token budget, which slice of an ever-growing history do I load? This is not a Daimon idea — it is the central problem of the entire memory-agent field.

## The canonical lens: LLM-as-OS

MemGPT (Packer et al. 2023) framed it best: treat the LLM like an operating system managing **virtual memory**. Main context = RAM (fast, tiny). External stores (vector DB, graph) = disk (slow, vast). The agent "pages" data in and out via tool calls. Hold this analogy — most of Daimon's components are an instance of it:

| OS concept | Daimon component |
|---|---|
| RAM | The active context window |
| Disk | Memory Core (vector + graph) |
| Paging in | Retrieval at session start (CRP read) |
| Paging out / swap | Checkpoint serialization (CRP write) |
| Page-replacement policy | Salience/decay scoring |
| Background daemon | Worker pool (cron/kanban) |

## Why this framing matters for the project

1. **It demystifies the bet.** "Cognitive Resumption Protocol" sounds like research. It is swap-in/swap-out of context, plus a generative summary step. The novel risk is concentrated in that generative step (→ `03-crp-reconstruction.md`).
2. **It tells us what's already built.** Paging, storage, scheduling = Hermes already has these. Daimon's net-new is concentrated in the summary/reconstruction quality and the epistemic graph (→ `01`, `04`, Track B).
3. **It separates solved from unsolved.** The OS plumbing is solved engineering. The two unsolved pieces are: (a) does the generative swap-in faithfully reconstruct state (confabulation), and (b) can we reason over the stored beliefs without false contradictions. Those are the only two boxes worth betting on.

## Component map → where each is studied

| Component | Solved? | Finding |
|---|---|---|
| Storage + retrieval | 🟢 Solved engineering | `01-memory-retrieval.md` |
| Checkpoint write (serialize) | 🟡 Buildable, method matters | `02-crp-serialization.md` |
| Reconstruction (read) | 🔴 THE BET | `03-crp-reconstruction.md` |
| Epistemic graph | 🔴 DIFFERENTIATOR/GAMBLE | `04-epistemic-graph.md` |
| Initiative | 🟢 Tractable decision theory | `05-initiative.md` |
| Evidence synthesis | — | `06-evidence-base.md` |
