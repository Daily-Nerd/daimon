"""#319: the bench must key and pin the scene-traces flag (#317).

Twin of the carry-axis tests (#274) in test_bench_carry.py: a scene-on run
must never read a scene-off cache entry, the env pin must make the host's
ambient DAIMON_SCENE_TRACES (env var or ~/.daimon/env) invisible to the
measurement, and the result stamp must record which mode produced the number.
"""

from tests.bench import adapter
from tests.bench import cache as cache_mod
from tests.bench import run as bench_run


class TestConfigStamp:
    def test_stamp_records_scene_on_and_off(self, tmp_path):
        ds = tmp_path / "dataset.json"
        ds.write_text("[]", encoding="utf-8")
        on = bench_run.build_parser().parse_args(["--scene"])
        off = bench_run.build_parser().parse_args([])
        assert bench_run._build_config_stamp(on, ds)["scene"] == "on"
        assert bench_run._build_config_stamp(off, ds)["scene"] == "off"


class TestCacheSeparation:
    def test_scene_modes_never_share_a_key(self):
        msgs = [{"role": "user", "content": "hello"}]
        base = dict(backend="b", model="m", prompt_version="v")
        assert cache_mod.cache_key(msgs, scene="on", **base) != \
            cache_mod.cache_key(msgs, scene="off", **base)

    def test_scene_off_key_is_backward_compatible(self):
        # Pre-#319 entries (keyed without a scene axis) must stay valid for
        # scene-off runs — the default and the explicit "off" are one key space.
        msgs = [{"role": "user", "content": "hello"}]
        base = dict(backend="b", model="m", prompt_version="v")
        assert cache_mod.cache_key(msgs, **base) == \
            cache_mod.cache_key(msgs, scene="off", **base)

    def test_scene_and_carry_axes_compose(self):
        msgs = [{"role": "user", "content": "hello"}]
        base = dict(backend="b", model="m", prompt_version="v")
        assert cache_mod.cache_key(msgs, carry="on", scene="on", **base) != \
            cache_mod.cache_key(msgs, carry="on", scene="off", **base)


class TestEnvPinning:
    def test_scene_env_var_follows_the_mode(self, tmp_path):
        on = adapter._question_env(tmp_path, "q", "2", scene_on=True)
        off = adapter._question_env(tmp_path, "q", "2", scene_on=False)
        assert on["DAIMON_SCENE_TRACES"] == "1"
        assert off["DAIMON_SCENE_TRACES"] == "0"

    def test_scene_env_is_pinned_by_default(self, tmp_path):
        # The host machine may carry DAIMON_SCENE_TRACES=1 in its env file
        # (the #317 field experiment does) — the default bench env must pin
        # it OFF explicitly, or a "baseline" silently runs with scenes.
        env = adapter._question_env(tmp_path, "q", "2")
        assert env["DAIMON_SCENE_TRACES"] == "0"
        assert "DAIMON_SCENE_TRACES" in adapter._ENV_KEYS
