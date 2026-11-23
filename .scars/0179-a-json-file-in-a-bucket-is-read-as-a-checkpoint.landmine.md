---
id: 179
type: landmine
title: Any file ending in .json inside a bucket directory is walked as a plaintext checkpoint by several readers, so a bucket-level document must not end in .json
severity: medium
confidence: 0.85
created: 2026-11-22
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: git-config-interactive
promoted_from: candidates/a-json-file-in-a-bucket-is-read-as-a-checkpoint.md
anchors:
  - path: plugin/daimon_briefing/store.py
  - path: plugin/daimon_briefing/recall.py
  - path: plugin/daimon_briefing/privacy.py
  - path: plugin/daimon_briefing/bucket_tools.py
evidence:
  - note: slice 3b of the project-identity design named the evidence record identity.json; reading the walkers showed store._plaintext_surfaces, store.project_surfaces, recall._fingerprint, recall's source scan and privacy._checkpoint_candidates each take every *.json in a bucket as a checkpoint, so the record shipped as the suffix-less file named by store._IDENTITY_NAME
expires:
  condition: "the bucket walkers share one classification of what a bucket file is, instead of each testing the .json suffix"
  review_after: 2027-06-01
status: active
---

Scar 0101 lists four registries for a new file under `checkpoints/{slug}/`. It
does not say that the suffix itself is a registry. `store._plaintext_surfaces`
and `store.project_surfaces` (forget), `recall._fingerprint` and the recall
source scan, and `privacy._checkpoint_candidates` all treat a `.json` file in a
bucket as a plaintext checkpoint: forget would try to rewrite it, recall would
index it and rebuild when it changes, and the privacy audit would load it as an
item surface. A JSON document of another kind under that suffix is walked by
all of them, and declaring it in `surfaces.SURFACES` does not stop that.

`root`, `.ledger-census` and the identity record are deliberately named without
`.json`, so every one of those walks skips them by construction. The recall
fingerprint comment about naming a ledger `.jsonl` to dodge the `*.json` walks
is the opposite hazard: that one is index content and must be seen.

What to do instead: name a new non-checkpoint bucket file without `.json`, then
declare it in `surfaces.py` (audit_exempt when it holds no item text) and add it
to `buckets._REMOVABLE`. Do not patch each walker to skip a name.
