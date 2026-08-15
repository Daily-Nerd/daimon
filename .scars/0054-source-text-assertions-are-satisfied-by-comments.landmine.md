---
id: 54
type: landmine
title: A getsource substring assertion on a call site is satisfied by its own explaining comment
severity: high
confidence: 0.9
created: 2026-08-15
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/tests/
  - pattern: "inspect\.getsource"
evidence:
  - pr: 693 PR 2 review round 1: both blind reviewers independently proved the mutation survives
  - note: removing admit=True from capture.py's write_checkpoint call left the suite green — the comment above the call contained the same literal
status: active
---

`test_capture_path_admits` asserted `"admit=True" in inspect.getsource(capture.run)`
to pin that the capture path opts into the #693 echo admission filter. The call
site carried the house-style comment `# admit=True (#693): capture is one of the
two admission paths` — so deleting the actual kwarg left the assertion green: the
comment alone satisfied it. The repo's comment discipline (name the flag you are
explaining) makes this collision the NORM, not a fluke: any source-text substring
assertion about a call site will usually also match the comment that documents it.
Two independent reviewers had to mutation-test to expose it. Instead: spy on the
seam (monkeypatch the callee, assert the kwarg it RECEIVES) or drive the caller
end-to-end; if you must assert on source text, first prove the test fails with the
real code removed and the comment left in place.
