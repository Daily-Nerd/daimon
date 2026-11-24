"""#1374: a handsal checker answer becomes an outcome, and a proven act names
who signed it. Both are pure: nothing here runs the checker.

The vendored table is a copy, held honest by four tests: the hash pin, the
class set, the machine-status codes and the pin-bump comparison.
"""
import base64
import hashlib
import json
from importlib import resources

import pytest

from daimon_briefing import authority
from daimon_briefing.authority import (
    CheckerFailure, Fault, MachineStatus, Proven, ProvenActError, Refuted,
    Undetermined,
)

from tests._handsal_acts import ACCEPTED_ACTS, GENESIS_SIGNATURES

ACT = ACCEPTED_ACTS["ok.approve"]


def _answer(reason, *, rechecked=False, device_class="apple-se-bio"):
    return {"class": device_class, "not_proven": [], "reason": reason,
            "receipt_id": "x", "rechecked": rechecked}


def _outcome(answer):
    return authority.recheck_outcome(answer, ACT)


# ---- the table copy ---------------------------------------------------------

def test_the_packaged_table_hashes_to_the_pin():
    data = authority.reason_classes_bytes()
    assert hashlib.sha256(data).hexdigest() == \
        authority.HANDSAL_PIN.table_sha256
    assert authority.HANDSAL_PIN.commit == \
        "09dc029c2d738c4346d05ea7bf2d9f57b338eb61"


def test_the_table_is_reachable_through_the_installed_package_api():
    """`importlib.resources`, not a path relative to the source tree: this is
    how an installed wheel finds it."""
    ref = resources.files("daimon_briefing") / "_handsal" / "reason-classes.json"
    assert hashlib.sha256(ref.read_bytes()).hexdigest() == \
        authority.HANDSAL_PIN.table_sha256


def test_a_table_that_does_not_hash_to_the_pin_is_refused(monkeypatch):
    monkeypatch.setattr(authority, "HANDSAL_PIN",
                        authority.HandsalPin("c", "0" * 64))
    authority._load_table.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="pin"):
            authority._load_table()
    finally:
        monkeypatch.undo()
        authority._load_table.cache_clear()


def test_the_classes_in_the_table_are_exactly_the_classes_daimon_maps():
    table = json.loads(authority.reason_classes_bytes())
    assert set(table["classes"]) == set(authority.MAPPED_CLASSES)
    # every class any code is given is one of them
    used = set(table["default"].values())
    for overrides in table["by_command"].values():
        used |= set(overrides.values())
    assert used <= set(authority.MAPPED_CLASSES)


def _enrolled_codes(table):
    """Every code an `enrolled` run can resolve: the command's own entry
    (the table has none today) over the default."""
    return set(table["default"]) | set(table["by_command"].get("enrolled", {}))


def test_every_code_enrolled_can_return_has_an_explicit_machine_status():
    table = json.loads(authority.reason_classes_bytes())
    missing = _enrolled_codes(table) - set(authority.ENROLLED_STATUS)
    assert not missing, sorted(missing)
    # and nothing is mapped that the table does not hold
    assert set(authority.ENROLLED_STATUS) <= _enrolled_codes(table)


def test_the_pinned_table_still_classes_stale_view_as_freshness_for_enrolled():
    assert authority._class_of("stale_view", "enrolled") == "freshness"


def test_stale_is_the_status_of_stale_view_alone():
    stale = [c for c, s in authority.ENROLLED_STATUS.items()
             if s is MachineStatus.STALE]
    assert stale == ["stale_view"]


def _pairs(table):
    """Every (command, code) pair with its class, in the lookup order the
    command uses: its own entry, then the default."""
    commands = set(table["by_command"])
    pairs = {(None, code): cls for code, cls in table["default"].items()}
    for command in commands:
        for code in table["default"]:
            pairs[(command, code)] = table["by_command"][command].get(
                code, table["default"][code])
        for code, cls in table["by_command"][command].items():
            pairs[(command, code)] = cls
    return pairs


def table_regressions(old, new):
    """The `(command, code)` pairs of `old` that `new` changed or dropped."""
    new_pairs = _pairs(new)
    bad = []
    for pair, cls in sorted(_pairs(old).items(), key=str):
        if pair not in new_pairs:
            bad.append(f"{pair}: dropped")
        elif new_pairs[pair] != cls:
            bad.append(f"{pair}: {cls} -> {new_pairs[pair]}")
    return bad


def _table(default, by_command=None):
    return {"classes": [], "default": default, "by_command": by_command or {}}


def test_the_real_table_passes_the_pin_bump_check_against_itself():
    table = json.loads(authority.reason_classes_bytes())
    assert table_regressions(table, table) == []


def test_growing_the_table_passes_the_pin_bump_check():
    old = _table({"a": "input"}, {"cmd": {"a": "validity"}})
    new = _table({"a": "input", "b": "ok"}, {"cmd": {"a": "validity"}})
    assert table_regressions(old, new) == []


@pytest.mark.parametrize("new", [
    _table({"a": "validity"}, {"cmd": {"a": "validity"}}),    # default moved
    _table({"a": "input"}, {"cmd": {"a": "input"}}),          # override moved
    _table({"a": "input"}, {"cmd": {}}),                      # override dropped
    _table({}, {"cmd": {"a": "validity"}}),                   # code dropped
    _table({"a": "input"}, {}),                               # command dropped
])
def test_a_class_change_or_a_dropped_pair_fails_the_pin_bump_check(new):
    old = _table({"a": "input"}, {"cmd": {"a": "validity"}})
    assert table_regressions(old, new) != []


# ---- recheck-act: one row at a time -----------------------------------------

def test_ok_with_rechecked_true_is_proven_and_carries_the_act():
    out = _outcome(_answer("ok", rechecked=True))
    assert out == Proven(act=ACT)
    assert out.reason == "ok"


@pytest.mark.parametrize("flag", [False, None, "true", 1, "yes"])
def test_ok_without_rechecked_exactly_true_is_not_proven(flag):
    answer = _answer("ok")
    answer["rechecked"] = flag
    out = _outcome(answer)
    assert isinstance(out, Undetermined)
    assert out.fault is Fault.UNCONFIRMED_OK


def test_ok_with_the_rechecked_key_missing_is_not_proven():
    answer = _answer("ok")
    del answer["rechecked"]
    assert isinstance(_outcome(answer), Undetermined)


def test_rechecked_true_never_proves_a_reason_that_is_not_ok():
    out = _outcome(_answer("bad_signature", rechecked=True))
    assert isinstance(out, Refuted)


@pytest.mark.parametrize("code", ["bad_signature", "unknown_key",
                                  "inputs_mismatch", "schema"])
def test_validity_is_refuted(code):
    out = _outcome(_answer(code))
    assert out == Refuted(reason=code)


def test_stale_view_from_recheck_act_is_refuted_by_the_command_override():
    """`stale_view` is `freshness` by default and `validity` for recheck-act."""
    table = json.loads(authority.reason_classes_bytes())
    assert table["default"]["stale_view"] == "freshness"
    assert _outcome(_answer("stale_view")) == Refuted(reason="stale_view")


@pytest.mark.parametrize("code", ["pin_missing", "store_missing",
                                  "lineage_broken", "fork"])
def test_environment_and_membership_are_undetermined_without_a_fault(code):
    out = _outcome(_answer(code))
    assert out == Undetermined(reason=code, fault=None)


def test_input_is_undetermined_and_a_bad_request_fault():
    out = _outcome(_answer("malformed_input"))
    assert out == Undetermined(reason="malformed_input",
                               fault=Fault.BAD_REQUEST)


def test_an_error_object_is_undetermined_and_a_bad_request_fault():
    out = _outcome({"error": "malformed_input"})
    assert out == Undetermined(reason="malformed_input",
                               fault=Fault.BAD_REQUEST)


@pytest.mark.parametrize("code", [
    "expired",            # time
    "stale_witness",      # freshness
    "already_stored",     # admission
    "store_full",         # capacity
])
def test_classes_recheck_act_never_gives_are_a_fault(code):
    out = _outcome(_answer(code))
    assert out == Undetermined(reason=code, fault=Fault.UNEXPECTED_CLASS)


def test_a_code_not_in_the_table_is_undetermined_and_a_fault():
    out = _outcome(_answer("a_code_from_a_newer_handsal"))
    assert out == Undetermined(reason="a_code_from_a_newer_handsal",
                               fault=Fault.UNKNOWN_CODE)


def test_a_class_daimon_does_not_map_is_undetermined_and_a_fault(monkeypatch):
    monkeypatch.setitem(authority._load_table()["default"], "odd_code",
                        "a_new_class")
    out = _outcome(_answer("odd_code"))
    assert out == Undetermined(reason="odd_code", fault=Fault.UNMAPPED_CLASS)


@pytest.mark.parametrize("failure,fault", [
    (CheckerFailure.NONZERO_EXIT, Fault.NONZERO_EXIT),
    (CheckerFailure.UNPARSEABLE, Fault.UNPARSEABLE),
    (CheckerFailure.BINARY_MISSING, Fault.BINARY_MISSING),
])
def test_a_failed_run_is_undetermined_and_names_what_happened(failure, fault):
    out = authority.recheck_outcome(failure, ACT)
    assert isinstance(out, Undetermined)
    assert out.fault is fault
    assert out.reason == failure.value


@pytest.mark.parametrize("answer", [
    {}, {"reason": 7}, {"reason": None}, {"error": 7}, [], "ok", None, 3,
])
def test_an_answer_with_no_usable_reason_is_undetermined_and_a_fault(answer):
    out = authority.recheck_outcome(answer, ACT)
    assert isinstance(out, Undetermined)
    assert out.fault is Fault.MALFORMED_ANSWER


@pytest.mark.parametrize("device_class", [
    "validity", "ok", "environment", "input", "membership"])
def test_the_class_key_is_the_device_class_and_never_changes_the_outcome(
        device_class):
    """The checker's `class` names the signing device's class. A reader that
    took it for a reason class would be moved by this field."""
    assert _outcome(_answer("ok", rechecked=True,
                            device_class=device_class)) == Proven(act=ACT)
    assert _outcome(_answer("bad_signature", device_class=device_class)) == \
        Refuted(reason="bad_signature")
    assert _outcome(_answer("pin_missing", device_class=device_class)) == \
        Undetermined(reason="pin_missing", fault=None)


def test_only_a_proven_outcome_is_proven():
    assert not isinstance(Refuted("x"), Proven)
    assert not isinstance(Undetermined("x", None), Proven)


# ---- enrolled: machine status -----------------------------------------------

def _status(result):
    return authority.machine_state(result).status


def test_enrolled_true_is_enrolled():
    assert _status({"enrolled": True, "reason": "ok"}) is \
        MachineStatus.ENROLLED


@pytest.mark.parametrize("flag", [1, "true", None])
def test_enrolled_must_be_exactly_true(flag):
    assert _status({"enrolled": flag, "reason": "ok"}) is \
        MachineStatus.BROKEN


@pytest.mark.parametrize("code", sorted([
    "pin_missing", "pin_probe_unavailable", "pin_probe_unprotected",
    "store_missing"]))
def test_the_not_set_up_codes(code):
    assert _status({"enrolled": False, "reason": code}) is \
        MachineStatus.NOT_SET_UP


@pytest.mark.parametrize("answer", [
    {"enrolled": False, "reason": "store_malformed"},
    {"enrolled": False, "reason": "fork"},
    {"enrolled": False, "reason": "a_code_from_a_newer_handsal"},
    {"enrolled": False, "reason": 5},
    {"enrolled": False},
    {"error": "pin_missing"},
    {},
    [],
    None,
])
def test_everything_else_is_broken(answer):
    assert _status(answer) is MachineStatus.BROKEN


def test_a_missing_binary_is_not_set_up_and_other_failures_are_broken():
    assert _status(CheckerFailure.BINARY_MISSING) is \
        MachineStatus.NOT_SET_UP
    assert _status(CheckerFailure.NONZERO_EXIT) is \
        MachineStatus.BROKEN
    assert _status(CheckerFailure.UNPARSEABLE) is \
        MachineStatus.BROKEN


def test_the_not_set_up_codes_are_the_issue_list_and_no_more():
    assert {c for c, s in authority.ENROLLED_STATUS.items()
            if s is MachineStatus.NOT_SET_UP} == {
        "pin_missing", "pin_probe_unavailable", "pin_probe_unprotected",
        "store_missing"}


def test_a_stale_view_is_stale_and_carries_its_reason():
    state = authority.machine_state(
        {"enrolled": False, "reason": "stale_view"})
    assert state == authority.MachineState(MachineStatus.STALE, "stale_view")
    assert _status(
        {"enrolled": False, "reason": "stale_view"}) is MachineStatus.STALE


@pytest.mark.parametrize("code", [
    "epoch_superseded", "view_in_future", "witness_bad_signature",
    "witness_key_mismatch", "witness_missing", "stale_witness"])
def test_other_freshness_codes_are_broken_with_their_own_reason(code):
    state = authority.machine_state({"enrolled": False, "reason": code})
    assert state == authority.MachineState(MachineStatus.BROKEN, code)


@pytest.mark.parametrize("answer,reason", [
    ({"enrolled": False, "reason": "a_code_from_a_newer_handsal"},
     "a_code_from_a_newer_handsal"),
    ({"enrolled": False, "reason": "ok"}, "ok"),
    ({"enrolled": False, "reason": 5}, "no_reason"),
    ({"enrolled": False}, "no_reason"),
    ({"error": "stale_view"}, "stale_view"),
    ({"enrolled": False, "reason": "stale_view", "error": "internal"},
     "internal"),
    ({}, "no_reason"),
    ([], "not_an_object"),
    (None, "not_an_object"),
    ({"enrolled": 1, "reason": "ok"}, "ok"),
])
def test_broken_answers_say_why(answer, reason):
    assert authority.machine_state(answer) == authority.MachineState(
        MachineStatus.BROKEN, reason)


def test_failed_runs_carry_the_failure_as_reason():
    assert authority.machine_state(CheckerFailure.BINARY_MISSING) == \
        authority.MachineState(MachineStatus.NOT_SET_UP, "binary_missing")
    assert authority.machine_state(CheckerFailure.NONZERO_EXIT) == \
        authority.MachineState(MachineStatus.BROKEN, "nonzero_exit")


def test_an_enrolled_machine_carries_ok():
    assert authority.machine_state({"enrolled": True, "reason": "ok"}) == \
        authority.MachineState(MachineStatus.ENROLLED, "ok")


# ---- who signed -------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(ACCEPTED_ACTS))
def test_performer_of_reads_the_performer_from_an_accepted_act(name):
    jws = ACCEPTED_ACTS[name]
    payload = jws.split(".")[1]
    expected = json.loads(
        base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    )["performer_id"]
    assert expected == "alice"
    proven = authority.recheck_outcome(_answer("ok", rechecked=True), jws)
    assert authority.performer_of(proven) == expected


@pytest.mark.parametrize("name,person", [
    ("genesis.sign.alice", "alice"),
    ("genesis.sign.bob", "bob"),
    ("genesis.sign.carol", "carol"),
])
def test_each_act_gives_its_own_performer_and_time(name, person):
    """Distinct people and times, so a constant or a cross-wired value fails.
    The genesis signatures stand in for the payload reader's input only."""
    jws = GENESIS_SIGNATURES[name]
    proven = authority.recheck_outcome(_answer("ok", rechecked=True), jws)
    assert authority.performer_of(proven) == person
    assert authority.claimed_signing_time(proven) == "2026-11-08T10:00:00Z"
    other = authority.recheck_outcome(_answer("ok", rechecked=True), ACT)
    assert authority.claimed_signing_time(other) == "2026-11-08T11:59:00Z"


def test_the_three_people_are_three_different_answers():
    seen = {
        authority.performer_of(Proven(act=jws))
        for jws in GENESIS_SIGNATURES.values()}
    assert seen == {"alice", "bob", "carol"}


def test_the_claimed_signing_time_is_read_from_the_payload():
    proven = Proven(act=ACT)
    assert authority.claimed_signing_time(proven) == "2026-11-08T11:59:00Z"


@pytest.mark.parametrize("not_proven", [
    ACT,
    Refuted(reason="bad_signature"),
    Undetermined(reason="pin_missing", fault=None),
    {"performer_id": "alice"},
    None,
])
def test_performer_of_cannot_be_called_for_anything_but_a_proven_outcome(
        not_proven):
    with pytest.raises(TypeError):
        authority.performer_of(not_proven)
    with pytest.raises(TypeError):
        authority.claimed_signing_time(not_proven)


def _jws_with_payload(raw: bytes) -> str:
    header, _, tail = ACT.split(".")
    payload = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return f"{header}.{payload}.{tail}"


def _payload(jws):
    seg = jws.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def test_a_rebuilt_payload_with_the_same_bytes_decodes():
    """Guards the helpers below: they are not what makes the errors."""
    jws = _jws_with_payload(_canonical(_payload(ACT)))
    assert authority.performer_of(Proven(act=jws)) == "alice"


def _broken_acts():
    base = _payload(ACT)
    pad = ACT.split(".")[1]
    yield "one segment", "nodots"
    yield "two segments", ".".join(ACT.split(".")[:2])
    yield "four segments", ACT + ".x"
    yield "padded", ACT.replace(pad, pad + "=", 1)
    yield "not base64url", ACT.replace(pad, "!" + pad[1:], 1)
    yield "standard alphabet", ACT.replace(pad, pad[:5] + "+/" + pad[7:], 1)
    yield "not json", _jws_with_payload(b"{nope")
    yield "json but not an object", _jws_with_payload(b"[1]")
    yield "whitespace", _jws_with_payload(
        _canonical(base).replace(b'{"', b'{ "', 1))
    yield "not sorted", _jws_with_payload(json.dumps(
        dict(reversed(list(base.items()))), separators=(",", ":")).encode())
    yield "non ascii", _jws_with_payload(
        _canonical({**base, "performer_id": "aliçe"}))
    yield "control byte", _jws_with_payload(
        _canonical(base).replace(b"alice", b"al\x01ce"))
    yield "extra key", _jws_with_payload(_canonical({**base, "extra": "x"}))
    yield "missing key", _jws_with_payload(_canonical(
        {k: v for k, v in base.items() if k != "nonce"}))
    yield "empty payload", _jws_with_payload(b"")


@pytest.mark.parametrize("label,jws", list(_broken_acts()),
                         ids=[label for label, _ in _broken_acts()])
def test_a_proven_act_that_does_not_decode_raises_and_never_defaults(
        label, jws):
    proven = Proven(act=jws)
    with pytest.raises(ProvenActError):
        authority.performer_of(proven)
    with pytest.raises(ProvenActError):
        authority.claimed_signing_time(proven)


def test_a_non_string_act_in_a_proven_outcome_raises():
    with pytest.raises(ProvenActError):
        authority.performer_of(Proven(act=b"bytes"))  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [None, "", 7, ["alice"]])
def test_a_performer_that_is_not_a_non_empty_string_raises(value):
    jws = _jws_with_payload(_canonical({**_payload(ACT), "performer_id": value}))
    with pytest.raises(ProvenActError):
        authority.performer_of(Proven(act=jws))


def test_a_missing_performer_raises_as_a_key_set_breach():
    base = {k: v for k, v in _payload(ACT).items() if k != "performer_id"}
    with pytest.raises(ProvenActError):
        authority.performer_of(Proven(act=_jws_with_payload(_canonical(base))))


@pytest.mark.parametrize("value", [None, "", 7])
def test_a_claimed_time_that_is_not_a_non_empty_string_raises(value):
    jws = _jws_with_payload(_canonical({**_payload(ACT), "ts": value}))
    with pytest.raises(ProvenActError):
        authority.claimed_signing_time(Proven(act=jws))


# ---- enrolled: the two fields must agree ------------------------------------

@pytest.mark.parametrize("answer,reason", [
    ({"enrolled": True, "reason": "bad_signature"}, "bad_signature"),
    ({"enrolled": True, "reason": "stale_view"}, "stale_view"),
    ({"enrolled": True, "reason": "pin_missing"}, "pin_missing"),
    ({"enrolled": True}, "no_reason"),
    ({"enrolled": True, "reason": "ok", "error": "internal"}, "internal"),
    ({"reason": "ok"}, "ok"),
    ({"reason": "stale_view"}, "stale_view"),
    ({"enrolled": "true", "reason": "ok"}, "ok"),
])
def test_enrolled_and_reason_must_agree_or_the_machine_is_broken(
        answer, reason):
    assert authority.machine_state(answer) == authority.MachineState(
        MachineStatus.BROKEN, reason)


def test_the_status_groups_do_not_overlap():
    groups = [set(authority._NOT_SET_UP), set(authority._STALE),
              set(authority._BROKEN)]
    for i, a in enumerate(groups):
        for b in groups[i + 1:]:
            assert not a & b, sorted(a & b)
    assert len(authority._BROKEN) == len(set(authority._BROKEN))
    assert "ok" not in set().union(*groups)


def test_machine_status_wrapper_is_gone():
    assert not hasattr(authority, "machine_status")
