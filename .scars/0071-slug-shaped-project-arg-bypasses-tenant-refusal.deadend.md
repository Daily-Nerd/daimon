---
id: 71
type: deadend
title: Letting --project pass a slug-shaped value through to the bucket bypasses the tenant refusal
severity: high
confidence: 0.9
created: 2026-09-06
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/config.py
  - path: plugin/daimon_briefing/cli/__init__.py
violation: "allow_slug=True"
evidence:
  - pr: 951
  - note: review on #951 measured it: DAIMON_TENANT_SCOPED=1, `ruling list --project=<foreign slug>` printed the foreign ruling at rc 0 and `ruling propose --project=<foreign slug>` wrote into that bucket
expires:
  condition: "_refuses_caller_scope guards every path a bucket name can enter a verb, not only --slug and --all-projects"
  review_after: 2027-03-06
status: active
---

`config.resolve_project_dir` passes a value with no path separator that names no
existing directory through untouched, because `_slug_route`, `brief --slug` and
the bucket-iterating readers in `pending` hand bucket SLUGS in as `project_dir`.
That passthrough was first applied to the CLI's `--project` handler too. It
looked harmless: on main the same value absolutized against the cwd and could
never reach a foreign bucket, and no test asserted that.

Under a tenant-scoped home it was a full bypass. `_refuses_caller_scope` guards
`--slug` and `--all-projects` only, since before this change `--project` could
not name a bucket. With the passthrough, `ruling list --project=-Users-x-secret`
read the foreign bucket at rc 0 and `ruling propose --project=<slug>` wrote into
it, on every verb, including the ten `_slug_route` deliberately restricts.

The fix is `allow_slug=False` on the CLI path so a `--project` value is ALWAYS
absolutized. The rule to keep: any NEW way to hand a bucket name into a verb is
a routing primitive and must meet the tenant refusal; test it under
`DAIMON_TENANT_SCOPED=1` with a foreign bucket planted before shipping.
