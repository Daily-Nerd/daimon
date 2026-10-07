import argparse
import webbrowser
from pathlib import Path

from daimon_briefing import config, store

from . import server


def build_config(argv, cwd: Path):
    ap = argparse.ArgumentParser(prog="daimon_ui", description="Read-only daimon checkpoint inspector")
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--project-dir", type=Path, default=None)
    # 7717 is the viewer's home port (the design freezes localhost:7717 in its
    # chrome); pass --port 0 for an ephemeral one.
    ap.add_argument("--port", type=int, default=7717)
    ap.add_argument("--no-browser", action="store_true")
    ns = ap.parse_args(argv)
    # One resolver for the store and the project, the CLI's own: the data dir
    # is `config.checkpoint_dir()` (process env, then the env file), and the
    # project is the one `config.resolve_project_dir` picks (#948), so a
    # viewer started in a subdirectory or through a symlink opens the bucket
    # the CLI would. `--project-dir` is a PATH, never a bucket name
    # (`allow_slug=False`, as the CLI's `--project`): a slug-shaped value is
    # absolutized, not handed through as a bucket.
    data_dir = ns.data_dir or config.checkpoint_dir()
    project_dir = ns.project_dir or cwd
    return {
        "data_dir": data_dir,
        "default_slug": store.project_slug(
            config.resolve_project_dir(str(project_dir), allow_slug=False)),
        "project_label": Path(project_dir).name,
        "port": ns.port,
        "open_browser": not ns.no_browser,
    }


def main(argv=None):
    import sys
    cfg = build_config(argv if argv is not None else sys.argv[1:], Path.cwd())
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
