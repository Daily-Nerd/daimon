---
id: 0
type: deadend
title: "request from_label cannot be planted in the census: the sender's directory name is also its bucket slug, which every bucket listing prints"
severity: low
confidence: 0.8
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - path: plugin/tests/_sentinel_world.py
  - path: plugin/tests/test_request_masking.py
evidence:
  - commit: 65f2baf
  - note: "tests/test_request_masking.py::test_an_inbox_ask_and_its_sender_label_are_masked covers the column at the printer"
expires:
  condition: "bucket names stop carrying the directory name"
  review_after: 2027-04-09
status: candidate
---

`from_label` is the basename of the sending project's directory, captured at write time. To plant a whole
quarantined value in it the peer directory must be NAMED the sentinel, and `store.project_slug` munges that
path into the bucket name, so `projects`, `status`, `recall --all-projects` and `request list` (the To: line)
then print the sentinel as a bucket name and the census fails on surfaces that have nothing to do with the
column.

The census keeps a benign peer directory. The column is proved at the printer instead, with a peer whose
directory is the quarantined text and an assertion that the inbox shows the marker.
