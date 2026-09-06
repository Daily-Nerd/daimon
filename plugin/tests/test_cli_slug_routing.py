"""#766 slice 4: `--slug` is a ROUTING flag on the ten human-only verbs.

`decide --all-projects` prints the one command that closes each foreign
entry, and that command has to be runnable from wherever the person is
standing. `--project` cannot carry a slug (it resolves a path and the slug
flattening is not invertible), so the four decision-writing families take
`--slug=<slug>` exactly as `brief`, `recall` and `why` already do.

Routing only. It mints nothing, adds no assertion power, and leaves every
channel gate untouched: the same verb refuses `--by agent` with or without
the flag. On a tenant-scoped home (#899) the flag is refused outright, since
a caller choosing a bucket is the primitive that mode exists to remove.
"""

import json

import pytest

from daimon_briefing import cli, refutations, requests, store

A, B, C = "/p/A", "/p/B", "/p/C"


@pytest.fixture
def project(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", A)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    return A


def _b() -> str:
    return store.project_slug(B)


def _open_to_b() -> str:
    return requests.open_request(to=_b(), ask="do the thing", why="because",
                                 channel="cli-agent", project_dir=C)


def _ruling_in_b() -> str:
    return refutations.assert_ruling(
        subject="release", verdict="never bump to 1.0 without a human call",
        scope="repo", evidence=["issue:766"], channel="cli-agent",
        project_dir=B)


def _refutation_in_b(capsys) -> str:
    assert cli.main([
        "refute", "add", "--subject", "whole-file receipt design",
        "--verdict", "whole-file hashes do not prove spans",
        "--scope", "carried receipt tiers", "--evidence", "measurement:1/2",
        "--by", "agent", "--json", "--project", B]) == 0
    return json.loads(capsys.readouterr().out)["refutation_id"]


# ---- the four families route ---------------------------------------------


def test_request_accept_routes_to_the_named_bucket(project, capsys):
    q = _open_to_b()
    assert cli.main(["request", "accept", q, f"--slug={_b()}"]) == 0
    assert requests.recipient_join(project_dir=B)[q]["state"] == "accepted"
    # nothing landed in the bucket the person was standing in
    assert not any(r.get("request_id") == q
                   for r in requests.events(project_dir=A))


def test_request_suppress_routes_to_the_named_bucket(project, capsys):
    q = _open_to_b()
    assert cli.main(["request", "suppress", q, f"--slug={_b()}"]) == 0
    assert requests.recipient_join(project_dir=B)[q]["suppressed"] is True


def test_ruling_ratify_routes_to_the_named_bucket(project, capsys,
                                                  monkeypatch):
    rid = _ruling_in_b()
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert cli.main(["ruling", "ratify", rid, f"--slug={_b()}",
                     "--json"]) == 0
    assert refutations.records(project_dir=B)[rid]["state"] == "active"
    assert rid not in refutations.records(project_dir=A)


def test_ruling_retire_routes_to_the_named_bucket(project, capsys,
                                                  monkeypatch):
    rid = _ruling_in_b()
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert cli.main(["ruling", "ratify", rid, f"--slug={_b()}",
                     "--json"]) == 0
    capsys.readouterr()
    assert cli.main(["ruling", "retire", rid, f"--slug={_b()}",
                     "--json"]) == 0
    assert refutations.records(project_dir=B)[rid]["state"] != "active"


def test_refute_ratify_routes_to_the_named_bucket(project, capsys):
    rid = _refutation_in_b(capsys)
    assert cli.main(["refute", "ratify", rid, f"--slug={_b()}",
                     "--json"]) == 0
    assert refutations.records(project_dir=B)[rid]["state"] == "active"
    assert rid not in refutations.records(project_dir=A)


def test_refute_overturn_routes_to_the_named_bucket(project, capsys):
    rid = _refutation_in_b(capsys)
    assert cli.main(["refute", "ratify", rid, f"--slug={_b()}",
                     "--json"]) == 0
    capsys.readouterr()
    assert cli.main(["refute", "overturn", rid, "--evidence", "issue:766",
                     f"--slug={_b()}", "--json"]) == 0
    assert refutations.records(project_dir=B)[rid]["state"] != "active"


def test_amend_reject_routes_to_the_named_bucket(project, capsys):
    """No verified amendment can be seeded without a session-end byte-check,
    so the routing is pinned on the refusal path: an unknown id refused in
    the NAMED bucket, with nothing written in either."""
    from daimon_briefing import amendments
    rc = cli.main(["amend", "reject", "a-0123456789ab", f"--slug={_b()}"])
    assert rc == 1
    assert amendments.events(project_dir=B) == []
    assert amendments.events(project_dir=A) == []


# ---- routing only: every gate stays where it was --------------------------


def test_the_channel_gate_is_unchanged_under_slug(project, capsys):
    q = _open_to_b()
    rc = cli.main(["request", "accept", q, f"--slug={_b()}", "--by", "agent"])
    assert rc == 1
    assert requests.recipient_join(project_dir=B)[q]["state"] == "open"


@pytest.mark.parametrize("argv", [
    ["request", "accept", "q-0123456789ab"],
    ["amend", "ratify", "a-0123456789ab"],
    ["ruling", "ratify", "r-0123456789ab"],
    ["refute", "overturn", "r-0123456789ab", "--evidence", "x"],
])
def test_slug_and_project_are_two_answers_to_which_bucket(project, capsys,
                                                          argv):
    rc = cli.main(argv + [f"--slug={_b()}", "--project", A])
    assert rc == 2
    assert "--slug" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["request", "accept", "q-0123456789ab"],
    ["amend", "reject", "a-0123456789ab"],
    ["ruling", "retire", "r-0123456789ab"],
    ["refute", "ratify", "r-0123456789ab"],
])
def test_a_tenant_scoped_home_refuses_the_routing_flag(project, capsys,
                                                       monkeypatch, argv):
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    rc = cli.main(argv + [f"--slug={_b()}"])
    assert rc == 2
    assert "tenant-scoped" in capsys.readouterr().err


def test_request_done_takes_no_slug(project, capsys):
    """`done` is the one either-channel state move. Routing it by slug would
    let an agent standing anywhere mark a foreign ask done; it stays
    `--project` only, on purpose."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["request", "done", "q-0123456789ab", "--evidence", "x",
                  f"--slug={_b()}", "--by", "agent"])
    assert exc.value.code == 2


# ---- `--project` is a PATH, never a bucket name (#948 review blocker) ------


def test_project_never_addresses_a_bucket_by_its_slug(project, capsys,
                                                      tmp_checkpoint_dir):
    """`--slug` is the ONLY way to name a bucket, and it is gated. If a
    slug-shaped `--project` addressed one too, every verb would carry the
    routing primitive `_slug_route` deliberately restricted to ten human-only
    decision verbs, with no channel gate and no tenant check anywhere on the
    path."""
    ruling_id = _ruling_in_b()
    capsys.readouterr()

    rc = cli.main(["ruling", "list", "--json", f"--project={_b()}"])

    assert ruling_id not in capsys.readouterr().out, \
        "--project named a bucket by its slug"
    assert rc == 1, "the slug resolved as a path, so it has no bucket"


def test_a_tenant_scoped_home_cannot_be_escaped_through_project(
        project, capsys, monkeypatch, tmp_checkpoint_dir):
    """#899 removes the caller's ability to choose a bucket. The refusal guards
    `--slug` and `--all-projects` because `--project` could never reach one."""
    ruling_id = _ruling_in_b()
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    capsys.readouterr()

    assert cli.main(["ruling", "list", "--json", f"--project={_b()}"]) == 1
    assert ruling_id not in capsys.readouterr().out


def test_a_tenant_scoped_home_cannot_be_written_through_project(
        project, capsys, monkeypatch, tmp_checkpoint_dir):
    """The write half. A candidate landing in a foreign bucket is an agent
    planting a record in a project it was never given."""
    _ruling_in_b()
    foreign = tmp_checkpoint_dir / (store.project_slug(B) or "")
    before = (foreign / "refutations.jsonl").read_bytes()
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    capsys.readouterr()

    cli.main(["ruling", "propose", "--subject", "theirs",
              "--verdict", "an injected rule", "--scope", "theirs",
              "--evidence", "issue:1", "--by", "agent",
              f"--project={_b()}"])

    assert (foreign / "refutations.jsonl").read_bytes() == before, \
        "a write reached a foreign bucket through --project"


def test_a_slug_shaped_project_resolves_the_way_it_did_before(project):
    """The pre-branch behavior, restored: the value is a path, made absolute
    against the working directory like any other relative path."""
    from pathlib import Path

    from daimon_briefing import config

    assert cli._resolve_project(_b()) == str(Path(_b()).resolve())
    # the library keeps the passthrough its bucket-iterating readers need
    assert config.resolve_project_dir(_b()) == _b()
