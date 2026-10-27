"""The CLI side of the read-scope owner (#1223 PR 3).

On a tenant-scoped home no CLI verb lets the caller leave the own bucket:
`--project` naming another directory, `audit privacy --all`, the `decide`
footer, `--global-fallback` / DAIMON_BRIEF_GLOBAL_FALLBACK and the global
pointer all go through ONE decision, `view.cli_read` (and the store's single
display reader for the pointer). Off tenant scope the CLI is unchanged.

An identity-less read (no project at all) reads nothing on every display
route; only the persist path keeps the global pointer, because there it IS the
stream's own previous checkpoint (#126)."""
import ast
import json
import argparse
from pathlib import Path

import pytest

from daimon_briefing import cli, config, pending, receipts, requests, store, view
from daimon_briefing.surfaces import Writer

MINE = "/repo/mine"
OTHER = "/repo/other-tenant"
THIRD = "/repo/third-asker"
TERM = "walrusprobe"
OTHER_SLUG = store.project_slug(OTHER)


def _checkpoint(session, text):
    return {
        "session_id": session, "created": "2026-08-01T00:00:00Z",
        "working_context": {
            "active_topic": {"text": text, "trust": "inferred"},
            "recent_decisions": [{"text": text, "trust": "inferred"}]},
        "epistemic_snapshot": {}}


@pytest.fixture
def world(tmp_checkpoint_dir, monkeypatch):
    """A foreign bucket with content that matches TERM and a waiting request,
    the newest checkpoint on the machine (so it is the global pointer), and a
    server running in MINE, which has no bucket of its own."""
    store.write_checkpoint("S-third", _checkpoint("S-third", "asker notes"),
                           project_dir=THIRD, writer=Writer.HUMAN)
    store.write_checkpoint("S-other",
                           _checkpoint("S-other", f"the {TERM} gateway"),
                           project_dir=OTHER, writer=Writer.HUMAN)
    requests.open_request(to=OTHER_SLUG, ask="foreign ask", why="because",
                          channel="cli-agent", project_dir=THIRD)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", MINE)


@pytest.fixture
def tenant(monkeypatch):
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")


# ---- --project naming another bucket -----------------------------------


@pytest.mark.parametrize("argv", [
    ["recall", TERM],
    ["brief"],
    ["why", "o-3f8a2c"],
    ["status"],
    ["status", "--json"],
    ["decide"],
    ["ruling", "list"],
    ["request", "inbox"],
])
def test_tenant_scope_refuses_a_project_that_is_another_bucket(
        world, tenant, capsys, argv):
    rc = cli.main([*argv, "--project", OTHER])
    cap = capsys.readouterr()
    assert rc == 2
    assert config.TENANT_SCOPE_REFUSAL in cap.err
    assert TERM not in cap.out + cap.err.replace(config.TENANT_SCOPE_REFUSAL, "")
    assert OTHER_SLUG not in cap.out


def test_tenant_scope_refuses_a_project_write_into_another_bucket(
        world, tenant, capsys):
    rc = cli.main(["ruling", "propose", "--subject", "theirs",
                   "--verdict", "an injected rule", "--scope", "theirs",
                   "--evidence", "issue:1", "--by", "agent",
                   "--project", OTHER])
    assert rc == 2
    assert config.TENANT_SCOPE_REFUSAL in capsys.readouterr().err
    assert not (Path(config.checkpoint_dir()) / OTHER_SLUG
                / "refutations.jsonl").exists()


def test_tenant_scope_answers_a_project_that_is_the_own_bucket(
        world, tenant, capsys):
    store.write_checkpoint("S-mine", _checkpoint("S-mine", f"own {TERM} notes"),
                           project_dir=MINE, writer=Writer.HUMAN)
    assert cli.main(["recall", TERM, "--project", MINE]) == 0
    out = capsys.readouterr().out
    assert "own walrusprobe notes" in out
    assert "gateway" not in out


def test_without_tenant_scope_project_still_names_any_directory(world, capsys):
    assert cli.main(["recall", TERM, "--project", OTHER]) == 0
    assert "gateway" in capsys.readouterr().out


# ---- audit privacy --all ------------------------------------------------


def test_tenant_scope_refuses_audit_privacy_all(world, tenant, capsys):
    rc = cli.main(["audit", "privacy", "--all"])
    cap = capsys.readouterr()
    assert rc == 2
    assert config.TENANT_SCOPE_REFUSAL in cap.err
    assert OTHER_SLUG not in cap.out


def test_without_tenant_scope_audit_privacy_all_names_every_bucket(
        world, capsys):
    cli.main(["audit", "privacy", "--all"])
    assert OTHER_SLUG in capsys.readouterr().out


def test_tenant_scope_leaves_audit_privacy_of_the_own_project_working(
        world, tenant, capsys):
    assert cli.main(["audit", "privacy"]) in (0, 1, 3)
    assert config.TENANT_SCOPE_REFUSAL not in capsys.readouterr().err


# ---- the decide footer --------------------------------------------------


def test_without_tenant_scope_the_decide_footer_counts_other_buckets(
        world, capsys):
    cli.main(["decide"])
    assert "more waiting in other projects" in capsys.readouterr().out


def test_tenant_scope_decide_names_no_other_bucket(world, tenant, capsys):
    cli.main(["decide"])
    out = capsys.readouterr().out
    assert "more waiting in other projects" not in out
    assert OTHER_SLUG not in out


def test_tenant_scope_foreign_counts_are_empty_at_the_source(world, tenant):
    assert pending.foreign_counts(project_dir=MINE) == {}


# ---- the global pointer: --global-fallback and the env var --------------


def test_without_tenant_scope_a_bucketless_brief_names_the_elsewhere_slug(
        world, capsys):
    assert cli.main(["brief"]) == 0
    assert "Most recent activity elsewhere" in capsys.readouterr().out


def test_tenant_scope_refuses_the_global_fallback_flag(world, tenant, capsys):
    rc = cli.main(["brief", "--global-fallback"])
    cap = capsys.readouterr()
    assert rc == 2
    assert config.TENANT_SCOPE_REFUSAL in cap.err
    assert "gateway" not in cap.out


def test_tenant_scope_ignores_the_global_fallback_env(world, tenant, capsys,
                                                      monkeypatch):
    monkeypatch.setenv("DAIMON_BRIEF_GLOBAL_FALLBACK", "full")
    assert config.brief_global_fallback() is False
    cli.main(["brief"])
    cap = capsys.readouterr()
    assert "gateway" not in cap.out
    assert OTHER_SLUG not in cap.out
    assert "elsewhere" not in cap.out


def test_tenant_scope_status_shows_no_global_pointer(world, tenant, capsys):
    assert cli.main(["status", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["global"]["exists"] is False


def test_tenant_scope_display_read_never_takes_the_global_pointer(world, tenant):
    got = store.read_latest_body(
        project_dir=MINE, route=store.Route.OWN_ELSE_GLOBAL,
        admit=store.Admit.ANY)
    assert got is None


# ---- identity-less reads ------------------------------------------------


@pytest.mark.parametrize("route", list(store.Route))
@pytest.mark.parametrize("admit", list(store.Admit))
def test_an_identity_less_display_read_reads_nothing(world, route, admit):
    assert store.read_latest_body(project_dir=None, route=route,
                                  admit=admit) is None
    assert store.read_latest_result(project_dir=None, route=route,
                                    admit=admit).checkpoint is None


def test_the_receipts_line_of_an_identity_less_process_names_no_session(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_RECEIPTS", "1")
    monkeypatch.delenv("DAIMON_PROJECT_DIR", raising=False)
    store.write_checkpoint("S-global", {"session_id": "S-global"},
                           writer=Writer.HUMAN)
    line = receipts.status_line()
    assert "S-global" not in line
    assert "no checkpoint to sign yet" in line


def test_the_persist_read_of_an_unknown_project_keeps_the_global_pointer(
        tmp_checkpoint_dir):
    store.write_checkpoint("S-prev", {"session_id": "S-prev"},
                           writer=Writer.HUMAN)
    got = store.read_own_stream_latest(None)
    assert got is not None and got["session_id"] == "S-prev"


def test_the_persist_read_of_a_known_project_never_inherits_a_foreign_one(
        world):
    assert store.read_own_stream_latest(MINE) is None


# ---- the decision itself ------------------------------------------------


def test_cli_read_refuses_only_what_leaves_the_own_bucket(world, tenant):
    own = view.cli_read(project=MINE)
    assert own.project == config.resolve_read_project(MINE)
    for kwargs in ({"project": OTHER}, {"all_projects": True},
                   {"slug": OTHER_SLUG}, {"global_fallback": True}):
        with pytest.raises(view.ScopeRefused) as exc:
            view.cli_read(**kwargs)
        assert str(exc.value) == config.TENANT_SCOPE_REFUSAL


def test_cli_read_grants_everything_off_tenant_scope(world):
    assert view.cli_read(project=OTHER).project == config.resolve_read_project(
        OTHER)
    assert isinstance(view.cli_read(all_projects=True).scope, view.All)
    assert view.cli_read(global_fallback=True)


# ---- structural walks: a new CLI read that skips the owner fails here ----

_CLI_DIR = Path(cli.__file__).parent

# The reads of `args.project` that do not go through `_resolve_project`, each
# with the reason it is not a bucket read.
_PROJECT_READS_OUTSIDE_THE_OWNER = {
    # `bucket migrate` refuses an explicit --project under tenant scope itself
    # and reads the raw value only to report on a legacy bucket.
    ("projects.py", "_cmd_bucket_migrate"),
    # the --slug/--project conflict guard and the routing of a --slug, which
    # `_refuses_caller_scope` gates.
    ("brief.py", "_cmd_brief"),
    ("search.py", "_cmd_recall"),
    ("search.py", "_cmd_why"),
    # skill install: a PROJECT SCOPE name for where to install, not a read
    ("skill.py", "_cmd_skill_install"),
    ("skill.py", "_cmd_skill_uninstall"),
    # the status verbs pass the raw argument to `_status_world`, which
    # resolves it through `_resolve_project`.
    ("status.py", "_cmd_status"),
    # the legacy-bucket hint for a path the verb already resolved
    ("ruling.py", "_cmd_ruling_list"),
    # heal echoes the value in an error line after `_resolve_project` took it
    ("serialize.py", "_heal_loop"),
    # import asks only whether the flag was TYPED (`is not None`), after
    # `_resolve_project` resolved it: a transcript that records no project
    # (Windsurf, Kimi) is accepted on the user's word, never on an inferred
    # default (#1236)
    ("import_cmd.py", "_cmd_import"),
    # `request open --to` looks up a NAME in the tenant-filtered list
    ("request.py", "_cmd_request_open"),
}


def _project_attr_reads():
    """(file, function) for every `args.project` read in cli/*.py that is not
    directly an argument of `_resolve_project(...)`."""
    found = set()
    for path in sorted(_CLI_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            guarded = set()
            for call in ast.walk(fn):
                if (isinstance(call, ast.Call)
                        and getattr(call.func, "attr", getattr(call.func, "id", ""))
                        == "_resolve_project"):
                    for arg in call.args:
                        guarded.update(id(n) for n in ast.walk(arg))
            for node in ast.walk(fn):
                if (isinstance(node, ast.Attribute) and node.attr == "project"
                        and isinstance(node.value, ast.Name)
                        and node.value.id == "args" and id(node) not in guarded):
                    found.add((path.name, fn.name))
    return found


def test_every_args_project_read_goes_through_the_owner_or_is_classified():
    found = _project_attr_reads()
    unclassified = found - _PROJECT_READS_OUTSIDE_THE_OWNER
    assert not unclassified, (
        "these verbs read `args.project` without `_resolve_project`, the one "
        f"place the tenant decision is made: {sorted(unclassified)}")
    stale = _PROJECT_READS_OUTSIDE_THE_OWNER - found
    assert not stale, f"classified reads that no longer exist: {sorted(stale)}"


def test_resolve_project_asks_the_owner():
    src = (_CLI_DIR / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_resolve_project")
    names = {getattr(c.func, "attr", getattr(c.func, "id", ""))
             for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert "cli_read" in names


def _leaves_with(option_strings):
    def walk(parser, prefix=""):
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    full = f"{prefix}{name}"
                    if any(o in a.option_strings for a in sub._actions
                           for o in option_strings):
                        yield full
                    yield from walk(sub, full + " ")
    return sorted(set(walk(cli.build_parser())))


def test_the_verbs_with_a_scope_widening_flag_are_the_classified_ones():
    """Every leaf that carries a flag naming more than the own bucket is
    listed. A new one fails until it is classified and routed through
    `_refuses_caller_scope` / `view.cli_read`."""
    assert _leaves_with(["--all-projects"]) == ["decide", "recall"]
    assert _leaves_with(["--global-fallback"]) == ["brief"]
    # `--all` is a scope on these two; on heal/hooks/skill it is a host or
    # machine-repair switch, not a content read.
    assert [v for v in _leaves_with(["--all"]) if v.startswith("audit")] == [
        "audit privacy", "audit quotes", "audit-quotes"]


@pytest.mark.parametrize("argv", [
    ["recall", TERM, "--all-projects"],
    ["decide", "--all-projects"],
    ["brief", "--global-fallback"],
    ["audit", "privacy", "--all"],
])
def test_each_widening_flag_is_refused_on_a_tenant_home(world, tenant, capsys,
                                                        argv):
    assert cli.main(argv) == 2
    assert config.TENANT_SCOPE_REFUSAL in capsys.readouterr().err
