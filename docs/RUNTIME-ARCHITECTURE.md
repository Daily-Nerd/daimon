# Runtime Architecture

This is the current architecture of the shipped Daimon runtime.

Daimon is a local-first, host-agnostic modular monolith. It does not require an
always-on daemon, a remote memory service, a vector database, or a graph database.
Native host hooks and optional local surfaces enter the same Python runtime, which
stores project-scoped checkpoints and renders them back into future agent sessions.

The diagrams below describe five logical layers and two cross-cutting views. These
are responsibility boundaries, not a claim that imports follow a strict layered or
hexagonal architecture. The strongest enforced seams today are the host-to-CLI
boundary, the single capture pipeline, the checkpoint write boundary, and the
deterministic briefing renderer.

Historical designs are preserved in [Architecture Overview](./ARCHITECTURE.md) and
[MVP Dream-Briefing](./MVP-DREAM-BRIEFING.md). They explain how the current runtime
emerged, but they are not the current component map.

## 1. System context

```mermaid
flowchart LR
    User["Developer"]

    Hosts["AI coding hosts<br/>Claude Code, Codex, Gemini,<br/>Windsurf, and Hermes"]
    Daimon["Daimon local runtime"]
    LLM["Configured LLM backend"]
    Local["Local Daimon state"]
    Team["Optional Git team sidecar"]
    Receipt["Optional receipt signer"]
    World["Local and remote state probes"]

    User --> Hosts
    User --> Daimon
    Hosts <--> Daimon
    Daimon --> LLM
    Daimon <--> Local
    Daimon <--> Team
    Daimon --> Receipt
    Daimon --> World
```

The LLM extracts a checkpoint candidate from a transcript. Deterministic code owns
quote verification, provenance binding, admission, carry, lifecycle state, storage,
ranking, and rendering. The model proposes memory; it does not grant that memory its
trust class or final authority.

The local viewer and MCP server are optional read surfaces. They do not turn Daimon
into a required service and they are not remote memory backends.

## 2. Integration layer

```mermaid
flowchart TB
    subgraph HostProducts["Host products"]
        Claude["Claude Code"]
        Codex["Codex"]
        Gemini["Gemini"]
        Windsurf["Windsurf"]
        Hermes["Hermes"]
    end

    subgraph Integration["Integration layer"]
        Scripts["Standalone host scripts"]
        Shared["Shared hook support"]
        InProcess["In-process plugin hooks"]
        CLI["daimon CLI"]
        Skills["Installed agent skills"]
        MCP["Read-only MCP server"]
        Viewer["Local HTTP viewer"]
    end

    Human["Direct CLI user"]
    MCPClient["MCP client"]
    Browser["Browser"]
    App["Application services"]

    Claude --> Scripts
    Codex --> Scripts
    Gemini --> Scripts
    Windsurf --> Scripts
    Scripts --> Shared
    Shared -->|"shell execution"| CLI
    Hermes --> InProcess
    InProcess --> App
    Human --> CLI
    Skills --> CLI
    CLI --> App
    MCPClient --> MCP
    MCP --> App
    Browser --> Viewer
    Viewer --> App
```

Native hook scripts are standalone and standard-library-only. They cannot assume the
installed package is importable from the host interpreter, so they locate and execute
the `daimon` CLI. Host-specific payload handling stays in the adapter; serialization,
storage, and rendering stay in the runtime.

Hermes is the secondary in-process integration. The MCP surface is read-only. The
viewer binds locally and treats files as its compatibility seam.

Host capabilities are not identical. Each adapter implements only the lifecycle
events its host exposes; the host guides document where capture, start injection, or
prompt recall is unavailable.

## 3. Application layer

```mermaid
flowchart TB
    WriteEntry["CLI and write-capable hooks"]
    ReadEntry["CLI, read hooks, MCP, and viewer"]

    subgraph UseCases["Application services"]
        Capture["Capture orchestration"]
        Brief["Briefing orchestration"]
        Recall["Recall and prompt injection"]
        Lifecycle["Resolve, forget, reverify, and decide"]
        Knowledge["Rulings, refutations, amendments,<br/>requests, and relations"]
        Operations["Configure, audit, heal,<br/>install, and team operations"]
    end

    Core["Cognitive integrity core"]
    State["State engines"]
    Output["Read models and presentation"]

    WriteEntry --> Capture
    WriteEntry --> Lifecycle
    WriteEntry --> Knowledge
    WriteEntry --> Operations
    ReadEntry --> Brief
    ReadEntry --> Recall

    Capture --> Core
    Brief --> Core
    Recall --> State
    Lifecycle --> State
    Knowledge --> State
    Operations --> State

    Core --> State
    State --> Output
```

`capture.run` is the single write orchestration path for transcript-derived
checkpoints. Hook capture and direct CLI serialization enter that same pipeline so
they cannot silently stamp, carry, admit, or verify different checkpoint shapes.

The other application services coordinate existing engines. Command handlers own
interaction and exit behavior; domain modules own state transitions and invariants.

## 4. Cognitive integrity core

```mermaid
flowchart LR
    Transcript["Host transcript"]
    Parse["Transcript parser"]
    Serialize["Chunk, extract, and merge"]
    LLM["LLM backend"]
    Verify["Quote verification"]
    Provenance["Source and provenance binding"]
    Previous["Previous project checkpoint"]
    Carry["Deterministic carry and link binding"]
    Policy["Admission policy"]
    Candidate["Admitted checkpoint"]
    WriteBoundary["Ownership stamping and write boundary"]

    Stored["Stored checkpoint"]
    Score["Recency and importance scoring"]
    World["External-state checks"]
    Compose["Deterministic briefing composition"]

    Transcript --> Parse
    Parse --> Serialize
    LLM --> Serialize
    Serialize --> Verify
    Provenance --> Verify
    Verify --> Carry
    Previous --> Carry
    Carry --> Policy
    Policy --> Candidate
    Candidate --> WriteBoundary

    Stored --> Score
    Score --> Compose
    World --> Compose
```

The capture direction is deliberately asymmetric:

1. The serializer extracts structured claims from the transcript.
2. Quote verification mechanically checks claimed quotations against source text.
3. Provenance binds supported items to their source.
4. Carry moves unresolved items forward using deterministic matching.
5. Admission applies redaction, deletion tombstones, identifiers, and standing-rule
   filters.
6. The write boundary stamps project and branch ownership before persistence.

The read direction scores admitted items and composes a deterministic briefing.
External-state checks can mark carried claims for renewed verification, but they do
not rewrite the remembered claim.

## 5. Persistence and trust layer

```mermaid
flowchart TB
    Write["Checkpoint write boundary"]
    Ledgers["Lifecycle and domain ledger engines"]
    Recall["Recall indexer"]
    Receipts["Receipt engine"]
    Sync["Team synchronization"]
    CaptureLog["Capture ledger"]

    subgraph LocalState["Local state"]
        Sessions["Per-session checkpoint JSON"]
        Global["Global latest pointer"]
        Project["Per-project pointers and history"]
        Events["Lifecycle events"]
        Domain["Rulings, refutations, amendments,<br/>requests, and relations"]
        FTS["Derived SQLite FTS5 index"]
        Sidecars["Optional signed receipts"]
        Logs["Capture and usage logs"]
    end

    Team["Optional Git team sidecar"]

    Write --> Sessions
    Write --> Global
    Write --> Project
    Ledgers --> Events
    Ledgers --> Domain
    Recall --> FTS
    Receipts --> Sidecars
    CaptureLog --> Logs
    Sync <--> Team
    Write --> Sync
```

Checkpoint writes are atomic and update both the per-session record and the relevant
latest pointers. Project routing prevents one project's checkpoint from becoming
another project's carried state. The global pointer remains a compatibility and
orientation surface, not proof of project ownership.

Lifecycle and knowledge stores are append-oriented state-transition ledgers. Privacy
deletion is the deliberate exception: forgetting content may rewrite declared
surfaces and publish tombstones so erased text cannot return through re-capture or
team synchronization.

The SQLite recall database is derived state. Checkpoint JSON and ledgers are the
sources of truth; the index can be rebuilt when its source fingerprint changes.

## 6. Read and presentation layer

```mermaid
flowchart LR
    State["Checkpoints, ledgers, and recall index"]

    subgraph ReadEngines["Read engines"]
        Briefing["Briefing builder"]
        Search["Recall search"]
        Inspector["Trust inspector"]
        Folds["Ledger folds"]
        FileReader["Import-free viewer reader"]
    end

    subgraph Surfaces["Presentation surfaces"]
        Host["Session-start context"]
        CLI["Plain or Rich CLI"]
        MCP["MCP JSON"]
        API["Local viewer API"]
        Browser["Browser UI"]
    end

    State --> Briefing
    State --> Search
    State --> Inspector
    State --> Folds
    State --> FileReader

    Briefing --> Host
    Briefing --> CLI
    Briefing --> MCP
    Search --> CLI
    Search --> MCP
    Search --> API
    Inspector --> CLI
    Inspector --> API
    Folds --> CLI
    Folds --> API
    FileReader --> API
    API --> Browser
```

The CLI renderer is the source of truth for human-facing terminal output. Host start
hooks inject that rendered briefing rather than reimplementing it. MCP returns a
bounded read-only subset as JSON.

The viewer has two read paths by design. Its reader normalizes checkpoint files
without importing Daimon, which tests the file contract a separate consumer receives.
Its server delegates search, trust inspection, and ledger folds to Daimon's engines so
those semantics do not fork inside the same distribution.

## 7. End-to-end lifecycle

```mermaid
sequenceDiagram
    participant Host as AI host
    participant Hook as Host hook
    participant CLI as daimon CLI
    participant Core as Cognitive core
    participant LLM as LLM backend
    participant Store as Local store
    participant Recall as Recall index

    Host->>Hook: Session ends
    Hook->>CLI: Spawn serialize without blocking exit
    CLI->>Core: Run the capture pipeline
    Core->>LLM: Extract and merge checkpoint candidate
    LLM-->>Core: Structured candidate
    Core->>Core: Verify quotes and bind provenance
    Core->>Store: Read previous project state
    Core->>Core: Carry, admit, and apply privacy policy
    Core->>Store: Atomically write checkpoint and events

    Host->>Hook: Next session starts
    Hook->>CLI: Request project briefing
    CLI->>Store: Read permitted project state
    CLI->>Core: Fold lifecycle state, score, and check staleness
    Core-->>CLI: Deterministic briefing
    CLI-->>Hook: Rendered text
    Hook-->>Host: Inject context before the first model turn

    Host->>Hook: Later user prompt
    Hook->>CLI: Request recall suggestion
    CLI->>Recall: Refresh derived index if needed
    Recall-->>CLI: Matching prior context or no match
    CLI-->>Host: Inject one pointer or remain silent
```

Host hooks are fail-open because memory support must not break the host session.
Capture work that may outlive session exit is detached where the host contract allows
it. Read paths remain project-aware: content from another project may orient the user
only through an explicitly labeled fallback and must never be persisted as this
project's own memory.

## Component map

| Logical layer | Primary responsibilities | Main implementation areas |
| --- | --- | --- |
| Integration | Host lifecycle translation, CLI entry, skills, MCP, local viewer | `hook/`, `hooks.py`, `cli/`, `mcp_server.py`, `mcp_tools.py`, `daimon_ui/` |
| Application | Capture, briefing, recall, lifecycle, knowledge, audit, and team use cases | `capture.py`, `briefing.py`, `recall.py`, `inspector.py`, `pending.py`, CLI command modules |
| Cognitive integrity | Transcript parsing, extraction, verification, provenance, carry, admission, scoring, and external checks | `transcript.py`, `serializer.py`, `provenance.py`, `carry.py`, `policy.py`, `scoring.py`, `worldcheck.py`, `schema.py`, `normalize.py` |
| Persistence and trust | Checkpoints, events, domain ledgers, receipts, declared surfaces, and team synchronization | `store.py`, `ledger.py`, `refutations.py`, `amendments.py`, `requests.py`, `relations.py`, `receipts.py`, `surfaces.py`, `teamsync.py`, `teamproject.py` |
| Read and presentation | Deterministic terminal output, read models, inspection, API responses, and browser presentation | `render.py`, `briefing.py`, `inspector.py`, `mcp_tools.py`, `daimon_ui/reader.py`, `daimon_ui/server.py`, `daimon_ui/static/` |

## Architectural invariants

- **Local state is authoritative.** No remote memory backend is required.
- **One capture pipeline owns transcript-derived writes.** Entry points may differ;
  checkpoint semantics may not.
- **Trust is deterministic after extraction.** A model cannot award its own quote
  verification or provenance.
- **Project identity is enforced at read and write boundaries.** A global pointer is
  not ownership.
- **Human decisions outrank machine suggestions.** Resolution, amendment, ruling,
  refutation, request, and relation transitions preserve that authority boundary.
- **Recall is derived.** The FTS index may be discarded and rebuilt from durable
  sources.
- **Deletion crosses every declared surface.** Forgetting is not complete while a
  plaintext copy can return through history, logs, indexes, or team state.
- **Optional surfaces stay optional.** The viewer, MCP server, team sidecar, and
  receipt signer do not become required runtime infrastructure.
