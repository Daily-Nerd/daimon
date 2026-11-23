"""Slice 4b: the real claim, birth, and the root check on `ensure_bucket`.

A claim resolves a location and, when nothing is bound to it, BIRTHS the bucket:
built whole in `.birth/<pid>-<rand>/` with its `root` inside, then renamed to
its final name, so a bucket is never visible without the record that says whom
it belongs to. A name already taken is walked past (seed, `-6hex`, `-12hex`);
the errno discipline separates "taken" from "failing".
"""

import errno
import os
from pathlib import Path

import pytest

from daimon_briefing import hostgit, identity, requests, scope, store
from daimon_briefing.surfaces import Writer

from tests.test_hostgit_sealed import SEALED
from tests.test_identity_resolver import _bucket, _digest


@pytest.fixture
def where(tmp_path):
    p = tmp_path / "work" / "proj"
    p.mkdir(parents=True)
    return str(p.resolve())


def _row():
    return {"ts": "2026-11-20T00:00:00Z", "k": 1}


def _root(bucket):
    return (bucket / "root").read_text(encoding="utf-8").strip()


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(identity.time, "sleep", lambda s: None)


# ---- birth ------------------------------------------------------------------------


def test_a_new_location_is_born_under_its_seed_with_its_root(
        where, tmp_checkpoint_dir):
    ref = identity.claim(where)
    assert ref.slug == store.location_slug(where)
    assert ref.bound is True
    assert _root(tmp_checkpoint_dir / ref.slug) == where


def test_birth_leaves_no_stage_behind(where, tmp_checkpoint_dir):
    identity.claim(where)
    stages = list((tmp_checkpoint_dir / scope.BIRTH).iterdir())
    assert stages == []


def test_a_bucket_is_never_visible_without_its_root(
        where, tmp_checkpoint_dir, monkeypatch):
    """The stage carries `root` BEFORE the rename, and nothing else makes the
    final directory: there is no instant where the name exists unstamped."""
    seen = []
    real = os.rename

    def spy(src, dst, *a, **kw):
        seen.append((Path(src).parent.name, (Path(src) / "root").is_file(),
                     os.path.lexists(dst)))
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(identity.os, "rename", spy)
    identity.claim(where)
    assert seen == [(scope.BIRTH, True, False)]


def test_claiming_twice_is_one_bucket(where, tmp_checkpoint_dir):
    first = identity.claim(where)
    second = identity.claim(where)
    assert (first.slug, first.bound) == (second.slug, second.bound)
    assert [p.name for p in tmp_checkpoint_dir.iterdir()
            if p.name != scope.BIRTH] == [first.slug]


def test_a_location_bound_by_its_root_claims_that_bucket(
        where, tmp_checkpoint_dir):
    _bucket(tmp_checkpoint_dir, "-permanent-name", where)
    ref = identity.claim(where)
    assert ref.slug == "-permanent-name"
    assert [p.name for p in tmp_checkpoint_dir.iterdir()] == [
        "-permanent-name"]


def test_an_unstamped_legacy_bucket_is_stamped_by_the_claim(
        where, tmp_checkpoint_dir):
    seed = store.location_slug(where)
    legacy = _bucket(tmp_checkpoint_dir, seed)
    ref = identity.claim(where)
    assert ref.slug == seed
    assert _root(legacy) == where


def test_an_empty_seed_directory_is_stamped_not_replaced(
        where, tmp_checkpoint_dir):
    """POSIX `rename` onto an EMPTY directory silently replaces it: the claim
    must not race that, it stamps what is there."""
    seed = store.location_slug(where)
    legacy = _bucket(tmp_checkpoint_dir, seed)
    before = legacy.stat().st_ino
    identity.claim(where)
    assert legacy.stat().st_ino == before


def test_a_twin_is_born_under_a_suffixed_name_and_never_shares(
        tmp_path, tmp_checkpoint_dir):
    one = tmp_path / "my" / "app"
    two = tmp_path / "my-app"
    one.mkdir(parents=True)
    two.mkdir()
    assert (store.location_slug(str(one.resolve()))
            == store.location_slug(str(two.resolve())))
    first = identity.claim(str(one))
    second = identity.claim(str(two))
    assert first.slug != second.slug
    assert second.slug == f"{first.slug}-{_digest(str(two.resolve()))[:6]}"
    assert _root(tmp_checkpoint_dir / first.slug) == str(one.resolve())
    assert _root(tmp_checkpoint_dir / second.slug) == str(two.resolve())
    # and each keeps finding its own, claim after claim
    assert identity.claim(str(one)).slug == first.slug
    assert identity.claim(str(two)).slug == second.slug


def test_a_merged_away_name_is_skipped_by_birth(where, tmp_checkpoint_dir):
    seed = store.location_slug(where)
    tmp_checkpoint_dir.mkdir(parents=True)
    (tmp_checkpoint_dir / "migrations.jsonl").write_text(
        '{"mode": "merge", "from_slug": "%s", "to_slug": "-elsewhere", '
        '"complete": true}\n' % seed, encoding="utf-8")
    ref = identity.claim(where)
    assert ref.slug == f"{seed}-{_digest(where)[:6]}"
    assert not (tmp_checkpoint_dir / seed).exists()


def test_all_three_names_taken_fails_loudly(where, tmp_checkpoint_dir):
    seed = store.location_slug(where)
    for name in (seed, f"{seed}-{_digest(where)[:6]}",
                 f"{seed}-{_digest(where)[:12]}"):
        _bucket(tmp_checkpoint_dir, name, "/other/" + name[-6:])
    with pytest.raises(identity.BucketClaimFailed):
        identity.claim(where)


def test_losing_the_rename_to_another_location_takes_the_next_name(
        where, tmp_checkpoint_dir, monkeypatch):
    """A twin wins the seed between the free check and the rename: ENOTEMPTY
    means TAKEN, so the claim reads whose it is and walks on."""
    seed = store.location_slug(where)
    real = os.rename
    fired = []

    def racing(src, dst, *a, **kw):
        if not fired and Path(dst).name == seed:
            fired.append(1)
            _bucket(tmp_checkpoint_dir, seed, "/the/twin/that/won")
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(identity.os, "rename", racing)
    ref = identity.claim(where)
    assert ref.slug == f"{seed}-{_digest(where)[:6]}"
    assert _root(tmp_checkpoint_dir / seed) == "/the/twin/that/won"
    assert list((tmp_checkpoint_dir / scope.BIRTH).iterdir()) == []


def test_losing_the_rename_to_the_same_location_is_a_win(
        where, tmp_checkpoint_dir, monkeypatch):
    """Two processes of one location birth together: the loser finds the
    winner's bucket already bound to it and uses it, creating nothing more."""
    seed = store.location_slug(where)
    real = os.rename
    fired = []

    def racing(src, dst, *a, **kw):
        if not fired:
            fired.append(1)
            _bucket(tmp_checkpoint_dir, seed, where)
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(identity.os, "rename", racing)
    ref = identity.claim(where)
    assert ref.slug == seed
    assert [p.name for p in tmp_checkpoint_dir.iterdir()
            if p.name != scope.BIRTH] == [seed]


@pytest.mark.parametrize("code", [errno.ENOTEMPTY, errno.EEXIST])
def test_only_the_exists_errnos_mean_taken(code, where, tmp_checkpoint_dir,
                                           monkeypatch):
    seed = store.location_slug(where)
    real = os.rename
    fired = []

    def racing(src, dst, *a, **kw):
        if not fired:
            fired.append(1)
            _bucket(tmp_checkpoint_dir, seed, "/another/place")
            raise OSError(code, os.strerror(code))
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(identity.os, "rename", racing)
    assert identity.claim(where).slug == f"{seed}-{_digest(where)[:6]}"


def test_any_other_rename_failure_retries_three_times_then_raises(
        where, tmp_checkpoint_dir, monkeypatch):
    attempts = []

    def failing(src, dst, *a, **kw):
        attempts.append(1)
        raise PermissionError(errno.EACCES, "an open handle")

    monkeypatch.setattr(identity.os, "rename", failing)
    with pytest.raises(identity.BucketClaimFailed):
        identity.claim(where)
    assert len(attempts) == 3
    assert list((tmp_checkpoint_dir / scope.BIRTH).iterdir()) == []


def test_a_transient_rename_failure_that_clears_is_not_an_error(
        where, tmp_checkpoint_dir, monkeypatch):
    real = os.rename
    attempts = []

    def flaky(src, dst, *a, **kw):
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError(errno.EACCES, "busy")
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(identity.os, "rename", flaky)
    assert identity.claim(where).slug == store.location_slug(where)


def test_a_store_that_cannot_hold_a_stage_defers_like_before(
        where, tmp_checkpoint_dir):
    tmp_checkpoint_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_checkpoint_dir.write_text("a file where the store should be")
    ref = identity.claim(where)
    assert ref is not None and ref.slug == store.location_slug(where)
    assert requests.append(_row(), project_dir=ref,
                           writer=Writer.HUMAN) is False


def test_the_kill_switch_births_nothing(where, tmp_checkpoint_dir,
                                        monkeypatch):
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    ref = identity.claim(where)
    assert ref.slug and not tmp_checkpoint_dir.exists()


def test_a_slug_routed_claim_names_no_root(tmp_checkpoint_dir):
    ref = identity.claim("-routed-by-slug")
    assert ref.bound is False
    assert (tmp_checkpoint_dir / "-routed-by-slug").is_dir()
    assert not (tmp_checkpoint_dir / "-routed-by-slug" / "root").exists()


def test_a_claim_is_one_scan(where, tmp_checkpoint_dir, monkeypatch):
    _bucket(tmp_checkpoint_dir, "-permanent-name", where)
    calls = []
    real = identity._scan
    monkeypatch.setattr(identity, "_scan", lambda: calls.append(1) or real())
    identity.claim(where)
    assert len(calls) == 1


# ---- the root check ------------------------------------------------------------------


def test_ensure_bucket_raises_when_the_root_now_names_another_location(
        where, tmp_checkpoint_dir):
    ref = identity.claim(where)
    (tmp_checkpoint_dir / ref.slug / "root").write_text(
        "/rebound/elsewhere\n", encoding="utf-8")
    with pytest.raises(identity.BucketMismatch):
        store.ensure_bucket(ref)
    with pytest.raises(identity.BucketMismatch):
        requests.append(_row(), project_dir=ref, writer=Writer.HUMAN)


def test_a_mismatch_message_never_prints_the_other_location(
        where, tmp_checkpoint_dir):
    ref = identity.claim(where)
    (tmp_checkpoint_dir / ref.slug / "root").write_text(
        "/rebound/elsewhere\n", encoding="utf-8")
    with pytest.raises(identity.BucketMismatch) as caught:
        identity.verify(ref)
    assert "/rebound/elsewhere" not in str(caught.value)


def test_a_bucket_with_no_root_verifies(where, tmp_checkpoint_dir):
    ref = identity.claim(where)
    (tmp_checkpoint_dir / ref.slug / "root").unlink()
    identity.verify(ref)
    assert store.ensure_bucket(ref) == tmp_checkpoint_dir / ref.slug


def test_a_slug_ref_always_verifies(tmp_checkpoint_dir):
    identity.verify(identity.claim("-routed-by-slug"))


def test_a_declared_root_is_not_root_checked(tmp_path, tmp_checkpoint_dir):
    """The host named that bucket itself (SEALED): two roots it declares under
    one slug are its decision, not a mismatch."""
    home = tmp_path / "home"
    (home / "a").mkdir(parents=True)
    hostgit.configure(posture=SEALED, ceiling=home)
    try:
        where = str((home / "a").resolve())
        hostgit.declare(home / "a", slug="acme-widget")
        ref = identity.claim(where)
        assert ref.slug == "acme-widget"
        (tmp_checkpoint_dir / "acme-widget" / "root").write_text(
            "/some/other/clone\n", encoding="utf-8")
        identity.verify(ref)
    finally:
        hostgit.configure(posture=hostgit.Posture.AMBIENT)


def test_a_twin_writes_to_its_own_bucket_not_the_first_ones(
        tmp_path, tmp_checkpoint_dir):
    one = tmp_path / "my" / "app"
    two = tmp_path / "my-app"
    one.mkdir(parents=True)
    two.mkdir()
    assert requests.append(_row(), project_dir=str(one), writer=Writer.HUMAN)
    assert requests.append(_row(), project_dir=str(two), writer=Writer.HUMAN)
    buckets = [p for p in tmp_checkpoint_dir.iterdir()
               if p.name != scope.BIRTH]
    assert len(buckets) == 2
    for b in buckets:
        assert len(requests_lines(b)) == 1


def requests_lines(bucket):
    return [ln for ln in (bucket / "requests.jsonl").read_text(
        encoding="utf-8").splitlines() if ln]


def test_the_claim_is_loud_through_the_best_effort_handlers(
        where, tmp_checkpoint_dir, monkeypatch):
    """`BucketClaimFailed` is not an `OSError`: a writer's best-effort handler
    lets it through instead of reporting a quiet False."""
    monkeypatch.setattr(identity.os, "rename",
                        lambda *a, **k: (_ for _ in ()).throw(
                            PermissionError(errno.EACCES, "no")))
    with pytest.raises(identity.BucketClaimFailed):
        requests.append(_row(), project_dir=where, writer=Writer.HUMAN)

