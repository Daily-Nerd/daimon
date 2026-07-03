---
id: 19
type: landmine
title: Scar pattern anchors are NOT YAML — quotes stripped, escapes untouched, liveness scans only 8KB heads; wrong escaping reads as rot or blocks promote
severity: medium
confidence: 0.9
created: 2026-07-02
authors: ["claude-code", "kibukx"]
anchors:
  - path: .scars/
  - pattern: "pattern:.*\\\\"
evidence:
  - note: 2026-07-02, promote refusal: \"briefing\\\\.build\\\\(\" (reflexive YAML double-backslash) reaches the regex engine RAW — '\\\\(' = literal backslash + bare group-open: 'missing ), unterminated subpattern'. scar promote refused.
  - note: 2026-07-02, rot round-trip on scar #16: double-backslash form reported dead (raw '\\\\.' never matches a plain dot); single-QUOTED form kept the quotes as pattern chars (echoed /'briefing\\.build\\b'/) — still dead; double-quoted single-backslash \"briefing\\.build\\b\" went live. Parser strips double quotes only, processes NO escapes.
  - note: 2026-07-02, scar #9 false rot: its pattern matches cli.py:538, but liveness reads only READ_HEAD_BYTES (8KB) per file (scar/orphan.py) — deep matches report as dead anchors.
expires:
  condition: "scar lint parses patterns with documented quoting/escaping semantics matching the injector AND liveness scans full file contents (or distinguishes 'beyond scan head' from dead)"
  review_after: 2027-01-02
status: active
---

A scar `pattern:` is not parsed as YAML. The tool strips surrounding DOUBLE
quotes and hands the bytes straight to the regex engine: escape sequences are
never processed, single quotes become part of the pattern, and the reflexive
double-backslash (`\\.`, `\\(`) means literal backslashes — `\\.` never
matches a plain dot, and `\\(` splits into a literal backslash plus a bare
group-open, an unterminated subpattern that hard-blocks `scar promote`.
Separately, anchor liveness (rot detection) reads only the first 8KB of each
tracked file, so a good pattern whose only match sits deep in a large file
(scar #9: `_parse_serialize_log`, cli.py:538) reports as a dead anchor.

What a future author must do instead: write patterns DOUBLE-quoted with SINGLE
backslashes ("briefing\.build\b"), avoid parens and lookarounds entirely, and
treat partial-rot HINTs on large anchored files as suspect — grep the file
yourself before "re-anchoring". Run `scar lint` before handing a candidate to
review; a dead-anchor HINT on a fresh scar means the escaping is wrong.
