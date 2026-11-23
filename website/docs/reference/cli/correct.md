---
sidebar_position: 5
description: "Every `daimon` command in the Correct group: usage, arguments and defaults, generated from the CLI."
---

# Correct

Change what daimon carries: close, re-assert, amend, refute and rule.

:::note
This page is generated from the CLI's argument parsers. Do not edit it: change the flag or its help text in the code, then run `UPDATE_DOCS_REFERENCE=1 uv run pytest tests/test_docs_generated.py` from `plugin/`.
:::

See also: [Correct memory](../../guides/correct-memory.md), [Rulings and checks](../../guides/rulings-and-checks.md), [Lifecycle](../../concepts/lifecycle.md), [Rulings](../../concepts/rulings.md), [Negative knowledge](../../concepts/negative-knowledge.md).

## `daimon resolve`

mark a checkpoint item resolved: append-only event, folds at read so the item stops carrying (#102)

```text
daimon resolve target [--status STATUS] [--note NOTE] [--by agent] [--evidence EVIDENCE] [--project PROJECT] [--dry-run] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `target` |  |  | item id (exact) or a query that must match exactly one item |
| `--status` | `STATUS` | `resolved` | free-form lifecycle status (default: resolved; a status starting with 'reopen' revives the item) |
| `--note` | `NOTE` |  | optional context recorded on the event |
| `--by` | `agent` |  | declare an agent-initiated claim (requires --evidence); omit for the human path (default) |
| `--evidence` | `EVIDENCE` |  | verbatim transcript quote proving the claim: required with --by agent, rejected without it |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--dry-run` |  |  | show what would resolve without writing an event: look before the write (#304) |
| `--json` |  |  | print &#123;item_id, status, event_id&#125; instead of the sentence; the event_id is the one `why --json` shows as lifecycle_event.event_id |

**Examples**

```text
daimon resolve o-3f8a2c
daimon resolve "release pipeline approval" --note "shipped in 0.9"
```

## `daimon reverify`

evidence-gated human reopen of a resolved item (#103): requires an interactive terminal and refuses without proof

```text
daimon reverify target [--evidence EVIDENCE] [--project PROJECT]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `target` |  |  | item id (exact only: reverify is deliberate, no fuzzy match) |
| `--evidence` | `EVIDENCE` |  | why this claim can be trusted again |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

**Examples**

```text
daimon reverify o-3f8a2c --evidence "checked release page"
daimon reverify o-3f8a2c   # reopens only if the anchor still checks live
daimon reverify o-3f8a2c   # rejects an unconfirmed supersede candidate: no evidence needed
```

## `daimon trust`

record or settle a human quarantine on a checkpoint value (#1109): an active one is withheld from every read, and is published to the team when one is enabled

```text
daimon trust <command>
```

| command | what it does |
| --- | --- |
| [`propose`](#daimon-trust-propose) | propose a quarantine; agent proposals stay candidates until a human confirms or dismisses them |
| [`confirm`](#daimon-trust-confirm) | activate a candidate quarantine as a human decision |
| [`dismiss`](#daimon-trust-dismiss) | reject a candidate quarantine as a human decision |
| [`release`](#daimon-trust-release) | lift an active quarantine as a human decision |
| [`republish`](#daimon-trust-republish) | re-send this project's active and released quarantines to the team sidecar; a human act, safe to repeat |
| [`list`](#daimon-trust-list) | list project quarantines, candidates first |
| [`repair`](#daimon-trust-repair) | alias for `daimon ledger repair trust`: heal trust.jsonl and re-run forget over it |
| [`show`](#daimon-trust-show) | show one quarantine record |

**Examples**

```text
daimon trust propose --text "the runbook was fabricated" --kind decision --reason "no matching PR" --evidence issue:1109 --by agent
daimon trust confirm tr-0123456789ab
daimon trust release tr-0123456789ab
```

### `daimon trust propose`

propose a quarantine; agent proposals stay candidates until a human confirms or dismisses them

```text
daimon trust propose --text TEXT --kind belief|contradiction|decision|question|topic|uncertainty --reason REASON --evidence EVIDENCE [--item-id ITEM_ID] [--by agent] [--project PROJECT]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--text` | `TEXT` |  | the checkpoint value to quarantine, verbatim (required) |
| `--kind` | `belief\|contradiction\|decision\|question\|topic\|uncertainty` |  | which checkpoint field kind this concerns (required) |
| `--reason` | `REASON` |  | why this value is unsafe to act on (required) |
| `--evidence` | `EVIDENCE` |  | typed evidence source (repeatable): message:&lt;id&gt;, transcript:&lt;session&gt;, artifact:&lt;path&gt;, issue:&lt;number&gt;, measurement:&lt;receipt&gt;, receipt:&lt;id&gt;, url:&lt;source&gt; (required) |
| `--item-id` | `ITEM_ID` |  | the checkpoint item id this was observed on (an accelerator only: identity is the value, not this id) |
| `--by` | `agent` |  | declare yourself an agent; omit it only from an interactive terminal, which is the human path and lands active immediately |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

### `daimon trust confirm`

activate a candidate quarantine as a human decision

```text
daimon trust confirm quarantine_id [--by agent] [--project PROJECT]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `quarantine_id` |  |  | exact tr-… id |
| `--by` | `agent` |  | declare yourself an agent; confirm then refuses, because it requires a human channel |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

### `daimon trust dismiss`

reject a candidate quarantine as a human decision

```text
daimon trust dismiss quarantine_id [--by agent] [--project PROJECT]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `quarantine_id` |  |  | exact tr-… id |
| `--by` | `agent` |  | declare yourself an agent; dismiss then refuses, because it requires a human channel |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

### `daimon trust release`

lift an active quarantine as a human decision

```text
daimon trust release quarantine_id [--by agent] [--project PROJECT]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `quarantine_id` |  |  | exact tr-… id |
| `--by` | `agent` |  | declare yourself an agent; release then refuses, because it requires a human channel |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

### `daimon trust republish`

re-send this project's active and released quarantines to the team sidecar; a human act, safe to repeat

```text
daimon trust republish [--by agent] [--project PROJECT]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--by` | `agent` |  | declare yourself an agent; republish then refuses, because it requires a human channel |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

### `daimon trust list`

list project quarantines, candidates first

```text
daimon trust list [--project PROJECT] [--json] [--team]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |
| `--team` |  |  | list the quarantines teammates published instead: kind and count anywhere, author directory and ts at a terminal |

### `daimon trust repair`

alias for `daimon ledger repair trust`: heal trust.jsonl and re-run forget over it

```text
daimon trust repair [--project PROJECT] [--dry-run]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--dry-run` |  |  | report what would change and write nothing |

### `daimon trust show`

show one quarantine record

```text
daimon trust show quarantine_id [--project PROJECT] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `quarantine_id` |  |  | exact tr-… id |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |

## `daimon anchor`

resolve a code symbol to an anchor block for a cognitive item

```text
daimon anchor file symbol [--project PROJECT] [--attach TEXT-MATCH]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `file` |  |  | repo-relative path to the source file |
| `symbol` |  |  | symbol name or Class.method |
| `--project` | `PROJECT` |  | project root the file is relative to (default: cwd) |
| `--attach` | `TEXT-MATCH` |  | attach the anchor to the one checkpoint item whose text contains TEXT-MATCH (case-insensitive), re-writing the latest checkpoint |

**Examples**

```text
daimon anchor daimon_briefing/cli.py _cmd_brief
daimon anchor pkg/mod.py MyClass.method --project .
daimon anchor pkg/mod.py fn --attach 'auth decision'
```

## `daimon refute`

author and query evidence-cited negative knowledge (#573)

```text
daimon refute <command>
```

| command | what it does |
| --- | --- |
| [`add`](#daimon-refute-add) | assert a scoped refutation; agent assertions stay candidates |
| [`ratify`](#daimon-refute-ratify) | explicitly activate a candidate as a human decision |
| [`revise`](#daimon-refute-revise) | append a new evidence-cited version; inactive until ratified |
| [`overturn`](#daimon-refute-overturn) | end a refutation: a candidate as rejected, an active one as retired; agent calls propose, humans deactivate |
| [`show`](#daimon-refute-show) | show one refutation and its trust signals |
| [`list`](#daimon-refute-list) | list project refutations |
| [`search`](#daimon-refute-search) | search the complete ledger without age decay |
| [`guard`](#daimon-refute-guard) | check active refutations by exact anchor or subject phrase |

**Examples**

```text
daimon refute list
daimon refute search receipt verification
daimon refute guard 'should we revisit #502?'
```

### `daimon refute add`

assert a scoped refutation; agent assertions stay candidates

```text
daimon refute add --subject SUBJECT --verdict VERDICT --scope SCOPE --evidence SOURCE [--anchor ANCHOR] [--revisit-when REVISIT_WHEN] [--by agent] [--ratify] [--project PROJECT] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--subject` | `SUBJECT` |  | approach or claim rejected (required) |
| `--verdict` | `VERDICT` |  | what no longer holds (required) |
| `--scope` | `SCOPE` |  | where the verdict applies (required) |
| `--evidence` | `SOURCE` |  | cited measurement, artifact, issue, or transcript source; recorded verbatim, not resolved or verified; repeatable (required) |
| `--anchor` | `ANCHOR` |  | stable exact-match key such as issue:502 or command:daimon-why; repeatable |
| `--revisit-when` | `REVISIT_WHEN` |  | condition that makes reconsideration legitimate |
| `--by` | `agent` |  | declare yourself an agent; omit it only from an interactive terminal, which is the human path |
| `--ratify` |  |  | activate immediately; valid only on the human path, which is an interactive terminal with no --by. |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |

**Examples**

```text
daimon refute add --subject 'original #502 receipt design' --verdict 'whole-file hashes do not prove span claims' --scope 'carried-item receipt tiers' --anchor issue:502 --evidence 'measurement:566/623 origin misses' --ratify
```

### `daimon refute ratify`

explicitly activate a candidate as a human decision

```text
daimon refute ratify refutation_id [--by agent] [--note NOTE] [--project PROJECT] [--slug SLUG] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `refutation_id` |  |  | exact r-… id |
| `--by` | `agent` |  | declare yourself an agent; ratification then refuses, because activation requires a human channel |
| `--note` | `NOTE` |  | optional ratification rationale |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--slug` | `SLUG` |  | route to another project's bucket by its slug, written --slug=&lt;slug&gt; (a slug starts with '-'); routing only, the channel gate is unchanged (#766) |
| `--json` |  |  | machine-readable output |

### `daimon refute revise`

append a new evidence-cited version; inactive until ratified

```text
daimon refute revise refutation_id [--subject SUBJECT] [--verdict VERDICT] [--scope SCOPE] [--anchor ANCHOR] [--revisit-when REVISIT_WHEN] --evidence SOURCE [--by agent] [--ratify] [--project PROJECT] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `refutation_id` |  |  | exact r-… id |
| `--subject` | `SUBJECT` |  | replacement subject |
| `--verdict` | `VERDICT` |  | replacement verdict |
| `--scope` | `SCOPE` |  | replacement scope |
| `--anchor` | `ANCHOR` |  | replacement anchor set; repeatable |
| `--revisit-when` | `REVISIT_WHEN` |  | replacement revisit condition |
| `--evidence` | `SOURCE` |  | new evidence cited for the revision; recorded, not verified; repeatable (required) |
| `--by` | `agent` |  |  |
| `--ratify` |  |  | activate the revision immediately; valid only on the human path, which is an interactive terminal with no --by. |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |

### `daimon refute overturn`

end a refutation: a candidate as rejected, an active one as retired; agent calls propose, humans deactivate

```text
daimon refute overturn refutation_id [--evidence SOURCE] [--note NOTE] [--by agent] [--project PROJECT] [--slug SLUG] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `refutation_id` |  |  | exact r-… id |
| `--evidence` | `SOURCE` |  | evidence cited against the verdict; required for an active refutation, not for a candidate; recorded, not verified; repeatable |
| `--note` | `NOTE` |  | optional explanation |
| `--by` | `agent` |  |  |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--slug` | `SLUG` |  | route to another project's bucket by its slug, written --slug=&lt;slug&gt; (a slug starts with '-'); routing only, the channel gate is unchanged (#766) |
| `--json` |  |  | machine-readable output |

### `daimon refute show`

show one refutation and its trust signals

```text
daimon refute show refutation_id [--project PROJECT] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `refutation_id` |  |  | exact r-… id |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |

### `daimon refute list`

list project refutations

```text
daimon refute list [--state active|candidate|overturned] [--project PROJECT] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--state` | `active\|candidate\|overturned` |  | filter by state; repeatable |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |

### `daimon refute search`

search the complete ledger without age decay

```text
daimon refute search query ... [--state active|candidate|overturned] [--project PROJECT] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `query` |  |  | subject, verdict, scope, or anchor terms |
| `--state` | `active\|candidate\|overturned` |  | filter by state; repeatable |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |

### `daimon refute guard`

check active refutations by exact anchor or subject phrase

```text
daimon refute guard query ... [--anchor ANCHOR] [--project PROJECT] [--json] [--quiet]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `query` |  |  | the proposed approach or user prompt |
| `--anchor` | `ANCHOR` |  | candidate action anchor; repeatable |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |
| `--quiet` |  |  | print nothing when no active refutation matches |

## `daimon ruling`

standing rulings: human-ratified records that never decay (#693)

```text
daimon ruling <command>
```

| command | what it does |
| --- | --- |
| [`propose`](#daimon-ruling-propose) | propose a standing ruling; agent proposals stay candidates until a human ratifies |
| [`ratify`](#daimon-ruling-ratify) | activate a candidate ruling; prints the full text and confirms before the append |
| [`revise`](#daimon-ruling-revise) | revise a ruling; on an active ruling an agent call records a proposal and the text stands |
| [`retire`](#daimon-ruling-retire) | end a ruling: a candidate as rejected, an active one as retired; agent calls propose, humans retire directly; evidence optional |
| [`check`](#daimon-ruling-check) | the check a ruling carries (#943): try it before a human arms it |
| [`checks`](#daimon-ruling-checks) | what this project has armed, the mode each host gives it, and whether it ever fired (#943) |
| [`list`](#daimon-ruling-list) | list project rulings |
| [`show`](#daimon-ruling-show) | show one ruling, including pending proposals |

**Examples**

```text
daimon ruling propose --subject 'public posts' --verdict 'internal numbers never appear in public posts' --scope publishing --evidence issue:693 --by agent
daimon ruling ratify r-5b19c3
```

### `daimon ruling propose`

propose a standing ruling; agent proposals stay candidates until a human ratifies

```text
daimon ruling propose --subject SUBJECT --verdict VERDICT --scope SCOPE --evidence SOURCE [--anchor ANCHOR] [--revisit-when REVISIT_WHEN] [--by agent] [--ratify] [--check-body-file PATH] [--check-match REGEX] [--check-intent enforce|record-only|warn] [--request-policy KEY=VALUE] [--project PROJECT] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--subject` | `SUBJECT` |  | what the ruling governs (required) |
| `--verdict` | `VERDICT` |  | the ruling text itself (max 280 chars; refused when it is too long to be shown whole to a person signing it) (required) |
| `--scope` | `SCOPE` |  | where it applies (required) |
| `--evidence` | `SOURCE` |  | cited source behind the ruling; recorded verbatim, not resolved or verified; repeatable (required) |
| `--anchor` | `ANCHOR` |  | stable exact-match key such as issue:693; repeatable |
| `--revisit-when` | `REVISIT_WHEN` |  | condition that makes reconsideration legitimate |
| `--by` | `agent` |  | declare yourself an agent; omit it only from an interactive terminal |
| `--ratify` |  |  | activate immediately; valid only on the human path |
| `--check-body-file` | `PATH` |  | file whose contents become the ruling's check script; the bytes are stored, the path is not (#943) |
| `--check-match` | `REGEX` |  | regex on the command string that selects the actions the check runs before |
| `--check-intent` | `enforce\|record-only\|warn` |  | what the check asks each host for; the host delivers the strongest it supports (default warn) |
| `--request-policy` | `KEY=VALUE` |  | grant this ruling authorizes, one of two shapes: sender=&lt;slug&gt; kind=work\|info verb=accept by=agent (a sender this project trusts to record its own accept), or to=&lt;slug&gt; kind=info verb=open by=agent (this project's own agent may open an info ask there); repeatable, all four required per shape (#961) |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |

### `daimon ruling ratify`

activate a candidate ruling; prints the full text and confirms before the append

```text
daimon ruling ratify ruling_id [--by agent] [--note NOTE] [--project PROJECT] [--slug SLUG] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `ruling_id` |  |  | exact r-… id |
| `--by` | `agent` |  | declare yourself an agent; ratification then refuses |
| `--note` | `NOTE` |  | optional ratification rationale |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--slug` | `SLUG` |  | route to another project's bucket by its slug, written --slug=&lt;slug&gt; (a slug starts with '-'); routing only, the channel gate is unchanged (#766) |
| `--json` |  |  | machine-readable output |

### `daimon ruling revise`

revise a ruling; on an active ruling an agent call records a proposal and the text stands

```text
daimon ruling revise ruling_id [--subject SUBJECT] [--verdict VERDICT] [--scope SCOPE] [--anchor ANCHOR] [--revisit-when REVISIT_WHEN] --evidence SOURCE [--by agent] [--ratify] [--check-body-file PATH] [--check-match REGEX] [--check-intent enforce|record-only|warn] [--request-policy KEY=VALUE] [--no-request-policy] [--project PROJECT] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `ruling_id` |  |  | exact r-… id |
| `--subject` | `SUBJECT` |  | replacement subject |
| `--verdict` | `VERDICT` |  | replacement ruling text (same size rule as propose) |
| `--scope` | `SCOPE` |  | replacement scope |
| `--anchor` | `ANCHOR` |  | replacement anchor set; repeatable |
| `--revisit-when` | `REVISIT_WHEN` |  | replacement revisit condition |
| `--evidence` | `SOURCE` |  | source cited for the revision; recorded, not verified; repeatable (required) |
| `--by` | `agent` |  | declare yourself an agent; on an active ruling the call then records a proposal and the text stands |
| `--ratify` |  |  | refused: activation goes through `daimon ruling ratify`, which shows the text before the write |
| `--check-body-file` | `PATH` |  | file whose contents become the ruling's check script; the bytes are stored, the path is not (#943) |
| `--check-match` | `REGEX` |  | regex on the command string that selects the actions the check runs before |
| `--check-intent` | `enforce\|record-only\|warn` |  | what the check asks each host for; the host delivers the strongest it supports (default warn) |
| `--request-policy` | `KEY=VALUE` |  | replacement grant, one of two shapes: sender=&lt;slug&gt; kind=work\|info verb=accept by=agent (a sender this project trusts to record its own accept), or to=&lt;slug&gt; kind=info verb=open by=agent (this project's own agent may open an info ask there); repeatable, all four required per shape (#961) |
| `--no-request-policy` |  |  | clear this ruling's request_policy; mutually exclusive with --request-policy |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |

### `daimon ruling retire`

end a ruling: a candidate as rejected, an active one as retired; agent calls propose, humans retire directly; evidence optional

```text
daimon ruling retire ruling_id [--evidence SOURCE] [--note NOTE] [--by agent] [--project PROJECT] [--slug SLUG] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `ruling_id` |  |  | exact r-… id |
| `--evidence` | `SOURCE` |  | optional source for why the ruling stopped applying; repeatable |
| `--note` | `NOTE` |  | optional explanation |
| `--by` | `agent` |  | declare yourself an agent; the call then records a retirement proposal and the ruling stands until a human verdict |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--slug` | `SLUG` |  | route to another project's bucket by its slug, written --slug=&lt;slug&gt; (a slug starts with '-'); routing only, the channel gate is unchanged (#766) |
| `--json` |  |  | machine-readable output |

### `daimon ruling check`

the check a ruling carries (#943): try it before a human arms it

```text
daimon ruling check <command>
```

| command | what it does |
| --- | --- |
| [`try`](#daimon-ruling-check-try) | run this ruling's check against a command and print the outcome; arms nothing, logs nothing |

#### `daimon ruling check try`

run this ruling's check against a command and print the outcome; arms nothing, logs nothing

```text
daimon ruling check try ruling_id --command CMD [--cwd DIR] [--proposed] [--by agent] [--project PROJECT]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `ruling_id` |  |  | exact r-... id |
| `--command` | `CMD` |  | the command string to check, as a host would hand it to the hook (required) |
| `--cwd` | `DIR` |  | working directory the command would run in; relative file arguments resolve against it (default: the current directory) |
| `--proposed` |  |  | run the pending revision's check instead of the armed one |
| `--by` | `agent` |  | declare yourself an agent; a dry run then refuses, because it executes the body |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

### `daimon ruling checks`

what this project has armed, the mode each host gives it, and whether it ever fired (#943)

```text
daimon ruling checks [--project PROJECT] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |

### `daimon ruling list`

list project rulings

```text
daimon ruling list [--state active|candidate|overturned] [--project PROJECT] [--json] [--inherited]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--state` | `active\|candidate\|overturned` |  | filter by state; repeatable |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |
| `--inherited` |  |  | also list active rulings inherited from a ruling layer above this project (#1092); each row (each `--json` row too) carries `inherited_from`; refused on a tenant-scoped home unless the host declares DAIMON_TENANT_LAYERS (#899) |

### `daimon ruling show`

show one ruling, including pending proposals

```text
daimon ruling show ruling_id [--project PROJECT] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `ruling_id` |  |  | exact r-… id |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |

## `daimon amend`

record an evidence-carrying state transition on a briefed item (#691)

```text
daimon amend <command>
```

| command | what it does |
| --- | --- |
| [`propose`](#daimon-amend-propose) | propose an amendment; agent proposals stay candidates until the session-end byte-check or a human verdict |
| [`ratify`](#daimon-amend-ratify) | activate a candidate or verified amendment as a human decision |
| [`reject`](#daimon-amend-reject) | reject an amendment with a reason, as a human decision |
| [`list`](#daimon-amend-list) | list project amendments, candidates first |

**Examples**

```text
daimon amend o-3f8a2c --change progressed --evidence 'the PR merged' --by agent
daimon amend ratify a-7d41e0
```

### `daimon amend propose`

propose an amendment; agent proposals stay candidates until the session-end byte-check or a human verdict

```text
daimon amend propose item_id --change blocked|changed|progressed --evidence EVIDENCE [--note NOTE] [--by agent] [--project PROJECT]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `item_id` |  |  | exact item id from a briefing/loops handle |
| `--change` | `blocked\|changed\|progressed` |  | the typed transition; the closed vocabulary is the render bound (required) |
| `--evidence` | `EVIDENCE` |  | verbatim transcript quote backing the change; byte-checked against this session's transcript at session end (required) |
| `--note` | `NOTE` |  | short context; human channel only |
| `--by` | `agent` |  | declare yourself an agent; omit it only from an interactive terminal, which is the human path |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

### `daimon amend ratify`

activate a candidate or verified amendment as a human decision

```text
daimon amend ratify amendment_id ... [--by agent] [--project PROJECT] [--slug SLUG]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `amendment_id` |  |  | one or more exact a-… ids; all-or-nothing if any is unknown or in the wrong state |
| `--by` | `agent` |  | declare yourself an agent; ratification then refuses, because it requires a human channel |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--slug` | `SLUG` |  | route to another project's bucket by its slug, written --slug=&lt;slug&gt; (a slug starts with '-'); routing only, the channel gate is unchanged (#766) |

### `daimon amend reject`

reject an amendment with a reason, as a human decision

```text
daimon amend reject amendment_id ... [--note NOTE] [--by agent] [--project PROJECT] [--slug SLUG]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `amendment_id` |  |  | one or more exact a-… ids; all-or-nothing if any is unknown |
| `--note` | `NOTE` |  | why it is wrong; kept on the record |
| `--by` | `agent` |  | declare yourself an agent; rejection then refuses, because it requires a human channel |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--slug` | `SLUG` |  | route to another project's bucket by its slug, written --slug=&lt;slug&gt; (a slug starts with '-'); routing only, the channel gate is unchanged (#766) |

### `daimon amend list`

list project amendments, candidates first

```text
daimon amend list [--project PROJECT] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |
