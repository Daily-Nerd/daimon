# Docs in this folder

The user-facing documentation lives on the site, https://daily-nerd.github.io/daimon/. The pages here are either pointers to it, the current architecture, or design documents kept as a record. This index says which is which.

## Current

- [RUNTIME-ARCHITECTURE.md](./RUNTIME-ARCHITECTURE.md): the layers, components and data flow of the shipped runtime.
- [checkpoint-schema.json](./checkpoint-schema.json): the JSON schema of a checkpoint.
- [backends-tested.md](./backends-tested.md), [configuration.md](./configuration.md), [team.md](./team.md), [trust-inspector.md](./trust-inspector.md) and the [hosts](./hosts/README.md) pages are pointers to their pages on the site.

## Historical

These were written before the runtime took its current shape. They are kept because the research logbook and the decision log cite them, and they are not a description of what daimon does today.

| document | what it is | read instead |
| --- | --- | --- |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | the standalone-system architecture, superseded in June 2026 | [RUNTIME-ARCHITECTURE.md](./RUNTIME-ARCHITECTURE.md) |
| [RFC.md](./RFC.md) | the technical RFC for that standalone system, superseded | [RUNTIME-ARCHITECTURE.md](./RUNTIME-ARCHITECTURE.md) |
| [PITCH.md](./PITCH.md) | an early positioning document, replaced by later decisions | the site's overview page |
| [PROBLEM.md](./PROBLEM.md) | the forensic analysis of the resume problem; the thesis holds, the answer changed | [RUNTIME-ARCHITECTURE.md](./RUNTIME-ARCHITECTURE.md) |
| [VALIDATION.md](./VALIDATION.md) | the validation gate that preceded the pivot, with its results | the [research logbook](../research/README.md) |
| [MVP-DREAM-BRIEFING.md](./MVP-DREAM-BRIEFING.md) | the shipped MVP baseline at v0.11.0 | [RUNTIME-ARCHITECTURE.md](./RUNTIME-ARCHITECTURE.md) |
