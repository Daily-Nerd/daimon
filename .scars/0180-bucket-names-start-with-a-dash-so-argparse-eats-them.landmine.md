---
id: 180
type: landmine
title: A bucket name starts with a dash, so a verb taking one must be written --flag=NAME or argparse reads it as an option
severity: low
confidence: 0.9
created: 2026-11-22
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: git-config-interactive
promoted_from: candidates/bucket-names-start-with-a-dash-so-argparse-eats-them.md
anchors:
  - path: plugin/daimon_briefing/cli/projects.py
  - path: plugin/tests/test_bucket_cli.py
evidence:
  - note: the first run of the bucket merge CLI tests failed with "argument --from: expected one argument" for every absolute-path bucket name, because the slug of /private/var/... is -private-var-...
expires:
  condition: "bucket names stop beginning with the separator of the location they were seeded from"
  review_after: 2027-06-01
status: active
---

`store.location_slug` turns every separator into a dash, so the slug of any
absolute POSIX path starts with `-`. `daimon bucket merge --from -Users-me-repo`
is therefore an argparse error, not a merge: the value is parsed as an unknown
option and the flag is left with no argument.

The verb takes the names as `--from=NAME` and `--into=NAME`, and its help and the
doctor suggestions print that form. A test or a doc example that writes the
space-separated form passes only for names that do not start with a dash, which
in the suite means only hand-built ones. Use real slugs in CLI tests.
