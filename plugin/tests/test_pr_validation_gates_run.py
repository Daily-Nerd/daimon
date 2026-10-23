"""The PR validation commit gates, run for real against a throwaway repo.

test_pr_template_matches_gates.py pins the workflow TEXT. That catches a gate
that goes back to reading the PR body, but not a gate that keeps the right
words and loses the behaviour: a collector that splits on newline instead of
NUL still contains the format string, and a deleted `exit 1` on the empty
range leaves every pin intact. So each step's `run:` block is extracted from
the workflow byte-for-byte and executed with bash, the way GitHub runs it,
over commits made in a temporary repository.

The parse gate needs node and the pinned @conventional-commits/parser. The
test never reaches the network for it: the parser is taken from the local npm
cache, and the case is skipped with a reason when it is not there.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github/workflows/pr-validation.yml"
PARSER = "@conventional-commits/parser@0.4.1"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="the gates are bash over git; both must be on PATH")


def _run_block(step):
    """The dedented `run: |` block of one named workflow step."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.strip() == f"- name: {step}")
    while lines[i].strip() != "run: |":
        i += 1
    indent = len(lines[i]) - len(lines[i].lstrip()) + 2
    out = []
    for ln in lines[i + 1:]:
        if ln.strip() == "":
            out.append("")
            continue
        if len(ln) - len(ln.lstrip()) < indent:
            break
        out.append(ln[indent:])
    return "\n".join(out).rstrip("\n") + "\n"


def test_the_extractor_finds_the_three_commit_gates():
    # If a step is renamed the extractor raises StopIteration; this names it.
    assert "git log -z" in _run_block("Collect branch commits")
    assert "BREAKING" in _run_block("Check Breaking-Change Marker")
    assert "parser(" in _run_block("Check Release-Please Can Parse")


class Repo:
    """A fresh repository, isolated from the developer's git configuration."""

    def __init__(self, path):
        self.path = path
        path.mkdir()
        self.env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        }
        self.git("init", "-q", "-b", "main")
        self.commit("chore: root")

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args], cwd=self.path,
            env=self.env, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, subject, body=None):
        args = ["commit", "-q", "--allow-empty", "-m", subject]
        if body is not None:
            args += ["-m", body]
        self.git(*args)
        return self.git("rev-parse", "HEAD")

    def branch(self, name):
        self.git("checkout", "-q", "-b", name, "main")

    def head(self):
        return self.git("rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path / "repo")


@pytest.fixture
def runner(tmp_path, repo):
    """Run one gate step the way Actions does: bash -eo pipefail, CI env."""
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir()
    scripts = tmp_path / "steps"
    scripts.mkdir()

    def run(step, base, head, extra_env=None):
        script = scripts / (step.replace(" ", "_") + ".sh")
        script.write_text(_run_block(step), encoding="utf-8")
        env = {**repo.env, "RUNNER_TEMP": str(runner_temp),
               "BASE_SHA": base, "HEAD_SHA": head, **(extra_env or {})}
        return subprocess.run(
            ["bash", "-eo", "pipefail", str(script)], cwd=repo.path, env=env,
            capture_output=True, text=True)

    run.commits = runner_temp / "commits"
    run.temp = runner_temp
    return run


def _main(repo):
    return repo.git("rev-parse", "main")


def test_a_body_with_blank_lines_is_collected_as_one_message(repo, runner):
    repo.branch("d")
    message = ("feat(x): paragraphs\n\nFirst paragraph.\n\n"
               "Second paragraph after a blank line.\n\nRefs #2")
    sha = repo.commit("feat(x): paragraphs", message.split("\n\n", 1)[1])
    res = runner("Collect branch commits", _main(repo), repo.head())
    assert res.returncode == 0, res.stdout + res.stderr
    files = sorted(runner.commits.iterdir())
    assert [f.name for f in files] == [f"0001-{sha[:7]}.txt"]
    assert files[0].read_text(encoding="utf-8") == message + "\n"


def test_history_order_and_merge_commits_skipped(repo, runner):
    repo.branch("f")
    first = repo.commit("feat(f): the change")
    repo.git("checkout", "-q", "main")
    repo.commit("chore: main moved on")
    repo.git("checkout", "-q", "f")
    repo.git("merge", "-q", "--no-edit", "main")
    second = repo.commit("fix(f): after the merge")
    res = runner("Collect branch commits", _main(repo), repo.head())
    assert res.returncode == 0, res.stdout + res.stderr
    assert [f.name for f in sorted(runner.commits.iterdir())] == [
        f"0001-{first[:7]}.txt", f"0002-{second[:7]}.txt"]


def test_zero_commits_in_range_fails_instead_of_passing(repo, runner):
    res = runner("Collect branch commits", _main(repo), _main(repo))
    assert res.returncode == 1
    assert "no commits found" in res.stdout
    assert "::error::" in res.stdout


def test_missing_history_is_named_as_the_cause(repo, runner):
    # A shallow or stale checkout lacks the base commit. git log then fails
    # inside the collector; the step must say so rather than blame the branch.
    absent = "deadbeef" * 5
    res = runner("Collect branch commits", absent, repo.head())
    assert res.returncode == 1
    assert "::error::" in res.stdout
    assert "history" in res.stdout and absent in res.stdout
    assert "no commits found" not in res.stdout


def _collect(repo, runner):
    res = runner("Collect branch commits", _main(repo), repo.head())
    assert res.returncode == 0, res.stdout + res.stderr


def test_a_breaking_mention_with_no_marker_fails_naming_the_commit(repo, runner):
    repo.branch("b")
    repo.commit("docs: harmless")
    sha = repo.commit("feat(api): rename the verb",
                      "This is a breaking change for every caller.")
    _collect(repo, runner)
    res = runner("Check Breaking-Change Marker", _main(repo), repo.head())
    assert res.returncode == 1
    assert f"::error::commit {sha[:7]} (feat(api): rename the verb)" in res.stdout
    assert "no '!'" in res.stdout


@pytest.mark.parametrize("subject,body", [
    ("feat(api)!: rename the verb", "This is a breaking change for every caller."),
    ("fix(api): drop the alias",
     "Breaking change for scripts.\n\nBREAKING CHANGE: the alias is gone."),
    ("feat(api): nothing breaks", "Plain body."),
])
def test_a_marked_or_harmless_commit_passes_the_marker_gate(repo, runner, subject, body):
    repo.branch("e")
    repo.commit(subject, body)
    _collect(repo, runner)
    res = runner("Check Breaking-Change Marker", _main(repo), repo.head())
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.fixture(scope="module")
def parser_prefix(tmp_path_factory):
    """The pinned parser installed from the local npm cache only, or a skip."""
    if shutil.which("node") is None or shutil.which("npm") is None:
        pytest.skip("node and npm are needed to run the parse gate")
    prefix = tmp_path_factory.mktemp("parser")
    res = subprocess.run(
        ["npm", "install", "--prefix", str(prefix), "--no-save", "--no-audit",
         "--no-fund", "--silent", "--offline", PARSER],
        capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        pytest.skip(f"{PARSER} is not in the local npm cache; the parse gate "
                    "cannot run without the network")
    return prefix


def _parse(repo, runner, parser_prefix):
    # The step installs into $RUNNER_TEMP/rp itself; seed that directory from
    # the cache-only install and keep npm offline so the step cannot reach out.
    rp = runner.temp / "rp"
    shutil.copytree(parser_prefix / "node_modules", rp / "node_modules")
    return runner("Check Release-Please Can Parse", _main(repo), repo.head(),
                  {"npm_config_offline": "true"})


def test_a_commit_the_parser_rejects_fails_naming_it(repo, runner, parser_prefix):
    repo.branch("c")
    repo.commit("docs: fine")
    sha = repo.commit("fix(scope): one owner",
                      "foo(bar(x)) was the old call and it is gone.")
    _collect(repo, runner)
    res = _parse(repo, runner, parser_prefix)
    assert res.returncode == 1, res.stdout + res.stderr
    assert f"::error::release-please cannot parse commit {sha[:7]} (fix(scope): one owner)" in res.stdout
    assert "scar 0065" in res.stdout


def test_conventional_commits_with_blank_line_bodies_parse(repo, runner, parser_prefix):
    repo.branch("a")
    repo.commit("feat(cli): add the thing")
    repo.commit("fix(store): close the bucket",
                "First paragraph.\n\nSecond paragraph.\n\nRefs #1")
    _collect(repo, runner)
    res = _parse(repo, runner, parser_prefix)
    assert res.returncode == 0, res.stdout + res.stderr
