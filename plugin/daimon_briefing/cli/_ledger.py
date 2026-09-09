"""Shared ledger-record helpers for the refutation and ruling verb families.

Pure moves out of the cli monolith (#708): the JSON stamper, the two record
printers, the channel resolver, and the polarity gate. Both `cli.refute` and
`cli.ruling` render through these; `refute search` prints BOTH polarities, so
they cannot live inside either family module without a cross-family import.
"""

import hashlib
import json
import os
import sys

from .. import refutations, render
from . import _cap_refusal

# #920: over-cap `subject`/`verdict`/`scope`/`evidence` name where the
# overflow belongs, mirroring #916's request-ledger refusal. `anchor`,
# `revisit_when` and `note` are absent on purpose: an anchor is an
# identifier, not authored prose, and `revisit_when`/`note` are short
# secondary annotations rather than the text a ruling or refutation
# governs — naming a destination for them would be advice for a case that
# does not meaningfully arise (the #916 reasoning for `request`'s `note`,
# restated for this ledger).
_ARTIFACT_DESTINATION = (
    " — the long form belongs in the artifact it governs (the issue, the "
    "design record, the file); the record carries the one-paragraph rule "
    "and a pointer."
)
_DESTINATION_BY_FIELD = {
    "subject": _ARTIFACT_DESTINATION,
    "verdict": _ARTIFACT_DESTINATION,
    "scope": _ARTIFACT_DESTINATION,
    "evidence": (
        " — evidence is a pointer to the proof (a commit, a PR, a path, a "
        "verbatim line), not the proof itself."
    ),
}


def _refusal_message(prefix: str, exc: refutations.RefutationError) -> str:
    """`prefix: str(exc)`, plus a destination sentence when `exc` is the
    over-cap subclass on a field that has one. Shared by both `cli.refute`
    and `cli.ruling`, since both catch `refutations.RefutationError` off the
    same ledger and the same `_text` cap."""
    return _cap_refusal.format_cap_refusal(
        prefix, exc, refutations.RefutationTooLong, _DESTINATION_BY_FIELD)


def _report_vanished_write(record_id: str, verb: str, *,
                           as_json: bool) -> None:
    """Report a write whose record did not survive to the render, or False.

    Every write verb in both families appends and then re-reads the record to
    show it. The lookup verbs already check the read, because a user-supplied
    id may name nothing; the write verbs rested on having just written it,
    which holds right up until a competing writer removes it in the gap. That
    window is the one #857 confirmed reachable in the request ledger, and both
    render branches died on the None: the text path on `record["state"]`, the
    JSON path on `{**record}` inside the stamper.

    The wording reports the half that actually happened. The append landed and
    is on the ledger; only the render could not resolve it. Saying the verb
    failed would be false, and saying nothing would leave a user who just
    changed a ruling with no confirmation that it took.

    The caller owns the `is None` test rather than this helper returning a
    flag: the guard then reads the same way the lookup verbs' guards already
    do, and a checker can narrow the record on the line after it.

    The JSON branch answers with a document rather than the text sentence,
    because a consumer parsing `--json` needs something it can parse. It is
    deliberately not shaped like a record: `record` is null and `outcome`
    names the write, so nothing can mistake it for the thing that vanished.
    """
    if as_json:
        print(json.dumps({"id": record_id, "outcome": f"{verb}-recorded",
                          "record": None,
                          "note": "the write is on the ledger; the record was "
                                  "not readable from this project at render "
                                  "time"},
                         ensure_ascii=False, sort_keys=True))
    else:
        render.render_ledger_lines(
            [f"{record_id}: {verb} recorded in this project's ledger",
             "  the record was not readable here at render time — the write "
             "landed and renders with the record"])


def _refutation_json(record) -> str:
    """JSON for one folded record or a list of them.

    #576: every record carries `evidence_status: "cited"`.  `evidence` holds
    typed source strings that were shape-checked and redacted on the way in and
    never resolved — daimon does not open them, does not confirm the referent
    exists, and does not judge whether it entails the verdict.  The text
    renderer says so in a parenthetical; without this key a machine consumer
    read sourced-looking strings under a key called `evidence` with nothing to
    contradict the obvious reading.  The value is a constant today because
    `cited` is the only status the ledger can currently earn (#581 would add a
    resolved one).
    """
    def stamped(row: dict) -> dict:
        return {**row, "evidence_status": "cited"}
    payload = ([stamped(row) for row in record]
               if isinstance(record, list) else stamped(record))
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _refutation_lines(record: dict, *, detailed: bool = False,
                      tag: bool = False) -> list:
    state = record.get("state") or "candidate"
    activation = record.get("activation") or f"{record.get('asserted_by', '?')}-proposed"
    mark = "✗" if state == "active" else ("×" if state == "overturned" else "?")
    word = "refutation " if tag else ""
    lines = [f"[{word}{mark} {state} · {activation}] {record['refutation_id']}  "
             f"{record.get('subject', '')}"]
    if not detailed:
        return lines
    lines.append(f"  Verdict: {record.get('verdict', '')}")
    lines.append(f"  Scope: {record.get('scope', '')}")
    anchors = record.get("anchors") or []
    if anchors:
        lines.append(f"  Anchors: {', '.join(anchors)}")
    revisit = record.get("revisit_when") or ""
    if revisit:
        lines.append(f"  Revisit when: {revisit}")
    evidence = record.get("evidence") or []
    for item in evidence:
        lines.append(f"  Evidence: {item}")
    if evidence:
        # #576: Evidence sits in the same Label: value register as Provenance
        # and Authority, which ARE derived from recorded lifecycle facts.  The
        # source string is shape-checked and never resolved, so say so here —
        # this is the only surface a reader of `refute show` actually reads.
        lines.append("  (evidence sources are recorded as cited; "
                     "daimon does not verify them)")
    lines.append(f"  Provenance: asserted by {record.get('asserted_by', '?')} "
                 f"({record.get('asserted_author') or 'unknown'})")
    if record.get("activation"):
        lines.append(f"  Authority: {record['activation']} "
                     f"({record.get('activation_author') or 'unknown'})")
    pending = record.get("overturn_proposed")
    if isinstance(pending, dict):
        lines.append(f"  Overturn proposed by {pending.get('by', '?')} — still active")
    return lines


def _print_refutation(record: dict, *, detailed: bool = False,
                      tag: bool = False) -> None:
    render.render_ledger_lines(
        _refutation_lines(record, detailed=detailed, tag=tag))


def _refute_channel(args) -> str:
    """The channel this invocation actually arrived through.

    `--by agent` is a self-declaration of the NARROWER authority, mirroring
    `resolve`, where the human path is likewise the ABSENCE of the flag. A
    human path has to show an interactive terminal, and the CLI can mint
    nothing stronger: `ui` and `signed` are in-process-only, because a channel
    an agent can reach by shelling out is the deleted `--by human` renamed.
    """
    if getattr(args, "by", None) == "agent":
        return "cli-agent"
    if not sys.stdin.isatty():
        raise refutations.RefutationError(
            "this is the human path and there is no interactive terminal; "
            "pass --by agent to record a candidate, or run it from a terminal")
    return "cli-tty"


def _check_args(args) -> dict | None:
    """#943: the three `--check-*` flags as the dict `refutations._check`
    takes, or None when none was given. Half a check is refused here, before
    any write, and the body FILE is read once at the CLI boundary: the path
    is never stored, only the bytes."""
    body_file = getattr(args, "check_body_file", None)
    match = getattr(args, "check_match", None)
    intent = getattr(args, "check_intent", None)
    if body_file is None and match is None and intent is None:
        return None
    if body_file is None or match is None:
        raise refutations.RefutationError(
            "a check needs both --check-body-file and --check-match "
            "(--check-intent is optional, default warn)")
    try:
        # The cap is a property of the FILE before it is a property of a
        # string: `_check` would refuse the same body, but only after the
        # whole thing has been decoded into memory. One stat is enough.
        size = os.stat(body_file).st_size
        if size > refutations._MAX_CHECK_BODY:
            raise refutations.RefutationError(
                f"check body is too long ({size} > "
                f"{refutations._MAX_CHECK_BODY} bytes)")
        with open(body_file, encoding="utf-8") as handle:
            body = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise refutations.RefutationError(
            f"check body file could not be read: {exc}")
    return {"match": match, "body": body, "intent": intent or "warn"}


def _policy_args(args) -> dict | None:
    """#961 slice 4: the repeatable `--request-policy KEY=VALUE` flags as the
    dict `refutations._policy` takes, or None when none was given.

    Answers only what was SET — `--no-request-policy` (revise only) is a
    separate bool the caller reads for itself, on the same terms
    `_check_args` has no "clear" equivalent for a check."""
    raw = getattr(args, "request_policy", None) or []
    if not raw:
        return None
    out: dict[str, str] = {}
    for item in raw:
        key, separator, value = str(item).partition("=")
        if not separator:
            raise refutations.RefutationError(
                f"--request-policy expects KEY=VALUE, got {item!r}")
        out[key.strip()] = value.strip()
    return out


def _check_sync_warning() -> str:
    """#943: the line to show when a write landed but the armed-check
    manifest did not, or "" when there is nothing to say.

    The verb succeeded — the ledger is the record and it changed. What did
    not change is the derived view a host hook reads, so the check the human
    just armed is not yet armed anywhere. Silence there would leave someone
    believing a ratified check is running. The exit code stays the verb's:
    this is bookkeeping, not a failed ratification.

    One wording, two renderers: the ruling verbs print it directly and
    `forget` folds it into its report list."""
    report = refutations.last_check_sync()
    if report is None or report.ok:
        return ""
    return (f"warning: check manifest not updated ({report.reason}); "
            "run daimon check sync")


def _warn_check_sync() -> None:
    line = _check_sync_warning()
    if line:
        print(line)


def _check_ceremony_lines(check: dict, *, label: str, verb: str) -> list[str]:
    """#943: the two disclosure lines a ceremony prints before a human arms
    a check. One home for the wording, shared by ratify and revise, so the
    two ceremonies cannot drift apart about what they tell the human.

    Hashing the raw body when no `sha256` is present shows the human the same
    hash the row will carry: `refutations._check` refuses any body it would
    alter, so the authored bytes and the stored bytes are the same bytes."""
    body = str(check.get("body") or "")
    sha = str(check.get("sha256") or "") or hashlib.sha256(
        body.encode("utf-8")).hexdigest()
    return [
        f"  {label}: {check.get('intent')} · match /{check.get('match')}/ "
        f"· {body.count(chr(10))} lines · sha {sha[:12]}",
        "  This check will run before matching actions on every host that "
        f"supports it. {verb} arms an executable.",
    ]


def _policy_ceremony_lines(request_policy: dict, *, verb: str) -> list[str]:
    """#961 slice 4: the disclosure line a ceremony prints before a human
    arms a request-accept policy. Mirrors `_check_ceremony_lines`: the hash
    is computed by `refutations._policy`, this only renders what came back."""
    sha = str(request_policy.get("sha256") or "")
    return [
        f"  Policy: sender={request_policy.get('sender')} "
        f"kind={request_policy.get('kind')} verb={request_policy.get('verb')} "
        f"by={request_policy.get('by')} · sha {sha[:12]}",
        f"  {verb} lets that sender's agent record this verdict on this "
        "project's behalf for asks it covers.",
    ]


def _ruling_lines(record: dict, *, detailed: bool = False,
                  tag: bool = False, firing=None) -> list:
    """#693: a ruling renders its VERDICT (the rule text) and never the
    refutation's ✗ glyph; an overturned ruling reads "retired" (label only,
    the state vocabulary is unchanged); and text authored by a non-human
    channel is labeled as such even after human ratification."""
    state = record.get("state") or "candidate"
    shown_state = "retired" if state == "overturned" else state
    activation = (record.get("activation")
                  or f"{record.get('asserted_by', '?')}-proposed")
    authored = record.get("text_authored_by")
    if state == "active" and authored and authored != "human":
        activation = f"{authored}-written, {activation}"
    mark = "§" if state == "active" else ("×" if state == "overturned" else "?")
    word = "ruling " if tag else ""
    lines = [f"[{word}{mark} {shown_state} · {activation}] "
             f"{record['refutation_id']}  {record.get('verdict', '')}"]
    lifecycle = record.get("check_lifecycle")
    if lifecycle and not detailed:
        lines[0] += f" [check: {lifecycle}]"
    if not detailed:
        return lines
    lines.append(f"  Governs: {record.get('subject', '')}")
    lines.append(f"  Scope: {record.get('scope', '')}")
    anchors = record.get("anchors") or []
    if anchors:
        lines.append(f"  Anchors: {', '.join(anchors)}")
    revisit = record.get("revisit_when") or ""
    if revisit:
        lines.append(f"  Revisit when: {revisit}")
    for item in record.get("evidence") or []:
        lines.append(f"  Evidence: {item}")
    check = record.get("check")
    if lifecycle and isinstance(check, dict):
        shown = "proposed, not armed" if lifecycle == "proposed" else lifecycle
        lines.append(f"  Check: {shown} · intent {check.get('intent')} · "
                     f"match /{check.get('match')}/")
        # #943 slice 5: liveness, and only where something could have fired.
        # A `never` on a proposed or disarmed check reports a wiring that
        # does not exist. `firing` is None on every caller but `ruling show`,
        # so `list` keeps its exact shape and does not pay to fold the log
        # once per record.
        if firing is not None and lifecycle == "armed":
            fold = firing.for_ruling(record["refutation_id"])
            if firing.log_state == "unreadable":
                # Not `never`: this read cannot support that claim, and the
                # author who believes it widens the pattern on a gate that
                # has been firing all along.
                lines.append("  Fired: unknown, firing log unreadable")
            elif fold["last_ts"]:
                # #955: counts over the retained window, named by its first
                # day. `lifetime` stood here while the log grew forever.
                since = firing.window_since[:10]
                window = f"since {since}: " if since else ""
                lines.append(
                    f"  Fired: last {fold['last_ts']} on {fold['host']} · "
                    f"{window}{fold['clean']} clean, "
                    f"{fold['violation']} violation, "
                    f"{fold['unresolved']} unresolved")
            else:
                lines.append("  Fired: never")
    # #961 slice 4: where `check` prints its line above, on the identical
    # terms — a `request_policy` present on the record but the ruling not
    # `active` is a candidate grant, authorizing nothing yet.
    request_policy = record.get("request_policy")
    if isinstance(request_policy, dict):
        armed = "in force" if state == "active" else "not in force (candidate)"
        lines.append(
            f"  Policy: sender={request_policy.get('sender')} "
            f"kind={request_policy.get('kind')} "
            f"verb={request_policy.get('verb')} "
            f"by={request_policy.get('by')} · {armed}")
    proposal = record.get("revision_proposed")
    if proposal:
        line = (f"  Pending revision proposal ({proposal.get('by', '?')}): "
                f"{proposal.get('verdict') or proposal.get('subject') or ''}")
        # #943: a proposal to swap the EXECUTABLE is a different ask from one
        # that rewords the rule, and a check-only proposal has no text to
        # show — it would otherwise render as a bare colon.
        if isinstance(proposal.get("check"), dict):
            line += " (carries a check)"
        if isinstance(proposal.get("request_policy"), dict):  # #961 slice 4
            line += " (carries a request_policy)"
        lines.append(line)
    retirement = record.get("overturn_proposed")
    if retirement:
        lines.append(f"  Pending retirement proposal ({retirement.get('by', '?')})")
    return lines


def _print_ruling(record: dict, *, detailed: bool = False,
                  tag: bool = False, firing=None) -> None:
    render.render_ledger_lines(
        _ruling_lines(record, detailed=detailed, tag=tag, firing=firing))


def _refuse_ruling_id(record, verb: str) -> bool:
    """#693: one conversation per record — `refute` verbs refuse ruling ids
    with a pointer, and vice versa."""
    if record is not None and record.get("polarity") == "ruling":
        print(f"{record['refutation_id']} is a ruling; use "
              f"`daimon ruling {verb}`")
        return True
    return False
