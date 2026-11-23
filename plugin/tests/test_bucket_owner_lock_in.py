"""Slice 4a of "project identity survives moves" (#1384): ONE owner decides every
bucket name.

The lock-in is a rebinding. The test replaces the owner (`store.bucket_of`) so a
project directory P names bucket B instead of the slug of P, then runs every
writer and every ledger-path builder and asserts nothing landed under the old
slug and everything landed under B. A site that still derives a name from the
location (`project_slug(P)`, `checkpoint_dir() / <slug of P>`) writes under the
old slug and fails here, which is the failure the owner exists to make
impossible.

The claim and verify halves (`identity`) are pinned beside it: a ref is a `str`
that keeps `Path`, `git_branch` and `declared_slug` working, `ensure_bucket`
verifies and raises `BucketMismatch` (never an `OSError`), the best-effort
handlers let it through, and the serialize spawn line that `heal` parses does
not change shape.
"""

import time
from pathlib import Path

import pytest

from daimon_briefing import (amendments, cli, config, heal, hostgit, identity,
                             ledger, ledger_repair, refutations, relations,
                             requests, store, trust, view)
from daimon_briefing.surfaces import Writer

B = "-rebound-bucket-B"


@pytest.fixture
def rebound(tmp_path, tmp_checkpoint_dir):
    """(P, old slug of P) with P bound to bucket B by B's recorded root: the
    real resolver finds B by its root, so P's seed names nothing."""
    proj = tmp_path / "proj"
    proj.mkdir()
    location = str(proj.resolve())
    old = store.project_slug(location)
    assert old != B
    bucket = tmp_checkpoint_dir / B
    bucket.mkdir(parents=True)
    (bucket / "root").write_text(location + "\n", encoding="utf-8")
    assert store.bucket_of(location) == B
    return location, old


def _checkpoint(sid):
    return {"session_id": sid, "created": "2026-11-20T00:00:00Z",
            "working_context": {"recent_decisions": []},
            "epistemic_snapshot": {}}


def _row():
    return {"ts": "2026-11-20T00:00:00Z", "k": 1}


WRITERS = {
    "write_checkpoint": lambda p: store.write_checkpoint(
        "S-1", _checkpoint("S-1"), project_dir=p, writer=Writer.HUMAN),
    "append_events": lambda p: store.append_events(
        [{"item_ref": "i-1", "status": "resolved", "source": "cli-tty"}],
        project_dir=p, writer=Writer.HUMAN),
    "append_verification": lambda p: store.append_verification(
        "i-1", "quote", "missed", project_dir=p),
    "append_world_outcomes": lambda p: store.append_world_outcomes(
        [("i-1", "file-exists", "confirmed", "a" * 64)], project_dir=p),
    "record_forget_hits": lambda p: store.record_forget_hits(
        [{"text": "a forgotten value"}], project_dir=p),
    "refutations.append": lambda p: refutations.append(
        _row(), project_dir=p, writer=Writer.HUMAN),
    "trust.append": lambda p: trust.append(
        _row(), project_dir=p, writer=Writer.HUMAN),
    "relations._append": lambda p: relations._append(
        _row(), project_dir=p, writer=Writer.HUMAN),
    "amendments.append": lambda p: amendments.append(
        _row(), project_dir=p, writer=Writer.HUMAN),
    "requests.append": lambda p: requests.append(
        _row(), project_dir=p, writer=Writer.HUMAN),
}


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_every_writer_lands_under_the_bucket_the_owner_names(
        name, rebound, tmp_checkpoint_dir):
    location, old = rebound
    WRITERS[name](location)
    assert not (tmp_checkpoint_dir / old).exists(), \
        f"{name} named a bucket from the location, not through the owner"
    landed = [p for p in (tmp_checkpoint_dir / B).iterdir()
              if p.name != "root"]
    assert landed, f"{name} wrote nothing under the bucket the owner names"
    assert (tmp_checkpoint_dir / B / "root").read_text(
        encoding="utf-8").strip() == location


def test_policy_tombstones_land_under_the_owners_bucket(
        rebound, tmp_checkpoint_dir, monkeypatch):
    location, old = rebound
    entry = ("s", "t", "work", "v", "b", "r-aaaaaaaaaaaa", "sha", 1, None)
    monkeypatch.setattr(refutations, "request_policy_history",
                        lambda **kw: frozenset({entry}))
    refutations._write_policy_tombstones({"r-aaaaaaaaaaaa"},
                                         project_dir=location)
    assert not (tmp_checkpoint_dir / old).exists()
    assert (tmp_checkpoint_dir / B
            / "request_policy_tombstones.jsonl").is_file()


def test_the_signed_stamp_and_the_pointer_follow_the_owner(
        rebound, tmp_checkpoint_dir):
    location, old = rebound
    WRITERS["write_checkpoint"](location)
    assert store.read_checkpoint("S-1")["project_slug"] == B
    assert (tmp_checkpoint_dir / B / "latest.json").is_file()
    assert not (tmp_checkpoint_dir / old).exists()


def test_every_ledger_path_builder_answers_the_owners_bucket(
        rebound, tmp_checkpoint_dir):
    location, old = rebound
    bucket = tmp_checkpoint_dir / B
    paths = {
        "store._events_path": store._events_path(location),
        "store._ledger_path": store._ledger_path(location),
        "store._outcomes_path": store._outcomes_path(location),
        "store._forget_hits_path": store._forget_hits_path(location),
        "store.ledger_file": store.ledger_file(location, "x.jsonl"),
        "store.project_latest_path": store.project_latest_path(location),
        "refutations._path": refutations._path(location),
        "refutations._tombstone_path": refutations._tombstone_path(location),
        "requests._path": requests._path(location),
        "trust._path": trust._path(location),
        "relations._path": relations._path(location),
        "amendments._path": amendments._path(location),
        "ledger_repair._bucket_dir": ledger_repair._bucket_dir(location),
        "view._bucket": view._bucket(location),
        "store.bucket_dir": store.bucket_dir(location),
    }
    for name, path in paths.items():
        assert Path(path).parent == bucket or Path(path) == bucket, name
    assert store.project_bucket(location) == B


def test_a_raw_caller_that_never_claimed_still_lands_under_the_owner(
        rebound, tmp_checkpoint_dir):
    location, old = rebound
    bucket = store.ensure_bucket(location)
    assert bucket == tmp_checkpoint_dir / B
    assert not (tmp_checkpoint_dir / old).exists()


# ---- ledger repair takes the ref -------------------------------------------


def _torn_events(bucket, root="/recorded/root"):
    bucket.mkdir(parents=True, exist_ok=True)
    (bucket / "events.jsonl").write_text(
        '{"ts": "2026-11-20T00:00:00Z", "item_ref": "i-1"}\n'
        '{"ts": "2026-11-20T00:00:01Z", "item_re',
        encoding="utf-8")
    (bucket / "root").write_text(root + "\n", encoding="utf-8")


def test_repair_dry_run_on_a_rebound_bucket_reports_its_real_counts(
        rebound, tmp_checkpoint_dir):
    """The dry run copies the bucket to a scratch store and runs the real repair
    there. It must address the bucket by the ref it named once, not re-derive
    the name from the location inside a store that lacks the real one."""
    location, old = rebound
    bucket = tmp_checkpoint_dir / B
    _torn_events(bucket, location)
    before = (bucket / "events.jsonl").read_bytes()

    dry = ledger_repair.repair(location, "events.jsonl", dry_run=True)

    assert (dry.outcome, dry.torn) == ("repaired", 1)
    assert (bucket / "events.jsonl").read_bytes() == before
    real = ledger_repair.repair(location, "events.jsonl")
    assert (real.outcome, real.torn) == ("repaired", 1)
    assert not (tmp_checkpoint_dir / old).exists()


def test_the_scratch_store_carries_the_root_record(tmp_checkpoint_dir):
    bucket = tmp_checkpoint_dir / "-some-bucket"
    _torn_events(bucket)
    with ledger_repair._scratch_store(bucket):
        copy = config.checkpoint_dir() / "-some-bucket"
        assert (copy / "root").read_text(encoding="utf-8") == "/recorded/root\n"
        assert (copy / "events.jsonl").is_file()


# ---- the ref ---------------------------------------------------------------


def test_a_ref_is_the_location_string_and_carries_the_slug(tmp_path,
                                                          tmp_checkpoint_dir):
    proj = tmp_path / "repo"
    proj.mkdir()
    ref = identity.claim(str(proj))
    assert isinstance(ref, str) and isinstance(ref, identity.BucketRef)
    assert ref == str(proj.resolve())
    assert ref.slug == store.project_slug(str(proj.resolve()))
    assert ref.bound is True
    assert Path(ref).name == "repo"
    assert hostgit.declared_slug(ref) is None
    assert config.git_branch(ref) in (None, "")
    assert (tmp_checkpoint_dir / ref.slug / "root").is_file()


def test_claim_is_idempotent_and_a_ref_passes_through_every_layer(
        tmp_path, tmp_checkpoint_dir):
    proj = tmp_path / "repo"
    proj.mkdir()
    ref = identity.claim(str(proj))
    assert identity.claim(ref) is ref
    assert identity.bind(ref) is ref
    assert store._resolved(ref) is ref
    assert store.bucket_of(ref) == ref.slug == store.project_bucket(ref)
    assert store.bucket_dir(ref) == tmp_checkpoint_dir / ref.slug


def test_claim_of_an_unknown_project_is_none(tmp_checkpoint_dir):
    assert identity.claim(None) is None
    assert identity.claim("") is None
    assert identity.bind(None) is None
    assert not tmp_checkpoint_dir.exists()


def test_a_slug_routed_claim_is_unbound_and_stamps_no_root(
        tmp_checkpoint_dir):
    ref = identity.claim("-routed-by-slug")
    assert ref.slug == "-routed-by-slug"
    assert ref.bound is False
    assert (tmp_checkpoint_dir / "-routed-by-slug").is_dir()
    assert not (tmp_checkpoint_dir / "-routed-by-slug" / "root").exists()
    identity.verify(ref)  # names no location, always verifies


def test_the_kill_switch_claim_creates_nothing(tmp_path, tmp_checkpoint_dir,
                                               monkeypatch):
    proj = tmp_path / "repo"
    proj.mkdir()
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    ref = identity.claim(str(proj))
    assert ref.slug
    assert not tmp_checkpoint_dir.exists()


def test_a_claim_that_cannot_make_the_directory_defers_to_ensure_bucket(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    """Claim never raises `OSError`: the writer's own `ensure_bucket(ref)` hits
    the same failure inside the handler every writer already has."""
    proj = tmp_path / "repo"
    proj.mkdir()
    tmp_checkpoint_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_checkpoint_dir.write_text("a file where the store should be")
    ref = identity.claim(str(proj))
    assert ref is not None
    assert requests.append(_row(), project_dir=ref,
                           writer=Writer.HUMAN) is False


# ---- verify and BucketMismatch ---------------------------------------------


def test_ensure_bucket_verifies_the_ref_and_raises_bucket_mismatch(
        tmp_path, tmp_checkpoint_dir):
    proj = tmp_path / "repo"
    proj.mkdir()
    ref = identity.claim(str(proj))
    assert store.ensure_bucket(ref) == tmp_checkpoint_dir / ref.slug
    (tmp_checkpoint_dir / ref.slug / "root").write_text(
        "/rebound/to/another/place\n", encoding="utf-8")
    with pytest.raises(identity.BucketMismatch):
        store.ensure_bucket(ref)


def test_bucket_mismatch_is_not_an_oserror_so_no_writer_absorbs_it(
        tmp_path, tmp_checkpoint_dir):
    assert not issubclass(identity.BucketMismatch, OSError)
    assert not issubclass(identity.BucketClaimFailed, OSError)
    proj = tmp_path / "repo"
    proj.mkdir()
    ref = identity.claim(str(proj))
    (tmp_checkpoint_dir / ref.slug / "root").write_text(
        "/rebound/to/another/place\n", encoding="utf-8")
    with pytest.raises(identity.BucketMismatch):
        requests.append(_row(), project_dir=ref, writer=Writer.HUMAN)


def test_a_lossy_twin_is_refused_the_bucket_another_location_holds(
        tmp_path, tmp_checkpoint_dir):
    """4b: the root check is on. A ref whose bucket records another location is
    a mismatch (the twin that arrives later is born its own bucket by its
    claim, see test_identity_claim)."""
    one = tmp_path / "my" / "app"
    two = tmp_path / "my-app"
    one.mkdir(parents=True)
    two.mkdir()
    ref = identity.claim(str(one))
    (tmp_checkpoint_dir / ref.slug / "root").write_text(
        str(two.resolve()) + "\n", encoding="utf-8")
    with pytest.raises(identity.BucketMismatch):
        store.ensure_bucket(ref)


def test_the_verb_decorator_and_the_capture_handlers_let_it_through(
        tmp_path, tmp_checkpoint_dir):
    @cli.guarded
    def _cmd_probe(args):
        raise identity.BucketMismatch("probe")

    with pytest.raises(identity.BucketMismatch):
        _cmd_probe(None)

    @cli.guarded(writes=True)
    def _cmd_probe_writes(args):
        raise identity.BucketClaimFailed("probe")

    with pytest.raises(identity.BucketClaimFailed):
        _cmd_probe_writes(None)


def _boom(*a, **k):
    raise identity.BucketMismatch("probe")


def _capture_run(tmp_path, monkeypatch):
    from daimon_briefing import capture
    from tests.conftest import make_messages

    monkeypatch.setattr(
        capture.serializer, "serialize_strict",
        lambda *a, **k: {"session_id": "S-1", "working_context": {},
                         "epistemic_snapshot": {}})
    monkeypatch.setattr(capture.store, "write_checkpoint",
                        lambda *a, **k: tmp_path / "out.json")
    return lambda: capture.run("S-1", make_messages(24),
                               project=str(tmp_path), chat=None,
                               deadline=None)


@pytest.mark.parametrize("name", ["_verify_agent_resolutions",
                                  "_verify_agent_amendments",
                                  "_verify_agent_request_done"])
def test_each_capture_verification_pass_re_raises_a_mismatch(
        name, tmp_path, tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import capture

    run = _capture_run(tmp_path, monkeypatch)
    monkeypatch.setattr(capture, name, _boom)
    with pytest.raises(identity.BucketMismatch):
        run()


def test_the_capture_rejection_ledger_re_raises_a_mismatch(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import capture

    run = _capture_run(tmp_path, monkeypatch)
    monkeypatch.setattr(capture.serializer, "verification_rejections",
                        lambda cp: [{"item_ref": "i", "check": "q",
                                     "reason": "r"}])
    monkeypatch.setattr(capture.store, "append_verification", _boom)
    with pytest.raises(identity.BucketMismatch):
        run()


def test_carry_forward_re_raises_a_mismatch(tmp_path, tmp_checkpoint_dir,
                                            monkeypatch):
    from daimon_briefing import capture

    project = str(tmp_path)
    store.write_checkpoint("S-prev", _checkpoint("S-prev"),
                           project_dir=project, writer=Writer.HUMAN)
    monkeypatch.setattr(capture, "_emit_supersede_candidates", _boom)
    with pytest.raises(identity.BucketMismatch):
        capture.carry_forward(_checkpoint("S-new"), project)


def test_the_ruling_echo_filter_re_raises_a_mismatch(tmp_path,
                                                     tmp_checkpoint_dir,
                                                     monkeypatch):
    from daimon_briefing import briefing

    monkeypatch.setattr(briefing, "active_rulings", _boom)
    with pytest.raises(identity.BucketMismatch):
        store._drop_ruling_echoes(_checkpoint("S-1"), str(tmp_path))


# ---- serialize: the spawn line that heal parses ----------------------------


def test_the_host_run_spawn_line_is_byte_identical_for_a_ref_and_a_string(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    proj = tmp_path / "repo"
    proj.mkdir()
    location = str(proj.resolve())
    log = config.log_dir() / "serialize.log"

    ledger.write_host_run_marker("kimi", "S-1", location, "/t/wire.jsonl")
    plain = log.read_text(encoding="utf-8").split(" ", 1)[1]
    log.unlink()
    ref = identity.claim(location)
    ledger.write_host_run_marker("kimi", "S-1", ref, "/t/wire.jsonl")
    carried = log.read_text(encoding="utf-8").split(" ", 1)[1]

    assert carried == plain
    assert f"project: {location})" in carried


def test_a_failed_serialize_leaves_a_line_heal_routes_back_to_the_location(
        tmp_path, tmp_checkpoint_dir):
    """claim, fail the extraction, and heal re-reads the logged string: the plan
    routes to the LOCATION (not a slug, not a repr of the ref), the slug scope
    finds it, and claiming that string again is the same bucket."""
    proj = tmp_path / "repo"
    proj.mkdir()
    location = str(proj.resolve())
    transcript = tmp_path / "S-fail.jsonl"
    transcript.write_text(
        '{"type": "user", "message": {"role": "user", "content": "hi"}}\n',
        encoding="utf-8")
    ref = identity.claim(location)
    ledger.write_host_run_marker("codex", "S-fail", ref, str(transcript))
    log = config.log_dir() / "serialize.log"
    with log.open("a", encoding="utf-8") as f:
        f.write(f"error: boom (transcript: {transcript}) after 3s\n")

    plan = heal.plan_all(log.read_text(encoding="utf-8"), time.time(),
                         slug=ref.slug)

    (target,) = plan.targets
    assert target.project == location
    assert target.project_slug == ref.slug
    again = identity.claim(target.project)
    assert again == ref and again.slug == ref.slug
