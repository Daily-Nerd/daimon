"""#1379: daimon's builder, mapping and count run against the real handsal checker.

#1374 and #1377 were tested against handsal's published data. Here the same
code meets the checker itself (`handsal-check-conformance`, run as a program
from the pinned commit) and a real signature verifier (`vitni-verify`), so a
change on either side that the data could not show fails this file.

What this proves, and what it does not. The acts in
`handsal_contract_data/acts.json` are signed with handsal's FIXTURE keys and
are valid on the CONFORMANCE build with the FIXTURE profile only. The file
proves the rules daimon relies on (what the builder hands over, how the
answers map, who signed, how many lines the screen shows). It does not prove a
production install, its pin or its store.

The checker and the verifier come from two environment variables, set by the
`pytest (py3.13)` CI job and by hand locally (see
`handsal_contract_data/README.md`). With them unset every test here skips on a
developer machine; in the job that sets DAIMON_HANDSAL_CONTRACT=required (the
fetch step does) a missing piece FAILS (`_handsal_contract.resolve`,
pinned by `test_handsal_contract_support.py`). On Python 3.10 the file skips
by design: the checker needs 3.11.
"""
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

from daimon_briefing import authority

from tests import _handsal_contract as support

DATA = json.loads(
    (Path(__file__).parent / "handsal_contract_data" / "acts.json")
    .read_text(encoding="utf-8"))
CASES = sorted(DATA["cases"])
# What `recheck-act` refuses to be given: it names no moment and no view.
TIMED_KEYS = ("next", "now", "seen", "synced_at", "tip_statement")


@pytest.fixture(scope="module")
def contract():
    return support.resolve(dict(os.environ), sys.version_info)


# ---- reading what the checker was given --------------------------------------

def _unb64u(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hashstr(raw):
    return "u" + base64.urlsafe_b64encode(
        b"\x12\x20" + hashlib.sha256(raw).digest()).rstrip(b"=").decode()


def _payload(jws):
    return json.loads(_unb64u(jws.split(".")[1]))


def _header(jws):
    return json.loads(_unb64u(jws.split(".")[0]))


def _decision(case, **changes):
    """What daimon hands the signing side for the case's stored row."""
    entry = DATA["cases"][case]
    built = authority.build_decision(entry["verb"], entry["row"])
    built.update(changes)
    return built


def _check_input(case, **changes):
    built = _decision(case)
    data = dict(DATA["view"], jws=DATA["cases"][case]["jws"],
                expect_method=DATA["expect_method"],
                consumer=built["consumer"], action=built["action"],
                subject=built["subject"],
                decision_b64=built["decision_b64"])
    data.update(changes)
    return {k: v for k, v in data.items() if v is not _DROP}


_DROP = object()


def _recheck_input(case, **changes):
    data = _check_input(case, **changes)
    return {k: v for k, v in data.items() if k not in TIMED_KEYS}


def _ask(contract, command, payload):
    answer = support.run_checker(contract, command, payload)
    assert not isinstance(answer, authority.CheckerFailure), (
        f"{command} gave no answer: {answer}")
    return answer


def _table_class(code):
    table = json.loads(authority.reason_classes_bytes())
    own = table["by_command"].get("recheck-act", {})
    return own.get(code) or table["default"].get(code)


# ---- 1. the builder ----------------------------------------------------------

def test_the_acts_were_made_at_the_pinned_commit(contract):
    assert DATA["handsal_commit"] == authority.HANDSAL_PIN.commit


@pytest.mark.parametrize("case", CASES)
def test_the_builder_output_is_what_the_act_was_signed_over(contract, case):
    built = _decision(case)
    payload = _payload(DATA["cases"][case]["jws"])
    act = payload["ext"]["dev.handsal/act"]
    assert payload["method"] == DATA["expect_method"]
    assert act["body"] == {"action": built["action"],
                           "consumer": built["consumer"],
                           "subject": built["subject"]}
    assert payload["inputs_hash"] == _hashstr(
        base64.b64decode(built["decision_b64"]))

    checked = _ask(contract, "check-act", _check_input(case))
    assert (checked["reason"], checked["valid"]) == ("ok", True)
    rechecked = _ask(contract, "recheck-act", _recheck_input(case))
    assert (rechecked["reason"], rechecked["rechecked"]) == ("ok", True)


# ---- 2. the mapping, on real answers -----------------------------------------

def _other_text(case):
    return base64.b64encode(b"some other words").decode("ascii")


def _flipped_signature(case):
    head, body, sig = DATA["cases"][case]["jws"].split(".")
    middle = len(sig) // 2
    swapped = "B" if sig[middle] == "A" else "A"
    return ".".join((head, body, sig[:middle] + swapped + sig[middle + 1:]))


# (name, changes to the recheck input, reason the checker gives, its class,
# the outcome daimon must read it as)
SCENARIOS = [
    ("good", lambda c: {}, "ok", "ok", "proven"),
    ("another consumer", lambda c: {"consumer": "archive"},
     "consumer_mismatch", "validity", "refuted"),
    ("changed content", lambda c: {"decision_b64": _other_text(c)},
     "inputs_mismatch", "validity", "refuted"),
    ("changed signature", lambda c: {"jws": _flipped_signature(c)},
     "bad_signature", "validity", "refuted"),
    ("malformed request", lambda c: {"consumer": _DROP},
     "malformed_input", "input", "undetermined"),
]


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("name,changes,reason,cls,verdict", SCENARIOS,
                         ids=[s[0] for s in SCENARIOS])
def test_the_outcome_over_the_checkers_real_answer(
        contract, case, name, changes, reason, cls, verdict):
    payload = _recheck_input(case, **changes(case))
    answer = _ask(contract, "recheck-act", payload)
    code = answer.get("error", answer.get("reason"))
    assert code == reason
    # the code is in the vendored table with the class daimon expects
    assert _table_class(code) == cls
    outcome = authority.recheck_outcome(answer, payload["jws"])
    if verdict == "proven":
        assert outcome == authority.Proven(DATA["cases"][case]["jws"])
    elif verdict == "refuted":
        assert outcome == authority.Refuted(reason)
    else:
        assert outcome == authority.Undetermined(
            reason, authority.Fault.BAD_REQUEST)


# ---- 3. who signed -----------------------------------------------------------

@pytest.mark.parametrize("case", CASES)
def test_the_performer_is_the_person_of_the_signing_key(contract, case):
    jws = DATA["cases"][case]["jws"]
    answer = _ask(contract, "recheck-act", _recheck_input(case))
    proven = authority.recheck_outcome(answer, jws)
    assert isinstance(proven, authority.Proven)
    kid = _header(jws)["kid"]
    people = [d["person"] for d in DATA["view"]["lineage"][0]["doc"]["devices"]
              if d["kid"] == kid]
    assert people and authority.performer_of(proven) == people[0]
    assert authority.claimed_signing_time(proven) == _payload(jws)["ts"]


# ---- 4. the count ------------------------------------------------------------

_EXACT_2048 = "\n".join(["x" * 63] * 31 + ["y" * 64])

TEXTS = [
    ("empty", ""),
    ("one line", "keep it"),
    ("final lf", "a\n"),
    ("blank line between", "a\n\nb"),
    ("2048 bytes in 32 lines", _EXACT_2048),
    ("2049 bytes", "x" * 2049),
    ("32 lines", "\n".join(["x"] * 32)),
    ("33 lines", "\n".join(["x"] * 33)),
    ("96 wide", "x" * 96),
    ("97 wide continues", "x" * 97),
    ("300 wide continues", "x" * 300),
    ("12 accents fill a line", "é" * 12),
    ("13 accents continue", "é" * 13),
    ("escape moves whole", "x" * 95 + "é"),
    ("768 control bytes", "\x01" * 768),
    ("769 control bytes", "\x01" * 769),
    ("multi-byte over 2048", "é" * 1025),
    ("tab and cr", "a\tb\r\nc"),
]


def _render_payload(text):
    """The `render` input for `text` on the good act's payload: the checker
    binds the attachment to the payload by hash, so only `inputs_hash` moves."""
    jws = DATA["cases"]["ratify"]["jws"]
    payload = _payload(jws)
    content = authority.content_bytes(text)
    payload["inputs_hash"] = _hashstr(content)
    return {"attachments": {
                "decision_b64": base64.b64encode(content).decode("ascii")},
            "kid": _header(jws)["kid"], "method": DATA["expect_method"],
            "payload": payload}


def _block_lines(rendered):
    lines = rendered["text"].split("\n")
    start = next(i for i, line in enumerate(lines)
                 if line.startswith("----- decision ("))
    end = lines.index("----- end -----")
    block = lines[start + 1:end]
    assert all(line.startswith(("| ", "|+")) for line in block), block
    return block


@pytest.mark.parametrize("name,text", TEXTS, ids=[t[0] for t in TEXTS])
def test_the_count_agrees_with_the_checkers_render(contract, name, text):
    answer = _ask(contract, "render", _render_payload(text))
    fits = authority.signable_problem(text) is None
    if "error" in answer:
        assert answer == {"error": "render_refused"}
        assert not fits, f"daimon says it fits, handsal refuses: {name}"
        return
    assert fits, f"daimon says it does not fit, handsal renders it: {name}"
    content = authority.content_bytes(text)
    assert len(_block_lines(answer)) == authority.rendered_lines(content)


# ---- 5. the copy -------------------------------------------------------------

def test_the_vendored_table_is_the_file_at_the_pinned_commit(contract):
    # `uvx` installs the package only, and the table is not in it, so the one
    # file is read from the public repo at the pinned commit id.
    fetched = support.fetch_bytes(support.reason_classes_url())
    assert fetched == authority.reason_classes_bytes()
    assert hashlib.sha256(fetched).hexdigest() == \
        authority.HANDSAL_PIN.table_sha256


# ---- 6. the acts are what the generator makes now ----------------------------

def test_the_committed_acts_are_what_the_generator_makes(contract):
    """Runs handsal's own generator at the pinned commit and compares its
    output with `acts.json` (`make_acts.py --check`). Needs the network, so
    it lives in the contract job only."""
    commit = authority.HANDSAL_PIN.commit
    raw = support.fetch_bytes(support.source_tar_url())
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            tar.extractall(tmp, filter="data")
        (tree,) = list(Path(tmp).iterdir())
        gen = tree / "conformance" / "vectors" / "_gen"
        proc = subprocess.run(
            ["uv", "run", "--project", str(gen), "--frozen", "python", "-I",
             str(Path(__file__).parent / "handsal_contract_data" /
                 "make_acts.py"),
             "--handsal", str(tree), "--commit", commit, "--check"],
            capture_output=True, text=True, timeout=support.CALL_TIMEOUT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
