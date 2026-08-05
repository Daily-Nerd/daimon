# Revised refutation design: negative knowledge as an evidence ledger

Status: design proposal informed by the #573 retrospective. No production
implementation is included in this directory.

## Design position

A refutation is not a checkpoint item that happens to receive a stronger
weight. It is a project-level, evidence-bearing lifecycle record.

Checkpoints answer “what matters in this session?” Refutations answer “what
approach has already lost, in what scope, and what evidence would justify
trying it again?” Mixing those concerns makes permanence compete with an
attention budget and makes semantic state look like ranking metadata.

V1 should provide:

1. a dedicated append-only `refutations.jsonl` stream per project;
2. authored proposals and evidence-bound activation;
3. explicit query and distinct rendering;
4. exact-anchor guards for direct matches;
5. a deliberation check over approaches an agent is about to recommend;
6. explicit revise and overturn events;
7. instrumentation before any broader automatic injection.

V1 should not provide:

- a new checkpoint `ITEM_FIELDS` member;
- carry caps, decay exceptions, or ordinary recall-slot priority;
- automatic extraction writes;
- a hard command veto;
- a single scalar “trust score.”

## Why a separate stream

The stream belongs under the existing project bucket:

```text
~/.daimon/checkpoints/<project-slug>/refutations.jsonl
```

It must not reuse `events.jsonl`. Current `resolutions()` folds rows by
`item_ref` without filtering `kind`, and `is_resolved()` treats unknown
statuses as resolved. Scar 0025 documents that attaching a new event kind to
an item can silently remove that item from briefing, carry, and recall.

A separate stream costs another small fold and index path. In return it keeps
resolution semantics untouched, allows a purpose-built schema, and makes the
negative ledger auditable without pretending it is checkpoint state.

The stream is append-only. Current state is a deterministic fold; no command
rewrites or deletes historical assertions.

## Record and event model

One JSONL row is one lifecycle fact:

```json
{
  "version": 1,
  "ts": "2026-08-05T03:00:00Z",
  "event": "asserted",
  "refutation_id": "r-a81f4c2d",
  "subject": "original #502 receipt verdict tiers",
  "verdict": "whole-file hashes cannot substantiate span-level claims",
  "scope": "the original daimon why design for carried items",
  "anchors": ["issue:573", "issue:502", "command:daimon why"],
  "revisit_when": "the receipt binds and verifies the originating message span",
  "evidence": [
    {
      "kind": "measurement",
      "session_id": "source-session",
      "message_ids": ["source-message"],
      "claim": "566 of 623 carried items failed origin resolution"
    }
  ],
  "origin": {
    "author": "agent",
    "host": "agent-host",
    "requested_model": "requested-model",
    "served_model": "served-model"
  }
}
```

Lifecycle events:

- `asserted` creates a candidate and fixes its subject, verdict, scope,
  anchors, revisit condition, and evidence references.
- `ratified` records explicit human acceptance of the scoped verdict.
- `activated` records a mechanically checked outcome from a named verifier.
- `revised` creates a new version and names the prior version; it never edits
  the old assertion in place.
- `overturned` deactivates the record with new evidence and preserves both
  sides of the audit trail.

Derived states:

- `candidate`: asserted but not yet load-bearing;
- `active`: human-ratified or mechanically activated;
- `revised`: replaced by a newer scoped version;
- `overturned`: retained for history, excluded from guards.

Same-second ties must resolve from event content, not line order, following the
existing event-fold concurrency lesson.

## Trust is multidimensional

`[refuted]` describes semantic state. It does not answer who said it, whether
the source bytes exist, whether the evidence entails the verdict, or whether a
human accepted the scope.

Render those dimensions separately:

```text
[✗ refuted · human-ratified · measured]
#502 original receipt design
Whole-file hashes cannot substantiate span-level claims.
Scope: carried-item receipts in the original `daimon why` design.
Evidence: 566/623 origin-resolution misses.
Revisit when: receipts bind and verify the originating message span.
```

Required signals:

- provenance: origin author, host, session, and requested/served model where
  available;
- grounding: verbatim source, deterministic artifact, or measured outcome;
- authority: agent-proposed, mechanically activated, or human-ratified;
- lifecycle: active, revised, or overturned.

Byte-valid evidence proves provenance, not entailment. An agent-authored
claim with a matching quote remains a candidate unless a deterministic
verifier establishes the typed outcome or a human ratifies the interpretation.
This prevents a true quote attached to an unsupported generalization from
becoming a permanent veto.

## Authoring surface

Conceptual CLI:

```text
daimon refute add \
  --subject "original #502 receipt verdict tiers" \
  --verdict "whole-file hashes cannot substantiate span-level claims" \
  --scope "carried-item receipts in the original design" \
  --anchor issue:502 \
  --revisit-when "receipts bind and verify the origin message span" \
  --evidence message:<id>

daimon refute ratify r-a81f4c2d
daimon refute revise r-a81f4c2d ...
daimon refute overturn r-a81f4c2d --evidence message:<id>
daimon refute show r-a81f4c2d
daimon refute search "receipt verification"
```

`add` performs redaction and evidence-pointer validation before append. An
agent invocation creates a candidate by default. A human-authored invocation
may ratify explicitly; authorship alone must never be guessed from prose.

Extraction is a later proposal source only:

```text
raw transcript -> candidate extractor -> review queue -> human/mechanical gate
                                               |
                                               +-> never directly active
```

## Read and protection paths

### 1. Explicit query

`refute search/show/list` is the complete, auditable pull surface. Search does
not decay active records. Overturned records appear only when history is
requested.

### 2. Exact-anchor prompt guard

When a prompt names a strong anchor such as `issue:502`, a command, experiment
identifier, or exact subject alias, emit at most one active refutation in a
dedicated guard lane. It consumes no ordinary recall slot.

The lane is advisory, not a hard veto: “known negative result in this scope”
rather than “operation forbidden.” The human remains the decision maker.

Fuzzy topic matches may suggest an explicit lookup, but should not inject a
blocking verdict until false-veto evidence exists. Scope mismatch is the most
dangerous error direction.

### 3. Deliberation guard

Broad prompts such as “rank our open issues” do not contain the subject that
will appear in the answer. Prompt hooks cannot guard what has not been
proposed yet.

Before presenting recommendations, the agent-facing integration should batch
the candidate approaches and their anchors through a refutation lookup:

```text
human request
    -> agent develops candidate actions
    -> refutation lookup(candidate subjects + anchors)
    -> reconcile scope / revisit condition
    -> present recommendation with any warning
```

This is the primary prevention surface for the one observed historical
re-arm. It should produce a receipt recording which candidate was checked,
which refutation matched, and whether the agent withdrew, qualified, or kept
the recommendation because scope had changed.

### 4. Session briefing

Do not dump the permanent ledger into every briefing. Render a compact count
and only records connected to an explicit active topic or handoff anchor.
Until topic linkage is reliable, silence is better than a permanent wall of
warnings.

## Matching and false-veto posture

Match strength is ordered:

1. exact stable anchor;
2. exact normalized subject alias;
3. multiple salient-term overlap plus compatible scope;
4. semantic similarity, later and optional.

Only the first two may proactively emit in v1. Lower-confidence matches go to
explicit search or review. A `revisit_when` condition satisfied by current
evidence converts the interaction into reconsideration; it does not silently
overturn the old record.

Guards must record:

- match rail and matched anchors;
- whether scope was compatible;
- whether `revisit_when` appeared satisfied;
- disposition: prevented, qualified, legitimate reconsideration, false veto,
  or ignored.

## Instrumentation and expansion gates

V1 earns broader automation only with field evidence.

Measure separately:

- authored candidates and activation rate;
- explicit searches and useful-hit rate;
- exact-anchor guard fires;
- deliberation checks and prevented re-arms;
- legitimate reconsiderations;
- false vetoes;
- overturned records and time to overturn;
- records never read after creation.

Expansion gates:

- Prompt guard expansion requires at least one prevented re-arm and zero
  unresolved false vetoes in the observed set.
- Fuzzy proactive matching requires a pre-registered graded corpus and a
  false-veto upper bound chosen before implementation.
- Extraction may propose candidates only after an independent 40-span run
  reaches at least 80% precision with Wilson lower bound at least 60%.
- Extraction never activates a record, regardless of classifier score.

## Alternatives and tradeoffs

### Checkpoint refutation item

Reuses schema, rendering, and recall machinery, but couples permanence to
session attention, inherits carry/dedup behavior, and cannot protect broad
deliberation. Rejected for v1.

### Generic pinned item

Cheap, but “remember this” is not “this approach lost under these conditions.”
It lacks verdict scope, revisit semantics, and explicit overturn. Rejected.

### Existing `events.jsonl`

Avoids a new file, but violates scar 0025 under current resolution folding and
couples two lifecycle domains. Rejected unless the event architecture is
redesigned first—a much larger change than #573 warrants.

### Repository scars

Excellent for code-anchored hazards and edit-time enforcement. Refuted
experiments often have no surviving file or symbol and must surface during
deliberation. Complementary, not interchangeable.

### Host-curated memory

Immediately available but host-locked, free-form, and unaudited. It is the
failure source #573 exists to bridge.

## Recommended delivery slices

1. **Ledger kernel:** schema validation, append/fold, redaction, evidence
   pointers, lifecycle, and explicit CLI query. No proactive behavior.
2. **Human surface:** distinct rendering, ratify/revise/overturn workflows,
   and audit receipts.
3. **Exact-anchor guard:** one dedicated advisory result plus measurements.
4. **Deliberation integration:** batch-check proposed issue/experiment anchors
   before recommendations are emitted.
5. **Only after evidence:** candidate extraction and fuzzy proactive matching.

Each slice is independently useful and keeps the dangerous direction—wrongly
blocking a good idea—fail-open and visible.
