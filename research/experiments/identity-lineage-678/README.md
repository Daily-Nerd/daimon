# Identity Lineage Shadow Laboratory — #678

Read-only replay for testing persistent item lineage without changing Daimon's
checkpoint, carry, recall, lifecycle, corroboration, or viewer behavior.

The laboratory copies the selected checkpoint bytes into a temporary directory,
compares adjacent checkpoints there, and emits a private review queue. It hashes
the source corpus before and after the run and refuses success if any source byte
changed while it was reading.

## Self-check

```bash
uv run --project ../../../plugin pytest -q test_lineage_lab.py
```

## Private dogfood run

```bash
uv run --project ../../../plugin python lineage_lab.py \
  --project /path/to/your/project \
  --out out-current
```

Open `out-current/review.html` locally. Review decisions are stored only in the
browser's local storage until exported. The export and `candidates.jsonl` contain
real checkpoint text and MUST NOT be committed.

## Outputs

- `summary.json` — aggregate measurements and before/after integrity digests;
  contains no item text.
- `candidates.jsonl` — private typed-relation proposals with both item texts.
- `review.html` — private, dependency-free review UI over the same proposals.

## Interpretation

`observed` means the store already exposes the relationship (unchanged identical
ID). `candidate` means exactly that: it is never a resolution, merge, lifecycle
transition, corroboration, deletion scope, or recall-dedup instruction.

The current carry matcher appears as one evidence rail so its behavior can be
measured. It is not promoted to an identity oracle. Ambiguous endpoints remain
visible and are never collapsed to a guessed winner.

