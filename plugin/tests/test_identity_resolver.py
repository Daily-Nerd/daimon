"""Slice 4b of "project identity survives moves" (#1384): the real resolver.

`store.bucket_of` of an absolute location asks `identity.resolve`, which answers
from the buckets' recorded roots instead of from the path's slug alone. These
tests pin the steps in the order the design fixes them, the purity of tenant
scope and SEALED, and the shared index (built once per operation, never kept
between operations, a miss never remembered).
"""

import hashlib
import os

import pytest

from daimon_briefing import config, hostgit, identity, scope, store

from tests.test_hostgit_sealed import SEALED


@pytest.fixture
def where(tmp_path):
    """A resolved project location that exists."""
    p = tmp_path / "work" / "proj"
    p.mkdir(parents=True)
    return str(p.resolve())


def _bucket(root_dir, name, root=None):
    d = root_dir / name
    d.mkdir(parents=True, exist_ok=True)
    if root is not None:
        (d / "root").write_text(f"{root}\n", encoding="utf-8")
    return d


def _digest(location):
    return hashlib.sha256(
        os.path.normcase(os.path.realpath(location)).encode()).hexdigest()


# ---- the steps ---------------------------------------------------------------


def test_a_location_nobody_has_seen_is_unbound_and_named_by_its_seed(
        where, tmp_checkpoint_dir):
    got = identity.resolve(where)
    assert got == identity.Resolution(store.location_slug(where), False)
    assert store.bucket_of(where) == store.location_slug(where)


def test_step_2_a_stamped_seed_bucket_naming_this_location_is_bound(
        where, tmp_checkpoint_dir):
    seed = store.location_slug(where)
    _bucket(tmp_checkpoint_dir, seed, where)
    assert identity.resolve(where) == identity.Resolution(seed, True)


def test_step_3_a_bucket_bound_to_this_location_by_its_root_is_found(
        where, tmp_checkpoint_dir):
    _bucket(tmp_checkpoint_dir, "-permanent-name", where)
    assert identity.resolve(where) == identity.Resolution(
        "-permanent-name", True)
    assert store.bucket_of(where) == "-permanent-name"


def test_the_scan_outranks_an_unstamped_directory_at_the_seed(
        where, tmp_checkpoint_dir):
    """An unstamped stray at the seed name must not capture a location that a
    stamped bucket is bound to (the rebind the stray would shadow)."""
    _bucket(tmp_checkpoint_dir, store.location_slug(where))
    _bucket(tmp_checkpoint_dir, "-the-real-one", where)
    assert store.bucket_of(where) == "-the-real-one"


def test_step_3_two_buckets_with_one_root_return_neither(
        where, tmp_checkpoint_dir):
    _bucket(tmp_checkpoint_dir, "-one", where)
    _bucket(tmp_checkpoint_dir, "-two", where)
    got = identity.resolve(where)
    assert got.slug not in ("-one", "-two")
    assert got.bound is False


def test_step_4_an_unstamped_seed_directory_is_the_legacy_bucket(
        where, tmp_checkpoint_dir):
    seed = store.location_slug(where)
    _bucket(tmp_checkpoint_dir, seed)
    assert identity.resolve(where) == identity.Resolution(seed, True)
    (tmp_checkpoint_dir / seed / "root").write_text("", encoding="utf-8")
    assert identity.resolve(where) == identity.Resolution(seed, True)


def test_step_5_a_twin_finds_its_seed_taken_and_is_unbound(
        where, tmp_checkpoint_dir):
    seed = store.location_slug(where)
    _bucket(tmp_checkpoint_dir, seed, "/some/other/location")
    got = identity.resolve(where)
    assert got.bound is False
    assert got.slug == f"{seed}-{_digest(where)[:6]}"


def test_a_taken_second_name_falls_to_twelve_hex(where, tmp_checkpoint_dir):
    seed = store.location_slug(where)
    _bucket(tmp_checkpoint_dir, seed, "/other/one")
    _bucket(tmp_checkpoint_dir, f"{seed}-{_digest(where)[:6]}", "/other/two")
    assert identity.resolve(where).slug == f"{seed}-{_digest(where)[:12]}"


def test_every_candidate_taken_reads_as_unknown(where, tmp_checkpoint_dir):
    seed = store.location_slug(where)
    for name in (seed, f"{seed}-{_digest(where)[:6]}",
                 f"{seed}-{_digest(where)[:12]}"):
        _bucket(tmp_checkpoint_dir, name, "/other/" + name[-6:])
    assert identity.resolve(where) == identity.Resolution(None, False)
    assert store.bucket_of(where) is None


def test_a_merged_away_name_is_never_a_birth_name(where, tmp_checkpoint_dir):
    """Alias rows are keyed by name: reusing a merged-away bucket's name would
    fold the new project into the old one in recall and request routing."""
    seed = store.location_slug(where)
    tmp_checkpoint_dir.mkdir(parents=True, exist_ok=True)
    (tmp_checkpoint_dir / "migrations.jsonl").write_text(
        '{"mode": "merge", "from_slug": "%s", "to_slug": "-elsewhere", '
        '"complete": true}\n' % seed, encoding="utf-8")
    got = identity.resolve(where)
    assert got.bound is False
    assert got.slug == f"{seed}-{_digest(where)[:6]}"


def test_a_very_long_seed_still_yields_valid_bucket_names(
        tmp_checkpoint_dir):
    where = "/" + "a" * 249
    seed = store.location_slug(where)
    assert len(seed) == 250
    _bucket(tmp_checkpoint_dir, seed, "/other")
    got = identity.resolve(where)
    assert got.bound is False
    assert len(got.slug) <= 255 and scope.parse_bucket(got.slug) is not None


def test_a_location_too_long_to_be_a_directory_name_never_raises(
        tmp_checkpoint_dir):
    where = "/" + "a" * 400
    assert identity.resolve(where).bound is False


def test_a_non_path_is_a_slug_and_passes_through(tmp_checkpoint_dir):
    assert store.bucket_of("-Users-b-code-x") == "-Users-b-code-x"
    assert store.bucket_of(None) is None
    assert identity.resolve("") == identity.Resolution(None, False)


# ---- purity: tenant scope and SEALED ---------------------------------------------


def test_tenant_scope_never_scans_other_buckets(
        where, tmp_checkpoint_dir, monkeypatch):
    """A planted bucket named anything, whose root IS this tenant's directory,
    is not returned: only the seed's own record is consulted (scar 0071)."""
    _bucket(tmp_checkpoint_dir, "-foreign-plant", where)
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    got = identity.resolve(where)
    assert got.slug == store.location_slug(where)
    assert got.bound is False

    def no_scan():
        raise AssertionError("the tenant resolver scanned the store")

    monkeypatch.setattr(identity, "_scan", no_scan)
    assert store.bucket_of(where) == store.location_slug(where)


def test_tenant_scope_still_reads_the_seed_probe_and_unstamped_dir(
        where, tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    seed = store.location_slug(where)
    _bucket(tmp_checkpoint_dir, seed, where)
    assert identity.resolve(where) == identity.Resolution(seed, True)


def test_sealed_never_scans_and_a_declared_root_wins(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    repo = home / "r"
    repo.mkdir()
    hostgit.configure(posture=SEALED, ceiling=home)
    try:
        where = str(repo.resolve())
        _bucket(tmp_checkpoint_dir, "-foreign-plant", where)

        def no_scan():
            raise AssertionError("the SEALED resolver scanned the store")

        monkeypatch.setattr(identity, "_scan", no_scan)
        assert store.bucket_of(where) == store.location_slug(where)
        hostgit.declare(repo, slug="acme-widget")
        assert identity.resolve(where) == identity.Resolution(
            "acme-widget", True)
    finally:
        hostgit.configure(posture=hostgit.Posture.AMBIENT)


# ---- the shared index -------------------------------------------------------------


@pytest.fixture
def scans(monkeypatch):
    calls = []
    real = identity._scan

    def counting():
        calls.append(1)
        return real()

    monkeypatch.setattr(identity, "_scan", counting)
    return calls


def test_one_operation_scans_once_however_often_it_resolves(
        where, tmp_checkpoint_dir, scans):
    _bucket(tmp_checkpoint_dir, "-permanent-name", where)
    with identity.operation():
        for _ in range(5):
            assert store.bucket_of(where) == "-permanent-name"
    assert len(scans) == 1


def test_the_index_is_not_kept_between_operations(
        where, tmp_checkpoint_dir, scans):
    _bucket(tmp_checkpoint_dir, "-permanent-name", where)
    with identity.operation():
        store.bucket_of(where)
    with identity.operation():
        store.bucket_of(where)
    assert len(scans) == 2


def test_outside_an_operation_nothing_is_cached(
        where, tmp_checkpoint_dir, scans):
    _bucket(tmp_checkpoint_dir, "-permanent-name", where)
    store.bucket_of(where)
    store.bucket_of(where)
    assert len(scans) == 2


def test_a_rewritten_root_is_seen_by_the_next_operation(
        where, tmp_checkpoint_dir):
    b = _bucket(tmp_checkpoint_dir, "-permanent-name", where)
    with identity.operation():
        assert store.bucket_of(where) == "-permanent-name"
    (b / "root").write_text("/moved/away\n", encoding="utf-8")
    with identity.operation():
        assert store.bucket_of(where) == store.location_slug(where)


def test_an_unbound_answer_is_not_remembered_inside_an_operation(
        where, tmp_checkpoint_dir):
    """Negatives are never cached: a bucket bound while an operation is open is
    found by the next resolution in it."""
    with identity.operation():
        assert store.bucket_of(where) == store.location_slug(where)
        _bucket(tmp_checkpoint_dir, "-bound-meanwhile", where)
        identity.invalidate()
        assert store.bucket_of(where) == "-bound-meanwhile"


def test_the_probe_alone_costs_no_scan(where, tmp_checkpoint_dir, scans):
    seed = store.location_slug(where)
    _bucket(tmp_checkpoint_dir, seed, where)
    store.bucket_of(where)
    assert scans == []


def test_the_scan_skips_strays_files_and_internal_directories(
        where, tmp_checkpoint_dir):
    _bucket(tmp_checkpoint_dir, scope.BIRTH, where)
    _bucket(tmp_checkpoint_dir, "not a bucket.name", where)
    (tmp_checkpoint_dir / "a-file").write_text("x", encoding="utf-8")
    assert identity._scan() == {}
    _bucket(tmp_checkpoint_dir, "-real", where)
    assert identity._scan() == {os.path.normcase(where): ["-real"]}


def test_a_missing_checkpoint_dir_scans_empty(where, tmp_checkpoint_dir):
    assert not tmp_checkpoint_dir.exists()
    assert identity._scan() == {}


def test_a_bad_posture_value_never_raises_and_is_treated_as_sealed(
        where, tmp_checkpoint_dir, monkeypatch):
    _bucket(tmp_checkpoint_dir, "-planted", where)
    monkeypatch.setenv("DAIMON_GIT_POSTURE", "nonsense")
    config.resolve_project_root.cache_clear()
    assert identity.resolve(where).slug == store.location_slug(where)
