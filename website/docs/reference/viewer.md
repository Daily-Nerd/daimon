---
description: "daimon serve opens a local read-only view of one project's memory in your browser. Every surface renders existing engine output, and no route changes your memory."
---

# Viewer (read-only)

`daimon serve` opens a local, read-only view of one project's memory in your
browser. Every surface renders an existing engine's output — the viewer has no
logic of its own to disagree with the CLI, and nothing in it writes.

```bash
daimon serve                      # binds 127.0.0.1:7717, opens a browser tab
daimon serve --port 7800 --no-browser
```

Flags: `--data-dir` (checkpoint dir, and the store for every route; default
is the directory the CLI uses: `DAIMON_CHECKPOINT_DIR` from the environment or
from `~/.daimon/env`, then `~/.daimon/checkpoints`), `--project-dir` (project to
scope to, default the working directory, resolved to the project root the way
the CLI resolves `--project`), `--port` (default 7717), `--no-browser`.

## What you see

- **Search** is `daimon recall`, rendered. The results header says so —
  what you find in the browser is what an agent would be briefed with.
- **Every entry has a "why" page**: the stored text, its origin, the stored
  quote, a transcript context window (fetched at read time, never stored),
  the evidence axes behind the item, a **Life** panel showing how the entry
  changed across checkpoints, and a **History** panel rendering its
  human-confirmed relations (see [relations](relations.md)).
- **Sibling views** alongside the entry page: the project ledger, a session
  page, a **Refutations** page reading the negative-knowledge ledger, a
  **Check strip**, a checkpoint **Diff**, and a **print view** that sets one
  checkpoint as a printed record.

## What the viewer does not show

The project list, the checkpoint and session lists, a checkpoint and the Diff
show only what a reader of the project may see. An item you quarantined or
forgot does not appear in them, and a withheld topic reads as no topic. Counts
are counts of the items you can see, so a number never tells you that
something is hidden. If the trust ledger cannot be read, no item is shown and
the page says which ledger failed.

The Diff lists an item that left between two checkpoints as resolved when a
resolution closed it, whatever status closed it (the rule `daimon diff` uses),
and as dropped otherwise.

## Read-only as a commitment

The server answers GET requests only, and no route writes to your checkpoints,
ledgers or trust records. Confirming a relation, resolving a loop, or
forgetting an item all stay in the CLI, where the terminal enforces who is
speaking.

One derived cache is the exception: `/api/recall` may rebuild the recall index
(`recall.db`) when it no longer matches the store, and it logs an index error
beside it. The index is rebuilt from your ledgers and holds nothing the store
does not.

On a tenant-scoped home (`DAIMON_TENANT_SCOPED`) the viewer lists and opens only
the current project, the same rule `daimon projects` and the MCP projects tool
follow. If a request fails inside the viewer it answers HTTP 500 with a generic
JSON error that names only the exception type.

## Localhost-only posture

The server binds `127.0.0.1` and additionally refuses any request whose `Host`
header is not `127.0.0.1` or `localhost`. Nothing is exposed to your network,
and nothing is transmitted anywhere — the viewer reads the same local files
the CLI reads.
