---
sidebar_position: 3
---

# Receipts and offline verification

Receipts are daimon's answer to a question most memory systems cannot take
seriously: *how do you know the memory file wasn't edited after it was
written?*

## What a receipt is

With `DAIMON_RECEIPTS=1`, every checkpoint is paired with a
[vitni](https://github.com/Daily-Nerd/vitni) receipt — an Ed25519-signed
statement written to a `<session>.receipt` sidecar file that binds:

- the checkpoint's **exact on-disk bytes** (`outputs_hash`), to
- its **source transcript** (`inputs_hash`).

If anyone edits the checkpoint file after the fact — by hand, by script, by a
well-meaning cleanup job — the bytes no longer match the signature, and the
edit is detectable.

Everything happens locally. Receipts are minted offline, verified offline, and
nothing ever leaves the machine. There is no service, no timestamping
authority, no network call.

## Verifying

```sh
daimon verify-receipt              # this project's latest checkpoint
daimon verify-receipt <session-id> # a specific one
```

Verification is also woven into the briefing path: when a checkpoint from the
receipt era has a missing receipt, or a receipt that no longer matches the
file's bytes, the briefing does not silently trust it — the affected
`✓ verbatim` labels are **degraded with a visible note**. A verbatim claim is
only as strong as the integrity of the bytes behind it, and the briefing says
so when that integrity cannot be shown (see
[trust classes](./trust-classes.md)).

## Fail-open by design

Receipts never block memory. A missing vitni CLI, a timeout, or a bad signing
step logs one line and the serialize proceeds without a receipt — a receipts
failure cannot cost you a checkpoint or a briefing. Signing keys are
auto-created on first mint under `~/.daimon/keys` (mode 0600).

Receipts are **off by default** (each mint spawns a subprocess); the full knob
list — `DAIMON_RECEIPTS`, `DAIMON_VITNI_CLI`, `DAIMON_KEYS_DIR` — is in the
[configuration reference](../getting-started/configuration.md#receipts).

## Receipts and deletion

Deletion composes with receipts instead of fighting them. `daimon forget`
removes an item and **re-mints the receipt over the post-removal checkpoint**,
while an append-only tombstone event records that a removal happened — by
content hash, never by content. Hand-editing a checkpoint breaks its receipt;
forgetting through the CLI leaves a signed, provable trail. The
[item lifecycle](./lifecycle.md) page covers the mechanics.
