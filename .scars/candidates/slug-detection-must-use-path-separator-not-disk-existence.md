---
id: 0
type: landmine
title: Detecting a bucket slug by disk existence ("is this an absolute existing directory") misclassifies every non-existent test/production project path as a slug
severity: medium
confidence: 0.85
created: 2026-09-23
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/briefing.py
  - pattern: os\.path\.isdir
evidence:
  - note: "#1093 implementation, 2026-09-23: an initial `_is_slug_input` predicate ('not (os.path.isabs(text) and os.path.isdir(text)))' broke test_ruling_briefing.py::test_plain_rulings_still_render_byte_identical_to_today, whose PROJECT constant '/p/ruling-brief' is a well-formed absolute path that names no real directory on the test filesystem. Every ruling/briefing test in the suite uses fake absolute paths this way."
expires:
  condition: "the suite stops using non-existent absolute paths as project_dir stand-ins, or a real store.project_slug-shaped slug starts containing a path separator"
  review_after: 2027-03-23
status: candidate
---

`config.resolve_project_dir`'s own slug/path split is `os.sep in text` (or
`os.altsep`), not disk existence — a well-formed absolute path is a path
whether or not it happens to exist right now, and the whole test suite (plus
any production caller naming a project before its first checkpoint) relies on
that: `PROJECT = "/p/ruling-brief"` and dozens like it never exist as real
directories, yet are never slugs either.

`config._layer_scopes` uses a STRICTER gate (`isabs and isdir`) for a
different reason: a layer must be a real, walkable ancestor directory, so
non-existence there correctly means "no layer here." Copying that same gate
into a "is this a bucket slug" check (#1093's `briefing._is_slug_input`, built
to pick the render text for `brief --slug` vs. an ordinary project path) is
the wrong reuse: it silently reclassifies every fake-but-well-formed absolute
test path as a slug and renders an "unresolved for a slug" note on paths that
were never slugs.

The correct test for "is this string slug-shaped" is the same `looks_like_path`
predicate `resolve_project_dir` already uses: presence of a path separator, not
whether `os.path.isdir` currently agrees. A real `store.project_slug` output
never contains one (it is the flattened form); every directory path does,
existing or not. If you need to tell a slug apart from a path anywhere else in
this codebase, reuse the separator test, not an existence check.
