"""#954: the checkpoint store resolves a project path the way every ledger does.

Since #948 the CLI and every ledger helper route `project_dir` through
`config.resolve_project_dir` (absolute, symlinks collapsed, git toplevel) before
they derive a bucket. `store` did not, so an in-process host calling
`store.write_checkpoint(project_dir="<repo>/sub")` wrote one bucket while
`refutations.assert_ruling(project_dir="<repo>/sub")` in the same process wrote
another. Two buckets, no error.

The invariant these tests hold: no PUBLIC store entry point that accepts a
`project_dir` ever derives a bucket from the literal path it was handed.
`store.project_slug` itself is the deliberate exception — it is the documented
character transform pinned by `daimon slug` (#913) and must keep answering for a
path daimon has never seen.
"""

import ast
import subprocess
from pathlib import Path

import pytest

from daimon_briefing import config, store


def _init_git_repo(path: Path) -> None:
    """`rev-parse --show-toplevel` answers right after `git init` — no commit
    and no identity needed."""
    subprocess.run(["git", "init", "-q", str(path)], check=True)


@pytest.fixture
def repo_and_sub(tmp_path):
    """A git repository and a subdirectory of it that is NOT its own repo."""
    config.resolve_project_root.cache_clear()
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    sub = repo / "plugin" / "pkg"
    sub.mkdir(parents=True)
    return repo.resolve(), sub


@pytest.fixture
def resolve_calls(monkeypatch):
    """Every argument `store._resolved` was handed, call-through."""
    seen: list = []
    real = store._resolved

    def spy(project_dir=None):
        seen.append(project_dir)
        return real(project_dir)

    monkeypatch.setattr(store, "_resolved", spy)
    return seen


@pytest.fixture
def slug_calls(monkeypatch):
    """Every argument `store.project_slug` was handed, call-through."""
    seen: list = []
    real = store.project_slug

    def spy(project_dir=None):
        seen.append(project_dir)
        return real(project_dir)

    monkeypatch.setattr(store, "project_slug", spy)
    return seen


# ---- the headline defect ----


def test_write_checkpoint_from_a_subdir_lands_in_the_repo_bucket(
        tmp_checkpoint_dir, sample_checkpoint, repo_and_sub):
    """The issue in one test: a host writing from `<repo>/plugin/pkg` must land
    in the repository's bucket, the one the CLI and every ledger address."""
    repo, sub = repo_and_sub
    root_bucket = store.project_slug(str(repo))
    sub_bucket = store.project_slug(str(sub))
    assert root_bucket != sub_bucket

    store.write_checkpoint("S1", dict(sample_checkpoint), project_dir=str(sub))

    assert (tmp_checkpoint_dir / root_bucket / "latest.json").exists()
    assert not (tmp_checkpoint_dir / sub_bucket).exists()


def test_write_checkpoint_stamps_the_resolved_slug_on_the_caller_dict(
        tmp_checkpoint_dir, sample_checkpoint, repo_and_sub):
    """Scars 0059 and 0027: `write_checkpoint` mutates the dict it was handed
    and stamps with `setdefault`. Resolution changes the VALUE stamped, never
    that contract."""
    repo, sub = repo_and_sub
    checkpoint = dict(sample_checkpoint)
    assert "project_slug" not in checkpoint

    store.write_checkpoint("S1", checkpoint, project_dir=str(sub))

    assert checkpoint["project_slug"] == store.project_slug(str(repo))
    assert checkpoint["project_name"] == repo.name


def test_write_checkpoint_still_never_overwrites_a_stamped_slug(
        tmp_checkpoint_dir, sample_checkpoint, repo_and_sub):
    """The `setdefault` half of the same contract: a checkpoint that carries
    its own stamp is never re-stamped, resolution or not."""
    _repo, sub = repo_and_sub
    checkpoint = dict(sample_checkpoint, project_slug="-already-stamped")

    store.write_checkpoint("S1", checkpoint, project_dir=str(sub))

    assert checkpoint["project_slug"] == "-already-stamped"


def test_a_subdir_write_and_a_root_write_share_one_bucket(
        tmp_checkpoint_dir, sample_checkpoint, repo_and_sub):
    repo, sub = repo_and_sub

    store.write_checkpoint("S-sub", dict(sample_checkpoint, session_id="S-sub"),
                           project_dir=str(sub))
    store.write_checkpoint("S-root", dict(sample_checkpoint, session_id="S-root"),
                           project_dir=str(repo))

    buckets = [d.name for d in tmp_checkpoint_dir.iterdir() if d.is_dir()]
    assert buckets == [store.project_slug(str(repo))]


def test_a_subdir_write_is_read_back_from_the_repo_root(
        tmp_checkpoint_dir, sample_checkpoint, repo_and_sub):
    """The read side of the same coin, on the strict route: `Route.OWN` refuses
    the global fallback, so this can only pass if both calls agree."""
    repo, sub = repo_and_sub
    store.write_checkpoint("S1", dict(sample_checkpoint, session_id="S1"),
                           project_dir=str(sub))

    got = store.read_latest_body(project_dir=str(repo), route=store.Route.OWN,
                                 admit=store.Admit.ANY)
    assert got is not None and got["session_id"] == "S1"


def test_project_latest_path_answers_the_repo_bucket_for_a_subdir(
        tmp_checkpoint_dir, repo_and_sub):
    repo, sub = repo_and_sub
    assert store.project_latest_path(str(sub)) == store.project_latest_path(str(repo))


def test_sibling_buckets_excludes_the_repo_bucket_for_a_subdir(
        tmp_checkpoint_dir, sample_checkpoint, repo_and_sub):
    """`sibling_buckets` means "every bucket that is not mine". Asked from a
    subdirectory it used to call the repository's own bucket a sibling."""
    repo, sub = repo_and_sub
    store.write_checkpoint("S1", dict(sample_checkpoint), project_dir=str(repo))

    names = {b["slug"] for b in store.sibling_buckets(str(sub))}
    assert store.project_slug(str(repo)) not in names


# ---- the ledger surfaces the store owns ----


def test_verification_written_from_a_subdir_is_read_from_the_root(
        tmp_checkpoint_dir, repo_and_sub):
    repo, sub = repo_and_sub
    assert store.append_verification("i-1", "verbatim", "no match",
                                     project_dir=str(sub))

    rows = store.verification_rows(project_dir=str(repo))
    assert [r.get("item_ref") for r in rows] == ["i-1"]


def test_event_written_from_a_subdir_is_read_from_the_root(
        tmp_checkpoint_dir, repo_and_sub):
    repo, sub = repo_and_sub
    assert store.append_event("i-1", "resolved", project_dir=str(sub))

    assert "i-1" in store.resolutions(project_dir=str(repo))


def test_forget_hits_written_from_a_subdir_are_counted_from_the_root(
        tmp_checkpoint_dir, repo_and_sub):
    repo, sub = repo_and_sub
    assert store.record_forget_hits([{"text": "a value"}], project_dir=str(sub))

    assert store.forget_hit_stats(project_dir=str(repo))["count"] >= 1


def test_record_forget_hits_refuses_an_empty_list_before_it_resolves(
        tmp_checkpoint_dir, repo_and_sub, monkeypatch):
    """Nothing to record is decided from the arguments alone. Resolution reaches
    the filesystem and can fork git, so a call that is already a no-op must not
    pay for it: the guard sits above the resolve, not below."""
    _repo, sub = repo_and_sub

    def _never(project_dir=None):
        raise AssertionError("resolved on a call with nothing to record")

    monkeypatch.setattr(store, "_resolved", _never)

    assert store.record_forget_hits([], project_dir=str(sub)) is False


def test_project_surfaces_finds_a_subdir_write_from_the_root(
        tmp_checkpoint_dir, sample_checkpoint, repo_and_sub):
    repo, sub = repo_and_sub
    store.write_checkpoint("S1", dict(sample_checkpoint), project_dir=str(sub))

    assert store.project_surfaces(project_dir=str(repo))


def test_sessions_since_count_sees_a_subdir_write_from_the_root(
        tmp_checkpoint_dir, sample_checkpoint, repo_and_sub):
    repo, sub = repo_and_sub
    store.write_checkpoint("S1", dict(sample_checkpoint, created="2030-01-01T00:00:00Z"),
                           project_dir=str(sub))

    assert store.sessions_since_count("2020-01-01T00:00:00Z",
                                      project_dir=str(repo)) == 1


# ---- the host-facing check ----


def test_project_bucket_names_the_bucket_the_store_will_use(repo_and_sub):
    """#954 asks for the store to expose the bucket it will write, so a host can
    check before it writes rather than discover a fork afterwards."""
    repo, sub = repo_and_sub
    assert store.project_bucket(str(sub)) == store.project_slug(str(repo))
    assert store.project_bucket(str(repo)) == store.project_slug(str(repo))


def test_project_bucket_is_none_for_an_unknown_project():
    assert store.project_bucket(None) is None
    assert store.project_bucket("") is None


def test_project_bucket_passes_a_slug_through(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert store.project_bucket("-Users-x-proj") == "-Users-x-proj"


# ---- slug passthrough must survive (#766 slice 4, brief --slug) ----


def test_read_latest_still_addresses_a_bucket_named_by_slug(
        tmp_checkpoint_dir, sample_checkpoint, tmp_path, monkeypatch):
    """`brief --slug` and every bucket-iterating reader hand a BUCKET SLUG in as
    `project_dir`. Resolving one against the cwd would re-route it to the
    caller's own bucket."""
    monkeypatch.chdir(tmp_path)
    slug = "-Users-x-proj"
    store.write_checkpoint("S1", dict(sample_checkpoint, session_id="S1"),
                           project_dir=slug)

    assert (tmp_checkpoint_dir / slug / "latest.json").exists()
    got = store.read_latest_body(project_dir=slug, route=store.Route.OWN,
                                 admit=store.Admit.ANY)
    assert got is not None and got["session_id"] == "S1"


def test_a_slug_survives_every_public_entry_point(tmp_checkpoint_dir, tmp_path,
                                                  monkeypatch, slug_calls):
    """No entry point may re-route a slug: every slug handed to `project_slug`
    on this path comes back as the slug itself."""
    monkeypatch.chdir(tmp_path)
    slug = "-Users-x-proj"
    store.append_event("i-1", "resolved", project_dir=slug)
    store.append_verification("i-1", "verbatim", "no match", project_dir=slug)
    store.record_forget_hits([{"text": "a value"}], project_dir=slug)
    store.resolutions(project_dir=slug)
    store.verification_rows(project_dir=slug)
    store.forget_hit_stats(project_dir=slug)
    store.project_latest_path(slug)

    assert {c for c in slug_calls if c and str(c).startswith("-Users")} == {slug}


# ---- the audit: every public entry point, one invariant ----


def _public_entry_points(sub: str):
    """Every PUBLIC store function that accepts a `project_dir`, called once
    with a subdirectory path. `project_slug` is excluded on purpose: it is the
    literal character transform `daimon slug` pins (#913)."""
    return [
        ("project_surfaces", lambda: store.project_surfaces(project_dir=sub)),
        ("items_for_project", lambda: store.items_for_project(project_dir=sub)),
        ("scrub_content_key",
         lambda: store.scrub_content_key("deadbeef", project_dir=sub)),
        ("scrub_team_copies",
         lambda: store.scrub_team_copies("deadbeef", project_dir=sub)),
        ("publish_tombstone",
         lambda: store.publish_tombstone("deadbeef", project_dir=sub)),
        ("apply_foreign_tombstones",
         lambda: store.apply_foreign_tombstones(project_dir=sub)),
        ("write_checkpoint",
         lambda: store.write_checkpoint("S-audit", {"session_id": "S-audit"},
                                        project_dir=sub)),
        ("project_latest_path", lambda: store.project_latest_path(sub)),
        ("sibling_buckets", lambda: store.sibling_buckets(sub)),
        ("read_latest_body",
         lambda: store.read_latest_body(project_dir=sub, route=store.Route.OWN,
                                        admit=store.Admit.ANY)),
        ("read_latest_result",
         lambda: store.read_latest_result(project_dir=sub, route=store.Route.OWN,
                                          admit=store.Admit.ANY)),
        ("read_own_stream_latest",
         lambda: store.read_own_stream_latest(project_dir=sub)),
        ("read_team", lambda: store.read_team(project_dir=sub)),
        ("sessions_since_count",
         lambda: store.sessions_since_count("2020-01-01T00:00:00Z",
                                            project_dir=sub)),
        ("active_handoff", lambda: store.active_handoff(project_dir=sub)),
        ("append_verification",
         lambda: store.append_verification("i-1", "verbatim", "no match",
                                           project_dir=sub)),
        ("verification_rows", lambda: store.verification_rows(project_dir=sub)),
        ("latest_receipt_verdicts",
         lambda: store.latest_receipt_verdicts(project_dir=sub)),
        ("append_receipt_cure",
         lambda: store.append_receipt_cure("i-1", project_dir=sub)),
        ("verification_counts", lambda: store.verification_counts(project_dir=sub)),
        ("record_forget_hits",
         lambda: store.record_forget_hits([{"text": "a value"}],
                                          project_dir=sub)),
        ("forget_hit_stats", lambda: store.forget_hit_stats(project_dir=sub)),
        ("append_event",
         lambda: store.append_event("i-1", "resolved", project_dir=sub)),
        ("scrub_event_fields",
         lambda: store.scrub_event_fields("deadbeef", project_dir=sub)),
        ("resolutions", lambda: store.resolutions(project_dir=sub)),
        ("corroborations", lambda: store.corroborations(project_dir=sub)),
        ("forgotten_content_keys",
         lambda: store.forgotten_content_keys(project_dir=sub)),
        ("project_bucket", lambda: store.project_bucket(sub)),
    ]


def _public_project_dir_functions(source: str) -> set[str]:
    """Every top-level PUBLIC function in `source` that accepts a
    `project_dir`. Both function kinds: a coroutine that takes a project path
    would name a bucket exactly like a plain one, so an audit that only walks
    `ast.FunctionDef` would let it through unresolved."""
    found = set()
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue
        args = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
        if "project_dir" in args:
            found.add(node.name)
    return found


def test_the_audit_scan_sees_both_function_kinds():
    """The scan itself, on a scratch copy: private skipped, plain found, async
    found. Without the async arm the third name goes missing and the audit
    below passes over a real entry point."""
    source = (
        "def public_one(project_dir=None): pass\n"
        "def _private_one(project_dir=None): pass\n"
        "async def public_async(project_dir=None): pass\n"
        "def unrelated(other=None): pass\n"
    )
    assert _public_project_dir_functions(source) == {"public_one", "public_async"}


def test_the_audit_list_covers_every_public_entry_point():
    """The list above is checked against the module, so a new public function
    that takes a `project_dir` fails here rather than shipping unresolved."""
    found = _public_project_dir_functions(
        Path(store.__file__).read_text(encoding="utf-8"))

    covered = {name for name, _ in _public_entry_points("x")} | {"project_slug"}
    assert found - covered == set()


@pytest.mark.parametrize("name", [n for n, _ in _public_entry_points("x")])
def test_no_public_entry_point_slugs_the_literal_subdir(
        name, tmp_checkpoint_dir, repo_and_sub, slug_calls, resolve_calls,
        monkeypatch):
    """THE invariant, in two halves. Every entry point resolves the path it was
    handed, and none of them ever names a bucket from the literal subdirectory.

    The second half is the one that matters and it is checked at the seam where
    a bucket is actually derived, so an entry point that reaches its bucket
    through `teamproject` rather than `project_slug` is covered too."""
    # Both opt-in gates ON, so the team surfaces reach their resolution instead
    # of short-circuiting to [] before they ever name a bucket.
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_TEAM_APPLY_FORGET", "1")
    _repo, sub = repo_and_sub
    call = dict(_public_entry_points(str(sub)))[name]

    call()

    assert str(sub) in [str(c) for c in resolve_calls if c]
    assert str(sub) not in [str(c) for c in slug_calls if c]
