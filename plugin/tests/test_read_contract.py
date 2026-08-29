"""The scoped-reader contract table (#795 stage 1).

Forty cells: ten store states x four (route, admit) combinations, each cell
marked by why it holds — FORCED by shipped behaviour, PARTIAL (body forced,
route fact unobservable today), DEFINITIONAL (no caller exists), or ADDITIVE
(the Marker payload is new). Torn and non-object pointer variants must be
indistinguishable from absent (#139, #817): "yields nothing" is one state.

`fell_back` means "the global pointer yielded an OBJECT" — never "parseable",
never "produced the value" (spec v3.2 S1). Columns 3 and 4 agree on it in
every row because it is a pure route fact (v3.1 amendment 2).
"""

import dataclasses
import json
from pathlib import Path

import pytest

from daimon_briefing import store
from daimon_briefing.store import Admit, Marker, ReadResult, Route

PROJ = "/p/contract"
OTHER = "someone-elses-project"
CREATED = "2026-08-29T00:00:00Z"

C1 = (Route.OWN, Admit.ANY)                        # the old fallback=False
C2 = (Route.OWN, Admit.OWN_OR_UNROUTED)            # no caller today
C3 = (Route.OWN_ELSE_GLOBAL, Admit.ANY)            # the old fallback=True
C4 = (Route.OWN_ELSE_GLOBAL, Admit.OWN_OR_UNROUTED)  # the old read_latest_reportable
COMBOS = [C1, C2, C3, C4]

# "Yields nothing" is one state with three faces (#139 torn, #817 non-object).
NOTHING = {"absent": None, "torn": "{not json", "nonobject": '["x"]'}


def _ck(stamp=None):
    body = {"session_id": "S-contract", "created": CREATED}
    if stamp is not None:
        body["project_slug"] = stamp
    return body


def _write_own(tmp_checkpoint_dir, payload):
    pdir = tmp_checkpoint_dir / store.project_slug(PROJ)
    pdir.mkdir(parents=True, exist_ok=True)
    if payload is not None:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        (pdir / "latest.json").write_text(text, encoding="utf-8")


def _write_global(tmp_checkpoint_dir, payload):
    tmp_checkpoint_dir.mkdir(parents=True, exist_ok=True)
    if payload is not None:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        (tmp_checkpoint_dir / "latest.json").write_text(text, encoding="utf-8")


def _cell(project_dir, route, admit):
    got = store.read_latest_result(project_dir=project_dir, route=route, admit=admit)
    return (got.checkpoint, got.fell_back,
            got.refused.slug if got.refused is not None else None)


# ---- the 40 cells ---------------------------------------------------------
# Expected tuples are (checkpoint-or-None, fell_back, refused-slug-or-None);
# SELF/BODY placeholders are resolved inside the test against the state.


def _self_slug():
    return store.project_slug(PROJ)


# S1 own=self, S2 own=unstamped: every combo returns the body. All FORCED.
@pytest.mark.parametrize("route,admit", COMBOS)
@pytest.mark.parametrize("stamp", ["SELF", None], ids=["S1-own-self", "S2-own-unstamped"])
def test_own_bucket_admitted_bodies(tmp_checkpoint_dir, stamp, route, admit):
    body = _ck(_self_slug() if stamp == "SELF" else None)
    _write_own(tmp_checkpoint_dir, body)
    assert _cell(PROJ, route, admit) == (body, False, None)


# S3 own=other (#789 poisoned bucket): the PATH decides under ANY (scar 0059,
# R2 — FORCED at C1/C3); OWN_OR_UNROUTED refuses with a marker (C2 is the one
# genuinely DEFINITIONAL design choice; C4's marker payload is ADDITIVE).
@pytest.mark.parametrize("route,admit,expect", [
    (*C1, "body"), (*C2, "marker"), (*C3, "body"), (*C4, "marker"),
], ids=["C1-forced", "C2-definitional", "C3-forced", "C4-marker-additive"])
def test_s3_own_stamped_other(tmp_checkpoint_dir, route, admit, expect):
    body = _ck(OTHER)
    _write_own(tmp_checkpoint_dir, body)
    got = store.read_latest_result(project_dir=PROJ, route=route, admit=admit)
    assert got.fell_back is False
    if expect == "body":
        assert (got.checkpoint, got.refused) == (body, None)
    else:
        assert got.checkpoint is None
        assert (got.refused.slug, got.refused.created) == (OTHER, CREATED)


# S4 own yields nothing, global absent: every combo empty. FORCED.
# Asserted across all three "yields nothing" faces of the OWN pointer.
@pytest.mark.parametrize("route,admit", COMBOS)
@pytest.mark.parametrize("own", NOTHING.values(), ids=NOTHING.keys())
def test_s4_nothing_anywhere(tmp_checkpoint_dir, own, route, admit):
    _write_own(tmp_checkpoint_dir, own)
    assert _cell(PROJ, route, admit) == (None, False, None)


# S5 global=self, S6 global=unstamped: OWN routes see nothing (FORCED);
# OWN_ELSE_GLOBAL returns the body with fell_back=True (FORCED — S6 under C4
# is #791's "belongs to nobody and stays readable").
@pytest.mark.parametrize("own", NOTHING.values(), ids=NOTHING.keys())
@pytest.mark.parametrize("stamp", ["SELF", None], ids=["S5-global-self", "S6-global-unstamped"])
@pytest.mark.parametrize("route,admit", COMBOS)
def test_global_admitted_bodies(tmp_checkpoint_dir, stamp, own, route, admit):
    body = _ck(_self_slug() if stamp == "SELF" else None)
    _write_own(tmp_checkpoint_dir, own)
    _write_global(tmp_checkpoint_dir, body)
    expected = (body, True, None) if route is Route.OWN_ELSE_GLOBAL else (None, False, None)
    assert _cell(PROJ, route, admit) == expected


# S7 global=other: C3 admits it (FORCED — today's fallback body, brief labels
# it); C4 refuses with a marker, fell_back STILL True — an object was yielded,
# ADMIT refused it (v3.2 S1). Marker payload ADDITIVE.
@pytest.mark.parametrize("route,admit,expect", [
    (*C1, (None, False, None)), (*C2, (None, False, None)),
    (*C3, "body-fb"), (*C4, (None, True, OTHER)),
], ids=["C1", "C2", "C3-forced-body", "C4-refused-fb-true"])
def test_s7_global_stamped_other(tmp_checkpoint_dir, route, admit, expect):
    body = _ck(OTHER)
    _write_own(tmp_checkpoint_dir, None)
    _write_global(tmp_checkpoint_dir, body)
    got = _cell(PROJ, route, admit)
    assert got == ((body, True, None) if expect == "body-fb" else expect)


# S8-S10: reader identity UNKNOWN. OWN routes have no pointer to read — every
# column-1/2 cell is empty and NO production site may ever land there. Columns
# 3-4: nothing is foreign to a session with no project identity (R3,
# store's shipped identity-less branch), so even a stamped body is admitted,
# and fell_back=True because the value came off the global pointer (v3.1
# amendment 2 — PARTIAL: bodies forced, the route fact has no reader today).
@pytest.mark.parametrize("reader", [None, "", "   "], ids=["none", "empty", "whitespace"])
@pytest.mark.parametrize("route,admit", COMBOS)
@pytest.mark.parametrize("stamp", ["S8", None, OTHER],
                         ids=["S8-global-absent", "S9-unstamped", "S10-stamped"])
def test_identity_less_reader(tmp_checkpoint_dir, stamp, route, admit, reader):
    if stamp == "S8":
        body = None
    else:
        body = _ck(stamp)
        _write_global(tmp_checkpoint_dir, body)
    if body is None or route is Route.OWN:
        assert _cell(reader, route, admit) == (None, False, None)
    else:
        assert _cell(reader, route, admit) == (body, True, None)


# Torn/non-object GLOBAL pointer: S5-S7 collapse to S4, S9-S10 collapse to S8,
# and fell_back stays False — no OBJECT was yielded (v3.2 S1 pins fb here).
@pytest.mark.parametrize("route,admit", COMBOS)
@pytest.mark.parametrize("payload", [NOTHING["torn"], NOTHING["nonobject"]],
                         ids=["global-torn", "global-nonobject"])
@pytest.mark.parametrize("reader", [PROJ, None], ids=["slug-known", "slug-none"])
def test_global_yields_nothing_collapses(tmp_checkpoint_dir, reader, payload, route, admit):
    if reader is not None:
        _write_own(tmp_checkpoint_dir, None)
    _write_global(tmp_checkpoint_dir, payload)
    assert _cell(reader, route, admit) == (None, False, None)


# ---- coercion edges (v3.2 S3: the ADMIT comparison goes in verbatim) -------


@pytest.mark.parametrize("stamp", ["", "   ", 0, False, []],
                         ids=["empty", "whitespace", "zero", "false", "list"])
def test_falsy_stamps_read_as_unrouted(tmp_checkpoint_dir, stamp):
    body = _ck(stamp)
    _write_own(tmp_checkpoint_dir, None)
    _write_global(tmp_checkpoint_dir, body)
    got = store.read_latest_result(project_dir=PROJ, route=Route.OWN_ELSE_GLOBAL,
                                   admit=Admit.OWN_OR_UNROUTED)
    assert (got.checkpoint, got.refused) == (body, None)


def test_non_string_stamp_is_coerced_before_comparing(tmp_checkpoint_dir):
    body = _ck(123)  # str-coerced to "123": truthy, unequal, refused
    _write_own(tmp_checkpoint_dir, None)
    _write_global(tmp_checkpoint_dir, body)
    got = store.read_latest_result(project_dir=PROJ, route=Route.OWN_ELSE_GLOBAL,
                                   admit=Admit.OWN_OR_UNROUTED)
    assert got.checkpoint is None
    assert got.refused.slug == "123"


def test_path_and_str_project_dir_agree(tmp_checkpoint_dir):
    body = _ck(_self_slug())
    _write_own(tmp_checkpoint_dir, body)
    for route, admit in COMBOS:
        assert (_cell(PROJ, route, admit)
                == _cell(Path(PROJ), route, admit))


def test_slug_string_as_project_dir_reads_the_bucket(tmp_checkpoint_dir):
    # cli passes a bare slug as project_dir and survives because project_slug
    # is idempotent — the scoped reader must keep that property.
    body = _ck(_self_slug())
    _write_own(tmp_checkpoint_dir, body)
    got = store.read_latest_result(project_dir=_self_slug(), route=Route.OWN,
                                   admit=Admit.ANY)
    assert got.checkpoint == body


# ---- the two entry points are projections of the same read path ------------
# (The legacy-wrapper equivalence tests lived here through stages 1-3 and
# were deleted with the wrappers at stage 4.)


def test_body_is_the_result_projection(tmp_checkpoint_dir):
    body = _ck(_self_slug())
    _write_own(tmp_checkpoint_dir, body)
    for route, admit in COMBOS:
        assert (store.read_latest_body(project_dir=PROJ, route=route, admit=admit)
                == store.read_latest_result(project_dir=PROJ, route=route,
                                            admit=admit).checkpoint)


# ---- API guards ------------------------------------------------------------


def test_route_and_admit_are_required():
    with pytest.raises(TypeError):
        store.read_latest_body(project_dir=PROJ)
    with pytest.raises(TypeError):
        store.read_latest_result(project_dir=PROJ)
    with pytest.raises(TypeError):
        store.read_latest_body(project_dir=PROJ, route=Route.OWN)
    with pytest.raises(TypeError):
        store.read_latest_body(project_dir=PROJ, admit=Admit.ANY)


@pytest.mark.parametrize("route,admit", [
    ("own", Admit.ANY), (Route.OWN, "any"), (True, Admit.ANY), (Route.OWN, None),
], ids=["route-str", "admit-str", "route-bool", "admit-none"])
def test_bare_values_raise_instead_of_matching(route, admit):
    with pytest.raises(TypeError):
        store.read_latest_body(project_dir=PROJ, route=route, admit=admit)


def test_read_result_is_frozen():
    got = ReadResult(checkpoint=None, fell_back=False, refused=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        got.checkpoint = {}


def test_marker_carries_exactly_slug_and_created():
    # Stdout is a write (scar 0055): the marker must never widen to a body.
    assert [f.name for f in dataclasses.fields(Marker)] == ["slug", "created"]
