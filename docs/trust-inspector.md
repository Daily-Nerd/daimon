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
corroboration references, the durable receipt when one exists, a `ranking`
block, and no derived `summary` field. Scripts should inspect the axes they
actually care about.

### Ranking

`ranking` publishes the ordering key Daimon itself uses, together with the
inputs that produced it:

```json
{
  "effective_weight": 0.448,
  "computed_at": 1800000000.0,
  "item_type": "recent_decision",
  "rules": "recent_decision",
  "inputs": {
    "importance": 8,
    "importance_source": "item",
    "trust": "verbatim",
    "trust_ceiling": 3.0,
    "first_seen": "2026-08-01T00:00:00Z",
    "age_days": 10.0
  },
  "factors": {
    "base": 0.8,
    "recency": 0.7,
    "type_decay": 0.8,
    "overdue_boost": 1.0,
    "raw": 0.448
  }
}
```

The weight is recomputed on every read and is never stored, because it decays
with age: a value written at capture time would be stale by the time anything
read it. `computed_at` is the epoch it was computed against, and the number
cannot be interpreted without it.

The point of publishing the inputs is that a consumer can redo the arithmetic
and land on the same number. `raw` is the product of the four factors, and
`effective_weight` is that product under the trust ceiling. The ceiling
saturates rather than truncating, so `raw` above the ceiling is normal and
still ordered.

Two values are deliberately not what they look like:

- `importance_source: "default"` means the item carried no importance and 5
  was substituted. An unscored item is not an importance-5 item.
- `age_days: null` means no usable `first_seen`, which is not the same as a
  brand new item. Recency falls back to neutral rather than maximum.

`rules` names the type rules the computation actually applied, which differs
from `item_type` whenever the item's kind maps to no known type.

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
