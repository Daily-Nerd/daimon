# .scars/ — Negative knowledge for this repo

This directory records what this codebase **refused to be**: approaches that
were tried and failed (`deadend`), configuration that looks wrong but is
intentional (`fence`), and changes that break non-obvious things elsewhere
(`landmine`).

Before "cleaning up" anything these files anchor to — read the scar first.
Every scar carries evidence (commits, PRs, incidents). If a scar is stale,
challenge it: update or archive it with a note, don't ignore it.

## The contract (humans and agents)

1. **New scars start as candidates.** Copy `template.md`, write to
   `candidates/<slug>.md` with `status: candidate`. Never write directly
   into `.scars/` — only a human reviewer promotes.
2. **YAML frontmatter is mandatory.** A scar without it is unparseable and
   will NEVER fire in any tool. The hooks warn loudly when they find one.
3. **Promotion** = human review: move to `NNNN-<slug>.<type>.md` (next free
   number), set `status: active`, add the reviewer to `authors`.
4. **Evidence required.** A scar without a commit/PR/incident reference is
   an opinion and can be challenged on sight.

Format details: `template.md`.
