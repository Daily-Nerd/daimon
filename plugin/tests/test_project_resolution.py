"""#948: a library caller and the CLI must route the same path to one bucket.

The defect this file pins: the CLI resolved `--project` to an absolute path
and then to the git toplevel, while every library ledger entry point slugged
the raw argument it was handed. A host calling
`refutations.assert_ruling(project_dir="<repo>/plugin")` therefore wrote the
`-repo-plugin` bucket, and `daimon ruling list --project <repo>/plugin` read
`-repo` and printed an empty list at exit 0. Nothing errored, and the ruling
was simply not there.

Every test here writes from a SUBDIR and reads from the repo ROOT, or the
reverse, and asserts a single bucket exists afterwards.
"""

import subprocess
from pathlib import Path

import pytest

from daimon_briefing import (amendments, briefing, cli, config, pending,
                             privacy, recall, refutations, relations,
                             requests, store)


def _init_git_repo(path: Path) -> None:
    """A bare `git init` is enough: `rev-parse --show-toplevel` answers with no
    commit and no configured identity."""
    subprocess.run(["git", "init", "-q", str(path)], check=True)


@pytest.fixture
def repo_and_subdir(tmp_path):
    """(repo root, a subdirectory of it) inside a real git repo."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    subdir = repo / "plugin" / "pkg"
    subdir.mkdir(parents=True)
    return str(repo), str(subdir)


def _buckets(tmp_checkpoint_dir) -> list[str]:
    if not tmp_checkpoint_dir.exists():
        return []
    return sorted(child.name for child in tmp_checkpoint_dir.iterdir()
                  if child.is_dir())


# ---- the four ledgers ----


def _write_ruling(project_dir) -> str:
    return refutations.assert_ruling(
        subject="bucket routing", verdict="one bucket per repo",
        scope="daimon", evidence=["issue:948"], channel="cli-tty",
        project_dir=project_dir)


_ASK_WHY = ("confirm the bucket a subdir routes to",
            "a write and a read disagreed about the project")


def _write_request(project_dir) -> str:
    return requests.open_request(
        to="-p-recipient", ask=_ASK_WHY[0], why=_ASK_WHY[1],
        channel="cli-tty", project_dir=project_dir)


def _write_amendment(project_dir) -> str:
    return amendments.propose(
        item_id="o-1234567890ab", change="progressed",
        evidence="issue:948", channel="cli-tty", project_dir=project_dir)


def _endpoint(session: str, item: str) -> dict:
    return {"session_id": session, "field": "recent_decisions",
            "item_id": item}


def _write_relation(project_dir) -> str:
    return relations.propose(
        type_="revision-of",
        from_endpoint=_endpoint("S2", "r-abc123456789"),
        to_endpoint=_endpoint("S1", "r-def123456789"),
        matched_by=["carry-absolute"], matcher_version="lineage-v1",
        channel="lab-import", project_dir=project_dir)


LEDGERS = [
    ("refutations", _write_ruling, refutations.records),
    ("requests", _write_request, requests.records),
    ("amendments", _write_amendment, amendments.records),
    ("relations", _write_relation, relations.records),
]


@pytest.mark.parametrize("name,write,read",
                         LEDGERS, ids=[row[0] for row in LEDGERS])
def test_a_subdir_write_is_read_back_from_the_repo_root(
        name, write, read, repo_and_subdir, tmp_checkpoint_dir):
    repo, subdir = repo_and_subdir
    record_id = write(subdir)

    assert record_id in read(project_dir=repo), \
        f"{name}: written from a subdir, invisible from the repo root"
    assert _buckets(tmp_checkpoint_dir) == [store.project_slug(repo)]


@pytest.mark.parametrize("name,write,read",
                         LEDGERS, ids=[row[0] for row in LEDGERS])
def test_a_root_write_is_read_back_from_a_subdir(
        name, write, read, repo_and_subdir, tmp_checkpoint_dir):
    repo, subdir = repo_and_subdir
    record_id = write(repo)

    assert record_id in read(project_dir=subdir), \
        f"{name}: written from the repo root, invisible from a subdir"
    assert _buckets(tmp_checkpoint_dir) == [store.project_slug(repo)]


# ---- the CLI reads what the library wrote ----


def test_cli_ruling_list_sees_a_ruling_written_from_a_subdir(
        repo_and_subdir, tmp_checkpoint_dir, capsys):
    """The reported symptom, end to end: `[]` at exit 0 for a ruling that is
    on disk."""
    repo, subdir = repo_and_subdir
    ruling_id = _write_ruling(subdir)
    refutations.ratify(ruling_id, channel="signed", project_dir=repo)
    capsys.readouterr()

    assert cli.main(["ruling", "list", "--json", "--project", subdir]) == 0
    assert ruling_id in capsys.readouterr().out


# ---- the #927-pinned host API ----


def test_pinned_host_readers_see_a_ruling_ratified_from_the_repo_root(
        repo_and_subdir, tmp_checkpoint_dir):
    """`refutations.listing` and `briefing.active_rulings` are pinned for host
    processes (#927). A host standing in a subdir must read the ruling a
    ratification from the repo root activated."""
    repo, subdir = repo_and_subdir
    ruling_id = _write_ruling(subdir)
    refutations.ratify(ruling_id, channel="signed", project_dir=repo)

    listed = refutations.listing(states={"active"}, polarity="ruling",
                                 project_dir=subdir)
    assert [row["refutation_id"] for row in listed] == [ruling_id]
    assert [row["refutation_id"]
            for row in briefing.active_rulings(subdir)] == [ruling_id]


# ---- slug passthrough survives ----


def test_a_bucket_slug_still_addresses_that_bucket_directly(
        tmp_checkpoint_dir, monkeypatch, tmp_path):
    """`--slug` routing and every bucket-iterating reader hand a SLUG in as
    project_dir. Resolving it against the cwd would re-route them all."""
    monkeypatch.chdir(tmp_path)
    assert refutations._path("-some-slug") == \
        config.checkpoint_dir() / "-some-slug" / "refutations.jsonl"
    assert requests._path("-some-slug") == \
        config.checkpoint_dir() / "-some-slug" / "requests.jsonl"
    assert amendments._path("-some-slug") == \
        config.checkpoint_dir() / "-some-slug" / "amendments.jsonl"
    assert relations._path("-some-slug") == \
        config.checkpoint_dir() / "-some-slug" / "relations.jsonl"


# ---- the sites that slug the raw dir for something other than a file path ----


def test_a_request_id_names_the_project_not_the_directory_it_was_typed_in(
        repo_and_subdir, tmp_checkpoint_dir, monkeypatch):
    """`make_id` hashes the SENDER SLUG, and `from_label` is the directory
    basename. Unresolved, one ask opened from a subdir and the same ask opened
    from the repo root mint two ids for one request, and the recipient reads a
    label naming a directory instead of the project."""
    repo, subdir = repo_and_subdir
    order = 1_700_000_000_000_000_000
    monkeypatch.setattr("daimon_briefing.requests.time.time_ns",
                        lambda: order)

    q_id = _write_request(subdir)

    assert q_id == requests.make_id(
        store.project_slug(repo) or "", *_ASK_WHY, requests._ts(order))
    assert requests.records(project_dir=repo)[q_id]["from_label"] == \
        Path(repo).name


def test_the_decide_queue_from_a_subdir_is_this_projects_queue(
        repo_and_subdir, tmp_checkpoint_dir):
    """`pending.queue` slugs the raw dir to decide which asks are addressed
    HERE. From a subdir it would answer for a project that has no bucket."""
    repo, subdir = repo_and_subdir
    own = store.project_slug(repo)
    requests.open_request(
        to=own, ask="decide this from wherever you are standing",
        why="the queue must not depend on the caller's cwd",
        channel="cli-agent", to_human=True, project_dir=repo)

    rows = pending.queue(project_dir=subdir)["rows"]
    assert [row["kind"] for row in rows] == ["request"]


def test_the_privacy_audit_from_a_subdir_reports_the_project_slug(
        repo_and_subdir, tmp_checkpoint_dir):
    repo, subdir = repo_and_subdir
    assert privacy.audit_project(subdir)["slug"] == store.project_slug(repo)


def test_an_ambient_recall_scope_from_a_subdir_is_the_project_bucket(
        repo_and_subdir, tmp_checkpoint_dir):
    """#899 scoping reads this project's own slug. A subdir would scope an
    unaddressed read to a bucket nothing was ever written to."""
    repo, subdir = repo_and_subdir
    assert recall._ambient_scopes(subdir) == [store.project_slug(repo)]
