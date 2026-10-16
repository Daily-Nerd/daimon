"""A real-shaped outstanding item for tests that feed the heal plan.

`ledger._outstanding_failures` is the only producer of these dicts, and
`heal.plan_all` reads their keys as required. A hand-written fake that omits a
key tests a shape production never emits, so fakes build theirs here.
`tests/test_heal_core.py` pins this helper's key set to the real producer's,
so the helper cannot drift from it silently."""


_DEFAULT = object()


def item(sid, cls="healable", *, kind="error", age=60, project="/p",
         transcript=_DEFAULT, line=None, retried=False, **extra):
    """An outstanding error (or hung, via `kind`) item as the classifier emits it."""
    if transcript is _DEFAULT:
        transcript = f"/t/{sid}.jsonl"
    if line is None and kind == "error":
        line = f"error: boom (transcript: {transcript}) after 3s"
    out = {"sid": sid, "kind": kind, "class": cls, "age": age,
           "age_str": f"{age // 60}m", "transcript": transcript,
           "project": project, "spawned": True, "line": line,
           "retried": retried}
    out.update(extra)
    return out
