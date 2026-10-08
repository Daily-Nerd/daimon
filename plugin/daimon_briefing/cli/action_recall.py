"""`daimon action-recall`: the PreToolUse recall backend (moved out of cli/__init__.py, #1132 PR 5).

Shares the seen-state, slot widths and suggestion rendering of
`recall-inject`; reached through `cli.<name>`, which re-exports every name.
"""

import sys
import time
from datetime import datetime, timezone

import daimon_briefing.cli as _cli

from .. import briefing, config, recall, recall_telemetry, store
from ..terms import salient_terms


# ---- #1031: action-keyed recall, in front of a shell action ----------------

# The verbs whose commands are worth a query. Short and literal on purpose: a
# classifier here would be a second ranking axis in front of every shell
# action, and the measured claim is narrower than that — the command string of
# a CLUSTER or REPOSITORY-CHANGING action carries enough signal on its own.
# Everything else (`ls`, `cat`, a test run) is silence for free.
_ACTION_VERBS = frozenset({
    "kubectl", "helm", "argocd", "terraform", "gh",
})


# The same list, for verbs whose first token is too broad to take whole. `git`
# is most of what anyone types; only these two reach outside the working copy.
_ACTION_VERB_PAIRS = frozenset({"git push", "git merge"})


# What a heredoc opens with, in every spelling (`<<EOF`, `<<'EOF'`, `<<-EOF`).
# The body after it is the payload the action WRITES, not the action itself,
# and querying on it asks "have I seen this manifest before" instead of "have
# I learned anything about doing this".
_HEREDOC_MARK = "<<"


def _is_env_assignment(token: str) -> bool:
    """`FOO=bar` — a shell env prefix, not the verb."""
    name, sep, _ = token.partition("=")
    return bool(sep) and bool(name) and (name[0].isalpha() or name[0] == "_") \
        and all(ch.isalnum() or ch == "_" for ch in name)


def _action_verb(command: str):
    """The allowlisted verb this command leads with, or None.

    Leading `FOO=bar` assignments are stepped over; nothing else is. No shell
    parsing, no `sudo` unwrapping, no pipeline splitting: this decides whether
    to spend a query, and every bit of cleverness here is a way to spend one on
    a command nobody meant.
    """
    tokens = command.split()
    index = 0
    while index < len(tokens) and _is_env_assignment(tokens[index]):
        index += 1
    if index >= len(tokens):
        return None
    head = tokens[index]
    if head in _ACTION_VERBS:
        return head
    pair = " ".join(tokens[index:index + 2])
    return pair if pair in _ACTION_VERB_PAIRS else None


def _action_query_text(command: str) -> str:
    """The part of a command worth querying on: everything before the first
    heredoc marker."""
    cut = command.find(_HEREDOC_MARK)
    return command if cut < 0 else command[:cut]


def _cmd_action_recall(args) -> int:
    """Print 0-1 'you worked on this before' lines for the shell command on
    stdin, or nothing.

    rc 0 ALWAYS, same fail-open posture as `recall-inject` and for a sharper
    reason: this runs in front of an action the agent is about to take, and a
    recall that failed loudly would be a recall that blocked work.

    It is a SEPARATE process from the pre-action check on purpose. That hook is
    the only one that can deny, its stdout must be exactly one JSON object, and
    a fault here between its decision and its write would drop the deny in a
    way byte-identical to a clean allow. Separate processes make that
    structural rather than tested.
    """
    _cli._note_usage("action-recall")
    try:
        command = sys.stdin.read()
        # The allowlist runs BEFORE anything else, including the session check:
        # most shell actions are not on it, and those must cost one usage line
        # and no index read at all.
        if _action_verb(command) is None:
            # Counted apart from `action-recall`, which still counts every
            # fire: the flip condition is a fire rate per 100 shell actions,
            # and a denominator that only counted the queries it ran would
            # make that rate uninterpretable.
            _cli._note_usage("action-recall:skip-verb")
            return 0
        session = str(args.session or "")
        # The session id keys the cooldown. Without one, every action in a
        # session would repeat the same line, so silence is the honest answer
        # rather than a suggestion that arrives once per command.
        if not session:
            return 0
        query = _action_query_text(command)
        project = _cli._resolve_project(args.project)
        # Same exclusion as the prompt surface: whatever the SessionStart
        # briefing already carried is not news (#784 — that is ONE checkpoint,
        # chosen by the same route the injection hook reads).
        exclude = set()
        briefed = store.read_latest_body(
            project_dir=project,
            route=briefing.injection_read_route(project),
            admit=store.Admit.ANY)
        sid = (briefed or {}).get("session_id")
        if sid:
            exclude.add(str(sid))
        # Its OWN cooldown file, and the prompt surface's read-only. A claim
        # this session was already told at prompt time is not worth repeating
        # before the action; a claim this surface delivered is not worth
        # repeating either. Writing the prompt surface's file from here would
        # put two processes on different events into one read-modify-write.
        seen_file = _cli._seen_path(session, suffix="action")
        origin_counts, own_keys = (_cli._load_seen(seen_file) if seen_file
                                   else ({}, set()))
        prompt_file = _cli._seen_path(session)
        prompt_keys = (_cli._load_seen(prompt_file)[1] if prompt_file else set())
        matches = recall.suggest(query, project_dir=project,
                                 current_session=session,
                                 exclude_sessions=(
                                     exclude | _cli.cooled_origins(origin_counts)),
                                 limit=_cli._INJECT_FETCH)
        now = time.time()
        chosen, chosen_keys = _cli._choose_recall_rows(
            matches, own_keys | prompt_keys, now, budget=_cli._ACTION_BUDGET,
            usage_prefix="action-recall")
        terms = salient_terms(query)
        if not chosen:
            _cli._note_usage("action-recall:no-match")
            # #1073: same honest-empty placeholder as recall-inject — see
            # its call site for the reasoning behind `best_refused`.
            mcp_available = config.mcp_tool_available()
            numeric = [m["match_score"] for m in matches
                      if isinstance(m.get("match_score"), (int, float))]
            recall_telemetry.record(
                [],
                query_terms=terms,
                surface="action-recall",
                hint_form="tool" if mcp_available else "shell",
                injected_into=session or None,
                now=datetime.fromtimestamp(now, tz=timezone.utc),
                best_refused=max(numeric) if numeric else None,
            )
            return 0
        row = chosen[0]
        # One slot, and the only slot is the lead, so it renders at the lead
        # width (#1030). Width is a property of the slot; nothing about an
        # action buys extra room.
        rendered, truncated = _cli._fit_item_text(row["text"], _cli._LEAD_WIDTH)
        # #1036: same flag as recall-inject, read once. This surface's own
        # hook caps per host (see CAPS in the hook, #1046): `claude-code`
        # runs `record-only`, `codex` stays `unsupported`, so the value is
        # recorded for later comparison even where nothing prints.
        mcp_available = config.mcp_tool_available()
        # #1062: same sibling value as recall-inject's own call site.
        mcp_name = config.mcp_tool_name()
        recall_telemetry.record(
            [{**row, "rendered_chars": len(rendered), "truncated": truncated}],
            query_terms=terms,
            surface="action-recall",
            hint_form="tool" if mcp_available else "shell",
            # #1043: the live session running the shell action, not the
            # (possibly different) session that captured the matched belief.
            injected_into=session or None,
            now=datetime.fromtimestamp(now, tz=timezone.utc),
        )
        # The ladder's middle rung: the ledger row is written either way, so a
        # record-only soak measures exactly what delivery would have measured.
        if not args.record_only:
            print(_cli._suggest_line(row, terms, now,
                                own_slug=store.project_slug(project),
                                width=_cli._LEAD_WIDTH,
                                mcp_tool_available=mcp_available,
                                mcp_tool_name=mcp_name,
                                session=session or None))
        if seen_file:
            spent = dict(origin_counts)
            spent[str(row["session_id"])] = \
                spent.get(str(row["session_id"]), 0) + 1
            # Own keys only. The prompt surface's keys were read for
            # suppression and are not this file's to record.
            _cli._save_seen_atomic(seen_file, spent, own_keys | chosen_keys)
    except Exception:  # noqa: BLE001 — see docstring: fail-open, always rc 0
        pass
    return 0


def register(sub, fmt) -> None:
    """Register this family's parsers on the top-level subparsers."""
    # #1031: the PreToolUse backend, top-level beside `recall-inject` for the
    # same reason — it is a hook backend, and the command-catalogue guard
    # (#650) only partitions the TOP-LEVEL surface.
    p_action = sub.add_parser(
        "action-recall",
        help="action-keyed recall backend for the PreToolUse hook (#1031): "
             "shell command on stdin, prints 0-1 prior-work lines, rc 0 always",
    )
    p_action.add_argument("--project", default=None,
                          help="project dir for scoping (defaults to cwd detection)")
    p_action.add_argument("--session", default=None,
                          help="current session id (excluded from matches; keys the cooldown)")
    p_action.add_argument("--record-only", action="store_true",
                          help="write the delivery-ledger row and print nothing "
                               "(the soak rung of the per-host ladder)")
    p_action.set_defaults(func=_cmd_action_recall)
