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

`preceding_tool_context` is a separate audit section. Its `observed` state
maps each verified assistant quote source to up to 20 preceding tool-result
row IDs, ending at a recognized host user-input boundary. `unavailable` names
why the host capture could not establish that window, and `not_recorded` is
used for older checkpoints. An empty result list under `observed` means the
bounded window contained no tool results. These IDs describe recorded order
only and never prove that the model read or used a result; tool output and
arguments are never stored.

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

### Which sets refuse the window

The window is drawn from the raw transcript, and redaction only catches known
secret shapes, never free text someone deliberately forgot or a person
quarantined. No key can scan a block of transcript for one value, so the
refusal is set-based rather than per item. `--source` is refused when any of
these holds, and the refusal names the reason and never a count:

| `reason` | When |
| --- | --- |
| `closed` | The trust ledger cannot be read, so nothing can be proven not quarantined. |
| `forgotten-set` | Any forget tombstone exists on this machine, in any project, or a teammate published one. |
| `quarantine-set` | This project holds an active quarantine. |
| `withheld-item` | The item itself is withheld. |

The cost is stated plainly: once anything has been forgotten anywhere on the
machine, `why --source` is refused everywhere, for good, because tombstones
never expire. The item itself still prints; only the transcript excerpt is
withheld. A count would say how many values other tenants forgot, so none is
published.

## A withheld id

An exact id the reader may not see still answers, with the marker in place of
the value:

```console
$ daimon why o-3f8a2c
Now: capture unknown; item withheld; lifecycle active
Item: [o-3f8a2c] [question] [withheld: quarantine tr-1a2b3c4d5e6f] (daimon trust show tr-1a2b3c4d5e6f)
Lifecycle: active
```

The marker is one of `[withheld: quarantine tr-…]` (a person quarantined the
value; `daimon trust show` has the record), `[withheld: trust ledger
unreadable]` (nothing can be proven not quarantined; run `daimon status`) and
`[withheld: forgotten]`. In `--json` the item's `text` and `quote` are
`{"state": "withheld", "reason": …}` (plus `quarantine_id` for a quarantine),
`ranking`, `receipt`, `source` and `preceding_tool_context` are `null`, and
`current_support` is `withheld`. No value, key, quote, tool context or
transcript window is in the document. The evidence axes that rest on the
item's own receipt report `unknown` for a withheld item, because reading them
would mean reading the item.

Forgotten is announced only here: `daimon why`, `daimon blame` and the
viewer's why page say `[withheld: forgotten]` for the one exact id you asked
about. Every listing (`daimon diff`, recall, the briefing, `status`, the
viewer's tables) treats a forgotten value as absent: no row, no count, no edge.
Asking about an id that was never stored answers `no item`, so the one thing
an exact-id question can learn is that this id was stored and later forgotten,
which is the same fact a teammate already receives as a tombstone.

An id whose copies are gone but whose lifecycle the ledger still names (a
resolved loop that left every retained checkpoint) prints `Item: … (content
unavailable)` and its lifecycle; `daimon blame` answers the same id the same
way.

## Where an id is looked up

A lookup is bounded by this project's bucket, never by the machine, and never
rebuilds the recall index:

1. the pointer window (`latest.json` and the `prev-N.json` files kept by
   `DAIMON_CHECKPOINT_HISTORY`, three by default); the newest `created` stamp
   wins when several copies differ;
2. the recall index, opened read-only, only to learn which session file holds
   an id the window no longer does; the value comes from that file through the
   same judgement as everything else;
3. the forgotten-id and quarantine records;
4. nothing.

An item answered from the index alone (a teammate's mirrored checkpoint, or a
legacy file with no project stamp) is judged at read time like any other copy
and is marked `index_only` in the JSON.

What this changes, each in the safe direction (it can under-announce, never
leak):

- A quarantined item older than the pointer window, whose quarantine record
  was opened without `--item-id`, reads `no item`.
- The same id can read `[withheld: quarantine …]` while the index still holds
  its pre-quarantine row and `no item` after the next index rebuild, so the
  answer can change with the timing of `daimon recall` or any other call that
  warms the index.
- `why` answers for this project's own bucket. It no longer reaches the other
  slugs listed in `DAIMON_EXTRA_READ_SLUGS`.
- An item serialized since the last index build is found through the pointer
  window only.

`occurrences` counts the distinct sessions the window and the index know to
hold the id, not every copy on disk.
