"""`store.all_forgotten_content_keys` is memoized per process and
`foreign_forgotten_content_keys` forks nothing without a sidecar (#1132 PR 7a)."""

import json

from daimon_briefing import config, jsonl, store


def _bucket(root, name, status):
    bucket = root / name
    bucket.mkdir(parents=True, exist_ok=True)
    (bucket / "latest.json").write_text("{}")
    row = {"ts": "2026-01-01T00:00:00Z", "item_ref": "o-aaaaaa",
           "status": status, "item_text": "x"}
    with open(bucket / "events.jsonl", "a") as handle:
        handle.write(json.dumps(row) + "\n")


def test_the_union_is_memoized_until_a_ledger_changes(tmp_checkpoint_dir,
                                                      monkeypatch):
    _bucket(tmp_checkpoint_dir, "one", "forgotten:aaaa")
    assert store.all_forgotten_content_keys() == {"aaaa"}
    calls = []
    real = jsonl.read

    def read(path, *a, **k):
        if path.name == "events.jsonl":
            calls.append(path.parent.name)
        return real(path, *a, **k)

    monkeypatch.setattr(jsonl, "read", read)
    assert store.all_forgotten_content_keys() == {"aaaa"}
    assert calls == []                         # served from the memo
    _bucket(tmp_checkpoint_dir, "two", "forgotten:bbbb")
    assert store.all_forgotten_content_keys() == {"aaaa", "bbbb"}
    assert sorted(calls) == ["one", "two"]     # a new bucket recomputes
    calls.clear()
    with open(tmp_checkpoint_dir / "one" / "events.jsonl", "a") as handle:
        handle.write(json.dumps({"ts": "2026-02-01T00:00:00Z",
                                 "item_ref": "o-aaaaaa", "status": "reopened",
                                 "item_text": "x"}) + "\n")
    assert store.all_forgotten_content_keys() == {"bbbb"}   # reopen lifts it
    assert calls                                # an appended row recomputes


def test_the_memo_hands_out_copies(tmp_checkpoint_dir):
    _bucket(tmp_checkpoint_dir, "one", "forgotten:aaaa")
    first = store.all_forgotten_content_keys()
    first.add("poison")
    assert store.all_forgotten_content_keys() == {"aaaa"}


def test_a_missing_checkpoint_root_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "nope"))
    assert store.all_forgotten_content_keys() == set()


def test_foreign_keys_do_not_resolve_the_author_without_a_sidecar(
        monkeypatch):
    def boom():
        raise AssertionError("author resolved with no sidecar")
    monkeypatch.setattr(config, "author", boom)
    assert store.foreign_forgotten_content_keys() == set()
