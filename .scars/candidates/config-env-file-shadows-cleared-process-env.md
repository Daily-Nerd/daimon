---
id: 0
type: landmine
title: Clearing os.environ does NOT unset a daimon config value — config._get falls back to ~/.daimon/env on disk
severity: high
confidence: 0.95
created: 2026-07-31
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/config.py
  - pattern: "os\.environ\.pop|monkeypatch\.delenv"
evidence:
  - note: "daimon#475 part 2 review. An exhaustive parity harness compared the old inline rescue_gap formula against rescue_posture() across 40 backend/key/fallback/command combinations. Every 'no API key' row was produced with os.environ.pop('DAIMON_LLM_API_KEY') + pop('LITELLM_API_KEY'). The harness reported rows where config.llm_api_key() was truthy despite the pops, and the first run's conclusions were wrong in BOTH directions: it hid the real defect (2 rows) and invented 2 fake ones. Caught only because a row was internally inconsistent (OLD=True is impossible when the formula requires a truthy key). Re-run with the accessors patched directly, the true answer was 3 changed rows, one of which was a genuine design defect."
expires:
  condition: "config._get stops falling back to the env file, or an official test helper lands that neutralises both sources at once"
  review_after: 2027-01-31
status: candidate
---

`config._get()` is `process env, then env file`:

```python
val = os.environ.get(name)
if val is not None:
    return val
return _file_values().get(name)      # ~/.daimon/env
```

So `os.environ.pop("DAIMON_LLM_API_KEY")` / `monkeypatch.delenv(...)` does **not**
simulate "this operator has no API key" on any machine where daimon is actually
installed. The developer's own `~/.daimon/env` silently supplies the value, and
the test or diagnostic quietly measures the wrong configuration. Nothing errors.
The numbers just come back confident and wrong.

This is worst in **ad-hoc analysis scripts**, which have no conftest protecting
them. It produced a parity table whose "no key" column was entirely fictional
and which pointed at two non-existent defects while concealing a real one.

To genuinely simulate an absent value, do ONE of:

- patch the accessor: `monkeypatch.setattr(config, "llm_api_key", lambda: None)`
  (preferred — states the intent, immune to both sources), or
- point the file lookup at nothing: set `DAIMON_ENV_FILE` to a path that does
  not exist, which makes `_file_values()` return `{}` via its `OSError` guard.

Deleting the process-env var alone is never sufficient. If a config-dependent
result looks surprising, verify the input you *think* you set is the input the
code actually read before trusting any conclusion drawn from it.
