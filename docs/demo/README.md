# README demo — the trust loop, reproducibly

The GIF at the top of the main README is recorded from the two transcripts in
this directory. Nothing is mocked: the checkpoints are produced by
`daimon serialize`, the briefing and the supersede flag are real output.

## The scenario

- `session1-aws.md` — a study session: commit to the AWS Solutions Architect
  cert, a 10-week plan, Tutorials Dojo for practice exams.
- `session2-gcp-pivot.md` — the pivot: new job runs on GCP, AWS prep dropped,
  new target is the Associate Cloud Engineer cert.

Session 2 *reverses* session 1's central decision. That is the hard case for
any memory tool: inject the old decision as current fact and the briefing lies.
Daimon carries it **flagged as a supersede-candidate** with the confirm/reject
commands inline, and withholds it once the human confirms.

## Reproduce it

```sh
# an isolated store so your real checkpoints stay untouched
export DAIMON_CHECKPOINT_DIR=$PWD/demo-store
mkdir -p demo-store demo-project

daimon serialize --project demo-project session1-aws.md
daimon serialize --project demo-project session2-gcp-pivot.md

daimon brief --project demo-project        # ⚠ likely superseded by <new-id> …
daimon resolve <old-id> --status superseded-by:<new-id>
daimon brief --project demo-project        # stale decision withheld
```

Serialization is an LLM pass, so item ids and exact wording vary between runs —
read the ⚠ line for the two ids to plug into `daimon resolve`. The model
sometimes omits the supersession link on the pivot decision, or emits a target
too vague to bind (binding never guesses) — expect the flag to take a rerun or
two of session 2. Rerunning needs two things:

```sh
# 1. delete the session-2 checkpoint — daimon refuses to re-serialize an
#    unchanged transcript (identical-bytes guard), so a plain rerun is skipped
rm demo-store/session2-gcp-pivot.json

# 2. force a fresh generation (busts any caching gateway in front of the LLM)
DAIMON_LLM_NO_CACHE=1 daimon serialize --project demo-project session2-gcp-pivot.md
```

## Ask the agent instead

With the Claude Code integration installed, the briefing is injected
automatically when a session starts — including headless runs. From the demo
project directory:

```sh
claude -p "where did we leave off?"
```

The agent answers from the briefing: the pivot as current state, verbatim
quotes cited, and the pre-pivot carried items called out as superseded
(`claude-p.png`). Push it further and it self-audits its own memory —
which items are verified quotes, which are its own inferences, and which
carried items contradict each other (`claude-p-audit.png`):

```sh
claude -p "where did we leave off? and tell me how you know that — which of
your memories of this project are verified quotes vs your own inferences,
what beliefs are you carrying, and what should I do next?"
```

Answers vary per run — retake until the stale-item callout appears.

## Re-record the assets

The recordings are scripted with [vhs](https://github.com/charmbracelet/vhs)
(`brew install vhs`). Every tape expects the two serialized checkpoints to
already exist (steps above) and a small `demo-env.sh` next to it that the
hidden setup sources — exports `DAIMON_CHECKPOINT_DIR`, cd's into the demo
project, and puts `daimon` on PATH:

```sh
#!/bin/zsh
export DAIMON_CHECKPOINT_DIR=/path/to/demo-store
cd /path/to/demo-project
PROMPT='%F{cyan}~/cloud-study%f ❯ '
setopt interactive_comments
```

Adjust the two `daimon resolve` ids in `demo.tape` to your run, then:

```sh
vhs demo.tape            # daimon-demo.gif + daimon-demo-flag.png
vhs claude-p.tape        # claude-p.png (crop the idle rows below the output)
vhs claude-p-audit.tape  # claude-p-audit.png
```
