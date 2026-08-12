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
