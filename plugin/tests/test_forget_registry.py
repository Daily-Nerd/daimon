"""PR 12: every forget step is a registry row, and the registry is two-way.

A deleter that is not a row, or a row whose step does not resolve, is the
hole the registry exists to close. Nothing here runs a forget; the run is
covered where the steps are called.
"""
import ast
import importlib
from pathlib import Path

from daimon_briefing import jsonl, surfaces

PKG = Path(surfaces.__file__).parent

# Public functions whose names look like deleters and are not. Two-way: an
# entry that stops existing fails, so the list cannot rot into a blind spot.
NON_DELETERS = {
    "redact.redact_text": "secret redaction of a string",
    "policy.redact_checkpoint": "admission redaction of a payload",
    "policy.scrub_forgotten_payload": "the pure splice the write gate runs",
    "policy.scrub_forgotten_fields": "the pure per-item splice",
    "display.forget_incomplete_note": "a sentence",
    "store.forget_hit_stats": "a counter read",
    "ledger_repair.scrub_forgotten_key": "the composite entry the steps feed",
}

_PREFIXES = ("forget_", "scrub_", "purge_", "redact_")


def _steps():
    for row in surfaces.SURFACES:
        for step in row.steps:
            yield row, step


def _public_deleter_names():
    found = set()
    for path in sorted(PKG.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) \
                    and not node.name.startswith("_") \
                    and node.name.startswith(_PREFIXES):
                found.add(f"{path.stem}.{node.name}")
    return found


def test_every_step_names_a_known_phase_call_and_scope():
    seen = list(_steps())
    assert seen, "the registry declares steps"
    for row, step in seen:
        assert step.phase in surfaces.PHASES, (row.shape, step)
        assert step.call in surfaces.CALLS, (row.shape, step)
        assert step.scope in surfaces.SCOPES, (row.shape, step)
        assert step.label.strip(), (row.shape, step)
        if step.done:
            assert "{n}" in step.done, (row.shape, step)


def test_every_step_resolves_to_a_callable():
    for row, step in _steps():
        module, _, attr = step.fn.rpartition(".")
        fn = getattr(importlib.import_module(f"daimon_briefing.{module}"),
                     attr, None)
        assert callable(fn), f"{row.shape}: {step.fn} does not resolve"


def test_every_forget_walker_row_declares_a_step():
    for row in surfaces.SURFACES:
        if row.walker == "forget":
            assert row.steps, f"{row.shape}: walker forget, no step"


def test_a_step_on_another_walker_is_a_publish_or_a_derived_one():
    for row, step in _steps():
        if row.walker != "forget":
            assert step.phase in ("publish", "derived"), (row.shape, step)


def test_every_public_deleter_is_a_registry_step_or_named_a_non_deleter():
    declared = {step.fn for _row, step in _steps()}
    found = _public_deleter_names()
    assert found - declared - set(NON_DELETERS) == set(), \
        "a deleter that is not a registry row"
    assert declared - found - {
        "store.publish_tombstone", "recall.refresh_store_caches",
        "refutations.sync_armed_checks", "forget.write_tombstone",
        "forget.splice_checkpoint"} == set(), \
        "a step that is not a deleter by name or a declared non-deletion step"


def test_the_non_deleter_list_names_only_functions_that_exist():
    for dotted in NON_DELETERS:
        module, _, attr = dotted.rpartition(".")
        mod = importlib.import_module(f"daimon_briefing.{module}")
        assert callable(getattr(mod, attr, None)), dotted


def test_a_step_shared_by_two_rows_agrees_with_itself():
    by_fn = {}
    for _row, step in _steps():
        by_fn.setdefault(step.fn, set()).add(step)
    for fn, variants in by_fn.items():
        assert len(variants) == 1, f"{fn} declared two different ways"


def test_the_phases_are_ordered_tombstone_publish_value_id_wholesale_derived():
    assert surfaces.PHASES == ("tombstone", "publish", "value", "id",
                               "wholesale", "derived")


def test_the_team_row_is_split_into_the_own_directory_and_the_known_gap():
    own = surfaces.match("team/R/authors/" + surfaces.own_author_slug()
                         + "/s1.json")
    other = surfaces.match("team/R/authors/someone-else/s1.json")
    assert own.delete == "rewrite" and own.walker == "forget"
    assert [s.fn for s in own.steps] == ["store.scrub_team_copies"]
    assert other.delete == "known-gap" and other.issue == "#600"
    nested = surfaces.match("team/R/projects/a/b/authors/"
                            + surfaces.own_author_slug() + "/s1.json")
    assert nested is own


def test_serialize_log_is_its_own_row_ahead_of_the_log_glob():
    row = surfaces.match("logs/serialize.log")
    assert row.shape == "logs/serialize.log"
    assert [s.fn for s in row.steps] == ["store.scrub_serialize_log"]
    assert surfaces.match("logs/other.log").delete == "exempt-no-plaintext"


def test_the_publish_row_carries_a_step_not_a_deletion():
    row = surfaces.match("team/R/authors/Ada/tombstones.jsonl")
    assert row.walker == "none" and row.delete == "exempt-no-plaintext"
    assert [(s.fn, s.phase) for s in row.steps] == [
        ("store.publish_tombstone", "publish")]


# ---- a step that raised is a Reached that names the type, never the text ---

def test_a_raised_step_is_unreached_and_names_only_the_exception_type():
    secret = "the staging password is correct horse battery staple"
    try:
        raise ValueError(f"bad value {secret}")
    except ValueError as exc:
        got = jsonl.raised(exc)
    assert isinstance(got, jsonl.Reached)
    assert got.reached is False
    assert got.detail == "ValueError"
    assert secret not in repr(vars(got)) and secret not in str(list(got))


def test_without_a_registered_author_the_own_row_matches_nothing(monkeypatch):
    """`{own}` is read from the layer that knows the author; until one is
    registered it matches no directory, so every mirror file reads as the
    open gap rather than as a file forget rewrites."""
    monkeypatch.setattr(surfaces, "_own_author", surfaces._no_author)
    row = surfaces.match("team/R/authors/anyone/s1.json")
    assert row.delete == "known-gap" and row.issue == "#600"
