"""Presentation layer for human-facing CLI output.

Single capability gate (`supports_rich`) decides plain vs rich. `rich` is an
OPTIONAL dependency (`daimon[pretty]`) imported lazily inside the rich branch,
so this module is import-safe with rich absent and the hook/serialize path —
which is non-TTY — always renders plain.
"""

import os
import sys
from contextlib import contextmanager

from . import briefing, config, schema, serializer

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


def render_brief(checkpoint, drift=None, teammates=None) -> None:
    b = briefing.build(checkpoint)
    if b is None:
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
        rendered = briefing.render(checkpoint)  # tries LLM, falls back to deterministic
        if rendered:
            print(rendered)
            _print_drift(drift)
            _print_teammates(teammates)
            return
    # #204: degrade verbatim labels when the receipt can't be locally confirmed.
    # Cheap check (sidecar + byte match), computed once for both render paths.
    degraded = briefing.receipt_degraded(checkpoint)
    if not supports_rich():
        print(briefing.render_plain(b, degraded))
    else:
        _rich_brief(b, degraded)
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


def _rich_brief(b: dict, degraded: bool = False) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    console = Console()
    console.print(Text("While you were away — here's where we left off.", style="bold"))
    if degraded:
        # One header note (#204), parity with the plain path's embedded note.
        console.print(Text(briefing.DEGRADE_NOTE, style="bold red"))
    for key, title, style in _SECTIONS:
        items = b.get(key) or []
        if not items:
            continue
        body = Text()
        for i in items:
            trust = _trust_key(i)
            # Degrade a verbatim item's confident green — its integrity is
            # unverified (#204). Inferred/untagged never claimed it, so untouched.
            item_style = "bold red" if (degraded and trust == "verbatim") \
                else _TRUST_STYLE[trust]
            body.append(f"• {i.get('text', '').strip()}\n", style=item_style)
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
            print(f"  Active topic: {active.get('text', '').strip()}")
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
        decisions = b.get("decisions") or []
        if decisions:
            body.append("Decisions made:\n", style="bold")
            for i in decisions:
                trust = _trust_key(i)
                body.append(f"• {i.get('text', '').strip()}\n", style=_TRUST_STYLE[trust])
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
                return f"backend: {rb} (claude CLI, zero-config)"
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


def _plain_configure(st: dict) -> None:
    mark = "✓" if st["ready"] else "✗"
    state = "ready" if st["ready"] else "not ready"
    print(f"{mark} {state} — {_explain(st)}")
    print(f"  env file: {st['env_file']}")


def _rich_configure(st: dict) -> None:
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    style = "green" if st["ready"] else "red"
    state = "ready" if st["ready"] else "not ready"
    body = f"[{style}]{state}[/{style}] — {_explain(st)}\nenv file: [dim]{st['env_file']}[/dim]"
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
    if data.get("team"):
        print(data["team"])  # one objective line; absent when team unused (#113)
    if data.get("receipts"):
        print(data["receipts"])  # #204: one line, only when receipts are on
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
    if data.get("team"):
        console.print(data["team"])  # one objective line; absent when team unused (#113)
    if data.get("receipts"):
        console.print(data["receipts"])  # #204: one line, only when receipts are on
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
    [{mark, slug, age, branch, topic}], pre-formatted strings — sorting,
    truncation, and the current-project mark are the CLI's concern."""
    if not supports_rich():
        w = max((len(r["slug"]) for r in rows), default=0)
        a = max((len(r["age"]) for r in rows), default=0)
        b = max((len(r["branch"]) for r in rows), default=0)
        for r in rows:
            print(f"{r['mark']} {r['slug']:<{w}}  {r['age']:<{a}}  "
                  f"{r['branch']:<{b}}  {r['topic']}")
        return
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(show_header=True, header_style="bold")
    table.add_column("")
    table.add_column("project")
    table.add_column("last")
    table.add_column("branch")
    table.add_column("topic")
    for r in rows:
        table.add_row(r["mark"], r["slug"], r["age"], r["branch"], r["topic"])
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


# ---- stats: `daimon stats` (#68) --------------------------------------------


def render_stats(data: dict) -> None:
    if supports_rich():
        _rich_stats(data)
    else:
        _plain_stats(data)


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
        print(f"  hook briefings: {r['hook_briefs']}")
        rr = r["rereads"]
        print(f"  deliberate re-reads: brief {rr['brief']}, "
              f"recall {rr['recall']}  (total {r['rereads_total']})")
        print(f"  status checks: {r['status_checks']}  (ops, not counted)")
        ratio = r["rereads_per_hook_brief"]
        print(f"  re-reads per hook briefing: "
              f"{ratio if ratio is not None else 'n/a'}")
        if r["untagged_briefs"]:
            print(f"  untagged brief lines (pre --auto): {r['untagged_briefs']}")
        if r["stale_hook_warning"]:
            print("  ⚠ sessions captured but no hook briefings logged — the "
                  "SessionStart hook may predate --auto; re-run `daimon hooks "
                  "install` (or update the plugin)")
    print("capture:")
    print(f"  serialized: {c['success']}  skipped: {c['skipped']}  "
          f"errors: {c['errors']}  via fallback backend: {c['fallback_serializes']}")
    if c["hosts"]:
        print("  spawns by host: " + ", ".join(
            f"{h}: {n}" for h, n in sorted(c["hosts"].items())))
    if c["success"]:
        print(f"  serialize seconds: max {c['max_serialize_seconds']}, "
              f"avg {c['total_serialize_seconds'] // c['success']}")
    print("store:")
    print(f"  checkpoints: {s['checkpoints']}  project buckets: {s['project_buckets']}")
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
        ret_table.add_row("hook briefings", str(r["hook_briefs"]))
        rr = r["rereads"]
        ret_table.add_row("deliberate re-reads",
                          f"brief {rr['brief']}, "
                          f"recall {rr['recall']} (total {r['rereads_total']})")
        ret_table.add_row("status checks (ops, not counted)",
                          str(r["status_checks"]))
        ratio = r["rereads_per_hook_brief"]
        ret_table.add_row("re-reads per hook briefing",
                          "n/a" if ratio is None else str(ratio))
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
    capture_table.add_row("via fallback backend", str(c["fallback_serializes"]))
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

    store_table = Table(title="store", title_justify="left",
                        show_header=True, header_style="bold")
    store_table.add_column("metric")
    store_table.add_column("value")
    store_table.add_row("checkpoints", str(s["checkpoints"]))
    store_table.add_row("project buckets", str(s["project_buckets"]))
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
