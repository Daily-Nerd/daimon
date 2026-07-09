# Agent hook adapters

> Setting up daimon for your agent? See [docs/hosts/](../docs/hosts/) for
> per-host install and setup guides. This file is contributor-facing
> reference for the adapter internals.

## What an adapter is

Each host adapter is a **standalone, stdlib-only** script (or script pair)
that a host invokes at its own session boundaries. It cannot import the
`daimon_briefing` package — that lives in an isolated `uv tool` venv — so it
locates and shells out to the installed `daimon` CLI, which owns all
serialization, rendering, and storage. The CLI is the single source of truth:
no host re-renders a checkpoint or re-implements parsing, so hosts never
drift from each other.

## Script-pair shape

A host adapter is one or two scripts against the same host-boundary events:

- A **read-path** script fires on a session/turn-start-shaped event, shells
  out to `daimon brief` (or `daimon recall-inject` for per-prompt recall),
  and returns the result in whatever envelope the host expects (raw stdout
  for Claude Code, `additionalContext` JSON for Codex/Gemini). Always
  fail-open: exit 0 even on failure, with a one-line diagnostic instead of a
  silent death (except per-prompt recall hooks, which stay silent on failure
  by design — see `daimon-prompt-recall.py`).
- A **write-path** script fires on a session/turn-end-shaped event and spawns
  `daimon serialize <transcript>` as a **detached** background process
  (`start_new_session=True`) — serialization is an LLM call (30s+), so the
  hook must return immediately rather than block the host's exit.

Hosts without a clean session-end event (Codex, Windsurf) serialize
opportunistically on a throttled turn-scoped event instead
(`DAIMON_CODEX_MIN_SERIALIZE_INTERVAL`,
`DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL`).

Per-host lifecycle managers (`daimon-hooks.py`, `codex-hooks.py`,
`gemini-hooks.py`) install/uninstall/status their host's script(s) into the
host's own config location, idempotently, with a settings backup before every
mutation.

## `_daimon_hook_lib.py`

Everything the adapters would otherwise duplicate lives in the shared
`hook/_daimon_hook_lib.py`: kill-switch check (`DAIMON_DISABLE`), `daimon`
CLI resolution (with the deprecated `daimon-briefing` alias as fallback),
per-project env (`DAIMON_PROJECT_DIR`), detached-spawn helpers, and
checkpoint-age formatting. Host-specific behavior — Gemini's pure-JSON
stdout, Codex's `additionalContext` envelope, Windsurf's three-event script —
stays in each script. A new host becomes first-class by adding a script pair
against this same shared lib.

## Probe conventions

When an adapter meets a payload shape it can't handle, it self-probes instead
of silently dropping data: dump the raw payload to
`~/.daimon/<host>/unparsed-<event>-<stamp>.json` (at most one dump per event
name), so the next iteration has real evidence instead of another manual
probe round. `daimon-windsurf-probe.py` is a separate, standalone dev tool
(not installed for end users) that captures raw Cascade hook payloads and
scans Windsurf's sqlite state (`--scan-vscdb`) — the ground-truth-gathering
step the Windsurf adapter was built from.

## `redact.py` ships alongside the hooks

`daimon hooks install <host>` copies `redact.py` next to the hook script(s)
so a standalone host adapter can scrub secrets at its own write sites
(transcript accumulation, checkpoint, event log) without importing the
venv-only `daimon_briefing` package. Re-run `daimon hooks install <host>`
after every `daimon` upgrade so the installed scripts (and the bundled
`redact.py`) stay in sync with the installed CLI.

## Drift guard

Canonical adapter sources live here in `hook/`; the Claude Code plugin and
the manual installers read them directly from this directory. Packaged
copies also ship inside the PyPI wheel at
`plugin/daimon_briefing/_hooks/` (so `daimon hooks install <host>` works
without a repo clone) and at `plugin/daimon_briefing/_hooks/redact.py` (the
copy of `plugin/daimon_briefing/redact.py` that installs alongside standalone
hosts). A byte-equality test (`plugin/tests/test_hooks_install.py`) guards
both copies against drift — when you edit a script or `redact.py` here, copy
the change into `plugin/daimon_briefing/_hooks/` in the same change.

### Editing hook-shipped files

Don't copy the duplicates by hand. Edit the canonical source, then run the
sync script, then commit both:

1. Edit the canonical file (`plugin/daimon_briefing/redact.py`, or the
   adapter source here in `hook/`).
2. `uv run python scripts/sync_hooks.py` — copies each canonical file to its
   mirrors byte-for-byte.
3. Commit the canonical file and its synced copies together.

Run `uv run python scripts/sync_hooks.py --check` to see which copies (if any)
have drifted without writing anything; it drives the same manifest the
byte-equality test reads.
