"""Shared ledger-record helpers for the refutation and ruling verb families.

Pure moves out of the cli monolith (#708): the JSON stamper, the two record
printers, the channel resolver, and the polarity gate. Both `cli.refute` and
`cli.ruling` render through these; `refute search` prints BOTH polarities, so
they cannot live inside either family module without a cross-family import.
"""

import json
import sys

from .. import refutations, render


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


def _ruling_lines(record: dict, *, detailed: bool = False,
                  tag: bool = False) -> list:
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
    proposal = record.get("revision_proposed")
    if proposal:
        lines.append(f"  Pending revision proposal ({proposal.get('by', '?')}): "
                     f"{proposal.get('verdict') or proposal.get('subject') or ''}")
    retirement = record.get("overturn_proposed")
    if retirement:
        lines.append(f"  Pending retirement proposal ({retirement.get('by', '?')})")
    return lines


def _print_ruling(record: dict, *, detailed: bool = False,
                  tag: bool = False) -> None:
    render.render_ledger_lines(
        _ruling_lines(record, detailed=detailed, tag=tag))


def _refuse_ruling_id(record, verb: str) -> bool:
    """#693: one conversation per record — `refute` verbs refuse ruling ids
    with a pointer, and vice versa."""
    if record is not None and record.get("polarity") == "ruling":
        print(f"{record['refutation_id']} is a ruling; use "
              f"`daimon ruling {verb}`")
        return True
    return False
