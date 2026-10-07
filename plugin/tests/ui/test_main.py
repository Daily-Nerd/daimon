from pathlib import Path
from daimon_briefing import config, store
from daimon_ui.__main__ import build_config


def test_defaults_derive_from_cwd(tmp_path):
    cfg = build_config([], cwd=Path("/Users/x/proj"))
    assert cfg["data_dir"] == config.checkpoint_dir()
    assert cfg["default_slug"] == "-Users-x-proj"
    assert cfg["project_label"] == "proj"
    assert cfg["port"] == 7717 and cfg["open_browser"] is True


def test_the_default_data_dir_honors_the_env_file(tmp_path, monkeypatch):
    """The store is `config.checkpoint_dir()`, the one answer every other
    reader gives: a `DAIMON_CHECKPOINT_DIR` set in the env file counts, not
    only one in the process environment."""
    elsewhere = tmp_path / "from-env-file"
    env_file = tmp_path / "env"
    env_file.write_text(f"DAIMON_CHECKPOINT_DIR={elsewhere}\n")
    monkeypatch.delenv("DAIMON_CHECKPOINT_DIR")
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
    cfg = build_config([], cwd=Path("/Users/x/proj"))
    assert cfg["data_dir"] == elsewhere


def test_the_default_project_is_the_one_the_cli_picks(tmp_path):
    """#948: a symlinked or nested working directory names the same bucket the
    CLI would, not a character transform of the path it was started in."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    cfg = build_config([], cwd=link)
    assert cfg["default_slug"] == store.project_slug(str(real.resolve()))
    assert cfg["default_slug"] != store.project_slug(str(link))
    cfg = build_config(["--project-dir", str(link)], cwd=Path("/elsewhere"))
    assert cfg["default_slug"] == store.project_slug(str(real.resolve()))


def test_a_slug_shaped_project_dir_is_a_path_not_a_bucket_name(tmp_path):
    cfg = build_config(["--project-dir=-Users-x-secret"], cwd=tmp_path)
    assert cfg["default_slug"] != "-Users-x-secret"
    assert cfg["default_slug"].endswith("-Users-x-secret")


def test_flags_override(tmp_path):
    cfg = build_config(
        ["--data-dir", str(tmp_path), "--project-dir", "/a/b", "--port", "7777", "--no-browser"],
        cwd=Path("/elsewhere"))
    assert cfg["data_dir"] == tmp_path
    assert cfg["default_slug"] == "-a-b"
    assert cfg["port"] == 7777 and cfg["open_browser"] is False


def test_main_serves_and_stops_cleanly(monkeypatch, tmp_path):
    """main() wires config -> server -> browser. Driven with a fake server whose
    serve_forever raises KeyboardInterrupt — the documented way to stop serve."""
    from daimon_ui import __main__ as ui_main

    calls = {}

    class FakeServer:
        server_address = ("127.0.0.1", 7717)

        def serve_forever(self):
            calls["served"] = True
            raise KeyboardInterrupt

    def fake_make_server(data_dir, slug, label, port):
        calls["make"] = (data_dir, slug, label, port)
        return FakeServer()

    monkeypatch.setattr(ui_main.server, "make_server", fake_make_server)
    monkeypatch.setattr("webbrowser.open", lambda url: calls.setdefault("browser", url))
    ui_main.main(["--data-dir", str(tmp_path), "--no-browser"])
    assert calls["served"] is True
    assert calls["make"][0] == tmp_path
    assert "browser" not in calls, "no-browser must not open a tab"


def test_main_opens_browser_by_default(monkeypatch, tmp_path):
    from daimon_ui import __main__ as ui_main

    calls = {}

    class FakeServer:
        server_address = ("127.0.0.1", 7800)

        def serve_forever(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(ui_main.server, "make_server",
                        lambda *a, **k: FakeServer())
    monkeypatch.setattr("webbrowser.open", lambda url: calls.setdefault("browser", url))
    ui_main.main(["--data-dir", str(tmp_path)])
    assert calls["browser"] == "http://127.0.0.1:7800/"
