# Experiments — running the validation tracks

Two ways to drive the LLM steps of each track:

- **Manual** — paste the prompts (in each track's `prompts/`) into any model, save the outputs. Zero setup, fully under your eye.
- **Automated via LiteLLM** — point the runners at your self-hosted LiteLLM gateway. Faster, repeatable, and self-hostable.

The **human** steps (writing ground truth, labeling contradictions/evolutions, scoring) stay manual in both modes — that is what keeps the experiments honest and blind.

---

## Getting real sessions — import from Claude Code

You don't need to hand-export transcripts. Claude Code already stores every session as JSONL under `~/.claude/projects/<slug>/`. `lib/claude_sessions.py` converts them into clean Track A/C transcripts.

```bash
cd research/experiments
uv run lib/claude_sessions.py --list-projects                      # which projects have sessions
uv run lib/claude_sessions.py --list --project Daily-Nerd-EdgePipe  # sessions + titles + size
uv run lib/claude_sessions.py --project Daily-Nerd-EdgePipe \
    --session <id> --out track-a/sessions/S1.txt                    # convert one
```

**What it does to keep you safe:**
- **Tool results are excluded** — command output, file reads, and env dumps (the highest-risk secret carriers) never enter the transcript. Bonus: this focuses the transcript on the *discussion* (decisions, open questions, beliefs) — exactly what Track A scores.
- **Text-borne secrets are regex-redacted** (best-effort, not bulletproof).
- **Still REVIEW before a cloud run.** Until the local GPU lands, kimi is cloud — eyeball the `.txt` before `runner.py`.

**Pick good Track A sessions:** real, multi-turn, with actual decisions and unresolved questions. Avoid the `daimon` project's own session (self-referential + may carry sensitive content). Spread across 5 different projects for variety.

## LiteLLM setup (once)

Point the harness at any OpenAI-compatible gateway. If yours runs in a cluster, reach it with a port-forward, e.g.:

```bash
kubectl port-forward -n <namespace> svc/<litellm-svc> 4000:4000   # leave running in a shell
```

Then set credentials in the shell you run the harness from (never commit these):

```bash
export LITELLM_BASE_URL=http://localhost:4000   # default; can omit
export LITELLM_API_KEY=sk-...                    # your LiteLLM key (master or virtual)
export LITELLM_MODEL=<name>                       # see below
```

Discover the model names configured in your gateway:

```bash
cd research/experiments
uv run lib/llm.py        # prints /v1/models
```

Pick a `LITELLM_MODEL` from that list.

### The shared client

`lib/llm.py` is a dependency-free OpenAI-compatible client (stdlib `urllib`). No SDK, no secrets in code — everything comes from env at runtime. Both runners import it.

---

## Track A — automated

```bash
cd research/experiments/track-a
# drop your transcripts in sessions/S1.txt … S5.txt first (git-ignored)
uv run runner.py --all          # serialize -> reconstruct for every session
```
Writes `runs/<id>/checkpoint.json` and `runs/<id>/reconstruction.md`. You still write `runs/<id>/ground-truth.json` (before reading the reconstruction) and score it:
```bash
uv run scoring/score.py runs/*/session-*.score.json
```

## Track C — automated

```bash
cd research/experiments/track-c
uv run extract.py --session corpus/S1.txt --timestamp 1 --out runs/S1.claims.json
uv run extract.py --session corpus/S3.txt --timestamp 3 --out runs/S3.claims.json
# merge the *.claims.json into runs/<pair>.run.json, audit is_belief, label gold/evolution
uv run pipeline/run.py runs/<pair>.run.json
```

---

## Privacy note (it matters here)

Daimon's promise is "your data never leaves your infra." When you route a **real session** through LiteLLM, where it goes depends on what LiteLLM routes to:

- **LiteLLM → a local model** (e.g. an in-cluster vLLM/ollama): data stays fully on your infra. ✅ aligned with the promise.
- **LiteLLM → OpenAI/Anthropic cloud:** your conversation leaves your infra to that provider for the duration of the validation run.

For the *validation* this may be an acceptable trade. For the *product* it's the whole differentiator. If you have a local model behind LiteLLM, prefer it for the Track A/C runs — set `LITELLM_MODEL` to that. Your call; just make it a choice.

## Security

- Keys come from env only. Nothing in `lib/llm.py` or the runners is committed with a secret.
- `sessions/`, `corpus/`, and `runs/` are git-ignored in each track — your conversations and outputs never get committed.
