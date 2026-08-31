"""Presentation layer for human-facing CLI output.

Single capability gate (`supports_rich`) decides plain vs rich. `rich` is an
OPTIONAL dependency (`daimon[pretty]`) imported lazily inside the rich branch,
so this module is import-safe with rich absent and the hook/serialize path —
which is non-TTY — always renders plain.
"""

import os
import re
import sys
from contextlib import contextmanager

from . import briefing, config, redact, schema, serializer

_TRUTHY = ("1", "true", "yes", "on")


def _isatty() -> bool:
    # Seam: tests monkeypatch this rather than the captured stdout object.
    return sys.stdout.isatty()


def supports_rich() -> bool:
    """True iff we should render with rich: it is installed, stdout is a real
    terminal, and the user has not opted out via NO_COLOR / DAIMON_PLAIN."""
    if os.environ.get("DAIMON_PLAIN", "").strip().lower() in _TRUTHY:
        return False
    if os.environ.get("NO_COLOR") is not None:
        return False
    if not _isatty():
        return False
    try:
        import rich  # noqa: F401
    except ImportError:
        return False
    return True


_TRUST_STYLE = {"verbatim": "bold green", "inferred": "yellow", "untagged": "dim"}

# A withheld retention ratio travels with its reason (#54, #477): a caveat
# documented elsewhere does not survive a pasted stats table.
_RATIO_WITHHELD = {
    "mixed": ("n/a (mixed hosts — a plain `brief` here is either a "
              "skill-delivered briefing or a re-read, and usage.log cannot "
              "tell them apart)"),
}


def _ratio_na(mode: str) -> str:
    return _RATIO_WITHHELD.get(mode, "n/a")


@contextmanager
def working(message: str):
    """Live 'this is running' indicator around a slow call (#182).

    Rich + TTY: an animated status spinner for the duration of the body —
    the first thing a new user runs (`configure --test`) is a ~15s silent
    LLM roundtrip, and dead terminal at that moment reads as hung. Plain
    path prints the message once and returns (hook/log-safe, exact-format
    testable). Body exceptions propagate untouched either way.

    #219: `daimon heal`'s re-serialize is the second call site, and unlike
    `configure --test` its body (`_run_serialize`) prints its own result line
    from inside the `with`, not after. That's safe here — rich's Status is a
    Live display with stdout redirection enabled, so a plain `print()` during
    the spinner is captured and rendered cleanly above the live line rather
    than garbling it (verified with a manual check: `Console(force_terminal=
    True).status(...)` wrapping a body that calls `print()` mid-spin)."""
    if not supports_rich():
        print(f"{message}...", flush=True)
        yield
        return
    from rich.console import Console
    with Console().status(f"{message}..."):
        yield


def _trust_key(item) -> str:
    """Three-way trust class for styling (#30): missing/empty trust is
    "untagged", never presented as a confident "inferred"."""
    trust = item.get("trust")
    if trust == "verbatim":
        return "verbatim"
    return "inferred" if trust else "untagged"

_SECTIONS = [
    ("external", "⚠ VERIFY BEFORE TRUSTING", "red"),
    ("open_loops", "Open loops", "cyan"),
    ("decisions", "Decisions made", "green"),
    ("beliefs", "Beliefs held", "blue"),
    ("uncertainties", "Was uncertain about", "magenta"),
    ("contradictions", "Contradictions flagged", "yellow"),
]


def _print_version_note(checkpoint) -> None:
    """Note when the checkpoint's format_version differs from the current serialize
    prompt. Legacy checkpoints (no format_version) render silently — nothing to
    compare (#93). #294: older-than-code is routine schema drift (#93); newer-
    than-code is impossible by construction (PROMPT_VERSION is a source constant)
    and gets distinct wording — see cli._status_health's sibling check for the
    full reasoning. Unparseable versions fail soft into the older-style wording."""
    fv = (checkpoint or {}).get("format_version")
    # `is not None`, not truthy: an absent key (legacy checkpoint, #93) stays
    # silent, but an explicitly stamped "" is a garbage value that still
    # deserves the fail-soft fallback wording below (#294).
    if fv is None or fv == serializer.PROMPT_VERSION:
        return
    order = schema.compare_format_versions(fv, serializer.PROMPT_VERSION)
    if order is not None and order > 0:
        print(f"⚠ checkpoint format {fv} claims a version newer than this "
              f"daimon's {serializer.PROMPT_VERSION} — a checkpoint cannot be "
              f"newer than the code that wrote it, so the stamp is unreliable "
              f"(check for a second daimon install writing to this checkpoint "
              f"dir, or a downgraded install).")
    else:
        print(f"⚠ checkpoint format {fv} != current {serializer.PROMPT_VERSION} — "
              f"schema changed; some sections may render partially.")


def _print_handoff(handoff) -> None:
    """The baton block (#523): authored, imperative, rendered ABOVE every
    briefing section on both render paths — it must never lose position to
    ambient sections. Multi-line batons keep one arrow per line.

    #566: on the rich path the baton gets a bordered panel like every ambient
    section below it — bare stdout above the panels inverted the visual
    hierarchy for the one block a human wrote deliberately. Magenta border:
    distinct from red (drift/external warnings) so "read this first" never
    reads as "something is wrong". The plain path stays byte-identical — it is
    a compatibility surface for non-TTY hosts and hook briefings."""
    if not handoff:
        return
    lines = [ln.strip() for ln in str(handoff["note"]).splitlines() if ln.strip()]
    if supports_rich():
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text

        body = Text("\n".join(f"→ {ln}" for ln in lines), style="bold")
        Console().print(Panel(
            body,
            title=f"HANDOFF (left deliberately by previous session, {handoff['ts']})",
            border_style="magenta", title_align="left",
        ))
        return
    print(f"HANDOFF (left deliberately by previous session, {handoff['ts']}):")
    for line in lines:
        print(f"→ {line}")
    print("")


def render_handoff(handoff) -> None:
    """Public seam for the baton block alone (#740): the no-checkpoint brief
    path never reaches render_brief, but a baton left for a fresh project is
    the only orientation that project has and must still lead the output."""
    _print_handoff(handoff)


def render_brief(checkpoint, drift=None, teammates=None, handoff=None,
                 project_dir=None, worldcheck_project=None) -> None:
    """`worldcheck_project` (#694 PR 2/3) is a SEPARATE gate from
    `project_dir` — the incoming-request panel's AND the sender-side
    verdict panel's `worldcheck_project` pattern (D2), never keyed on
    route: the caller passes it only on the CLI same-project brief path
    (`cli._render_briefing_body`), never for `--slug`, the global-pointer
    fallback body, or MCP. None here means neither panel, full stop."""
    _print_handoff(handoff)
    b = briefing.build(checkpoint)
    # #693/#694: each path below performs exactly one ledger read per
    # section — the LLM path reads inside briefing.render, every other path
    # reads here. None (legacy callers, or a deliberately excluded route)
    # skips the read entirely; an unknown project resolves to no ledger path
    # anyway, so the guard saves the call, not a leak.
    if b is None:
        rulings = (briefing.ruling_lines(project_dir)
                   if project_dir is not None else [])
        request_lines = (briefing.request_panel_lines(worldcheck_project)
                         if worldcheck_project is not None else [])
        verdict_lines = (briefing.verdict_panel_lines(worldcheck_project)
                         if worldcheck_project is not None else [])
        blocks = [blk for blk in (rulings, request_lines, verdict_lines) if blk]
        if blocks:
            # #693/#694: standing rulings, addressed requests, and decided
            # verdicts exist before the first checkpoint does — a day-one
            # ratification, or the first ask/verdict this project ever sees,
            # must not wait for a session to end.
            print("\n\n".join("\n".join(blk) for blk in blocks))
            print("")
        # Point at the real flow (#29): checkpoints come from the hooks; bare
        # `serialize` dead-ends (it needs a transcript path).
        print("No checkpoint yet — nothing to brief. Checkpoints are written "
              "automatically at session end; to backfill one manually, run "
              "`daimon serialize <transcript>`.")
        _print_teammates(teammates)
        return
    _print_version_note(checkpoint)
    # Honor the opt-in LLM briefing (DAIMON_LLM_BRIEFING) — same source of truth as
    # the hermes hook. Free-form LLM text can't be sectioned into rich panels, so when
    # it is active we print its narrative regardless of TTY.
    if config.llm_briefing():
        # Tries LLM, falls back to deterministic; #693 rulings and #694's
        # two request panels all ride inside. Unconditional return: `b` is
        # non-None here, so render() always yields text — a fall-through
        # would double the ledger reads below, and structure beats a comment
        # at keeping that invariant.
        print(briefing.render(checkpoint, project_dir=project_dir,
                              worldcheck_project=worldcheck_project))
        _print_drift(drift)
        _print_teammates(teammates)
        return
    rulings = (briefing.ruling_lines(project_dir)
               if project_dir is not None else [])
    request_lines = (briefing.request_panel_lines(worldcheck_project)
                     if worldcheck_project is not None else [])
    verdict_lines = (briefing.verdict_panel_lines(worldcheck_project)
                     if worldcheck_project is not None else [])
    # #204: degrade verbatim labels when the receipt can't be locally confirmed.
    # Cheap check (sidecar + byte match), computed once for both render paths.
    degraded = briefing.receipt_degraded(checkpoint)
    if not supports_rich():
        print(briefing.render_plain(b, degraded, rulings, request_lines,
                                    verdict_lines))
    else:
        _rich_brief(b, degraded, rulings, request_lines, verdict_lines)
    _print_drift(drift)
    _print_teammates(teammates)


def _print_drift(drift) -> None:
    if not drift:
        return
    if not supports_rich():
        print("")
        print("CODE DRIFT — verify before trusting (anchored code changed):")
        for d in drift:
            tag = "GONE" if d["kind"] == "hard" else "changed"
            qn = d["anchor"].get("qualified_name") or "malformed anchor"
            print(f"- [{tag}] {d['item'].get('text', '').strip()}  ({qn})")
        return
    from rich.console import Console
    from rich.text import Text
    from rich.panel import Panel

    body = Text()
    for d in drift:
        tag = "GONE" if d["kind"] == "hard" else "changed"
        body.append(f"[{tag}] {d['item'].get('text', '').strip()}\n",
                    style="red" if d["kind"] == "hard" else "yellow")
        qn = d["anchor"].get("qualified_name") or "malformed anchor"
        body.append(f"    {qn}\n", style="dim")
    Console().print(
        Panel(body, title="⚠ CODE DRIFT — verify before trusting",
              border_style="red", title_align="left")
    )


def _rich_brief(b: dict, degraded: bool = False, rulings=(),
                request_lines=(), verdict_lines=()) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    console = Console()
    console.print(Text("While you were away — here's where we left off.", style="bold"))
    if degraded:
        # One header note (#204), parity with the plain path's embedded note.
        console.print(Text(briefing.DEGRADE_NOTE, style="bold red"))
    if rulings:
        # #693: parity with the plain path's top section — same lines, same
        # order (rulings[0] is the header, promoted to the panel title).
        body = Text()
        for line in rulings[1:]:
            body.append(f"{line}\n", style="bold")
        console.print(Panel(body, title=rulings[0], border_style="cyan",
                            title_align="left"))
    if request_lines:
        # #694 PR 2: same shape as the rulings panel above, its own border
        # color so the two skeleton sections read as distinct at a glance.
        body = Text()
        for line in request_lines[1:]:
            body.append(f"{line}\n", style="bold")
        console.print(Panel(body, title=request_lines[0],
                            border_style="blue", title_align="left"))
    if verdict_lines:
        # #694 PR 3: same shape, its own border color — three skeleton
        # sections must each read as distinct at a glance.
        body = Text()
        for line in verdict_lines[1:]:
            body.append(f"{line}\n", style="bold")
        console.print(Panel(body, title=verdict_lines[0],
                            border_style="green", title_align="left"))
    for key, title, style in _SECTIONS:
        items = b.get(key) or []
        if not items:
            continue
        body = Text()
        # #480 slice 1: whether THIS section's items earn a resolve handle —
        # same set briefing._line's plain path keys off of, so the two
        # renders agree on which items are actionable.
        briefable = key in briefing.BRIEFABLE_SECTIONS
        for i in items:
            trust = _trust_key(i)
            # Degrade a verbatim item's confident green — its integrity is
            # unverified (#204). Inferred/untagged never claimed it, so untouched.
            item_style = "bold red" if (degraded and trust == "verbatim") \
                else _TRUST_STYLE[trust]
            # #268: the corroboration badge rides the text line here, the same
            # position it holds on the plain path (after the annotations,
            # before the quote) — one shared literal, so the two renders state
            # the witness count in identical bytes.
            body.append(f"• {i.get('text', '').strip()}"
                        f"{briefing.corroboration_badge(i)}", style=item_style)
            # #480 slice 1: the id handle, dim like the quote below — it is
            # a supplementary resolve target, not part of the claim itself.
            handle = briefing._handle_suffix(i, briefable)
            if handle:
                body.append(handle, style="dim")
            body.append("\n", style=item_style)
            quote = i.get("quote", "").strip()
            if quote:
                body.append(f'    "{quote}"\n', style="dim italic")
            candidate = i.get("_supersede_candidate")
            if candidate:
                # #14: parity with briefing._line's plain-path annotation —
                # this panel builds its own Text body rather than routing
                # through _line, so the flag has to be repeated here.
                item_id = i.get("id") or "?"
                body.append(
                    f"    ⚠ likely superseded by {candidate} — confirm: "
                    f"daimon resolve {item_id} --status superseded-by:{candidate}\n"
                    f"    reject: daimon reverify {item_id}\n",
                    style="yellow")
            wc = i.get("_worldcheck")
            if isinstance(wc, dict) and wc.get("note"):
                # #365: parity with briefing._line's worldcheck flag, same
                # repeated-here reasoning as the candidate block above.
                flag = f"    ⚠ state changed since capture: {wc['note']}"
                item_id = i.get("id")
                if item_id:
                    flag += (f" — confirm: daimon resolve {item_id} "
                             f"--status {wc.get('status') or 'resolved'}\n"
                             f"    reject: daimon reverify {item_id}")
                body.append(flag + "\n", style="yellow")
            claim = i.get("_agent_claim")
            if claim:
                # #480 slice 4: parity with briefing._line's agent-claim flag,
                # same repeated-here reasoning as the candidate/worldcheck
                # blocks above (this panel builds its own Text body).
                item_id = i.get("id") or "?"
                quote = briefing._truncate_agent_claim(claim)
                body.append(
                    f'    ⚠ agent claims resolved — unverified: "{quote}"\n'
                    f"    confirm: daimon resolve {item_id} --status resolved\n"
                    f"    reject: daimon reverify {item_id}\n",
                    style="yellow")
            amends = i.get("_amend")
            if isinstance(amends, dict):
                # #691: parity with briefing._line's amend annotation, same
                # repeated-here reasoning as the blocks above. Two frames:
                # human-ratified renders settled, quote-verified renders as
                # a flagged unconfirmed agent claim with confirm/reject.
                rows = amends.get("rows")
                for amend in rows if isinstance(rows, list) else []:
                    if not isinstance(amend, dict):
                        continue
                    change = str(amend.get("change") or "")
                    aq = briefing._truncate_agent_claim(amend.get("quote"))
                    a_id = amend.get("id") or "?"
                    if amend.get("state") == "ratified":
                        by = (", agent-proposed"
                              if amend.get("by") == "agent" else "")
                        flag = (f'    ↷ amended — {change} '
                                f'({amend.get("label")}{by}): "{aq}"\n')
                        amend_note = str(amend.get("note") or "").strip()
                        if amend_note:
                            nq = briefing._truncate_agent_claim(amend_note)
                            flag += f"    note: {nq}\n"
                        body.append(flag, style="cyan")
                    else:
                        role = str(amend.get("role") or "").strip()
                        body.append(
                            f'    ⚠ agent-proposed amendment — {change} '
                            f'(quote-verified, role: {role}), unconfirmed: '
                            f'"{aq}"\n'
                            f"    confirm: daimon amend ratify {a_id}\n"
                            f"    reject: daimon amend reject {a_id}\n",
                            style="yellow")
                overflow = amends.get("overflow")
                if isinstance(overflow, int) and overflow > 0:
                    body.append(
                        f"    … {overflow} earlier amendment(s) — "
                        f"daimon amend list\n", style="dim")
        if key == "decisions":
            note = briefing._overflow_note(b.get("decisions_overflow", 0))
            if note:
                body.append(f"{note}\n", style="dim")
        console.print(Panel(body, title=title, border_style=style, title_align="left"))
    if b.get("active_topic"):
        console.print(
            Panel(
                Text(b["active_topic"].get("text", "").strip()),
                title="Active topic", border_style="white", title_align="left",
            )
        )


def _print_teammates(teammates) -> None:
    """The #111 'Teammates' section — each teammate's active topic + recent
    decisions, clearly attributed and NEVER merged into the user's own sections.
    No-op on empty/None teammates (byte-identical to a non-team briefing).
    `teammates` is [(author, briefing-sections), ...] from briefing.build."""
    if not teammates:
        return
    if not supports_rich():
        _plain_teammates(teammates)
    else:
        _rich_teammates(teammates)


def _plain_teammates(teammates) -> None:
    print("")
    print("Teammates — where they left off:")
    for author, b in teammates:
        print("")
        print(f"[{author}]")
        active = b.get("active_topic")
        if active:
            line = f"  Active topic: {active.get('text', '').strip()}"
            if active.get("foreign_verbatim_claim"):
                # #423: decisions get this via briefing._line; the topic line
                # is built here, so the label has to be repeated.
                line += f" {briefing.FOREIGN_VERBATIM_NOTE}"
            print(line)
        decisions = b.get("decisions") or []
        if decisions:
            print("  Decisions made:")
            for i in decisions:
                print(f"  {briefing._line(i)}")
            note = briefing._overflow_note(b.get("decisions_overflow", 0))
            if note:
                print(f"    {note}")


def _rich_teammates(teammates) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    console = Console()
    for author, b in teammates:
        body = Text()
        active = b.get("active_topic")
        if active:
            body.append(f"Active topic: {active.get('text', '').strip()}\n", style="white")
            if active.get("foreign_verbatim_claim"):
                body.append(f"    {briefing.FOREIGN_VERBATIM_NOTE}\n", style="yellow")
        decisions = b.get("decisions") or []
        if decisions:
            body.append("Decisions made:\n", style="bold")
            for i in decisions:
                trust = _trust_key(i)
                body.append(f"• {i.get('text', '').strip()}\n", style=_TRUST_STYLE[trust])
                if i.get("foreign_verbatim_claim"):
                    # #423: parity with briefing._line's plain-path label —
                    # this panel builds its own Text body, so the label is
                    # repeated here (same reasoning as the #14 flag above).
                    body.append(f"    {briefing.FOREIGN_VERBATIM_NOTE}\n",
                                style="yellow")
            note = briefing._overflow_note(b.get("decisions_overflow", 0))
            if note:
                body.append(f"{note}\n", style="dim")
        console.print(Panel(body, title=f"Teammate — {author}",
                            border_style="white", title_align="left"))


def render_teammates(teammates) -> None:
    """Public entry point for the #223 header-only fallback path: `brief --team`
    on a project with no checkpoint of its own still needs the Teammates
    section, without reaching into the underscore-private `_print_teammates`
    from cli.py. Delegates as-is — same no-op-on-empty contract."""
    _print_teammates(teammates)


def _explain(st: dict) -> str:
    """One-line human explanation of a configure.status() snapshot."""
    rb = st["resolved_backend"]
    if rb in ("command", "claude-cli"):
        if st["ready"]:
            src = st["command_source"]
            if src == "claude-cli":
                # #546: name the binary PATH resolved. "zero-config" is a
                # feature, but the operator should be able to see WHICH claude
                # is about to receive the transcript without running `which`.
                path = st.get("command_path")
                where = f" from PATH: {path}" if path else ""
                return f"backend: {rb} (claude CLI{where}, zero-config)"
            base = f"backend: {rb} ({st['command']})"
            # #58: only note the input spec when it's not the stdin default —
            # keeps the common case's one-liner unchanged.
            input_spec = st.get("input")
            if input_spec and input_spec != "stdin":
                base += f" [input: {input_spec}]"
            return base
        return "no backend — install the claude CLI or set litellm creds"
    # litellm
    if st["ready"]:
        return "backend: litellm"
    missing = []
    if not st["has_api_key"]:
        missing.append("api_key")
    if not st["has_model"]:
        missing.append("model")
    if missing:
        return f"backend: litellm — missing: {', '.join(missing)}"
    return "no backend — install the claude CLI or set litellm creds"


def render_configure(st: dict) -> None:
    if supports_rich():
        _rich_configure(st)
    else:
        _plain_configure(st)


def _command_binary_warning(st: dict) -> str | None:
    """#747 half two: the PRIMARY command's binary can be missing too — the
    doctor warns and names it; ready semantics stay untouched in this slice.
    A missing primary implies an explicit DAIMON_LLM_COMMAND: the claude-cli
    preset only resolves when its binary is on PATH."""
    missing = st.get("command_missing_binary")
    if not missing:
        return None
    return (f"⚠ command binary not found: {missing} — the configured backend "
            f"({st.get('command')}) cannot run; fix DAIMON_LLM_COMMAND")


def _fallback_binary_warning(st: dict) -> str | None:
    """#747: the doctor line for a rescue whose binary does not exist — the
    posture already refuses to read "covered", but only this view can NAME the
    binary (field case: DAIMON_LLM_COMMAND_FALLBACK=1). The fix advice names
    the knob the rescue actually resolved from: the #475 legacy shim reads
    DAIMON_LLM_COMMAND, not the fallback variable."""
    missing = st.get("fallback_missing_binary")
    if not missing:
        return None
    knob = ("DAIMON_LLM_COMMAND"
            if st.get("fallback_source") == "legacy-command"
            else "DAIMON_LLM_COMMAND_FALLBACK")
    return (f"⚠ fallback binary not found: {missing} — the configured rescue "
            f"({st.get('fallback_command')}) cannot run; fix {knob}")


def _configure_warnings(st: dict) -> list:
    return [w for w in (_command_binary_warning(st),
                        _fallback_binary_warning(st)) if w]


def _plain_configure(st: dict) -> None:
    mark = "✓" if st["ready"] else "✗"
    state = "ready" if st["ready"] else "not ready"
    print(f"{mark} {state} — {_explain(st)}")
    print(f"  env file: {st['env_file']}")
    for warning in _configure_warnings(st):
        print(f"  {warning}")


def _rich_configure(st: dict) -> None:
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    style = "green" if st["ready"] else "red"
    state = "ready" if st["ready"] else "not ready"
    body = f"[{style}]{state}[/{style}] — {_explain(st)}\nenv file: [dim]{st['env_file']}[/dim]"
    for warning in _configure_warnings(st):
        body += f"\n[yellow]{warning}[/yellow]"
    console.print(Panel(body, title="daimon configure", border_style=style, title_align="left"))


def render_status(data: dict) -> None:
    if supports_rich():
        _rich_status(data)
    else:
        _plain_status(data)


def render_heal(plan: dict, *, dry_run: bool, force: bool = False) -> None:
    """Plain explanation of a heal decision. No rich — heal output is procedural.
    `force` (#15) gets its own wording ("force-heal") so the operator can tell
    a --force run apart from an ordinary heal at a glance."""
    t = plan["target"]
    if t:
        verb = "force-heal" if force else "heal"
        if dry_run:
            print(f"would {verb} {t['sid']} (failed {t['age_str']} ago, transcript {t['transcript']})")
        else:
            print(f"{verb}ing {t['sid']} (failed {t['age_str']} ago)…")
    elif plan["note"]:
        print(plan["note"])
    for s in plan["skipped"]:
        print(f"  - {s['sid']}  ({s['age_str']} ago) — {s['reason']}")


def _print_skips_plain(n) -> None:
    """Informational, not a warning (#28): a skip is by-design (too-short
    session), but an invisible skip reads as a captured session."""
    if n:
        print(f"recent sessions skipped (too short to serialize): {n}")


def _print_crash_plain(crash) -> None:
    """One line for the newest child-process crash (#28). serialize-crash.log
    is where spawn_serialize points child stderr; before this, nothing ever
    read it back. No-op when the log is absent/empty."""
    if not crash:
        return
    print(f"last serialize crash: {crash['age']} ago — {crash['last_line']}")
    print(f"  full traceback: {crash['path']}")


def _print_recall_error_plain(err) -> None:
    """Newest swallowed recall-index error (#28) — without it, a broken index
    reads as \"no prior work\"."""
    if not err:
        return
    print(f"last recall error: {err['age']} ago — {err['last_line']}")


def _recall_index_line(att) -> str | None:
    """One line of index attribution (#233), or None when there is no index.
    The unattributed clause appears only when dark matter exists — silence
    stays the default for a fully-stamped store."""
    if not att:
        return None
    if att["unattributed"]:
        return (f"recall index: {att['items']} items "
                f"({att['unattributed']} unattributed — reachable only via "
                f"recall --all-projects)")
    return f"recall index: {att['items']} items"


# The bookkeeping tail of a ledger error line (ledger.py:132-134) — the
# transcript path is already reachable from the record, and `after Ns` is a
# duration, not a cause. Anchored at the end so a message that merely mentions
# a transcript mid-sentence keeps it.
_CAUSE_TAIL_RE = re.compile(r"\s*\(transcript: .+?\)(?: after \d+s)?\s*$")
# Status is a diagnostic surface, not a pager: a backend can put kilobytes into
# an exception message. 160 chars is one wrapped line on an 80-column terminal
# and comfortably fits the shapes that actually occur ("command backend exited
# 1 (stderr: <path>)", "LLM unreachable/timeout after 3 tries at <base>").
_CAUSE_MAX_CHARS = 160


def _failure_cause(line) -> str | None:
    """The human-meaningful half of a ledger error line (#474): everything
    between the `error: ` prefix and the transcript/duration bookkeeping,
    flattened to one bounded line. None for anything that is not an error line
    — hung records carry `line: None` by construction, and status must degrade
    to its pre-#474 output rather than print a dangling label.

    Redacted here because result lines are NOT scrubbed at write time
    (_append_serialize_log and hooks._ledger_capture_failure both write
    `str(exc)` raw) — same #141 rule the backend-stderr log applies."""
    if not isinstance(line, str):
        return None
    text = " ".join(line.split())
    if not text.startswith("error: "):
        return None
    text = _CAUSE_TAIL_RE.sub("", text[len("error: "):]).strip()
    if not text:
        return None
    text, _ = redact.redact_text(text)
    if len(text) > _CAUSE_MAX_CHARS:
        text = text[:_CAUSE_MAX_CHARS].rstrip() + "…"
    return text


def _outstanding_lines(outstanding) -> list:
    """Human lines for lost sessions; empty list when nothing is outstanding."""
    lines = []
    for f in outstanding:
        age = f["age_str"]
        if f["kind"] == "hung":
            # #28: a hung spawn whose transcript survived is healable now.
            if f["class"] == "healable":
                lines.append(
                    f"  - {f['sid']}  spawned {age} ago, no result "
                    f"(hung/killed) — run `daimon heal`"
                )
            else:
                lines.append(
                    f"  - {f['sid']}  spawned {age} ago, no result "
                    f"(hung/killed; transcript unavailable)"
                )
        elif f["class"] == "retry-exhausted":
            # #15: name the escape hatch here — this is the one place an
            # operator sees "retry-exhausted" without already reading heal's
            # own skip reason.
            lines.append(
                f"  - {f['sid']}  error {age} ago — retry attempted, still failing "
                f"(re-run with `daimon heal --force`)"
            )
        elif f["class"] == "unrecoverable":
            lines.append(f"  - {f['sid']}  error {age} ago — transcript unavailable, cannot auto-heal")
        else:
            lines.append(f"  - {f['sid']}  error {age} ago — run `daimon heal`")
        # #474: the cause has always been in the record and was never read.
        # A continuation line keeps the class-specific hint (the actionable
        # part) at the end of the first line where operators already look.
        cause = _failure_cause(f.get("line"))
        if cause:
            lines.append(f"    cause: {cause}")
    return lines


def _capture_alarm_lines(alarm: dict) -> list[str]:
    """The silent-capture FAIL banner (#265), shared verbatim by the plain and
    rich renderers (#29): a headline plus the three concrete fix hints. Rendered
    at the very TOP of status because it flags a class of failure — hooks firing
    but zero checkpoints landing — that otherwise stays invisible until a
    briefing turns up empty."""
    n, days = alarm["spawns"], alarm["window_days"]
    return [
        f"FAIL — silent capture failure: {n} session{'s' if n != 1 else ''} "
        f"observed in the last {days} days, 0 checkpoints written",
        "  → run `daimon heal` to recover the most recent lost session",
        "  → inspect serialize.log for the underlying error",
        "  → run `daimon configure --test` to verify the serialize backend",
    ]


def _rescue_none_warns(data: dict) -> bool:
    """#475 part 2: the "none" posture warning ("`command` backend has no
    rescue path") fires ONLY when there is a real failure to point at.

    Gated on posture == "none" AND the 14-day capture window has errors > 0
    — same shape as the #349 false positive removed and the #477 fix that
    just landed: an operator who pinned `command` deliberately must not see
    a permanent warning about a permanent property of their own choice.
    `covered` / `disabled` / `no-backend` never warn at all (#84: no line,
    no false alarms)."""
    return (data.get("rescue_posture") == "none"
            and (data.get("rescue_window_errors") or 0) > 0)


def _forget_hits_line(data: dict) -> str | None:
    """#404: one line when the value-keyed tombstone has caught a re-assertion
    on this install; silent otherwise (same 'quiet by default' rule as the team
    and receipts lines). Shows the count + most-recent stamp, never the
    suppressed claim text."""
    fh = data.get("forget_hits")
    if not isinstance(fh, dict):
        return None
    n = fh.get("count") or 0
    if not n:
        return None
    ts = fh.get("last_hit_at") or "unknown"
    return (f"forget ledger (this project): suppressed {n} "
            f"re-assertion{'s' if n != 1 else ''}, most recent {ts}")


def _requests_line(data: dict) -> str | None:
    """#694 PR 3: one line summarizing the cross-project ask ledger — open
    sent / awaiting-you counts, read through the composer so the numbers
    agree with both ambient panels. Silent when both are zero (same "quiet
    by default" rule as the team/receipts/forget-hits lines)."""
    counts = data.get("requests")
    if not isinstance(counts, dict):
        return None
    sent = counts.get("open_sent") or 0
    awaiting = counts.get("awaiting_you") or 0
    if not sent and not awaiting:
        return None
    return (f"requests: {sent} open sent, {awaiting} awaiting you — "
            "daimon request list / inbox")


def _ruling_echo_line(data: dict) -> str | None:
    """#693: one line when the admission filter has dropped a ruling echo on
    this install; silent otherwise. Its own counter, never folded into the
    forget-suppression number — the echo rate is a different measurement."""
    fh = data.get("forget_hits")
    if not isinstance(fh, dict):
        return None
    n = fh.get("ruling_echo_count") or 0
    if not n:
        return None
    return (f"ruling echo (this project): dropped {n} "
            f"re-extraction{'s' if n != 1 else ''} of active ruling text "
            "at admission")


def _handoff_line(data: dict) -> str | None:
    """#662: one line when a handoff baton is waiting — this project wrote
    one and no session has consumed it yet. `data["handoff"]` is already
    "waiting only" (store.active_handoff returns None once two sessions
    have serialized past it, #523); silent otherwise, same "quiet by
    default" rule as the team/receipts/forget-hits/requests lines. Never
    the baton TEXT — that belongs to `daimon brief`, not status (the issue
    is explicit)."""
    handoff = data.get("handoff")
    if not isinstance(handoff, dict):
        return None
    written_at = handoff.get("written_at") or "unknown"
    return f"handoff: waiting baton, written {written_at} — daimon brief"


def _plugin_drift_line(pd: dict) -> str:
    """#554: name BOTH versions and the command that moves the stale half.
    "out of date" alone does not say which half, and the two are updated by
    different tools. The restart is part of the fix, not a footnote: hooks
    resolve at session start, so an updated plugin changes nothing until then.
    """
    if pd.get("behind"):
        fix = ("run /plugin in Claude Code and update daimon, then restart the "
               "session — hooks resolve at session start")
    else:
        fix = ("upgrade the CLI with `uv tool upgrade daimon-briefing`, then "
               "restart the session — hooks resolve at session start")
    return (f"⚠ Claude Code plugin is {pd['installed']} but the daimon CLI is "
            f"{pd['cli']} — {fix}")


def _plain_status(data: dict) -> None:
    alarm = data.get("capture_alarm")
    if alarm:
        for line in _capture_alarm_lines(alarm):
            print(line)
    ident = data.get("identity")
    if ident:
        print(f"identity: {ident['cwd']}  →  git-root {ident['git_root']}  →  bucket {ident['slug']}")
    health = data.get("health")
    if health:
        print(health["verdict"])
        for w in health["warnings"][1:]:
            print(f"  ⚠ {w}")
    if data.get("hook_drift"):
        print("⚠ installed hooks out of date — run daimon hooks status")
    if data.get("plugin_drift"):
        print(_plugin_drift_line(data["plugin_drift"]))
    if data.get("rescue_gap"):
        print("⚠ primary is a remote gateway and no fallback backend resolves "
              "— gateway failures won't be rescued (install claude or set "
              "DAIMON_LLM_COMMAND)")
    if _rescue_none_warns(data):
        print("⚠ the `command` backend has no rescue path — a failing "
              "command is not retried or substituted. Set "
              "DAIMON_LLM_COMMAND_FALLBACK, or run `daimon status` "
              "for the recorded failure cause.")
    if data.get("team"):
        print(data["team"])  # one objective line; absent when team unused (#113)
    if data.get("receipts"):
        print(data["receipts"])  # #204: one line, only when receipts are on
    fh_line = _forget_hits_line(data)
    if fh_line:
        print(fh_line)  # #404: one line, only when a re-assertion was suppressed
    re_line = _ruling_echo_line(data)
    if re_line:
        print(re_line)  # #693: one line, only when an echo was dropped
    rq_line = _requests_line(data)
    if rq_line:
        print(rq_line)  # #694 PR 3: one line, only when non-zero
    ho_line = _handoff_line(data)
    if ho_line:
        print(ho_line)  # #662: one line, only when a baton is waiting
    proj, glob, last = data["proj"], data["glob"], data["last"]
    print(f"project: {data['project']}")
    if proj["exists"]:
        print(f"project checkpoint: session {proj['session_id']}, written {proj['age']} ago")
        print(f"  {proj['path']}")
    else:
        print("project checkpoint: none")
    if glob["exists"]:
        if glob.get("same_session_as_project"):
            print("global checkpoint: same as project "
                  "(this project produced the most recent checkpoint anywhere)")
        else:
            print(f"global checkpoint (fallback): session {glob['session_id']}, "
                  f"written {glob['age']} ago")
        print(f"  {glob['path']}")
    else:
        print("global checkpoint (fallback): none")
    if last is None:
        print("last serialize: no serialize history")
        _print_crash_plain(data.get("crash"))
        _print_recall_error_plain(data.get("recall_error"))
        idx = _recall_index_line(data.get("recall_index"))
        if idx:
            print(idx)
        _print_skips_plain(data.get("skipped_recent"))
        return
    if last["result"]:
        print(f"last serialize result: {last['result']['outcome']} — {last['result']['line']}")
    else:
        print("last serialize result: none logged yet")
    if last["spawn"]:
        s = last["spawn"]
        ago = f", {s['age']} ago" if "age" in s else ""
        print(f"last serialize spawn: session {s['session_id']}{ago}")
    else:
        print("last serialize spawn: none logged yet")
    _print_crash_plain(data.get("crash"))
    _print_recall_error_plain(data.get("recall_error"))
    idx = _recall_index_line(data.get("recall_index"))
    if idx:
        print(idx)
    _print_skips_plain(data.get("skipped_recent"))

    outstanding = data.get("outstanding") or []
    if outstanding:
        n = len(outstanding)
        print("")
        print(f"⚠ {n} session{'s' if n != 1 else ''} failed to serialize (no checkpoint):")
        for line in _outstanding_lines(outstanding):
            print(line)


def _rich_status(data: dict) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    alarm = data.get("capture_alarm")
    if alarm:
        lines = _capture_alarm_lines(alarm)
        console.print(f"[bold red]{lines[0]}[/bold red]")
        for line in lines[1:]:
            console.print(f"[red]{line}[/red]")
    ident = data.get("identity")
    if ident:
        console.print(f"identity: {ident['cwd']}  →  git-root {ident['git_root']}  →  bucket {ident['slug']}")
    health = data.get("health")
    if health:
        style = "green" if health["ok"] else "red"
        console.print(f"[{style}]{health['verdict']}[/{style}]")
        for w in health["warnings"][1:]:
            console.print(f"  ⚠ {w}")
    if data.get("hook_drift"):
        console.print("[red]⚠ installed hooks out of date — "
                      "run daimon hooks status[/red]")
    if data.get("plugin_drift"):
        console.print(f"[red]{_plugin_drift_line(data['plugin_drift'])}[/red]")
    if data.get("rescue_gap"):
        console.print("[yellow]⚠ primary is a remote gateway and no fallback "
                      "backend resolves — gateway failures won't be rescued "
                      "(install claude or set DAIMON_LLM_COMMAND)[/yellow]")
    if _rescue_none_warns(data):
        console.print("[yellow]⚠ the `command` backend has no rescue path — "
                      "a failing command is not retried or substituted. Set "
                      "DAIMON_LLM_COMMAND_FALLBACK, or run `daimon status` "
                      "for the recorded failure cause.[/yellow]")
    if data.get("team"):
        console.print(data["team"])  # one objective line; absent when team unused (#113)
    if data.get("receipts"):
        console.print(data["receipts"])  # #204: one line, only when receipts are on
    fh_line = _forget_hits_line(data)
    if fh_line:
        console.print(fh_line)  # #404: one line, only when a re-assertion was suppressed
    re_line = _ruling_echo_line(data)
    if re_line:
        console.print(re_line)  # #693: one line, only when an echo was dropped
    rq_line = _requests_line(data)
    if rq_line:
        console.print(rq_line)  # #694 PR 3: one line, only when non-zero
    ho_line = _handoff_line(data)
    if ho_line:
        console.print(ho_line)  # #662: one line, only when a baton is waiting
    proj, glob, last = data["proj"], data["glob"], data["last"]
    table = Table(title=f"daimon status — {data['project']}", title_justify="left",
                  show_header=True, header_style="bold")
    table.add_column("pointer")
    table.add_column("session")
    table.add_column("age")
    table.add_row("project",
                  proj["session_id"] if proj["exists"] else "[dim]none[/dim]",
                  f"{proj['age']} ago" if proj["exists"] else "—")
    if glob["exists"] and glob.get("same_session_as_project"):
        table.add_row("global", "[green]same as project[/green]", "—")
    elif glob["exists"]:
        table.add_row("global (fallback)", glob["session_id"], f"{glob['age']} ago")
    else:
        table.add_row("global (fallback)", "[dim]none[/dim]", "—")
    console.print(table)
    # Mirror _plain_status fact-for-fact (#29): same command, same statements,
    # regardless of whether `rich` is installed. In particular a spawn with no
    # result yet (in-progress or hung serialize) must be visible here too.
    if last is None:
        console.print("[dim]no serialize history[/dim]")
    else:
        if last["result"]:
            style = "green" if last["result"]["outcome"] == "success" else "red"
            console.print(f"last serialize result: [{style}]{last['result']['outcome']}[/{style}] — "
                          f"{last['result']['line']}")
        else:
            console.print("last serialize result: none logged yet")
        if last["spawn"]:
            s = last["spawn"]
            ago = f", {s['age']} ago" if "age" in s else ""
            console.print(f"last serialize spawn: session {s['session_id']}{ago}")
        else:
            console.print("last serialize spawn: none logged yet")
    crash = data.get("crash")
    if crash:
        console.print(f"[red]last serialize crash:[/red] {crash['age']} ago — "
                      f"{crash['last_line']}")
        console.print(f"  [dim]full traceback: {crash['path']}[/dim]")
    recall_err = data.get("recall_error")
    if recall_err:
        console.print(f"[red]last recall error:[/red] {recall_err['age']} ago — "
                      f"{recall_err['last_line']}")
    idx = _recall_index_line(data.get("recall_index"))
    if idx:
        console.print(f"[dim]{idx}[/dim]")
    if data.get("skipped_recent"):
        console.print(f"[dim]recent sessions skipped (too short to serialize): "
                      f"{data['skipped_recent']}[/dim]")

    outstanding = data.get("outstanding") or []
    if outstanding:
        from rich.panel import Panel
        from rich.text import Text
        n = len(outstanding)
        body = Text("\n".join(_outstanding_lines(outstanding)))
        console.print(Panel(
            body,
            title=f"⚠ {n} session{'s' if n != 1 else ''} failed to serialize (no checkpoint)",
            border_style="red", title_align="left",
        ))


# ---- skill: `daimon skill list|install|uninstall` (#66) --------------------


def render_skill_list(rows) -> None:
    """`daimon skill list`: `rows` is [(host, scopes), ...], `scopes` a list of
    "global"/"project" strings — same shape a simple table needs."""
    if not supports_rich():
        for host, scopes in rows:
            print(f"{host}  ({', '.join(scopes)})")
        return
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(show_header=True, header_style="bold")
    table.add_column("host")
    table.add_column("scopes")
    for host, scopes in rows:
        table.add_row(host, ", ".join(scopes))
    console.print(table)


def render_skill_lines(lines, *, footer=None) -> None:
    """Generic renderer for `skill install`/`uninstall` result lines: mostly
    plain confirmations, occasionally a "warning: ..." line (e.g. a host's
    char-cap truncation notice) that gets yellow styling on the rich path.
    `footer`, if given, is trailing line(s) printed after a blank line — the
    upgrade-reminder `skill install` prints after its result lines."""
    _render_lines(lines, footer=footer)


def _render_lines(lines, *, footer=None) -> None:
    """Shared "print a list of pre-formatted lines" primitive behind
    render_skill_lines/render_recall_lines/render_hooks_*/render_team_*: the
    plain path is a bare print loop (byte-identical to each command's
    pre-#68 output); the rich path upgrades "warning:"- and "⚠"-prefixed
    lines to yellow. `markup=False` is load-bearing — recall lines contain literal
    "[author]"/"[trust]"/"[kind]" brackets, which rich's Console would
    otherwise parse as (invalid, silently-dropped) style tags, eating the
    content. `footer`, if given, prints after a blank-line separator."""
    if not supports_rich():
        for ln in lines:
            print(ln)
        if footer:
            print("")
            for ln in footer:
                print(ln)
        return
    from rich.console import Console

    console = Console()
    for ln in lines:
        style = "yellow" if ln.startswith(("warning:", "⚠")) else None
        console.print(ln, style=style, markup=False)
    if footer:
        console.print("")
        for ln in footer:
            console.print(ln, markup=False)


# ---- projects: `daimon projects` (#243) --------------------------------------


def render_projects(rows) -> None:
    """`daimon projects`: one row per checkpoint bucket. `rows` is
    [{mark, name, slug, age, branch, topic}], pre-formatted strings — sorting,
    truncation, and the current-project mark are the CLI's concern. `name` is
    the #672 write-time stamp; the slug column stays untouched because it is
    what users copy into --slug."""
    if not supports_rich():
        n = max((len(r["name"]) for r in rows), default=0)
        w = max((len(r["slug"]) for r in rows), default=0)
        a = max((len(r["age"]) for r in rows), default=0)
        b = max((len(r["branch"]) for r in rows), default=0)
        for r in rows:
            print(f"{r['mark']} {r['name']:<{n}}  {r['slug']:<{w}}  {r['age']:<{a}}  "
                  f"{r['branch']:<{b}}  {r['topic']}")
        return
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(show_header=True, header_style="bold")
    table.add_column("")
    table.add_column("name")
    table.add_column("project")
    table.add_column("last")
    table.add_column("branch")
    table.add_column("topic")
    for r in rows:
        table.add_row(r["mark"], r["name"], r["slug"], r["age"], r["branch"], r["topic"])
    console.print(table)


# ---- recall: `daimon recall` (#68) ------------------------------------------


def render_recall_lines(lines) -> None:
    """`daimon recall` human-facing matches, or the single "no matches" line.
    `--json` and `recall-inject` (machine-consumed) never route through here —
    they stay plain unconditionally."""
    _render_lines(lines)


# ---- hooks: `daimon hooks list|install` (#68) -------------------------------


def render_hooks_list(lines) -> None:
    _render_lines(lines)


def render_hooks_install(lines) -> None:
    _render_lines(lines)


def render_hooks_status(report) -> None:
    """Per-host, per-file drift audit (#266). NOT INSTALLED hosts get one line;
    installed hosts list each file's verdict, the registration state where the
    host uses one, and a single fix hint when anything drifted."""
    lines: list[str] = []
    for h in report:
        if not h["installed"]:
            lines.append(f"{h['host']}: NOT INSTALLED")
            continue
        lines.append(f"{h['host']}  ({h['dir']})")
        for f in h["files"]:
            lines.append(f"  {f['status']:<8} {f['name']}")
        if h["registration"] is not None:
            lines.append(f"  registration: {h['registration']}")
        if h["drift"]:
            lines.append(f"  → fix: daimon hooks install {h['host']}")
    if not lines:
        lines.append("no packaged hook hosts")
    _render_lines(lines)


# ---- team: `daimon team init|sync|status` (#68) -----------------------------


def render_team_init(lines) -> None:
    _render_lines(lines)


def render_team_sync(lines) -> None:
    _render_lines(lines)


def render_team_status(lines) -> None:
    _render_lines(lines)


# ---- residual command results (#75) ------------------------------------------


def render_write_checkpoint(lines) -> None:
    """`daimon write-checkpoint` success line. Validation errors stay plain on
    stderr and never route through here."""
    _render_lines(lines)


def render_anchor_attach(lines) -> None:
    """`daimon anchor --attach` success line. The no-attach JSON dump and all
    error paths stay plain unconditionally."""
    _render_lines(lines)


def render_configure_lines(lines) -> None:
    """`daimon configure` result lines: backend-test ok, "wrote <env path>",
    and the non-interactive not-ready guidance. The resolved-state block
    renders via render_configure; FAILED paths stay plain on stderr."""
    _render_lines(lines)


def render_brief_note(lines) -> None:
    """`daimon brief` advisory notes — the ⚠ global-fallback warning."""
    _render_lines(lines)


def render_heal_abort(lines) -> None:
    """`daimon heal` abort notice (target transcript vanished)."""
    _render_lines(lines)


# ---- ledger: refute/ruling/amend record cards (#707) -------------------------


def _ledger_style(ln: str) -> str | None:
    """Per-line style for a ledger record card. Header lines are literal
    bracketed text (`[§ active · …] r-… <verdict>`), so styling keys off the
    state token INSIDE the line — never off rich markup. Restraint on purpose:
    the polarity glyph carries the meaning (§ standing law, ✗ negative guard),
    color only echoes the state."""
    if ln.startswith("["):
        if "✗ active" in ln:
            return "red"
        if "§ active" in ln:
            return "green"
        if "? candidate" in ln:
            return "yellow"
        # both spellings: untagged "[× retired …" and tagged
        # "[refutation × overturned …" / "[ruling × retired …"
        if re.match(r"\[(?:\w+ )?× ", ln):
            return "dim"
        return None
    if ln.startswith(("  Pending", "  Overturn proposed")):
        return "yellow"
    if ln.startswith("  (evidence sources"):
        return "dim"
    if ln.startswith("over cap:"):
        return "yellow"
    if ln.startswith("warning:"):
        # #694: `request open --anyway` records an ask no bucket can surface.
        # Same lowercase register _lifecycle_style uses for a degraded
        # outcome, and the same colour, so one word means one thing across
        # both families.
        return "yellow"
    return None


# #694 widened the id class from r- to the ledger id space (r- rulings and
# refutations, q- requests): the three-span header is a per-LINE shape, not
# a per-ledger one, and a request card that fell back to whole-line styling
# would bury the id a human copies.
# #766 adds a- (amendments), which `decide` renders beside the other two.
# The failure this prevents is silent: the plain path is byte-identical
# either way, so only the rich path loses the id it was meant to highlight.
_LEDGER_HEADER_RE = re.compile(r"^(\[[^\]]*\]) ([rqa]-[0-9a-f]{12})  (.*)$")


def _ledger_header_spans(ln: str):
    """Split a record header into (state bracket, id, text) for span styling,
    or None for any other line shape."""
    m = _LEDGER_HEADER_RE.match(ln)
    return m.groups() if m else None


def _print_ledger_line(console, ln: str) -> None:
    """One ledger line on the rich path. A record header gets three spans —
    state bracket in the state's color (quiet metadata), the id bold cyan
    (the handle a human copies), the verdict/subject in the default color
    (prose reads as prose). A uniformly colored header buried all three
    (#707 stage 1 feedback). Every other line keeps whole-line styling; the
    recomposition prints exactly the plain line's characters."""
    spans = _ledger_header_spans(ln)
    if spans is None:
        console.print(ln, style=_ledger_style(ln), markup=False)
        return
    from rich.text import Text

    bracket, record_id, rest = spans
    text = Text()
    text.append(bracket, style=_ledger_style(bracket) or "dim")
    text.append(" ")
    text.append(record_id, style="bold cyan")
    text.append("  ")
    text.append(rest)
    console.print(text)


def _lifecycle_style(ln: str) -> str | None:
    """Per-line style for the lifecycle/report surfaces. The forget report
    interleaves successes with per-store degradations; the two failure
    registers stay visually distinct — a lowercase `warning:` is a degraded
    cleanup (yellow, the register _render_lines already uses), an uppercase
    `WARNING` discloses removal of an ACTIVE ruling (red, the same weight the
    active-refutation header carries)."""
    if ln.startswith("WARNING"):
        return "red"
    if ln.startswith(("warning:", "⚠")):
        return "yellow"
    return None


def render_lifecycle_lines(lines) -> None:
    """Human-facing result lines of the item-lifecycle and report verbs
    (#707 stage 2): resolve, forget (dry-run preview and success report),
    reverify, loops, handoff, log, verify-receipt, and the quote audit.
    Plain path is the bare print loop these commands always had
    (byte-identical); rich styles the two warning registers. `markup=False`
    is load-bearing — loops rows carry literal [key] [mark] brackets.
    Refusals and error lines stay plain, the render_anchor_attach contract."""
    if not supports_rich():
        for ln in lines:
            print(ln)
        return
    from rich.console import Console

    console = Console()
    for ln in lines:
        console.print(ln, style=_lifecycle_style(ln), markup=False)


def render_ledger_records(records) -> None:
    """A sequence of record cards (each a list of pre-formatted lines) —
    `ruling list`, `refute list`, `refute search`. Plain path prints them
    flat, byte-identical to the single-list form; rich separates records
    with one blank line so wrapped verdicts don't read as a wall."""
    if not supports_rich():
        for lines in records:
            for ln in lines:
                print(ln)
        return
    from rich.console import Console

    console = Console()
    for i, lines in enumerate(records):
        if i:
            console.print("")
        for ln in lines:
            _print_ledger_line(console, ln)


def render_ledger_lines(lines) -> None:
    """Refutation/ruling/amendment record cards and their advisory notes
    (#707). Plain path is the bare print loop the ledger verbs always had
    (byte-identical); rich upgrades header lines by state and dims the
    boilerplate. `markup=False` is load-bearing — the state header is literal
    bracketed text rich would otherwise parse as a (silently dropped) style
    tag. Refusal/error lines never route through here; they stay plain, the
    same contract render_anchor_attach documents."""
    if not supports_rich():
        for ln in lines:
            print(ln)
        return
    from rich.console import Console

    console = Console()
    for ln in lines:
        _print_ledger_line(console, ln)


# ---- stats: `daimon stats` (#68) --------------------------------------------


def render_stats(data: dict) -> None:
    if supports_rich():
        _rich_stats(data)
    else:
        _plain_stats(data)


def _capture_window_lines(c: dict) -> list[str]:
    """#364: the rolling-window capture-rate line(s), shared verbatim by the
    plain and rich stats renderers. Second line only when the rolling error
    rate trips the reopen gate recorded on #364."""
    from .ledger import _CAPTURE_ERROR_GATE_PCT, _RESCUE_GATE_PCT
    w = c.get("window")
    if not w:
        return []
    rate = w["error_rate_pct"]
    # #742: starved appears only when nonzero — a healthy window keeps the
    # line shape every existing reader knows.
    starved = w.get("starved", 0)
    starved_part = f"starved {starved}  " if starved else ""
    lines = [f"last {w['days']}d: serialized {w['success']}  "
             f"errors {w['errors']}  rescued {w['fallback_serializes']}  "
             f"{starved_part}"
             f"error rate: {'n/a' if rate is None else f'{rate}%'}"]
    if rate is not None and rate > _CAPTURE_ERROR_GATE_PCT:
        lines.append(f"⚠ capture error rate {rate}% (last {w['days']}d) "
                     f"exceeds the {_CAPTURE_ERROR_GATE_PCT}% gate — see "
                     "`daimon status` for failing serializes")
    attempts = w.get("fallback_attempts", 0)
    rescued = w.get("fallback_serializes", 0)
    if attempts and rescued * 100 < attempts * _RESCUE_GATE_PCT:
        lines.append(f"⚠ rescue succeeded {rescued} of {attempts} attempts "
                     f"(last {w['days']}d) — below the {_RESCUE_GATE_PCT}% "
                     "gate recorded on #742")
    return lines


# #475 part 2: `fallback: attempted 0, succeeded 0` reads identically whether
# a rescue existed and was never needed OR no rescue path can exist for this
# backend at all — the counter isn't wrong, it's unreadable. This suffix adds
# the missing fact (current-configuration posture) next to the historical
# counts, never merged into them.
_RESCUE_SUFFIXES = {
    "covered": "rescue available, not needed",
    "disabled": "fallback disabled by config",
    "gap": "no fallback resolves — gateway failures won't be rescued",
    # #475 split DAIMON_LLM_COMMAND's overload, so a command backend now HAS
    # a fallback direction — this posture means none is configured, which is
    # a fixable state and must name its fix rather than assert an absence.
    "none": "no rescue path — set DAIMON_LLM_COMMAND_FALLBACK",
}


def _rescue_suffix(posture, fallback_attempts: int) -> str | None:
    """The `(...)` parenthetical for the stats fallback line, or None for a
    posture with nothing to add (e.g. "no-backend", where there is no LLM
    configured at all).

    History-versus-config disagreement wins over the plain "none" text: an
    install that ran on litellm and later pinned `command` shows historical
    attempts under a current no-rescue posture. Same shape as #477 — do not
    infer which config produced the counts, state both facts and let them
    disagree visibly."""
    if posture == "none" and fallback_attempts > 0:
        return ("counts are historical; the current `command` backend has "
                "no rescue configured")
    return _RESCUE_SUFFIXES.get(posture)


def _generation_lines(s: dict) -> list[str]:
    """#514: corpus generation composition, shared by the plain and rich
    stats renderers. Shown only when MORE than one generation coexists —
    a uniform corpus is the healthy default and needs no line; "unknown"
    buckets (pre-stamp checkpoints) count as their own generation, because
    unknown-vs-current is exactly the mix worth surfacing."""
    lines = []
    for label, key in (("format versions", "format_versions"),
                       ("extraction versions", "extraction_versions")):
        counts = s.get(key) or {}
        if len(counts) > 1:
            lines.append(f"{label}: " + ", ".join(
                f"{v}: {n}" for v, n in sorted(counts.items())))
    return lines


def _plain_stats(data: dict) -> None:
    u, c, s = data["usage"], data["capture"], data["store"]
    print("usage (local, never transmitted):")
    if u:
        for cmd_name, n in sorted(u.items(), key=lambda kv: -kv[1]):
            print(f"  {cmd_name}: {n}")
    else:
        print("  none recorded yet")
    r = data.get("retention")
    if r:
        print(f"retention (last {r['window_days']}d):")
        print(f"  briefings delivered: hook {r['hook_briefs']}, "
              f"skill-invoked {r['skill_briefs']}  "
              f"(total {r['briefings_total']})")
        rr = r["rereads"]
        print(f"  deliberate re-reads: brief {rr['brief']}, "
              f"recall {rr['recall']}  (total {r['rereads_total']})")
        print(f"  status checks: {r['status_checks']}  (ops, not counted)")
        ratio = r["rereads_per_briefing"]
        shown = _ratio_na(r["delivery_mode"]) if ratio is None else ratio
        print(f"  re-reads per briefing: {shown}")
        if r["ambiguous_briefs"]:
            print(f"  unclassified `brief` invocations: {r['ambiguous_briefs']}")
        if r["untagged_briefs"]:
            print(f"  untagged brief lines (pre --auto): {r['untagged_briefs']}")
        if r["stale_hook_warning"]:
            print("  ⚠ sessions captured but no hook briefings logged — the "
                  "SessionStart hook may predate --auto; re-run `daimon hooks "
                  "install` (or update the plugin)")
    print("capture:")
    fallback_attempts = c.get("fallback_attempts", 0)
    suffix = _rescue_suffix(data.get("rescue_posture"), fallback_attempts)
    print(f"  serialized: {c['success']}  skipped: {c['skipped']}  "
          f"errors: {c['errors']}  fallback: "
          f"attempted {fallback_attempts}, "
          f"succeeded {c['fallback_serializes']}"
          + (f"  ({suffix})" if suffix else ""))
    for line in _capture_window_lines(c):
        print(f"  {line}")
    if c["hosts"]:
        print("  spawns by host: " + ", ".join(
            f"{h}: {n}" for h, n in sorted(c["hosts"].items())))
    if c["success"]:
        print(f"  serialize seconds: max {c['max_serialize_seconds']}, "
              f"avg {c['total_serialize_seconds'] // c['success']}")
    print("store:")
    print(f"  checkpoints: {s['checkpoints']}  project buckets: {s['project_buckets']}")
    for line in _generation_lines(s):
        print(f"  {line}")
    if s["items_by_kind"]:
        print("  items by kind: " + ", ".join(
            f"{k}: {n}" for k, n in sorted(s["items_by_kind"].items())))
    print(f"  trust: verbatim {s['items_verbatim']}, inferred {s['items_inferred']}, "
          f"untagged {s['items_untagged']}  (carried: {s['items_carried']})")
    e = data.get("events")
    if e:
        print("events (this project):")
        print(f"  log lines: {e['lines']}  resolved refs: {e['resolved_refs']}  "
              f"fold: {e['fold_ms']}ms")
    v = data.get("verification")
    if v and v.get("total"):
        # #376: what the checkers REJECTED here. Shown only when non-zero —
        # zero is a real answer but an all-zero block is noise in the default
        # view, and `--json` always carries it.
        print("verification (this project):")
        print(f"  rejections: {v['total']}  " + ", ".join(
            f"{k} {n}" for k, n in sorted(v["by_check"].items())))
    res = data.get("resolutions")
    if res:
        # #480 slice 5: the credit block — who is closing loops. Always shown
        # (like events, unlike verification's non-zero gate): zero here is
        # the design's own pre-registered failure signal, not noise.
        print("resolutions (this project, lifetime):")
        print(f"  human: {res['human']}  agent-verified: {res['agent_verified']}  "
              f"agent-pending: {res['agent_pending']}")
        # #562: the counters above are a lifetime fold. Human credit recorded
        # before agent credit could exist is not evidence that humans out-close
        # agents — the comparison had no other side. Say so, and only while it
        # is true; the line disappears on its own once both populations overlap.
        pre = res.get("human_before_agent") or 0
        if pre:
            since = res.get("agent_since")
            when = f", first seen {since[:10]}" if since else ""
            print(f"  note: {pre} of those predate any agent-recorded "
                  f"resolution{when} — not a human-vs-agent ratio")
        # #477 lesson: refused comes from usage.log, a DIFFERENT (per-machine)
        # population than the three counters above (per-project) — labeled
        # apart, on its own line, never summed with them.
        print(f"  refused (this machine): {res['refused']}")


def _rich_stats(data: dict) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    u, c, s = data["usage"], data["capture"], data["store"]

    usage_table = Table(title="usage (local, never transmitted)", title_justify="left",
                        show_header=True, header_style="bold")
    usage_table.add_column("command")
    usage_table.add_column("count", justify="right")
    if u:
        for cmd_name, n in sorted(u.items(), key=lambda kv: -kv[1]):
            usage_table.add_row(cmd_name, str(n))
    else:
        usage_table.add_row("[dim]none recorded yet[/dim]", "")
    console.print(usage_table)

    r = data.get("retention")
    if r:
        ret_table = Table(title=f"retention (last {r['window_days']}d)",
                          title_justify="left", show_header=True,
                          header_style="bold")
        ret_table.add_column("metric")
        ret_table.add_column("value", justify="right")
        ret_table.add_row("briefings delivered",
                          f"hook {r['hook_briefs']}, "
                          f"skill-invoked {r['skill_briefs']} "
                          f"(total {r['briefings_total']})")
        rr = r["rereads"]
        ret_table.add_row("deliberate re-reads",
                          f"brief {rr['brief']}, "
                          f"recall {rr['recall']} (total {r['rereads_total']})")
        ret_table.add_row("status checks (ops, not counted)",
                          str(r["status_checks"]))
        ratio = r["rereads_per_briefing"]
        ret_table.add_row("re-reads per briefing",
                          _ratio_na(r["delivery_mode"]) if ratio is None
                          else str(ratio))
        if r["ambiguous_briefs"]:
            ret_table.add_row("unclassified `brief` invocations",
                              str(r["ambiguous_briefs"]))
        if r["untagged_briefs"]:
            ret_table.add_row("untagged brief lines (pre --auto)",
                              str(r["untagged_briefs"]))
        console.print(ret_table)
        if r["stale_hook_warning"]:
            console.print("[yellow]⚠ sessions captured but no hook briefings "
                          "logged — re-run `daimon hooks install` (or update "
                          "the plugin)[/yellow]")

    capture_table = Table(title="capture", title_justify="left",
                          show_header=True, header_style="bold")
    capture_table.add_column("metric")
    capture_table.add_column("value")
    capture_table.add_row("serialized", str(c["success"]))
    capture_table.add_row("skipped", str(c["skipped"]))
    capture_table.add_row("errors", str(c["errors"]))
    fallback_attempts = c.get("fallback_attempts", 0)
    suffix = _rescue_suffix(data.get("rescue_posture"), fallback_attempts)
    capture_table.add_row("fallback", f"attempted {fallback_attempts}, "
                                      f"succeeded {c['fallback_serializes']}"
                                      + (f"  ({suffix})" if suffix else ""))
    window_lines = _capture_window_lines(c)
    if window_lines:
        # first line is `last Nd: <values>` — split it into the two columns
        label, _, values = window_lines[0].partition(": ")
        capture_table.add_row(label, values)
    if c["hosts"]:
        capture_table.add_row("spawns by host", ", ".join(
            f"{h}: {n}" for h, n in sorted(c["hosts"].items())))
    if c["success"]:
        capture_table.add_row(
            "serialize seconds",
            f"max {c['max_serialize_seconds']}, "
            f"avg {c['total_serialize_seconds'] // c['success']}",
        )
    console.print(capture_table)
    for warning in window_lines[1:]:  # the #364 gate warning, when tripped
        console.print(f"[yellow]{warning}[/yellow]")

    store_table = Table(title="store", title_justify="left",
                        show_header=True, header_style="bold")
    store_table.add_column("metric")
    store_table.add_column("value")
    store_table.add_row("checkpoints", str(s["checkpoints"]))
    store_table.add_row("project buckets", str(s["project_buckets"]))
    for line in _generation_lines(s):
        label, _, rest = line.partition(": ")
        store_table.add_row(label, rest)
    if s["items_by_kind"]:
        store_table.add_row("items by kind", ", ".join(
            f"{k}: {n}" for k, n in sorted(s["items_by_kind"].items())))
    store_table.add_row(
        "trust",
        f"verbatim {s['items_verbatim']}, inferred {s['items_inferred']}, "
        f"untagged {s['items_untagged']}  (carried: {s['items_carried']})",
    )
    console.print(store_table)

    e = data.get("events")
    if e:
        events_table = Table(title="events (this project)", title_justify="left",
                             show_header=True, header_style="bold")
        events_table.add_column("metric")
        events_table.add_column("value")
        events_table.add_row("log lines", str(e["lines"]))
        events_table.add_row("resolved refs", str(e["resolved_refs"]))
        events_table.add_row("fold", f"{e['fold_ms']}ms")
        console.print(events_table)

    v = data.get("verification")
    if v and v.get("total"):
        # #376: mirrors the plain renderer — non-zero only.
        ver_table = Table(title="verification (this project)",
                          title_justify="left", show_header=True,
                          header_style="bold")
        ver_table.add_column("check")
        ver_table.add_column("rejections")
        for check, n in sorted(v["by_check"].items()):
            ver_table.add_row(check, str(n))
        ver_table.add_row("total", str(v["total"]))
        console.print(ver_table)

    res = data.get("resolutions")
    if res:
        # #480 slice 5: mirrors the plain renderer — always shown, and the
        # refused row keeps its (this machine) label so the two populations
        # never read as one number (#477).
        res_table = Table(title="resolutions (this project, lifetime)",
                          title_justify="left", show_header=True,
                          header_style="bold")
        res_table.add_column("metric")
        res_table.add_column("value")
        res_table.add_row("human", str(res["human"]))
        res_table.add_row("agent-verified", str(res["agent_verified"]))
        res_table.add_row("agent-pending", str(res["agent_pending"]))
        res_table.add_row("refused (this machine)", str(res["refused"]))
        console.print(res_table)
        # #562: same caveat as the plain renderer, same disappearing condition.
        pre = res.get("human_before_agent") or 0
        if pre:
            since = res.get("agent_since")
            when = f", first seen {since[:10]}" if since else ""
            console.print(f"[yellow]note: {pre} of those predate any "
                          f"agent-recorded resolution{when} — not a "
                          f"human-vs-agent ratio[/yellow]")


def render_privacy_audit(results: list[dict]) -> None:
    """Hashes only, never the text: this output gets re-serialized into
    checkpoints, so printing a forgotten value would re-capture it."""
    for r in results:
        slug = r.get("slug") or "(unknown project)"
        print(f"project {slug}: {r['surfaces_scanned']} surface(s) scanned")
        if r.get("zero_surfaces"):
            print("  WARNING: zero surfaces found — cannot distinguish an"
                  " empty project from a scoping failure (not 'clean')")
        for f in r["findings"]:
            print(f"  RESIDUE [{f['surface']}] hash {f['content_hash']}"
                  f" item {f.get('item_id') or '?'} in {f['path']}")
            if f["surface"] == "team-copy":
                print("    note: a team copy may also exist upstream,"
                      " which no local scrub can reach")
        for f in r["informational"]:
            print(f"  stale [{f['surface']}] hash {f['content_hash']}"
                  f" in {f['path']} (deleted at next index rebuild)")
        for p in r["unscannable"]:
            print(f"  UNSCANNABLE {p}")
        cache = r.get("cache") or {}
        if cache.get("entries") and cache.get("oldest_days") is not None:
            print(f"  chunk cache: {cache['entries']} entr(y/ies), oldest"
                  f" {cache['oldest_days']:.1f}d — value-level scan impossible"
                  " (substring vs hash); purge is wholesale on forget")
        elif cache.get("entries"):
            print(f"  chunk cache: {cache['entries']} entr(y/ies) — age unknown,"
                  " value-level scan impossible (substring vs hash); purge is"
                  " wholesale on forget")
        led = r.get("ledger") or {}
        if led.get("rows"):
            print(f"  refutation ledger: {led['records']} record(s) in"
                  f" {led['rows']} row(s), {led['bytes'] / 1024:.0f} KB —"
                  " append-only negative knowledge; forget reaches it by"
                  " value, nothing reaps it by age")
        amd = r.get("amendments") or {}
        if amd.get("rows"):
            # #691: the growth measurement the surface registry promises —
            # printed, not just computed, so growth is never silent. Same
            # containment caveat as the event-note line: forget matches a
            # WHOLE value, and a quote merely containing one is invisible
            # to the hash scan.
            print(f"  amendment ledger: {amd['records']} record(s) in"
                  f" {amd['rows']} row(s), {amd['bytes'] / 1024:.0f} KB —"
                  " forget reaches it by value (whole-value match) and by"
                  " target item; quotes merely CONTAINING a forgotten value"
                  " are beyond the hash scan")
        req = r.get("requests") or {}
        if req.get("rows"):
            # #694: same measured-not-silent promise as the two ledgers
            # above. The containment caveat is the same one, and it matters
            # more here: the prose was authored FOR another project, so a
            # value quoted inside an ask is exactly the shape a whole-value
            # hash scan cannot see.
            print(f"  request ledger: {req['records']} record(s) in"
                  f" {req['rows']} row(s), {req['bytes'] / 1024:.0f} KB —"
                  " forget reaches it by value (whole-value match); asks"
                  " merely CONTAINING a forgotten value are beyond the hash"
                  " scan")
        ws = r.get("windsurf") or {}
        if ws.get("entries"):
            age = (f", oldest {ws['oldest_days']:.1f}d"
                   if ws.get("oldest_days") is not None else ", age unknown")
            print(f"  windsurf transcripts: {ws['entries']} file(s){age} —"
                  " daimon-authored conversation text; value-level scan"
                  " impossible (substring vs hash); purge is wholesale on"
                  " forget")
        bl = r.get("backend_log") or {}
        if bl.get("present"):
            age = (f", last write {bl['age_days']:.1f}d ago"
                   if bl.get("age_days") is not None else ", age unknown")
            print(f"  backend stderr log: present{age} — backend echo can"
                  " carry transcript text (#616); value-level scan impossible"
                  " (substring vs hash); purge is wholesale on forget")
        if not (r["findings"] or r["informational"] or r["unscannable"]
                or r.get("zero_surfaces")):
            print("  clean — no tombstoned value found on any surface")
    print("note: a free-text event note merely CONTAINING a forgotten value"
          " is undetectable by hash; only verbatim notes are caught")


# -- relations (#678): adjudication surfaces share one presentation seam so
#    the CLI list and any later viewer lane cannot drift apart. Plain output
#    is the contract the tests pin; rich is the same content, styled.

_RELATION_STATE_STYLE = {
    "candidate": "yellow",
    "confirmed": "bold green",
    "rejected": "red",
    "retracted": "dim",
}


def _plain_relation(record, texts, *, detailed: bool = False) -> None:
    frm, to = record.get("from") or {}, record.get("to") or {}
    flag = " CONTRADICTION" if record.get("contradiction") else ""
    print(f"{record['relation_id']}  {record['state']:<9} "
          f"{record['type']}{flag}")
    for label, endpoint in (("from", frm), ("to", to)):
        item_id = str(endpoint.get("item_id") or "")
        text = texts.get(item_id, "[unresolved]")
        print(f"  {label}: {item_id} ({endpoint.get('session_id')}) {text}")
    if detailed:
        for proposal in record.get("proposals") or []:
            print(f"  proposed by {proposal.get('matcher_version')}: "
                  f"{', '.join(proposal.get('matched_by') or [])}")
        if record.get("confirmed_channel"):
            print(f"  confirmed via {record['confirmed_channel']}")


def _relation_endpoint_cell(endpoint, texts) -> str:
    item_id = str(endpoint.get("item_id") or "")
    return f"{item_id}\n{texts.get(item_id, '[unresolved]')}"


def _rich_relations_list(rows, texts) -> None:
    from rich.console import Console
    from rich.table import Table

    table = Table(header_style="bold", pad_edge=False)
    for column in ("relation", "state", "type", "from", "to"):
        table.add_column(column, overflow="fold")
    for record in rows:
        style = _RELATION_STATE_STYLE.get(record["state"], "white")
        type_cell = record["type"] + (
            " [bold red]CONTRADICTION[/]" if record.get("contradiction")
            else "")
        table.add_row(
            record["relation_id"],
            f"[{style}]{record['state']}[/]",
            type_cell,
            _relation_endpoint_cell(record.get("from") or {}, texts),
            _relation_endpoint_cell(record.get("to") or {}, texts),
        )
    Console().print(table)


def _rich_relation(record, texts) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    body = Text()
    style = _RELATION_STATE_STYLE.get(record["state"], "white")
    body.append(f"{record['state']} · {record['type']}\n", style=style)
    if record.get("contradiction"):
        body.append("CONTRADICTION: the confirmed inverse edge also exists\n",
                    style="bold red")
    for label, endpoint in (("from", record.get("from") or {}),
                            ("to", record.get("to") or {})):
        item_id = str(endpoint.get("item_id") or "")
        body.append(f"{label}: {item_id} ({endpoint.get('session_id')})\n",
                    style="bold")
        body.append(f"  {texts.get(item_id, '[unresolved]')}\n")
    for proposal in record.get("proposals") or []:
        body.append(f"proposed by {proposal.get('matcher_version')}: "
                    f"{', '.join(proposal.get('matched_by') or [])}\n",
                    style="dim")
    if record.get("confirmed_channel"):
        body.append(f"confirmed via {record['confirmed_channel']}\n",
                    style="green")
    Console().print(Panel(body, title=record["relation_id"],
                          border_style="white", title_align="left"))


def render_relations_list(rows, texts, withheld: int) -> None:
    if not rows:
        print("no relations for this project")
    elif supports_rich():
        _rich_relations_list(rows, texts)
    else:
        for record in rows:
            _plain_relation(record, texts)
    if withheld:
        print(f"{withheld} edge(s) withheld (erased endpoint)")


def render_relation(record, texts) -> None:
    if supports_rich():
        _rich_relation(record, texts)
    else:
        _plain_relation(record, texts, detailed=True)
