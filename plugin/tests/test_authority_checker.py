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

from tests._handsal_acts import ACCEPTED_ACTS

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


def test_every_machine_status_code_exists_in_the_table():
    table = json.loads(authority.reason_classes_bytes())
    for code in authority.NOT_SET_UP_CODES:
        assert code in table["default"], code


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

def test_enrolled_true_is_enrolled():
    assert authority.machine_status({"enrolled": True, "reason": "ok"}) is \
        MachineStatus.ENROLLED


@pytest.mark.parametrize("flag", [1, "true", None])
def test_enrolled_must_be_exactly_true(flag):
    assert authority.machine_status({"enrolled": flag, "reason": "ok"}) is \
        MachineStatus.BROKEN


@pytest.mark.parametrize("code", sorted([
    "pin_missing", "pin_probe_unavailable", "pin_probe_unprotected",
    "store_missing"]))
def test_the_not_set_up_codes(code):
    assert authority.machine_status({"enrolled": False, "reason": code}) is \
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
    assert authority.machine_status(answer) is MachineStatus.BROKEN


def test_a_missing_binary_is_not_set_up_and_other_failures_are_broken():
    assert authority.machine_status(CheckerFailure.BINARY_MISSING) is \
        MachineStatus.NOT_SET_UP
    assert authority.machine_status(CheckerFailure.NONZERO_EXIT) is \
        MachineStatus.BROKEN
    assert authority.machine_status(CheckerFailure.UNPARSEABLE) is \
        MachineStatus.BROKEN


def test_the_not_set_up_codes_are_the_issue_list_and_no_more():
    assert authority.NOT_SET_UP_CODES == frozenset({
        "pin_missing", "pin_probe_unavailable", "pin_probe_unprotected",
        "store_missing"})


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
