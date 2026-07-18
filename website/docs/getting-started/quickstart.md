---
sidebar_position: 1
---

# Quickstart

From install to your first briefing. Four steps, one optional.

## 1. Install the CLI

```sh
uv tool install 'daimon-briefing[pretty]'
```

`pipx install 'daimon-briefing[pretty]'` works identically. The `[pretty]`
extra adds rich tables and panels to `status` and `brief`; without it you get
plain text.

## 2. Connect an LLM

Serialization — turning a finished session into a checkpoint — needs an LLM
endpoint. Run:

```sh
daimon configure
```

If the `claude` CLI is on your PATH, this prints `✓ ready` and you are done —
zero configuration. Otherwise, point daimon at any OpenAI-compatible endpoint:

```sh
daimon configure --backend litellm \
  --base-url https://generativelanguage.googleapis.com/v1beta/openai \
  --api-key <YOUR-KEY> --model gemini-2.5-flash
```

Then verify the backend end-to-end before trusting it:

```sh
daimon configure --test
```

This sends one tiny prompt through the resolved backend and reports pass or
fail. Config is written to `~/.daimon/env` — see
[Configuration](./configuration.md) for every variable, and the
[backends matrix](../reference/backends-tested.md) for field-measured model
combinations.

## 3. Hook up your host

Hooks are what capture your sessions. For Claude Code, install the plugin —
it registers the session hooks itself:

```
/plugin marketplace add Daily-Nerd/daimon
/plugin install daimon@daimon
```

For Windsurf, Codex, or Gemini CLI, follow your host's page in
[Hosts](../hosts/index.md) — each host has a different hook surface, and the
per-host guides cover the exact registration steps and caveats.

## 4. Teach your agent the protocol (optional, recommended)

Hooks capture sessions; the skill teaches the agent on the other side how to
*use* the briefing — read it at session start, treat `verbatim` items as
immutable quotes, verify stale-looking claims before repeating them:

```sh
daimon skill install claude
```

`daimon skill list` shows the install targets for other hosts.

## 5. End a session, start the next

That's the whole loop:

1. Work a normal session in your agent.
2. End it. The session-end hook serializes a checkpoint in the background.
3. Start the next session. The briefing is injected at session start:

```
While you were away — here's where we left off.

VERIFY BEFORE TRUSTING (state may have changed outside this session):
- [✓ verbatim] PR #212 state — you said you'd merge it yourself from the UI  — "I'll merge it after the demo"

Open loops:
- [✓ verbatim] Retry policy for the payments webhook — exponential or fixed?  — "don't ship the retry loop until we pick a policy"

Decisions made:
- [✓ verbatim] Postgres advisory locks over Redis locks for the scheduler  — "let's not add a Redis dependency for this"

Active topic: Migrating the scheduler off cron to the new worker pool
```

You can also read it in a terminal anytime with `daimon brief`.

:::note Short sessions are skipped by design
A session shorter than `DAIMON_MIN_MESSAGES` (default: 10 messages) is not
serialized — there is nothing worth remembering in a two-message exchange.
If you are evaluating daimon and want your short test sessions captured,
lower the threshold for now:

```sh
echo 'DAIMON_MIN_MESSAGES=4' >> ~/.daimon/env
```
:::

## Check that it's working

```sh
daimon status
```

Status reports capture health honestly — failures, skips, and crashes
included. The lines that matter on a fresh install:

```
project checkpoint: <session id>, written <n>m ago
last serialize result: success — wrote checkpoint: ...
```

If the last serialize failed or a session was never captured, a failed capture
self-heals on the next session start — or run `daimon heal` to retry
immediately.

## Where to next

- [Configuration](./configuration.md) — every environment variable, including
  the `DAIMON_DISABLE` kill switch.
- [Hosts](../hosts/index.md) — per-host setup detail and known limitations.
- [Team memory](../team/team.md) — share checkpoints with teammates through a
  private git remote (opt-in).
