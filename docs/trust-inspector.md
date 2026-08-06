# Trust Inspector

`daimon why` explains what Daimon can prove about one recalled item without
collapsing several kinds of evidence into a trust score.

The normal workflow is:

```console
daimon recall retry policy
daimon why o-3f8a2c
daimon resolve o-3f8a2c --dry-run
daimon resolve o-3f8a2c --note "shipped in 0.27.0"
```

`recall` prints the exact item ID in brackets. `why` is project-scoped by
default, just like `recall`; use `--project PATH` or `--slug SLUG` to name a
different scope explicitly. It never guesses across projects.

## Reading the evidence

The inspector reports independent axes because they can legitimately disagree:

| Axis | Values | Meaning |
| --- | --- | --- |
| Capture | `verified`, `not-verified`, `unknown` | The deterministic capture-time quote verdict recorded in a durable receipt. |
| Provenance | `bound`, `legacy-inferred`, `legacy-unbound` | Whether the quote carries a self-contained source receipt or only older diagnostic metadata. |
| Locator | `resolved`, `absent-local`, `unsupported`, `ambiguous`, `unreadable`, `remote-author` | Whether the strict host resolver can identify exactly one readable local source. |
| Bytes | `unchanged`, `changed`, `unknown` | Whether current source bytes reproduce the receipt digest. |
| Current support | `message-id-match`, `transcript-scan-match`, `not-reproduced`, `not-checked` | Whether the stored quote can be reproduced now. |
| Verifier | `same-version`, `different-version`, `unknown` | Whether the current deterministic verifier matches the recorded verifier. |
| Lifecycle | `active`, `resolved`, `forgotten`, `superseded` | The latest append-only lifecycle state. |

Corroboration is shown separately as a count and source-session references. It
does not modify any evidence axis.

For example, these facts can coexist:

```text
Now: capture verified; source changed; quote supported by its bound message
Bytes: changed
Current support: message-id-match
```

That does not mean the item is fabricated or invalid. It means the complete
source file changed while the bound message still supports the quote. The axes
preserve that distinction instead of inventing one verdict.

## JSON

Use `--json` for automation:

```console
daimon why o-3f8a2c --json
```

The V1 document has `schema_version`, stable values under `axes`, item metadata,
corroboration references, the durable receipt when one exists, and no derived
`summary` field. Scripts should inspect the axes they actually care about.

Exit codes describe command execution, not epistemic state:

- `0`: the uniquely scoped item was rendered, including degraded evidence states;
- `1`: the item was not found in that project;
- `2`: invalid arguments or item-ID shape.

## Source disclosure

Default output is metadata-only and prints no raw transcript excerpt. Add
`--source` deliberately:

```console
daimon why o-3f8a2c --source
```

When validated message bindings are available, Daimon reads the raw source in
memory, extracts at most three bound messages and 600 collapsed characters,
then redacts once at the final display boundary. It never persists the excerpt
or prints an absolute transcript path.

When exact bindings are unavailable, the inspector shows the already-redacted
stored quote and states that the exact raw message span cannot be reconstructed
safely. It does not redact stored evidence a second time.

`not-reproduced` is a current observation, not an accusation. Sources can be
edited, truncated, migrated, or parsed differently by a newer host adapter.
Use the other axes to understand which condition actually changed.
