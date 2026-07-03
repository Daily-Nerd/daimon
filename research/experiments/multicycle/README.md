# Q-STALE Multicycle Experiment

What do ~20 real serialize→brief→serialize cycles do to long-lived
checkpoint items? Design: vault note "Q-STALE Multicycle Experiment Design"
(Daimon project, progress/). Prior: memory-backend scale test's FUTURE-HURT
verdict (prose collapses under merge passes) — this instrument tests the
REAL daimon pipeline instead of an approximation.

## Layout
- `seed.py` — synthetic seed world, nonce vocab, zero-leak screen
- `synth.py` — deterministic session builder (flip turn at cycle 5)
- `run_multicycle.py` — driver: 3 arms × N cycles, resumable, 600K token abort
- `grade.py` — deterministic grading (no LLM judges)

## Dry run (no LLM, no cost)
    uv run --project ../../../plugin python run_multicycle.py --dry-run --run-dir /tmp/qstale-dry

## Live run (haiku via gateway)
    set -a; source ~/.daimon/env; set +a
    export LITELLM_BASE_URL=https://your-gateway.example.com  # your OpenAI-compatible gateway
    export LITELLM_VIRTUAL_KEY=$DAIMON_LLM_API_KEY
    uv run --project ../../../plugin python run_multicycle.py --run-dir results/run-01

Resumable: rerun the same command after a gateway failure — cached cycles
are skipped. Results land in `results/run-01/` (`results-<arm>.jsonl`,
`summary.md`) and get committed like the scale test's `scale-full/`.

## Arms
- `control` — briefing-mediated carry, quiet sessions (serializer drift)
- `distractor` — + unrelated work + 3000-token briefing budget (production)
- `carry` — raw checkpoint JSON carry (lossless upper bound ≈ #33 proxy)
