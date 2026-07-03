"""Presentation layer for human-facing CLI output.

Single capability gate (`supports_rich`) decides plain vs rich. `rich` is an
OPTIONAL dependency (`daimon[pretty]`) imported lazily inside the rich branch,
so this module is import-safe with rich absent and the hook/serialize path —
which is non-TTY — always renders plain.
"""

import os
import sys

from . import briefing, config, serializer

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


_TRUST_STYLE = {"verbatim": "bold green", "inferred": "yellow"}

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
    prompt: the schema changed under it, so sections may render partially. Legacy
    checkpoints (no format_version) render silently — nothing to compare (#93)."""
    fv = (checkpoint or {}).get("format_version")
    if fv and fv != serializer.PROMPT_VERSION:
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
    if not supports_rich():
        print(briefing.render_plain(b))
    else:
        _rich_brief(b)
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


def _rich_brief(b: dict) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    console = Console()
    console.print(Text("While you were away — here's where we left off.", style="bold"))
    for key, title, style in _SECTIONS:
        items = b.get(key) or []
        if not items:
            continue
        body = Text()
        for i in items:
            trust = "verbatim" if i.get("trust") == "verbatim" else "inferred"
            body.append(f"• {i.get('text', '').strip()}\n", style=_TRUST_STYLE[trust])
            quote = i.get("quote", "").strip()
            if quote:
                body.append(f'    "{quote}"\n', style="dim italic")
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
                trust = "verbatim" if i.get("trust") == "verbatim" else "inferred"
                body.append(f"• {i.get('text', '').strip()}\n", style=_TRUST_STYLE[trust])
            note = briefing._overflow_note(b.get("decisions_overflow", 0))
            if note:
                body.append(f"{note}\n", style="dim")
        console.print(Panel(body, title=f"Teammate — {author}",
                            border_style="white", title_align="left"))


def _explain(st: dict) -> str:
    """One-line human explanation of a configure.status() snapshot."""
    rb = st["resolved_backend"]
    if rb in ("command", "claude-cli"):
        if st["ready"]:
            src = st["command_source"]
            if src == "claude-cli":
                return f"backend: {rb} (claude CLI, zero-config)"
            return f"backend: {rb} ({st['command']})"
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


def render_heal(plan: dict, *, dry_run: bool) -> None:
    """Plain explanation of a heal decision. No rich — heal output is procedural."""
    t = plan["target"]
    if t:
        if dry_run:
            print(f"would heal {t['sid']} (failed {t['age_str']} ago, transcript {t['transcript']})")
        else:
            print(f"healing {t['sid']} (failed {t['age_str']} ago)…")
    elif plan["note"]:
        print(plan["note"])
    for s in plan["skipped"]:
        print(f"  - {s['sid']}  ({s['age_str']} ago) — {s['reason']}")


def _outstanding_lines(outstanding) -> list:
    """Human lines for lost sessions; empty list when nothing is outstanding."""
    lines = []
    for f in outstanding:
        age = f["age_str"]
        if f["kind"] == "hung":
            lines.append(
                f"  - {f['sid']}  spawned {age} ago, no result "
                f"(hung/killed; transcript unavailable)"
            )
        elif f["class"] == "retry-exhausted":
            lines.append(f"  - {f['sid']}  error {age} ago — retry attempted, still failing")
        elif f["class"] == "unrecoverable":
            lines.append(f"  - {f['sid']}  error {age} ago — transcript unavailable, cannot auto-heal")
        else:
            lines.append(f"  - {f['sid']}  error {age} ago — run `daimon heal`")
    return lines


def _plain_status(data: dict) -> None:
    ident = data.get("identity")
    if ident:
        print(f"identity: {ident['cwd']}  →  git-root {ident['git_root']}  →  bucket {ident['slug']}")
    health = data.get("health")
    if health:
        print(health["verdict"])
        for w in health["warnings"][1:]:
            print(f"  ⚠ {w}")
    if data.get("team"):
        print(data["team"])  # one objective line; absent when team unused (#113)
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
    ident = data.get("identity")
    if ident:
        console.print(f"identity: {ident['cwd']}  →  git-root {ident['git_root']}  →  bucket {ident['slug']}")
    health = data.get("health")
    if health:
        style = "green" if health["ok"] else "red"
        console.print(f"[{style}]{health['verdict']}[/{style}]")
        for w in health["warnings"][1:]:
            console.print(f"  ⚠ {w}")
    if data.get("team"):
        console.print(data["team"])  # one objective line; absent when team unused (#113)
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
