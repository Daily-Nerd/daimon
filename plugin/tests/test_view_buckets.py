"""`view.buckets` and `view.bucket_exists` (#1132 PR 8a).

The one place that enumerates checkpoint buckets and applies tenant scope
(#899): a tenant-scoped home sees the caller's own bucket and no other. Names
only; no checkpoint body is read."""

import json

import pytest

from daimon_briefing import config, store, view


def _bucket(name, *, pointer=True):
    d = config.checkpoint_dir() / name
    d.mkdir(parents=True)
    if pointer:
        (d / "latest.json").write_text(json.dumps({"session_id": "S"}))
    return d


def test_buckets_lists_every_bucket_with_a_pointer_sorted(tmp_checkpoint_dir):
    _bucket("-b")
    _bucket("-a")
    _bucket("-no-pointer", pointer=False)
    (tmp_checkpoint_dir / "S-flat.json").write_text("{}")
    assert view.buckets("-a") == ("-a", "-b")


def test_buckets_lists_a_torn_pointer_too(tmp_checkpoint_dir):
    d = _bucket("-torn", pointer=False)
    (d / "latest.json").write_text("{nope")
    assert view.buckets("-torn") == ("-torn",)


def test_buckets_of_a_missing_dir_is_empty(tmp_checkpoint_dir):
    assert view.buckets("-a") == ()


def test_buckets_agree_with_the_store_listing(tmp_checkpoint_dir):
    for n in ("-a", "-b", "-c"):
        _bucket(n)
    assert view.buckets("-a") == tuple(
        sorted(b["slug"] for b in store.list_buckets()))


def test_tenant_scope_yields_only_the_callers_own_bucket(
        tmp_checkpoint_dir, monkeypatch):
    _bucket("-a")
    _bucket("-b")
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    assert view.buckets("-a") == ("-a",)
    assert view.buckets("-gone") == ()


def test_tenant_scope_without_an_own_slug_yields_nothing(
        tmp_checkpoint_dir, monkeypatch):
    _bucket("-a")
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    assert view.buckets(None) == ()


def test_bucket_exists_is_a_pointer_check(tmp_checkpoint_dir):
    _bucket("-a")
    _bucket("-np", pointer=False)
    assert view.bucket_exists("-a", "-x") is True
    assert view.bucket_exists("-np", "-x") is False
    assert view.bucket_exists("-missing", "-x") is False


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "../x", "a\\b", "-a/.."])
def test_bucket_exists_refuses_a_name_that_is_not_one_path_segment(
        tmp_checkpoint_dir, bad):
    _bucket("-a")
    assert view.bucket_exists(bad, "-a") is False


def test_bucket_exists_follows_the_tenant_rule(tmp_checkpoint_dir, monkeypatch):
    _bucket("-a")
    _bucket("-b")
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    assert view.bucket_exists("-a", "-a") is True
    assert view.bucket_exists("-b", "-a") is False
    assert view.bucket_exists("-a", None) is False
