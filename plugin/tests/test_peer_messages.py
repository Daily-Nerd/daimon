"""#1350: one pure function decides whether this machine can take custody of a
message another person sent.

An acknowledgement makes the transport delete its copy, so it may only follow
`Custody`. These tests pin the byte grammar (the vectors file is the contract
the client on the other side builds against) and the structure that keeps
`Hold` and `Reject` from ever carrying a path to an acknowledgement."""

import ast
import hashlib
import json
from pathlib import Path
from unittest import mock

import pytest

from daimon_briefing import normalize, peer_messages, redact, requests
from daimon_briefing.peer_messages import Custody, Hold, Reject

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "peer_messages_vectors.json")
    .read_text(encoding="utf-8"))

BASE = {"v": 1, "org": "org-marker", "requester": "member-ana",
        "request_id": "q-0123456789ab", "revision": 1,
        "event_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90"}
OPENED = {**BASE, "type": "opened", "addressee": "member-luis",
          "kind_requested": "work", "blocking": False, "severity": "normal",
          "ask": "review the proposal", "why": "it blocks the note"}


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _vector_body(vector) -> bytes:
    if "body_hex" in vector:
        return bytes.fromhex(vector["body_hex"])
    if "body_repeat" in vector:
        rep = vector["body_repeat"]
        return (rep["char"] * rep["count"]).encode("utf-8")
    return vector["body"].encode("utf-8")


def _forgotten(vector) -> frozenset:
    return frozenset(normalize.content_key(v)
                     for v in vector.get("forgotten", ()))


def _admit(vector):
    return peer_messages.admit(vector["content_type"], _vector_body(vector),
                               _forgotten(vector))


def _describe(decision) -> dict:
    if isinstance(decision, Custody):
        out = {"decision": "custody", "outcome": decision.outcome}
        if decision.outcome == "forgotten":
            out["closed"] = dict(decision.row["closed"])
        return out
    if isinstance(decision, Hold):
        out = {"decision": "hold", "reason": decision.reason}
        if decision.fields:
            out["fields"] = list(decision.fields)
        if decision.closed_fields:
            out["closed_fields"] = dict(decision.closed_fields)
        return out
    return {"decision": "reject", "reason": decision.reason}


@pytest.mark.parametrize("vector", VECTORS["vectors"],
                         ids=[v["name"] for v in VECTORS["vectors"]])
def test_byte_vectors_decide_as_the_contract_says(vector):
    decision = _admit(vector)
    expected = vector["expect"]
    got = _describe(decision)
    # A vector may state only the parts of a decision it is about.
    assert {k: got.get(k) for k in expected} == expected
    assert decision.epoch == VECTORS["admission_epoch"] == peer_messages.ADMISSION_EPOCH


def test_a_custody_row_reproduces_its_bytes_and_hash():
    vector = next(v for v in VECTORS["vectors"] if v["name"] == "request opened, clean")
    body = _vector_body(vector)
    decision = _admit(vector)
    assert decision.row["content_type"] == "daimon.request/1"
    assert decision.row["body_sha256"] == hashlib.sha256(body).hexdigest()
    import base64
    assert base64.b64decode(decision.row["body_b64"], validate=True) == body


def test_only_custody_carries_a_row_so_only_custody_can_lead_to_an_ack():
    seen = set()
    for vector in VECTORS["vectors"]:
        decision = _admit(vector)
        if isinstance(decision, Custody):
            seen.add(("custody", decision.outcome))
            assert decision.row
        else:
            assert isinstance(decision, (Hold, Reject))
            # The structural half: nothing a writer could append or ack from.
            for attr in ("row", "outcome", "body", "body_b64"):
                assert not hasattr(decision, attr), attr
            seen.add(("hold" if isinstance(decision, Hold) else "reject",
                      decision.reason))
    seen.add(("reject", peer_messages.admit("daimon.request/1", "x", frozenset()).reason))
    declared = ({("custody", o) for o in peer_messages.CUSTODY_OUTCOMES}
                | {("hold", r) for r in peer_messages.HOLD_REASONS}
                | {("reject", r) for r in peer_messages.REJECT_REASONS})
    # Every outcome admit can return is declared, and the vectors reach all.
    assert seen == declared


def test_non_bytes_input_is_rejected_never_admitted():
    for body in ("{}", None, 5, bytearray(b"{}"), memoryview(b"{}")):
        got = peer_messages.admit("daimon.request/1", body, frozenset())
        assert got == Reject("malformed_input", peer_messages.ADMISSION_EPOCH)
    got = peer_messages.admit(None, b"{}", frozenset())
    assert isinstance(got, Reject) and got.reason == "malformed_input"


def test_a_real_compact_jws_is_held_and_never_scanned():
    jws = next(v for v in VECTORS["vectors"]
               if v["content_type"] == "signed.act" and v["expect"]["decision"] == "hold")
    assert jws["body"].startswith("eyJ")
    with mock.patch.object(redact, "redact_text", wraps=redact.redact_text) as spy:
        decision = _admit(jws)
    assert isinstance(decision, Hold) and decision.reason == "awaits_verification"
    assert dict(decision.closed_fields) == {"kid": "dev-7f3a91"}
    assert decision.body_sha256 == hashlib.sha256(jws["body"].encode()).hexdigest()
    assert spy.call_count == 0
    # key id and hash only: nothing of the payload survives
    payload = jws["body"].split(".")[1]
    assert payload not in repr(decision)


def test_an_extra_field_body_is_held_as_unsupported_with_no_bytes():
    decision = peer_messages.admit(
        "daimon.request/1", canon({**OPENED, "surprise": 1}), frozenset())
    assert isinstance(decision, Hold) and decision.reason == "unsupported_body"
    assert not decision.closed_fields and not decision.fields


def test_a_secret_pattern_ask_is_held_with_closed_fields_and_no_text():
    secret = "api_key=FAKEFAKEFAKE0000"
    body = canon({**OPENED, "ask": f"use {secret} now"})
    decision = peer_messages.admit("daimon.request/1", body, frozenset())
    assert isinstance(decision, Hold) and decision.reason == "secret_pattern"
    assert decision.fields == ("ask",)
    closed = dict(decision.closed_fields)
    assert closed["addressee"] == "member-luis"
    assert "ask" not in closed and "why" not in closed
    assert secret not in repr(decision) and "FAKEFAKE" not in repr(decision)


def test_a_teammates_tombstone_does_not_forget_but_a_local_one_does():
    # `admit` is handed only the local person's keys; the module has no way
    # to read teammates' published tombstones (see the import census below).
    teammate_only = frozenset({normalize.content_key("a teammate forgot this")})
    body = canon({**OPENED, "ask": "a teammate forgot this"})
    kept = peer_messages.admit("daimon.request/1", body, frozenset())
    assert isinstance(kept, Custody) and kept.outcome == "bytes"
    assert peer_messages.admit("daimon.request/1", body,
                               frozenset({"unrelated"})).outcome == "bytes"
    local = peer_messages.admit("daimon.request/1", body, teammate_only)
    assert isinstance(local, Custody) and local.outcome == "forgotten"
    assert local.row["closed"]["addressee"] == "member-luis"
    assert "body_b64" not in local.row
    assert "a teammate forgot this" not in json.dumps(dict(local.row), default=dict)
    assert "review the proposal" not in json.dumps(dict(local.row), default=dict)


def test_a_forgotten_value_beats_a_secret_pattern_in_the_same_record():
    # The record is deleted as `forget_content_key` deletes a whole record,
    # so nothing is written and nothing the person kept is lost.
    secret = "api_key=FAKEFAKEFAKE0000"
    body = canon({**OPENED, "ask": secret, "why": "ordinary"})
    got = peer_messages.admit("daimon.request/1", body,
                              frozenset({normalize.content_key("ordinary")}))
    assert isinstance(got, Custody) and got.outcome == "forgotten"


def test_redact_text_runs_only_on_prose_fields():
    body = canon({**OPENED, "title": "marker title", "ask": "marker ask"})
    with mock.patch.object(redact, "redact_text", wraps=redact.redact_text) as spy:
        peer_messages.admit("daimon.request/1", body, frozenset())
    scanned = sorted(c.args[0] for c in spy.call_args_list)
    assert scanned == sorted(["marker title", "marker ask", "it blocks the note"])
    closed_values = {str(v) for k, v in OPENED.items()
                     if k not in peer_messages.PROSE_LIMITS}
    assert not closed_values & set(scanned)


def test_a_closed_field_is_never_written_through_redaction():
    # A closed field that would match a rule is refused by its shape, not
    # rewritten: redaction output is never stored for a closed field.
    body = canon({**OPENED, "org": "token=FAKEFAKEFAKE0000"})
    with mock.patch.object(redact, "redact_text", wraps=redact.redact_text) as spy:
        got = peer_messages.admit("daimon.request/1", body, frozenset())
    assert isinstance(got, Hold) and got.reason == "unsupported_body"
    assert spy.call_count == 0


@pytest.mark.parametrize("raw", [
    json.dumps(OPENED, separators=(",", ":"), ensure_ascii=False),            # key order
    json.dumps(OPENED, sort_keys=True, ensure_ascii=False),                   # whitespace
    json.dumps({**OPENED, "ask": "café"}, sort_keys=True,
               separators=(",", ":")),                                        # \u escape
    canon(OPENED).decode().replace('"v":1', '"v":1.0'),                       # float
    canon({**OPENED, "ask": "a/b"}).decode().replace("a/b", "a\\/b"),         # \/ escape
])
def test_non_canonical_encodings_are_held(raw):
    got = peer_messages.admit("daimon.request/1", raw.encode("utf-8"), frozenset())
    assert isinstance(got, Hold) and got.reason == "unsupported_body"


def test_nan_is_not_json_so_it_is_rejected_not_held():
    raw = canon(OPENED).decode().replace('"v":1', '"v":NaN').encode()
    got = peer_messages.admit("daimon.request/1", raw, frozenset())
    assert got == Reject("malformed_body", peer_messages.ADMISSION_EPOCH)


def test_parse_decodes_one_valid_encoding_and_build_produces_it():
    msg = peer_messages.parse("daimon.request/1", canon(OPENED))
    assert msg.type == "opened"
    assert msg.closed["addressee"] == "member-luis"
    assert msg.prose == {"ask": "review the proposal", "why": "it blocks the note"}
    assert peer_messages.build("daimon.request/1", OPENED) == canon(OPENED)
    with pytest.raises(peer_messages.PeerMessageError) as err:
        peer_messages.build("daimon.request/1", {**OPENED, "extra": 1})
    assert err.value.reason == "unsupported_body"


def test_every_type_is_an_event_role_key():
    for content_type, events in peer_messages.CONTENT_TYPES.items():
        for event in events:
            assert event in requests.EVENT_ROLE, (content_type, event)


def test_the_content_types_are_the_ones_the_issue_names():
    assert dict(peer_messages.CONTENT_TYPES) == {
        "daimon.request/1": ("opened", "revised", "withdrawn", "severity_set"),
        "daimon.answer/1": ("accepted", "rejected", "needs_info"),
        "daimon.reply/1": ("replied",),
        "daimon.done/1": ("done",),
    }


def test_every_prose_limit_is_the_constant_requests_owns():
    limits = peer_messages.PROSE_LIMITS
    assert set(limits) == {"title", "ask", "why", "evidence",
                           "severity_reason", "note"}
    assert limits["title"] == requests.TITLE_CHARS
    assert limits["severity_reason"] == requests.SEVERITY_REASON_CHARS
    for name in ("ask", "why", "evidence", "note"):
        assert limits[name] == requests._MAX_TEXT


def test_prose_at_its_limit_is_admitted_and_one_over_is_held():
    at = canon({**OPENED, "title": "t" * requests.TITLE_CHARS})
    over = canon({**OPENED, "title": "t" * (requests.TITLE_CHARS + 1)})
    assert isinstance(peer_messages.admit("daimon.request/1", at, frozenset()), Custody)
    got = peer_messages.admit("daimon.request/1", over, frozenset())
    assert isinstance(got, Hold) and got.reason == "unsupported_body"


def test_severity_and_kind_come_from_requests_tables():
    for level in requests.SEVERITIES:
        got = peer_messages.admit(
            "daimon.request/1", canon({**OPENED, "severity": level}), frozenset())
        assert isinstance(got, Custody), level
    for kind in requests.KINDS:
        got = peer_messages.admit(
            "daimon.request/1", canon({**OPENED, "kind_requested": kind}), frozenset())
        assert isinstance(got, Custody), kind


def test_requests_offers_one_public_request_id_check():
    assert requests.is_request_id("q-0123456789ab") is True
    for bad in ("q-0123456789AB", "q-0123456789a", "x-0123456789ab",
                "q-0123456789ab\n", "", None, 5):
        assert requests.is_request_id(bad) is False, bad


def test_the_module_is_pure_stdlib_and_never_reads_stores():
    tree = ast.parse(Path(peer_messages.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
            if node.level:
                imported |= {"." + a.name for a in node.names}
    siblings = {n for n in imported if n.startswith(".")}
    assert siblings <= {".", ".normalize", ".redact", ".requests"}, siblings
    for name in imported - siblings:
        assert name in {"__future__", "base64", "binascii", "dataclasses", "hashlib",
                        "json", "re", "types", "typing"}, name
    # No file writes, clock reads or env reads in the source text.
    source = Path(peer_messages.__file__).read_text(encoding="utf-8")
    for needle in ("open(", "os.environ", "time.", "datetime", "clock"):
        assert needle not in source, needle


def test_every_decision_carries_the_epoch_and_every_hold_the_hash():
    for vector in VECTORS["vectors"]:
        decision = _admit(vector)
        assert decision.epoch == peer_messages.ADMISSION_EPOCH
        if isinstance(decision, Hold):
            assert decision.body_sha256 == hashlib.sha256(_vector_body(vector)).hexdigest()


def test_vectors_exercise_the_grammar_of_every_content_type():
    outcomes = {(v["content_type"], _admit(v).outcome) for v in VECTORS["vectors"]
                if isinstance(_admit(v), Custody)}
    assert {ct for ct, _ in outcomes} == set(peer_messages.CONTENT_TYPES)
    typed = set()
    for v in VECTORS["vectors"]:
        if v["expect"].get("decision") == "custody" and "body" in v:
            typed.add(json.loads(v["body"])["type"])
    assert typed == {e for evs in peer_messages.CONTENT_TYPES.values() for e in evs}


def test_every_grammar_field_has_a_shape_or_a_limit():
    prose, closed = set(), set()
    for grammar in peer_messages._GRAMMARS.values():
        closed |= set(grammar.closed) | set(grammar.closed_optional)
        prose |= set(grammar.prose) | set(grammar.prose_optional)
    assert prose == set(peer_messages.PROSE_LIMITS)
    assert closed <= set(peer_messages._CLOSED_SHAPE)
    assert not closed & prose
    assert set(peer_messages._GRAMMARS) == {
        e for evs in peer_messages.CONTENT_TYPES.values() for e in evs}


@pytest.mark.parametrize("kind", [[], {}, None, 5, "nonsense"])
def test_an_unreadable_type_is_held_not_raised(kind):
    got = peer_messages.admit("daimon.request/1", canon({**OPENED, "type": kind}),
                              frozenset())
    assert isinstance(got, Hold) and got.reason == "unsupported_body"


def _seg(obj) -> str:
    import base64
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


@pytest.mark.parametrize("act", [
    "A." + _seg({}) + ".c2ln",                    # one char left over: not base64
    _seg({}) + "." + _seg({}) + ".c2l",           # trailing bits set: non-canonical
    _seg([]) + "." + _seg({}) + ".c2ln",          # header not an object
    _seg({}) + "." + _seg([1]) + ".c2ln",         # payload not an object
    _seg({}) + "." + _seg({}) + ".",              # empty signature
    _seg({}) + "." + _seg({}) + ".c2ln.extra",    # four segments
])
def test_a_signed_act_that_is_not_a_compact_jws_is_rejected(act):
    got = peer_messages.admit("signed.act", act.encode(), frozenset())
    assert got == Reject("malformed_act", peer_messages.ADMISSION_EPOCH)


def test_a_lone_surrogate_escape_is_held_not_raised():
    raw = canon(OPENED).replace(b"review the proposal", b"\\ud800")
    got = peer_messages.admit("daimon.request/1", raw, frozenset())
    assert isinstance(got, Hold) and got.reason == "unsupported_body"


def test_a_jws_without_a_usable_key_id_is_still_held_with_no_closed_fields():
    act = _seg({"kid": "has space"}) + "." + _seg({}) + ".c2ln"
    got = peer_messages.admit("signed.act", act.encode(), frozenset())
    assert isinstance(got, Hold) and got.reason == "awaits_verification"
    assert not got.closed_fields
