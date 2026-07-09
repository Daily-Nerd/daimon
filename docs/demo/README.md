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
read the ⚠ line for the two ids to plug into `daimon resolve`. Occasionally the
model omits the supersession link on the pivot decision; rerun session 2 if the
flag doesn't appear (`DAIMON_LLM_NO_CACHE=1` forces a fresh generation behind a
caching gateway).

## Re-record the GIF

The recording is scripted with [vhs](https://github.com/charmbracelet/vhs)
(`brew install vhs`). `demo.tape` expects the two serialized checkpoints to
already exist (steps above), a `daimon` on PATH, and the same
`DAIMON_CHECKPOINT_DIR`; adjust the two `daimon resolve` ids to your run, then:

```sh
vhs demo.tape   # writes daimon-demo.gif + daimon-demo-flag.png
```
