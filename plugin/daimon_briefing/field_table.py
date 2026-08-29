"""#827: the declarative checkpoint field table — one source of truth.

Checkpoint consumers cannot import daimon, so every consumer-side normalizer
used to be a guess about the producer shape, and two guesses silently deleted
real data (importance clamped 1-5 against the producer's 1-10 range;
quote_provenance.verifier read as a string where the producer writes an
object). This module states, per field of the checkpoint envelope and of a
checkpoint item, exactly what the producer writes and what happens to an
out-of-contract value — and everything else derives from that one table:

  * the runtime validator (`item_reason` / `checkpoint_reason` — the functions
    serializer's `_item_reason` / `validation_reason` now delegate to),
  * the runtime normalizers (`normalize_field` — the per-item body of
    serializer's `sanitize_importance` / `sanitize_scene`),
  * the published machine-readable schema document
    (`schema_document` / `render_document` -> docs/checkpoint-schema.json),
    versioned with the envelope's `format_version`, so an external consumer
    can test its own normalizers against the producer contract without
    importing daimon.

NO BEHAVIOR CHANGE is the hard requirement: the table encodes today's
dispositions exactly, the generated reason strings are byte-identical to the
predicates they replace, and tests/test_field_table*.py pin the table against
the live serializer constants and the real on-disk corpus, so divergence is a
test failure instead of a silent consumer-side data loss.

Deliberately import-free (the schema.py discipline, #146): serializer imports
this module for the generated validator/normalizers, so this module sits below
the whole chain and imports nothing from the package. `format_version` is
therefore a PARAMETER of the document builders — the one caller-owned value —
and the `python -m daimon_briefing.field_table` entry point resolves it from
serializer lazily, outside the import graph.

Column semantics:
  * type       — the JSON type the producer writes when it writes the field.
  * optional   — whether a stored checkpoint may lack the field.
  * nullable   — whether an explicit null is tolerated where the field exists.
  * owner      — "model": extracted content the validator/normalizers police;
                 "code": stamped by daimon's own write pipeline (a model-
                 emitted value is stripped or overwritten, per the notes).
  * disposition — what happens to an out-of-contract value:
                 "reject" (the checkpoint is refused), "clamp" (pulled into
                 range / truncated), "drop" (the field is removed), "pass"
                 (stored as-is), "strip" (discarded at the serialize boundary;
                 the persisted value is always code-derived).
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple


SCHEMA_DOCUMENT_VERSION = 1

# Constraint keys the runtime engine executes (everything else is
# documentation for consumers): "validate", "enum", "reject_reason",
# "required_if_eq", "object_keys", "min", "max", "strip_whitespace",
# "max_chars".
Constraints = tuple[tuple[str, Any], ...]


class FieldRule(NamedTuple):
    """One row: a field of the checkpoint envelope or of a checkpoint item."""

    scope: str          # "envelope" | "item"
    name: str           # field name; envelope section fields are dotted paths
    type: str           # JSON type produced ("string", "integer", ...)
    optional: bool
    nullable: bool
    owner: str          # "model" | "code"
    disposition: str    # "reject" | "clamp" | "drop" | "pass" | "strip"
    constraints: Constraints
    notes: str


def _c(**kv: Any) -> Constraints:
    return tuple(kv.items())


_TIMESTAMP = "ISO-8601 UTC, %Y-%m-%dT%H:%M:%SZ"

# Row order is load-bearing: the generated validator applies reject rows in
# table order, reproducing the live predicate order (and therefore which
# reason a multiply-invalid input is rejected with) byte-for-byte.
ITEM_RULES: tuple[FieldRule, ...] = (
    FieldRule(
        "item", "text", "string", False, False, "model", "reject",
        _c(reject_reason="text is not a str"),
        "The claim. Empty string is valid (active_topic may be empty; "
        "briefing skips the empty section). Non-str rejects the checkpoint."),
    FieldRule(
        "item", "trust", "string", False, False, "model", "reject",
        _c(enum=("verbatim", "inferred"),
           reject_reason="trust is not a known trust class"),
        "D-006 trust class. verify_quotes and the grounding/foreign gates may "
        "downgrade verbatim to inferred after extraction; they never upgrade."),
    FieldRule(
        "item", "quote", "string", True, True, "model", "reject",
        _c(required_if_eq=("trust", "verbatim"),
           reject_reason="trust=verbatim item has no quote"),
        "Exact transcript span. Required non-empty when trust=verbatim "
        "(rejects otherwise); unconstrained on inferred items. A quote whose "
        "canonical value was forgotten is scrubbed and trust downgraded."),
    FieldRule(
        "item", "because", "string", True, True, "model", "reject",
        _c(reject_reason="because is not a str"),
        "D-018 stated-reasoning clause. Explicit null is tolerated; any other "
        "non-str rejects the checkpoint."),
    FieldRule(
        "item", "anchored_to", "object", True, True, "model", "reject",
        _c(object_keys=("file", "symbol", "body_hash"),
           reject_reason_missing=(
               "anchored_to is missing file, symbol, or body_hash")),
        "Code anchor (daimon anchor --attach): file, symbol, and body_hash "
        "must all be non-empty strings. Explicit null is tolerated."),
    FieldRule(
        "item", "importance", "integer", True, False, "model", "clamp",
        _c(min=1, max=10),
        "Producer scores 1-10 (serializer.py rule 14). Out-of-range ints "
        "clamp to the range; bool and every non-int (str, float, null) drop "
        "the field. Consumers clamping to any other range delete real data."),
    FieldRule(
        "item", "scene", "string", True, False, "model", "clamp",
        _c(strip_whitespace=True, max_chars=500),
        "#317 episodic scene trace. Stripped and truncated to max_chars; "
        "empty/whitespace and every non-str drop the field."),
    FieldRule(
        "item", "external_state", "boolean", True, True, "model", "pass",
        _c(),
        "Flags an answer that may have changed outside the session. Never "
        "validated or normalized: stored as emitted."),
    FieldRule(
        "item", "links", "array", True, True, "model", "pass",
        _c(element=("object with string fields: type, target",),
           link_types=("supersedes",)),
        "Supersession links; target names the OLD decision's stored text. "
        "Pass-through at admission; an element whose target was forgotten is "
        "removed, and the key is dropped when no element remains."),
    FieldRule(
        "item", "source_message_ids", "array", True, False, "model", "drop",
        _c(element=("string host message id",)),
        "#358 quote binding. A bare string coerces to a one-entry list; "
        "entries the transcript cannot vouch for are dropped (on inferred or "
        "quote-less items only tool-result signal ids survive, #359), and "
        "the key is removed when nothing valid remains."),
    FieldRule(
        "item", "id", "string", True, False, "code", "strip",
        _c(pattern=r"^[a-z]-[0-9a-f]{6,40}(-[0-9]+)?$",
           stripped_at_serialize=True),
        "Stable project-global item id (#102/#487), stamped at admission for "
        "non-empty-text list items; active_topic never carries one. Model-"
        "emitted values are stripped."),
    FieldRule(
        "item", "first_seen", "string", True, False, "code", "strip",
        _c(format=_TIMESTAMP, stripped_at_serialize=True),
        "Per-item birth stamp (#126), inherited across carries; feeds decay "
        "age. Model-emitted values are stripped (#725)."),
    FieldRule(
        "item", "origin_session", "string", True, False, "code", "strip",
        _c(stripped_at_serialize=True),
        "Session that first wrote the item (#268); carried copies keep their "
        "binding. Model-emitted values are stripped."),
    FieldRule(
        "item", "origin_author", "string", True, False, "code", "strip",
        _c(stripped_at_serialize=True),
        "Author that first wrote the item (#268). Model-emitted values are "
        "stripped."),
    FieldRule(
        "item", "carried_from", "string", True, False, "code", "strip",
        _c(stripped_at_serialize=True),
        "Session id of the LAST carry hop (#33); absent on native items. "
        "Model-emitted values are stripped (#725)."),
    FieldRule(
        "item", "quote_verified", "boolean", True, False, "code", "strip",
        _c(stripped_at_serialize=True),
        "verify_quotes verdict (#125): true on a byte-verified quote, false "
        "only where a verbatim claim was downgraded. Inferred items carry "
        "neither this nor last_verified."),
    FieldRule(
        "item", "last_verified", "string", True, False, "code", "strip",
        _c(format=_TIMESTAMP, stripped_at_serialize=True),
        "Stamped with quote_verified=true at serialize; checkpoint-append-"
        "only (#215). Model-emitted values are stripped."),
    FieldRule(
        "item", "quote_provenance", "object", True, False, "code", "strip",
        _c(stripped_at_serialize=True,
           shape=("version: integer (1)",
                  "source: object {version: 1, host: enum[claude-code, "
                  "codex, windsurf, gemini, hermes, manual], session_id: "
                  "string, locator: enum[managed, host-api, unsupported], "
                  "author?: string}",
                  "digest: object {algorithm: 'sha256', scope: "
                  "enum[raw-file, rendered-transcript], value: string "
                  "(64 hex)}",
                  "verifier: OBJECT {id: string ('tier-f'), version: "
                  "integer} — an object, never a string",
                  "outcome: enum[verified, not-verified]",
                  "checked_at: string (" + _TIMESTAMP + ")",
                  "binding: object {mode: enum[message-ids, "
                  "transcript-scan], message_ids: array of string}",
                  "stitching?: object {cross_message: boolean, cross_role: "
                  "boolean} — verified receipts only, when per-message "
                  "attribution was possible (#829); true means NO single "
                  "message (or single role) can account for all matched "
                  "quote fragments, i.e. the quote was stitched across "
                  "turns or speakers; absent = unknown (legacy receipts, "
                  "transcript-less captures)")),
        "Durable per-item evidence receipt (#594, provenance.quote_receipt). "
        "verifier is an OBJECT {id, version}: consumers normalizing it as a "
        "string rendered every verified claim's verifier as absent."),
    FieldRule(
        "item", "grounded", "boolean", True, False, "code", "pass",
        _c(),
        "#359 outcome-grounding verdict, re-derived every serialize: any "
        "model-emitted value is popped before ground_outcomes re-stamps."),
    FieldRule(
        "item", "pinned", "boolean", True, False, "code", "strip",
        _c(stripped_at_serialize=True),
        "#369 auto-pin marker on force-pinned hard-imperative constraints; "
        "always true when present. Model-emitted values are stripped."),
    FieldRule(
        "item", "quote_echo_only", "boolean", True, False, "code", "pass",
        _c(),
        "#440: quote existed only inside daimon's own injected output; the "
        "item is downgraded and this marker set. Model-emitted values are "
        "popped before verify_quotes re-derives."),
)

ENVELOPE_RULES: tuple[FieldRule, ...] = (
    FieldRule(
        "envelope", "session_id", "string", False, True, "code", "reject",
        _c(validate="presence", reject_reason="missing session_id"),
        "Host session id, assigned by the serialize pipeline after stripping "
        "model output. Only PRESENCE is validated."),
    FieldRule(
        "envelope", "working_context", "object", False, False, "model",
        "reject",
        _c(validate="object"),
        "Section holding active_topic, open_questions, recent_decisions."),
    FieldRule(
        "envelope", "epistemic_snapshot", "object", False, False, "model",
        "reject",
        _c(validate="object"),
        "Section holding strong_beliefs, uncertainties, "
        "contradictions_flagged."),
    FieldRule(
        "envelope", "working_context.open_questions", "array", False, False,
        "model", "reject",
        _c(validate="item-list-required"),
        "List of item objects; a non-list (absence included) rejects."),
    FieldRule(
        "envelope", "working_context.recent_decisions", "array", False, False,
        "model", "reject",
        _c(validate="item-list-required"),
        "List of item objects; a non-list (absence included) rejects."),
    FieldRule(
        "envelope", "working_context.active_topic", "object", False, False,
        "model", "reject",
        _c(validate="item-required",
           reject_reason="missing working_context.active_topic"),
        "Singleton item; per-session, never carried, never id-stamped. "
        "Presence is checked before the working_context lists, its item "
        "shape after them; an explicit null rejects (item shape applies). "
        "May be deleted by a forget rewrite."),
    FieldRule(
        "envelope", "epistemic_snapshot.strong_beliefs", "array", True, False,
        "model", "reject",
        _c(validate="item-list-optional"),
        "List of item objects; absence is tolerated (treated as empty), a "
        "present non-list rejects."),
    FieldRule(
        "envelope", "epistemic_snapshot.uncertainties", "array", True, False,
        "model", "reject",
        _c(validate="item-list-optional"),
        "List of item objects; absence is tolerated (treated as empty), a "
        "present non-list rejects."),
    FieldRule(
        "envelope", "epistemic_snapshot.contradictions_flagged", "array",
        True, True, "model", "pass",
        _c(element=("free-form: bare strings and objects both occur",)),
        "Never validated or normalized; item-shaped entries share the item "
        "fields but the shape varies by design (schema.py #146)."),
    FieldRule(
        "envelope", "worker_queue", "array", True, True, "model", "pass",
        _c(),
        "Prompt-schema artifact; the producer emits an empty list. Never "
        "read or validated."),
    FieldRule(
        "envelope", "format_version", "string", True, False, "code", "strip",
        _c(pattern=r"^D-[0-9]+$", stripped_at_serialize=True),
        "Envelope schema version (serializer.PROMPT_VERSION); this document "
        "versions with it. Stamped at write when absent; pre-#93 legacy "
        "checkpoints may lack it."),
    FieldRule(
        "envelope", "created", "string", True, False, "code", "strip",
        _c(format=_TIMESTAMP, stripped_at_serialize=True),
        "Session-end stamp (capture path) or write-time fallback. Readers "
        "prefer it over file mtime, which pointer rotation rewrites (#93)."),
    FieldRule(
        "envelope", "author", "string", True, False, "code", "strip",
        _c(stripped_at_serialize=True),
        "Team author (#111), resolved by config at write time."),
    FieldRule(
        "envelope", "transcript_hash", "string", True, False, "code", "strip",
        _c(pattern=r"^[0-9a-f]{64}$", stripped_at_serialize=True),
        "sha256 of the raw transcript file; absent when no transcript file "
        "exists (introspection path)."),
    FieldRule(
        "envelope", "source_ref", "object", True, False, "code", "strip",
        _c(stripped_at_serialize=True,
           shape=("version: integer (1)",
                  "host: enum[claude-code, codex, windsurf, gemini, hermes, "
                  "manual]",
                  "session_id: string (^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$)",
                  "locator: enum[managed, host-api, unsupported]",
                  "author?: string (non-empty)")),
        "Capture source descriptor (#594, provenance.capture_source_ref)."),
    FieldRule(
        "envelope", "project_slug", "string", True, False, "code", "strip",
        _c(stripped_at_serialize=True),
        "Project attribution, stamped only when the project is known "
        "(absent = unknown, never empty)."),
    FieldRule(
        "envelope", "project_name", "string", True, False, "code", "pass",
        _c(),
        "#672 display name (the slug is a lossy flattening). Stamped only "
        "when absent and the project is known; a pre-existing value is kept."),
    FieldRule(
        "envelope", "git_branch", "string", True, False, "code", "strip",
        _c(stripped_at_serialize=True),
        "Branch at capture time (#222); absent when unknown or detached."),
    FieldRule(
        "envelope", "receipts", "boolean", True, False, "code", "strip",
        _c(stripped_at_serialize=True),
        "#204 era marker: true when a signed provenance receipt sidecar was "
        "planned for this write; absent otherwise."),
    FieldRule(
        "envelope", "extraction_version", "integer", True, False, "code",
        "strip",
        _c(stripped_at_serialize=True),
        "#514 extraction-semantics version, stamped only by paths that ran "
        "the extractor; introspection checkpoints stay absent = unknown."),
    FieldRule(
        "envelope", "llm_backend", "string", True, False, "code", "pass",
        _c(),
        "#230 backend that produced this checkpoint; assignment always "
        "overwrites any model-emitted value at serialize."),
    FieldRule(
        "envelope", "llm_model", "string", True, False, "code", "pass",
        _c(),
        "Requested model alias, stamped only when config knows one; absent "
        "otherwise (a bare command backend stays honest-absent)."),
    FieldRule(
        "envelope", "llm_model_served", "string | array", True, False, "code",
        "pass",
        _c(element=("string served-model name; array when more than one "
                    "model served the run (#458)",)),
        "Per-call served-model truth; popped from model output "
        "unconditionally before the serialize-path stamp."),
    FieldRule(
        "envelope", "redactions", "object", True, False, "code", "pass",
        _c(shape=("map of redaction kind -> integer count",)),
        "#104 visible counter, present only when capture-time redaction "
        "scrubbed something; merged (never overwritten) across re-writes."),
    FieldRule(
        "envelope", "source", "string", True, False, "code", "pass",
        _c(),
        "write-checkpoint provenance stamp (default 'introspection'); "
        "absent on serialize-path checkpoints."),
    FieldRule(
        "envelope", "team_project", "string", True, False, "code", "pass",
        _c(),
        "Team-mirror copies only (#111): the shared project path segments."),
    FieldRule(
        "envelope", "origin_session", "string", True, False, "code", "strip",
        _c(stripped_at_serialize=True),
        "Reserved: stripped from model output and never stamped at the top "
        "level — origin binds per item only (#268)."),
    FieldRule(
        "envelope", "origin_author", "string", True, False, "code", "strip",
        _c(stripped_at_serialize=True),
        "Reserved: stripped from model output and never stamped at the top "
        "level — origin binds per item only (#268)."),
)

_ITEM_BY_NAME = {r.name: r for r in ITEM_RULES}
_ENVELOPE_BY_NAME = {r.name: r for r in ENVELOPE_RULES}

# Required-always item fields, in table order, for the combined missing-keys
# predicate ("missing text or trust") the live validator reports.
_ITEM_REQUIRED = tuple(
    r.name for r in ITEM_RULES if not r.optional and r.disposition == "reject")


def rule(scope: str, name: str) -> FieldRule:
    """The table row for one field; KeyError on an unknown field."""
    table = _ITEM_BY_NAME if scope == "item" else _ENVELOPE_BY_NAME
    return table[name]


def item_reason(item: object) -> str | None:
    """None when the item is valid, else WHICH predicate rejected it — the
    generated twin of the checks serializer._item_reason performs, driven by
    ITEM_RULES rows with disposition "reject", reproducing the live reason
    strings byte-for-byte (#555 named-predicate contract)."""
    if not isinstance(item, dict):
        return "not a dict"
    if any(name not in item for name in _ITEM_REQUIRED):
        return "missing " + " or ".join(_ITEM_REQUIRED)
    for r in ITEM_RULES:
        if r.disposition != "reject":
            continue
        c = dict(r.constraints)
        value = item.get(r.name)
        if "required_if_eq" in c:
            dep, expected = c["required_if_eq"]
            if item.get(dep) == expected and (
                    not isinstance(value, str) or not value.strip()):
                return str(c["reject_reason"])
            continue
        if "enum" in c:
            if value not in c["enum"]:
                return str(c["reject_reason"])
            continue
        if "object_keys" in c:
            if value is None:
                continue
            if not isinstance(value, dict):
                return f"{r.name} is not a dict"
            if not all(isinstance(value.get(k), str) and value.get(k)
                       for k in c["object_keys"]):
                return str(c["reject_reason_missing"])
            continue
        # plain string field: required rows reject any non-str; optional
        # rows tolerate an explicit null (the `because` posture).
        if r.optional and value is None:
            continue
        if not isinstance(value, str):
            return str(c["reject_reason"])
    return None


def checkpoint_reason(checkpoint: object) -> str | None:
    """None when the checkpoint is valid, else WHICH predicate rejected it —
    the generated twin of serializer.validation_reason, driven by
    ENVELOPE_RULES. Two phases, like the live validator: presence/type of the
    top-level keys first (in table order), then the item-bearing sections (in
    table order) — so a multiply-invalid checkpoint is rejected with the same
    reason string the live predicate order produced."""
    if not isinstance(checkpoint, dict):
        return "checkpoint is not a dict"
    for r in ENVELOPE_RULES:
        c = dict(r.constraints)
        validate = c.get("validate")
        if validate == "presence":
            if r.name not in checkpoint:
                return str(c["reject_reason"])
        elif validate == "object":
            if not isinstance(checkpoint.get(r.name), dict):
                return f"{r.name} is not a dict"
        elif validate == "item-required":
            section, key = r.name.split(".", 1)
            if key not in checkpoint[section]:
                return str(c["reject_reason"])
    for r in ENVELOPE_RULES:
        validate = dict(r.constraints).get("validate")
        if validate == "item-required":
            section, key = r.name.split(".", 1)
            reason = item_reason(checkpoint[section][key])
            if reason is not None:
                return f"{r.name}: {reason}"
        elif validate in ("item-list-required", "item-list-optional"):
            section, key = r.name.split(".", 1)
            if validate == "item-list-optional":
                items = checkpoint[section].get(key, [])
            else:
                items = checkpoint[section].get(key)
            if not isinstance(items, list):
                return f"{r.name} is not a list"
            for i, item in enumerate(items):
                reason = item_reason(item)
                if reason is not None:
                    return f"{r.name}[{i}]: {reason}"
    return None


def normalize_field(item: dict, name: str) -> None:
    """Apply one clamp row's disposition to `item` in place — the generated
    per-item body of serializer.sanitize_importance / sanitize_scene. Absent
    fields stay absent; a malformed advisory field is removed, never fatal
    (#119 posture).

    Only clamp rows are implemented HERE: the `drop` disposition of
    source_message_ids needs transcript context and lives in
    sanitize_source_ids, and reject rows belong to the validator. Calling
    this on any other row RAISES instead of silently no-opping (#828 review:
    a silent no-op would leave a malformed value in place while reading as
    normalization at the call site)."""
    r = _ITEM_BY_NAME[name]
    c = dict(r.constraints)
    if r.disposition == "clamp" and r.type == "integer":
        if name not in item:
            return
        value = item[name]
        # bool is an int subclass — True must not become importance 1.
        if isinstance(value, int) and not isinstance(value, bool):
            item[name] = min(int(c["max"]), max(int(c["min"]), value))
        else:
            del item[name]
    elif r.disposition == "clamp" and r.type == "string":
        if name not in item:
            return
        value = item[name]
        if isinstance(value, str) and value.strip():
            out = value.strip() if c.get("strip_whitespace") else value
            item[name] = out[:int(c["max_chars"])]
        else:
            del item[name]
    else:
        raise ValueError(
            f"normalize_field has no branch for {r.type}/{r.disposition} "
            f"(field {name!r})")


_ENGINE_VALIDATES = ("presence", "object", "item-required",
                     "item-list-required", "item-list-optional")


def engine_coverage_errors(
        item_rules: tuple[FieldRule, ...] = ITEM_RULES,
        envelope_rules: tuple[FieldRule, ...] = ENVELOPE_RULES) -> list[str]:
    """Rows the runtime engine has no branch for, as human-readable errors.

    #828 review: the validator dispatches on a row's constraint keys, not its
    `type` column, so a reject row of a shape the engine never anticipated
    (say an integer field with only a reject_reason) would fall through to
    the plain-string branch or KeyError deep inside a serialize. This guard
    runs at import, so a malformed row fails the first thing that touches
    the module — never a capture at the write boundary."""
    errors: list[str] = []
    for r in item_rules:
        c = dict(r.constraints)
        if r.disposition == "reject":
            if "required_if_eq" in c or "enum" in c:
                if "reject_reason" not in c:
                    errors.append(
                        f"item.{r.name}: reject row lacks reject_reason")
            elif "object_keys" in c:
                if "reject_reason_missing" not in c:
                    errors.append(
                        f"item.{r.name}: object_keys row lacks "
                        "reject_reason_missing")
            elif r.type == "string":
                if "reject_reason" not in c:
                    errors.append(
                        f"item.{r.name}: reject row lacks reject_reason")
            else:
                errors.append(
                    f"item.{r.name}: reject row of type {r.type!r} has no "
                    "engine branch")
        elif r.disposition == "clamp":
            if r.type == "integer":
                if not {"min", "max"} <= set(c):
                    errors.append(
                        f"item.{r.name}: integer clamp row lacks min/max")
            elif r.type == "string":
                if "max_chars" not in c:
                    errors.append(
                        f"item.{r.name}: string clamp row lacks max_chars")
            else:
                errors.append(
                    f"item.{r.name}: clamp row of type {r.type!r} has no "
                    "engine branch")
    for r in envelope_rules:
        c = dict(r.constraints)
        validate = c.get("validate")
        if r.disposition == "reject":
            if validate not in _ENGINE_VALIDATES:
                errors.append(
                    f"envelope.{r.name}: reject row validate={validate!r} "
                    "has no engine branch")
            elif (validate in ("presence", "item-required")
                    and "reject_reason" not in c):
                errors.append(
                    f"envelope.{r.name}: validate={validate!r} row lacks "
                    "reject_reason")
        elif validate is not None:
            errors.append(
                f"envelope.{r.name}: validate={validate!r} on a "
                f"non-reject row ({r.disposition})")
    return errors


_COVERAGE_ERRORS = engine_coverage_errors()
if _COVERAGE_ERRORS:  # pragma: no cover — a table bug fails every import
    raise AssertionError(
        "field_table engine coverage: " + "; ".join(_COVERAGE_ERRORS))


def _row_document(r: FieldRule) -> dict:
    return {
        "field": r.name,
        "type": r.type,
        "optional": r.optional,
        "nullable": r.nullable,
        "owner": r.owner,
        "disposition": r.disposition,
        "constraints": {k: list(v) if isinstance(v, tuple) else v
                        for k, v in r.constraints},
        "notes": r.notes,
    }


def schema_document(format_version: str) -> dict:
    """The published machine-readable schema document, as a dict. Versioned
    with the envelope's `format_version` (the caller passes
    serializer.PROMPT_VERSION); deterministic by construction — fixed key
    order, no clocks, no environment."""
    return {
        "document": "daimon-checkpoint-field-table",
        "document_version": SCHEMA_DOCUMENT_VERSION,
        "format_version": format_version,
        "dispositions": {
            "reject": "an out-of-contract value refuses the whole checkpoint",
            "clamp": "an out-of-range value is pulled into range or truncated",
            "drop": "an out-of-contract value removes the field, never fails "
                    "the write",
            "pass": "stored as written; consumers must tolerate any shape",
            "strip": "a model-emitted value is discarded at the serialize "
                     "boundary; the persisted value is always code-derived",
        },
        "owners": {
            "model": "extracted content, policed by the validator and "
                     "normalizers",
            "code": "stamped by daimon's own write pipeline",
        },
        "columns": {
            "type": "the JSON type the producer writes when it writes the "
                    "field",
            "optional": "whether a stored checkpoint may lack the field",
            "nullable": "whether an explicit null is tolerated where the "
                        "field is present; false on a reject row means an "
                        "explicit null is rejected like any other "
                        "out-of-contract value",
            "owner": "see owners",
            "disposition": "see dispositions",
            "constraints": "see constraint_semantics; keys not listed there "
                           "are documentation for consumers, not runtime "
                           "checks",
        },
        "constraint_semantics": {
            "validate": "the structural check the runtime validator "
                        "performs on this envelope field: presence, object, "
                        "item-required, item-list-required (absence "
                        "rejects), item-list-optional (absence = empty "
                        "list)",
            "enum": "closed set of accepted values; anything else rejects "
                    "with reject_reason",
            "required_if_eq": "[field, value]: this field must be a "
                              "non-empty string ONLY when the named sibling "
                              "field equals the value; otherwise it is "
                              "unconstrained and any shape passes (so a "
                              "reject disposition with this key is "
                              "conditional, not unconditional)",
            "object_keys": "sub-keys that must each be a non-empty string "
                           "when the field is present and non-null; a "
                           "non-object rejects, a missing/empty sub-key "
                           "rejects with reject_reason_missing",
            "reject_reason": "the exact reason string the validator returns "
                             "when this row rejects",
            "reject_reason_missing": "the reason string when a required "
                                     "sub-key is absent or empty",
            "min": "inclusive lower clamp bound for in-type integers; "
                   "non-integers (booleans included) drop the field",
            "max": "inclusive upper clamp bound for in-type integers",
            "strip_whitespace": "leading/trailing whitespace is removed "
                                "before storing",
            "max_chars": "the stored string is truncated to this many "
                         "characters",
            "stripped_at_serialize": "a model-emitted value for this field "
                                     "is discarded at the serialize "
                                     "boundary; the persisted value is "
                                     "always code-derived",
            "pattern": "regex the code-stamped value matches "
                       "(documentation, not a runtime check)",
            "format": "timestamp format of the code-stamped value "
                      "(documentation)",
            "element": "shape of array elements (documentation)",
            "shape": "shape of the object's sub-fields (documentation)",
            "link_types": "link `type` values the producer emits "
                          "(documentation)",
        },
        "envelope": [_row_document(r) for r in ENVELOPE_RULES],
        "item": [_row_document(r) for r in ITEM_RULES],
    }


def render_document(format_version: str) -> str:
    """schema_document as deterministic, newline-terminated JSON text — the
    exact bytes of docs/checkpoint-schema.json."""
    return json.dumps(schema_document(format_version), indent=2,
                      ensure_ascii=False) + "\n"


if __name__ == "__main__":  # pragma: no cover — the regeneration entry point
    # Lazy: keeps the module import-free (serializer imports it).
    from daimon_briefing import serializer as _serializer

    print(render_document(_serializer.PROMPT_VERSION), end="")
