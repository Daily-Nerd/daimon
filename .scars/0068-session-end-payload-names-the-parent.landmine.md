---
id: 68
type: landmine
title: A Claude Code SessionEnd payload can name the PARENT of a continued session; serialize what the transcript tail points to, not what the host says
severity: high
confidence: 0.85
created: 2026-09-03
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: git-config-interactive
anchors:
  - path: hook/daimon-session-end.py
evidence:
  - note: #923 (2026-09-03): a session continued into a new id via a background continuation ran 21h and 4900 lines; at exit the host fired SessionEnd with the parent id and the parent transcript path (358 lines). The hook re-serialized the stub for 223s and wrote nothing for the child. heal saw no failure because every spawn had succeeded. Only the
expires:
  condition: "the host reports the child session id and transcript path at SessionEnd for a continued session, or the serializer follows continuations itself"
  review_after: 2027-03-03
status: active
---

The SessionEnd payload's `session_id` and `transcript_path` are not the session
the work happened in when the transcript was continued. Claude Code writes a
`{"type":"continued-in","continuedInSessionId":"<child>"}` line near the tail of
the PARENT transcript, keeps appending cost lines after it, and at exit reports
the parent. Trusting the payload literally means: the stub is serialized again
(its bytes changed, so the identical-bytes guard does not fire), the child gets
no checkpoint, `daimon status` reads fresh, and `heal` has nothing to heal (#923).

`_follow_continuation` in this file walks those pointers (bounded tail read,
hop cap, cycle guard) and the spawn line names the CHILD's stem and path, which is
what the ledger pairs a result line with (#28). Do not "simplify" the hook back to
`payload["transcript_path"]`, and do not key the in-flight guard or the spawn
line on the payload's `session_id`. If a host adds another continuation shape,
extend `_continued_into`, not the sweep: the sweep is the safety net, not the path.
