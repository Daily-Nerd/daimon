---
id: 0
type: landmine
title: The viewer's default project slug must come from config.resolve_project_dir, not a character transform of the path
severity: medium
confidence: 0.85
created: 2026-10-07
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_ui/__main__.py
evidence:
  - note: "tests/ui/test_main.py::test_the_default_project_is_the_one_the_cli_picks and test_a_slug_shaped_project_dir_is_a_path_not_a_bucket_name"
expires:
  condition: "a single resolver object replaces config.resolve_project_dir plus store.project_slug at every entry point"
  review_after: 2027-04-07
status: candidate
---

`store.project_slug` is a pure character transform. The bucket a CLI verb opens
is `store.project_slug(config.resolve_project_dir(path))`: the path is absolutized,
symlinks are collapsed and it is normalized to the git toplevel first. A viewer
that slugs the working directory directly opens a different, usually empty,
bucket whenever it starts in a subdirectory or through a symlink.

`daimon_ui.__main__.build_config` resolves through `config.resolve_project_dir`
with `allow_slug=False` (the viewer's `--project-dir` is a path, never a bucket
name, the same stance as the CLI's `--project`) and takes the store from
`config.checkpoint_dir()`, so the env file counts. Do not add a second slug
function to a read surface; a new entry point calls the same two functions.
