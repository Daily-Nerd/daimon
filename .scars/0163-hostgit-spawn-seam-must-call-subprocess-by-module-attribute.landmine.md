---
id: 163
type: landmine
title: hostgit must call subprocess.run and shutil.which through the module attribute, or the caller suites stop seeing their patches
severity: medium
confidence: 0.85
created: 2026-11-08
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/hostgit.py
evidence:
  - note: plugin/tests/test_config.py patches the shared subprocess module; plugin/tests/test_hostgit.py exercises the hostgit spawn seam.
  - commit: a1b5b7f
  - note: moving the git spawns out of config, teamproject, worldcheck and teamsync into hostgit; the suites for those modules patch subprocess.run and shutil.which
expires:
  condition: "the caller suites patch hostgit._spawn through one recorder fixture instead of the stdlib module attributes"
  review_after: 2027-05-08
status: active
---

The config, teamproject, worldcheck and teamsync suites replace `subprocess.run`
and `shutil.which` on the shared stdlib module (`monkeypatch.setattr(x.subprocess,
"run", ...)` is the same object as `subprocess`). They keep working after the
spawns moved into hostgit only because `hostgit._spawn` looks `subprocess.run`
up at call time. `from subprocess import run` (or binding `which` at import)
makes every one of those patches silently miss: the real git runs, the "git
timed out" and "never spawns" tests pass for the wrong reason or fail far from
the cause.

The other half: a patch written as `config.subprocess` or `teamsync.shutil`
raises AttributeError the moment the caller drops that import, which is what
moving a spawn does. Retarget the patch to `subprocess` / `hostgit.shutil`
instead of keeping a dead import alive in the caller. New tests should replace
`hostgit._spawn` (see tests/test_hostgit.py) and not the stdlib at all.
