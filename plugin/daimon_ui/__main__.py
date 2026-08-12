import argparse
import webbrowser
from pathlib import Path

from . import reader, server


def build_config(argv, env, cwd: Path):
    ap = argparse.ArgumentParser(prog="daimon_ui", description="Read-only daimon checkpoint inspector")
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--project-dir", type=Path, default=None)
    # 7717 is the viewer's home port (the design freezes localhost:7717 in its
    # chrome); pass --port 0 for an ephemeral one.
    ap.add_argument("--port", type=int, default=7717)
    ap.add_argument("--no-browser", action="store_true")
    ns = ap.parse_args(argv)
    data_dir = ns.data_dir or reader.resolve_data_dir(env)
    project_dir = ns.project_dir or cwd
    return {
        "data_dir": data_dir,
        "default_slug": reader.project_slug(project_dir),
        "project_label": Path(project_dir).name,
        "port": ns.port,
        "open_browser": not ns.no_browser,
    }


def main(argv=None):
    import os
    import sys
    cfg = build_config(argv if argv is not None else sys.argv[1:], os.environ, Path.cwd())
    srv = server.make_server(cfg["data_dir"], cfg["default_slug"], cfg["project_label"], cfg["port"])
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"daimon-ui serving {cfg['project_label']} at {url}  (read-only, Ctrl-C to stop)")
    if cfg["open_browser"]:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":  # pragma: no cover — exercised only as a script
    main()
