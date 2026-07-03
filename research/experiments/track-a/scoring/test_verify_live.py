"""Guard tests for verify_live.py path constants (no live calls)."""

import verify_live


def test_lib_dir_points_at_real_llm_module():
    # _build_live_chat() does `import llm` after inserting LIB_DIR on sys.path;
    # if LIB_DIR is wrong, the live run dies with ModuleNotFoundError.
    assert (verify_live.LIB_DIR / "llm.py").exists()


def test_sessions_dir_is_a_real_dir():
    assert verify_live.SESSIONS_DIR.is_dir()
