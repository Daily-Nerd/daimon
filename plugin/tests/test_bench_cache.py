"""Unit tests for the serialized-checkpoint cache (#267)."""

import json

from tests.bench import cache


def _messages(a="hi", b="there"):
    return [{"role": "user", "content": a}, {"role": "assistant", "content": b}]


class TestCacheKey:
    def test_key_is_stable_for_same_inputs(self):
        k1 = cache.cache_key(_messages(), backend="litellm", model="m1", prompt_version="D-013")
        k2 = cache.cache_key(_messages(), backend="litellm", model="m1", prompt_version="D-013")
        assert k1 == k2

    def test_key_changes_with_message_content(self):
        k1 = cache.cache_key(_messages("hi"), backend="b", model="m", prompt_version="v")
        k2 = cache.cache_key(_messages("bye"), backend="b", model="m", prompt_version="v")
        assert k1 != k2

    def test_key_changes_with_backend_model_and_prompt_version(self):
        base = dict(backend="b", model="m", prompt_version="v")
        k0 = cache.cache_key(_messages(), **base)
        assert cache.cache_key(_messages(), **{**base, "backend": "other"}) != k0
        assert cache.cache_key(_messages(), **{**base, "model": "other"}) != k0
        assert cache.cache_key(_messages(), **{**base, "prompt_version": "other"}) != k0

    def test_key_is_order_sensitive_on_turns(self):
        fwd = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        rev = [{"role": "assistant", "content": "b"}, {"role": "user", "content": "a"}]
        args = dict(backend="b", model="m", prompt_version="v")
        assert cache.cache_key(fwd, **args) != cache.cache_key(rev, **args)


class TestCacheRoundTrip:
    def test_miss_then_hit(self, tmp_path):
        c = cache.CheckpointCache(tmp_path)
        key = "abc123"
        assert c.get(key) is None
        checkpoint = {"session_id": "s1", "working_context": {}}
        c.put(key, checkpoint)
        got = c.get(key)
        assert got == checkpoint

    def test_get_returns_a_copy_not_the_stored_object(self, tmp_path):
        c = cache.CheckpointCache(tmp_path)
        c.put("k", {"session_id": "s1", "n": [1, 2]})
        got = c.get("k")
        got["n"].append(3)
        # mutating the returned dict must not corrupt the on-disk cache
        assert c.get("k")["n"] == [1, 2]

    def test_persists_across_instances(self, tmp_path):
        cache.CheckpointCache(tmp_path).put("k", {"session_id": "s1"})
        assert cache.CheckpointCache(tmp_path).get("k") == {"session_id": "s1"}

    def test_corrupt_entry_is_a_miss_not_a_crash(self, tmp_path):
        c = cache.CheckpointCache(tmp_path)
        (tmp_path / "deadbeef.json").write_text("{not json", encoding="utf-8")
        assert c.get("deadbeef") is None

    def test_stats_track_hits_and_misses(self, tmp_path):
        c = cache.CheckpointCache(tmp_path)
        c.get("missing")            # miss
        c.put("k", {"session_id": "s"})
        c.get("k")                  # hit
        assert c.hits == 1
        assert c.misses == 1

    def test_put_writes_valid_json(self, tmp_path):
        c = cache.CheckpointCache(tmp_path)
        c.put("k", {"session_id": "s1"})
        stored = json.loads((tmp_path / "k.json").read_text(encoding="utf-8"))
        assert stored["session_id"] == "s1"
