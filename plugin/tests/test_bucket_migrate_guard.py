"""The `migrate` guard and the store helpers the repair tools share (slice 3b).

`bucket migrate` computes its target from the two slug RULES. Those rules
cannot see a rebind, so a directory whose recorded root lives in a third bucket
would be split by a move into the rule's name. The guard refuses and names the
verb that folds them on purpose.
"""

import pytest

from daimon_briefing import buckets, config, store
from tests.test_bucket_merge import _dir, _snapshot
from tests.test_bucket_migration import _row


@pytest.fixture
def linked(tmp_path, tmp_checkpoint_dir):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    legacy = _dir(buckets.legacy_slug(str(link)))
    legacy.mkdir(parents=True)
    (legacy / "requests.jsonl").write_text(_row("x"), encoding="utf-8")
    return str(link), str(real.resolve())


def test_migrate_refuses_when_the_directory_is_bound_to_a_third_bucket(linked):
    link, real = linked
    third = _dir("-rebound-elsewhere")
    third.mkdir()
    (third / "root").write_text(real + "\n", encoding="utf-8")
    before = _snapshot()
    with pytest.raises(buckets.MigrationError,
                       match="-rebound-elsewhere.*bucket merge"):
        buckets.migrate(link)
    assert _snapshot() == before


def test_migrate_is_not_blocked_by_an_unrelated_bucket_root(linked):
    link, _real = linked
    other = _dir("-other")
    other.mkdir()
    (other / "root").write_text("/not/this/one\n", encoding="utf-8")
    assert buckets.migrate(link)["mode"] == "rename"


def test_root_eq_is_normcase_string_equality_and_never_stats(tmp_path):
    ghost = str(tmp_path / "does" / "not" / "exist")
    assert store.root_eq(ghost, ghost)
    assert not store.root_eq(ghost, ghost + "x")
    assert not store.root_eq("", "")
    assert not store.root_eq(None, ghost)


def test_bucket_roots_lists_only_stamped_buckets(tmp_checkpoint_dir):
    for name, text in (("-stamped", "/a/b\n"), ("-empty", "")):
        d = _dir(name)
        d.mkdir(parents=True)
        (d / "root").write_text(text, encoding="utf-8")
    _dir("-unstamped").mkdir()
    assert store.bucket_roots() == {"-stamped": "/a/b"}
    assert config.checkpoint_dir() == tmp_checkpoint_dir
