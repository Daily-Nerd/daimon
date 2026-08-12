"""`daimon serve` (#670): the CLI front door to the read-only viewer. The
subcommand delegates to daimon_ui.__main__ so there is exactly one config
path — the CLI never grows its own copy of the flag handling."""
from pathlib import Path

import pytest

from daimon_briefing import cli
from daimon_ui.__main__ import build_config


def test_default_port_is_the_viewer_port():
    cfg = build_config([], {}, cwd=Path("/Users/x/proj"))
    assert cfg["port"] == 7717


def test_serve_subcommand_delegates_to_daimon_ui(monkeypatch, tmp_path):
    captured = {}

    def fake_ui_main(argv):
        captured["argv"] = argv
        return 0

    import daimon_ui.__main__ as ui_main
    monkeypatch.setattr(ui_main, "main", fake_ui_main)
    rc = cli.main(["serve", "--no-browser", "--port", "7800",
                   "--data-dir", str(tmp_path)])
    assert rc == 0
    assert "--no-browser" in captured["argv"]
    assert ["--port", "7800"] == captured["argv"][
        captured["argv"].index("--port"):captured["argv"].index("--port") + 2]
    assert str(tmp_path) in captured["argv"]


def test_serve_help_names_read_only(capsys):
    with pytest.raises(SystemExit):
        cli.main(["serve", "--help"])
    out = capsys.readouterr().out
    assert "read-only" in out
