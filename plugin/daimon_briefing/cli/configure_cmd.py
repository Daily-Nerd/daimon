"""`daimon configure`: detect the resolved LLM backend and fill gaps (moved out of cli/__init__.py, #1132 PR 5).

The interactive wizard asks through `_cli._prompt`, the seam tests patch.
Every name is re-exported from `cli`.
"""

import argparse
import getpass
import json
import sys
import time
from pathlib import Path

import daimon_briefing.cli as _cli

from .. import config, configure, llm, render, teamsync


# ---- team: sidecar private-repo sync (#113) ----


# ---- configure: detect/report the resolved backend + fill gaps in ~/.daimon/env ----


def _run_backend_test() -> int:
    """--test (#56): prove the RESOLVED backend works, interactively, at setup
    time — the alternative is a real serialize failing minutes later inside
    a detached hook child. One tiny prompt through the same llm.chat path
    serialization uses; failure prints the cause and where stderr landed."""
    start = time.monotonic()
    try:
        # working() (#182): the roundtrip is ~15s of otherwise-dead
        # terminal at the exact moment a new user decides whether the
        # tool works — spinner on rich/TTY, one plain line elsewhere.
        with render.working("testing backend — one tiny prompt through "
                            "the resolved backend"):
            reply = llm.chat(
                [{"role": "user", "content":
                  'Reply with exactly this JSON and nothing else: {"ok": true}'}],
                retries=1)
    except llm.ChatError as exc:
        print(f"backend test: FAILED — {exc}", file=sys.stderr)
        return 1
    # Same extraction path serialization uses (#59): a transport that
    # answers but cannot return extractable JSON — agent-style CLIs often
    # can't — must fail HERE, not on the first real serialize.
    try:
        llm.extract_json(reply)
    except json.JSONDecodeError:
        print("backend test: FAILED — transport works, but the backend did "
              "not return extractable JSON; serialization will fail. "
              "Agent-style CLIs often can't do this — use an "
              "OpenAI-compatible endpoint or a raw-completion CLI.",
              file=sys.stderr)
        return 1
    elapsed = time.monotonic() - start
    render.render_configure_lines([f"backend test: ok ({elapsed:.1f}s round trip)"])
    return 0


def _configure_write_flags(args) -> list:
    """The value flags of a non-interactive configure write, as (name, value)
    pairs — one list so the #749 --test guard, the no---backend guard, and the
    cross-backend warning can never disagree about what counts as a write flag."""
    return [(name, value) for name, value in (
        ("--api-key", args.api_key),
        ("--model", args.model),
        ("--base-url", args.base_url),
        ("--command", args.command),
        ("--output", args.output),
        ("--input", args.input),
    ) if value]


def _configure_wizard_flags(args) -> list:
    """Flags consumed ONLY by the --init wizard, as (name, value) pairs — same
    shared-list pattern as _configure_write_flags, so the --test guard and the
    ignored-without---init warning can never drift (#749)."""
    return [(name, value) for name, value in (
        ("--timeout", getattr(args, "timeout", None)),
        ("--author", getattr(args, "author", None)),
        ("--team-remote", getattr(args, "team_remote", None)),
    ) if value]


def _configure_flag_updates(args) -> dict:
    """Backend flags -> env updates (the non-interactive write path)."""
    updates = {"DAIMON_LLM_BACKEND": args.backend}
    litellm_flags = ("--api-key", "--model", "--base-url")
    if args.backend == "litellm":
        if args.api_key:
            updates["DAIMON_LLM_API_KEY"] = args.api_key
        if args.model:
            updates["DAIMON_LLM_MODEL"] = args.model
        if args.base_url:
            updates["DAIMON_LLM_BASE_URL"] = args.base_url
        applies: tuple[str, ...] = litellm_flags
    elif args.backend == "command":
        if args.command:
            updates["DAIMON_LLM_COMMAND"] = args.command
        if args.output:
            updates["DAIMON_LLM_COMMAND_OUTPUT"] = args.output
        if args.input:
            updates["DAIMON_LLM_COMMAND_INPUT"] = args.input
        applies = ("--command", "--output", "--input")
    else:
        # claude-cli: just pin the backend, no credentials needed.
        applies = ()
    # #749(c): a flag belonging to another backend was dropped without a word,
    # so `--backend command --model x` looked like it configured a model.
    for name, _ in _configure_write_flags(args):
        if name not in applies:
            print(f"warning: {name} ignored for backend {args.backend}",
                  file=sys.stderr)
    return updates


def _ask_backend_updates() -> dict:
    """Interactive backend Q&A -> env updates. Question order and wording are
    a stable contract with the tests' answer iterators — extend at the END."""
    backend = _cli._prompt("backend [litellm/command/claude-cli]: ").strip() or "litellm"
    updates = {"DAIMON_LLM_BACKEND": backend}
    if backend == "litellm":
        base_url = _cli._prompt("base_url (blank for default): ").strip()
        if base_url:
            updates["DAIMON_LLM_BASE_URL"] = base_url
        # getpass, not _prompt (#29): the secret must not echo to the
        # terminal or land in scrollback.
        api_key = getpass.getpass("api_key: ").strip()
        if api_key:
            updates["DAIMON_LLM_API_KEY"] = api_key
        model = _cli._prompt("model: ").strip()
        if model:
            updates["DAIMON_LLM_MODEL"] = model
    elif backend == "command":
        command = _cli._prompt("command: ").strip()
        if command:
            updates["DAIMON_LLM_COMMAND"] = command
        output = _cli._prompt("output spec [text/json:<key>] (blank=text): ").strip()
        if output:
            updates["DAIMON_LLM_COMMAND_OUTPUT"] = output
        input_spec = _cli._prompt(
            "input spec [stdin/arg/file:<flag>] (blank=stdin): "
        ).strip()
        if input_spec:
            updates["DAIMON_LLM_COMMAND_INPUT"] = input_spec
    # claude-cli: nothing more to ask.
    return updates


def _configure_wizard(args) -> int:
    """#368: `daimon configure --init` — the guided path the two
    highest-friction onboarding moments never had. Backend -> timeout ->
    probe offer -> optional team walk -> the same `daimon status` summary
    the docs reference. Every prompt has a flag escape hatch so scripts/CI
    can run the whole thing non-interactively."""
    interactive = sys.stdin.isatty() and not args.backend
    updates: dict = {}
    if args.backend:
        updates = _configure_flag_updates(args)
    elif interactive:
        updates = _ask_backend_updates()
        timeout = _cli._prompt(
            "serialize timeout seconds (blank = default "
            f"{config.timeout_seconds()}): ").strip()
        if timeout.isdigit():
            updates["DAIMON_TIMEOUT"] = timeout
    else:
        render.render_configure_lines(
            ["--init needs a terminal or --backend plus value flags "
             "(and optionally --timeout/--author/--team-remote)."])
        return 0
    if getattr(args, "timeout", None):
        updates["DAIMON_TIMEOUT"] = str(args.timeout)
    # Scar fence: DAIMON_TIMEOUT is a TOTAL budget shared across retries, and
    # real serialize/merge calls run 80-250s each — a sub-420 budget cannot
    # fit even one slow call. The wizard must not help a user write one.
    if updates.get("DAIMON_TIMEOUT") and int(updates["DAIMON_TIMEOUT"]) < 420:
        render.render_configure_lines(
            [f"timeout {updates['DAIMON_TIMEOUT']}s is below the 420s floor — "
             "ignored (real serialize/merge calls run 80-250s each; the "
             "budget must fit at least one slow call plus a retry)"])
        del updates["DAIMON_TIMEOUT"]
    if updates:
        path = configure.write_env(updates)
        render.render_configure_lines([f"wrote {path}"])

    # Probe right after writing (#368 item 2) — the alternative is the first
    # real serialize failing inside a detached hook child. Default YES on the
    # interactive path: a wizard that skips its own verification teaches the
    # user nothing about whether setup worked.
    rc = 0
    run_probe = getattr(args, "test", False)
    if interactive and not run_probe:
        run_probe = _cli._prompt("run backend test now? [Y/n]: ").strip().lower() \
            not in ("n", "no")
    if run_probe:
        rc = _run_backend_test()

    # Team walk (#368 item 3): env vars + `team init` in one place, instead
    # of spread across the reference page and a separate command.
    author = getattr(args, "author", None)
    remote = getattr(args, "team_remote", None)
    if interactive and not (author or remote):
        if _cli._prompt("set up team memory? [y/N]: ").strip().lower() in ("y", "yes"):
            author = _cli._prompt("author name (namespaces your checkpoints): ").strip()
            remote = _cli._prompt("team remote URL (git): ").strip()
    if author or remote:
        team_updates = {"DAIMON_TEAM": "1"}
        if author:
            team_updates["DAIMON_AUTHOR"] = author
        configure.write_env(team_updates)
        if remote:
            try:
                dest = teamsync.init(remote, project_dir=Path.cwd())
                render.render_team_init([
                    f"initialized team sidecar: {dest}",
                    "checkpoints now sync there — `daimon team sync` runs "
                    "opportunistically at session start",
                ])
            except teamsync.TeamError as exc:
                print(f"error: {exc}", file=sys.stderr)
                rc = rc or 1

    # End on the exact status view the docs reference (#368 item 4), so the
    # user leaves the wizard seeing the same health verdict every other
    # surface will show them.
    _cli._cmd_status(argparse.Namespace(project=None, json=False, suppressed=False))
    return rc


def _cmd_configure(args) -> int:
    """Detect + report the resolved LLM backend; fill gaps in ~/.daimon/env.

    Always prints a doctor view. With backend flags, writes non-interactively.
    With no flags it is SAFE everywhere: it only prompts on a TTY when daimon is
    not ready, and otherwise just prints guidance — it never blocks.
    `--init` (#368) runs the full guided wizard instead.
    """
    if getattr(args, "init", False):
        return _configure_wizard(args)
    wizard_flags = _configure_wizard_flags(args)
    if getattr(args, "test", False):
        # #749(b): --test used to short-circuit BEFORE the write branch —
        # `--backend litellm --model x --test` tested the OLD config and
        # silently discarded the write flags. Wizard-only flags (--timeout/
        # --author/--team-remote) would drop the same way. Refuse both.
        other_flags = ([("--backend", args.backend)] if args.backend else []) \
            + _configure_write_flags(args) + wizard_flags
        if other_flags:
            names = ", ".join(name for name, _ in other_flags)
            print(f"error: --test cannot be combined with other flags "
                  f"({names}) — apply them first (write flags via --backend, "
                  "wizard flags via --init), then run `daimon configure "
                  "--test` against the new config.",
                  file=sys.stderr)
            return 2
        return _run_backend_test()
    if _configure_write_flags(args) and not args.backend:
        # #749: the third silent-drop door — a value flag without --backend
        # fell through every branch below untouched.
        names = ", ".join(name for name, _ in _configure_write_flags(args))
        print(f"error: value flags ({names}) require --backend "
              "{litellm,command,claude-cli}", file=sys.stderr)
        return 2
    for name, _ in wizard_flags:
        # Consumed only by the --init wizard; anywhere else it would drop
        # silently (#749).
        print(f"warning: {name} ignored without --init", file=sys.stderr)

    st = configure.status()
    render.render_configure(st)

    if args.backend:
        path = configure.write_env(_configure_flag_updates(args))
        render.render_configure_lines([f"wrote {path}"])
        st = configure.status()
        render.render_configure(st)  # reprint the new resolved state
        if not st["ready"]:
            # #749(a): a write that lands not-ready must fail loud — scripts
            # read the rc, not the panel. The no-flag doctor path above keeps
            # returning 0; only the WRITE claims an outcome.
            print("not ready after write — see the doctor line above",
                  file=sys.stderr)
            return 1
        return 0

    if st["ready"]:
        return 0  # nothing to do
    if not sys.stdin.isatty():
        # Non-interactive and not ready: guide, never block.
        render.render_configure_lines(["not ready — re-run with --backend {litellm,command,claude-cli} "
                                       "and the matching value flags, or run interactively in a terminal."])
        return 0

    # Interactive: prompt for a backend and its values.
    path = configure.write_env(_ask_backend_updates())
    render.render_configure_lines([f"wrote {path}"])
    render.render_configure(configure.status())
    return 0


def register(sub, fmt) -> None:
    """Register this family's parsers on the top-level subparsers."""
    p_cfg = sub.add_parser(
        "configure",
        help="detect the resolved LLM backend and fill gaps in ~/.daimon/env",
    )
    p_cfg.add_argument(
        "--backend", choices=("litellm", "command", "claude-cli"),
        help="non-interactive: pin this backend and write the value flags below",
    )
    p_cfg.add_argument("--api-key", help="litellm: DAIMON_LLM_API_KEY")
    p_cfg.add_argument("--model", help="litellm: DAIMON_LLM_MODEL")
    p_cfg.add_argument("--base-url", help="litellm: DAIMON_LLM_BASE_URL")
    p_cfg.add_argument("--command", help="command: DAIMON_LLM_COMMAND")
    p_cfg.add_argument("--output", help="command: DAIMON_LLM_COMMAND_OUTPUT (text|json:<key>)")
    p_cfg.add_argument(
        "--input",
        help="command: DAIMON_LLM_COMMAND_INPUT (stdin|arg|file:<flag>) — how the "
             "prompt reaches a CLI that doesn't read stdin, e.g. --input "
             "'file:--prompt-file' for the Devin CLI (#58)",
    )
    p_cfg.add_argument(
        "--init", action="store_true",
        help="guided setup wizard (#368): backend, timeout, immediate --test "
             "offer, optional team walk, ends with the status summary; every "
             "prompt has a flag escape hatch for scripts",
    )
    p_cfg.add_argument(
        "--timeout", type=int,
        help="--init: DAIMON_TIMEOUT (total serialize budget, floor 420s)",
    )
    p_cfg.add_argument("--author", help="--init: DAIMON_AUTHOR for team memory")
    p_cfg.add_argument(
        "--team-remote",
        help="--init: git remote URL — sets DAIMON_TEAM=1 and runs "
             "`daimon team init <url>`",
    )
    p_cfg.add_argument(
        "--test", action="store_true",
        help="send one tiny prompt through the resolved backend and report "
             "pass/fail — run this right after configuring (#56)",
    )
    p_cfg.set_defaults(func=_cmd_configure)
