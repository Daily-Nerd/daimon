"""#1370: a tenant-scoped host can declare that the folders above a project
are its own (DAIMON_TENANT_LAYERS), so their rulings apply.

One owner answers "may layers be read in this process":
`config.layers_readable`. The briefing, `ruling list --inherited` and
`ruling checks` all follow from it (the MCP tools and the in-process API read
through the same `config.layer_scopes`). Every other tenant refusal is pinned
under the declaration in test_cli_scope_owner.py, test_read_scope.py and
test_mcp_model_scope.py (their `tenant` fixtures run with and without it).
"""
import ast
import json
import subprocess
from pathlib import Path

import pytest

import daimon_briefing
from daimon_briefing import briefing, cli, config, mcp_server, refutations

MATCH = "gh pr create"
BODY = "#!/bin/sh\nexit 0\n"
PACKAGE = Path(daimon_briefing.__file__).parent


@pytest.fixture
def home_work_repo(tmp_path, monkeypatch, tmp_checkpoint_dir):
    tmp_home = tmp_path / "home"
    work = tmp_home / "work"
    repo = work / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.setenv("HOME", str(tmp_home))
    return work, repo


@pytest.fixture
def rules(home_work_repo, monkeypatch):
    work, repo = home_work_repo
    # Under tenant scope the own project is the host's choice, never a flag's.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(repo))
    layer_id = refutations.assert_ruling(
        subject="layer enforced", verdict="layer enforced verdict",
        scope="publishing", evidence=["issue:1370"], channel="cli-tty",
        ratified=True,
        check={"match": MATCH, "body": BODY, "intent": "enforce"},
        project_dir=str(work))
    refutations.assert_ruling(
        subject="own rule", verdict="own rule verdict", scope="tests",
        evidence=["issue:1370"], channel="cli-tty", ratified=True,
        project_dir=str(repo))
    return work, repo, layer_id


@pytest.fixture
def declared(monkeypatch):
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    monkeypatch.setenv("DAIMON_TENANT_LAYERS", "1")


def test_declared_briefing_lists_own_and_inherited(rules, declared):
    work, repo, _ = rules
    assert config.layer_scopes(str(repo)) == [str(work)]
    joined = "\n".join(briefing.ruling_lines(str(repo)))
    assert "own rule verdict" in joined
    assert "layer enforced verdict" in joined


def test_declared_ruling_list_inherited_and_checks(rules, declared, capsys):
    work, repo, layer_id = rules
    assert cli.main(["ruling", "list", "--project", str(repo), "--json",
                     "--inherited"]) == 0
    subjects = {r["subject"] for r in json.loads(capsys.readouterr().out)}
    assert subjects == {"own rule", "layer enforced"}
    assert cli.main(["ruling", "checks", "--project", str(repo)]) == 0
    assert layer_id in capsys.readouterr().out


def test_declared_mcp_brief_returns_inherited(rules, declared):
    got = mcp_server._handle_tools_call({"name": "daimon_brief",
                                         "arguments": {}})
    assert got["isError"] is False
    assert "layer enforced verdict" in got["content"][0]["text"]


def test_tenant_scope_without_the_declaration_is_unchanged(rules, monkeypatch,
                                                           capsys):
    work, repo, layer_id = rules
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    assert config.layer_scopes(str(repo)) == []
    joined = "\n".join(briefing.ruling_lines(str(repo)))
    assert "own rule verdict" in joined
    assert "layer enforced verdict" not in joined
    assert cli.main(["ruling", "list", "--project", str(repo),
                     "--inherited"]) == 2
    err = capsys.readouterr().err
    assert config.TENANT_SCOPE_REFUSAL in err
    assert "DAIMON_TENANT_LAYERS" in err
    assert cli.main(["ruling", "checks", "--project", str(repo)]) == 0
    assert layer_id not in capsys.readouterr().out


def test_the_declaration_alone_changes_nothing(rules, monkeypatch):
    work, repo, _ = rules
    assert config.layers_readable() is True
    assert config.layer_scopes(str(repo)) == [str(work)]
    monkeypatch.setenv("DAIMON_TENANT_LAYERS", "1")
    assert config.layers_readable() is True
    assert config.layer_scopes(str(repo)) == [str(work)]


def test_the_declaration_is_read_like_every_flag(monkeypatch, tmp_path):
    env = tmp_path / "env"
    env.write_text("DAIMON_TENANT_SCOPED=1\nDAIMON_TENANT_LAYERS=1\n",
                   encoding="utf-8")
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env))
    assert config.layers_readable() is True
    env.write_text("DAIMON_TENANT_SCOPED=1\n", encoding="utf-8")
    assert config.layers_readable() is False


# ---- census: one owner of "may layers be read under tenant scope" ----------


def _names(fn):
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, ast.Attribute):
            yield node.attr
        elif isinstance(node, ast.arg):
            yield node.arg


def test_no_module_outside_config_decides_layers_under_tenant_scope():
    """AST census (never a text search): outside `config.py`, no function
    both calls `tenant_scoped()` and mentions layers or `inherited`. A new
    copy of the rule asks `config.layers_readable()` instead."""
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel == "config.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = [n.lower() for n in _names(fn)]
            if "tenant_scoped" in names and any(
                    "layer" in n or "inherited" in n for n in names):
                offenders.append(f"{rel}:{fn.name}")
    assert offenders == []


def test_the_declaration_is_named_as_a_flag_once_in_config():
    tree = ast.parse((PACKAGE / "config.py").read_text(encoding="utf-8"))
    reads = [n for n in ast.walk(tree) if isinstance(n, ast.Constant)
             and n.value == "DAIMON_TENANT_LAYERS"]
    assert len(reads) == 1
