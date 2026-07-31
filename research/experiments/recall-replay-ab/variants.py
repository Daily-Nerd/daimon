"""Variant registry for the recall-scoring replay A/B harness.

Arm A is ALWAYS today's shipped `recall.suggest()`, unmodified. Arm B is a
VARIANT: one hypothesis about how recall should select or score differently,
expressed as a single callable

    def variant(ctx, suggest) -> list[dict]

`suggest()` runs the shipped call for this prompt and returns its match list.
Keyword arguments override that call's inputs (`prompt=`, `limit=`,
`exclude_sessions=`, ...). So a hypothesis can be expressed

  * OUTPUT-side — ``return [m for m in suggest() if keep(m)]``
    (a gate, a re-rank, a truncation over what recall already chose)
  * INPUT-side  — ``return suggest(prompt=rewrite(ctx["prompt"]))``
    (a different query, a wider fetch, a different exclusion set)
  * or both.

`ctx` keys:
  prompt   — the replayed prompt text
  terms    — recall.salient_terms(prompt)
  project  — the project dir the historical session ran in
  session  — the historical session id
  ts       — the prompt's epoch timestamp (frozen `now` for this replay)
  param    — this arm's sweep value, a STRING, or None when --sweep is
             omitted. A variant with a knob (a threshold, a weight, a
             marker) parses `param` itself; a knobless variant ignores it.
             One arm B is replayed per sweep value.
  db_path  — the snapshot recall db, for a variant that needs its own
             lookups. Open it READ-ONLY and close it before returning.

Contract a variant MUST honour, or the comparison stops being interpretable:

  1. Rows must come from `suggest()` (or a re-parameterised call to it).
     Never fabricate a row — every judging unit is built from arm output,
     and a synthesised row has no provenance to judge.
  2. No side effects: do not write the db, the seen state, the env, or any
     module-level constant in `daimon_briefing`. Arm A is replayed first and
     must be reproducible afterwards.
  3. Deterministic for a given (ctx, param). verify.py byte-compares two
     runs of every analytical artifact; a nondeterministic variant fails it.

Registering a hypothesis: add a function here and name it in `BUILTIN`, or
keep it out of the repo entirely and pass `--variant mymodule:myfunc`.
"""

import importlib
import sys
from pathlib import Path


def none(ctx, suggest):
    """Identity variant: arm B is arm A, exactly.

    The default, and the only one that ships. It measures nothing about
    recall — it proves the RIG is sound: replay, snapshotting, per-arm
    cooldown state and the diff machinery must all produce A == B when the
    two arms are the same code. Any diff under `none` is a harness bug, not
    a finding. Start every session with it before wiring a real hypothesis.
    """
    return suggest()


BUILTIN = {"none": none}


def resolve(spec: str):
    """'none' -> a builtin; 'pkg.mod:func' / 'path/to/file.py:func' -> an
    external variant. The file form puts the file's directory on sys.path so
    a one-file hypothesis needs no packaging."""
    if spec in BUILTIN:
        return BUILTIN[spec]
    if ":" not in spec:
        raise SystemExit(
            f"replay-ab: unknown variant {spec!r}; expected one of "
            f"{sorted(BUILTIN)} or 'module:function'")
    mod_spec, _, func = spec.partition(":")
    if mod_spec.endswith(".py"):
        path = Path(mod_spec).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"replay-ab: no variant file at {path}")
        sys.path.insert(0, str(path.parent))
        mod_spec = path.stem
    try:
        mod = importlib.import_module(mod_spec)
    except ImportError as exc:
        raise SystemExit(f"replay-ab: cannot import variant module "
                         f"{mod_spec!r}: {exc}") from exc
    try:
        fn = getattr(mod, func)
    except AttributeError as exc:
        raise SystemExit(f"replay-ab: variant module {mod_spec!r} has no "
                         f"{func!r}") from exc
    if not callable(fn):
        raise SystemExit(f"replay-ab: variant {spec!r} is not callable")
    return fn
