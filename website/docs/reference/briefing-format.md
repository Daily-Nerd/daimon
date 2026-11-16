---
sidebar_position: 8
description: "How to read the marks and annotations on a briefing line."
---

# Briefing format

Every briefing line is marked. The full trust story is in [trust classes](../concepts/trust-classes.md).

- `[✓ verbatim]` / `[~ inferred]` / `[? untagged]`, how the item was captured.
- `[carried]`: inherited from an earlier session, not fresh context.
- `(noted <UTC time>, re-check before acting)`: on a carried item that names a PR, branch, file or dependency: when the note was first captured. It says nothing about the state since; see [carry](../concepts/carry.md).
- `[≈ corroborated ×N]`: N independent sessions witnessed the claim.
- `[✓ world-checked]`: a live probe agreed with this claim during this brief.
- `HANDOFF (…)`: an authored baton from the previous session; it outranks everything below it.
- `— because …`: the decision's stated reasoning, captured only when the transcript states it.
