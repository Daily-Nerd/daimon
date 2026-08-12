from pathlib import Path
from daimon_ui.__main__ import build_config


def test_defaults_derive_from_cwd(tmp_path):
    cfg = build_config([], {}, cwd=Path("/Users/x/proj"))
    assert cfg["data_dir"] == Path.home() / ".daimon" / "checkpoints"
    assert cfg["default_slug"] == "-Users-x-proj"
    assert cfg["project_label"] == "proj"
    assert cfg["port"] == 7717 and cfg["open_browser"] is True


def test_flags_override(tmp_path):
    cfg = build_config(
        ["--data-dir", str(tmp_path), "--project-dir", "/a/b", "--port", "7777", "--no-browser"],
        {}, cwd=Path("/elsewhere"))
    assert cfg["data_dir"] == tmp_path
    assert cfg["default_slug"] == "-a-b"
    assert cfg["port"] == 7777 and cfg["open_browser"] is False
