# Viewer (read-only)

`daimon serve` opens a local, read-only view of one project's memory in your
browser. Every surface renders an existing engine's output — the viewer has no
logic of its own to disagree with the CLI, and nothing in it writes.

```bash
daimon serve                      # binds 127.0.0.1:7717, opens a browser tab
daimon serve --port 7800 --no-browser
```

Flags: `--data-dir` (checkpoint dir, default `DAIMON_CHECKPOINT_DIR` then
`~/.daimon/checkpoints`), `--project-dir` (project to scope to, default the
working directory), `--port` (default 7717), `--no-browser`.

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

## Read-only as a commitment

Read-only is structural, not a promise: the server answers GET requests only —
there is no code path that writes. Confirming a relation, resolving a loop, or
forgetting an item all stay in the CLI, where the terminal enforces who is
speaking.

## Localhost-only posture

The server binds `127.0.0.1` and additionally refuses any request whose `Host`
header is not `127.0.0.1` or `localhost`. Nothing is exposed to your network,
and nothing is transmitted anywhere — the viewer reads the same local files
the CLI reads.
