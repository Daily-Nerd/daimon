"""#1379: the guards around `test_handsal_contract.py`, which run everywhere.

The contract test itself needs the pinned checker and a real verifier, so it
skips on a developer machine. These tests need neither: they pin that a
missing piece FAILS in CI instead of skipping, that no test in the contract
file can bypass that, that a run that gives no answer reads the way the
package says it does, and that the YAML holds no second copy of the pin.
"""
import ast
import os
import re
import stat
import sys
from pathlib import Path

import pytest

from daimon_briefing import authority
from tests import _handsal_contract as support

HERE = Path(__file__).parent
# What the contract job sets (via the fetch step). GitHub's own CI=true is in
# every job and means nothing here.
CI_ENV = {support.ENV_REQUIRED: support.REQUIRED}
RUNNER_ONLY = {"CI": "true", "GITHUB_ACTIONS": "true"}


# ---- a skip fails in CI ------------------------------------------------------

@pytest.mark.parametrize("environ", [
    CI_ENV,
    {**CI_ENV, support.ENV_CHECKER: "uvx x"},
    {**CI_ENV, support.ENV_VERIFIER: "/bin/sh"},
    {**CI_ENV, support.ENV_CHECKER: "uvx x",
     support.ENV_VERIFIER: "/no/such/vitni-verify"},
    {**CI_ENV, support.ENV_CHECKER: "uvx x",
     support.ENV_VERIFIER: "relative/vitni-verify"},
], ids=["both missing", "verifier missing", "checker missing",
        "binary missing", "path not absolute"])
def test_a_missing_piece_fails_when_the_job_demands_the_contract(environ):
    with pytest.raises(pytest.fail.Exception, match="must run it"):
        support.resolve(environ, (3, 13, 0))


def test_ci_true_alone_does_not_demand_the_contract():
    # the normal pytest jobs (py3.10, and py3.13 before any fetch) run with
    # CI=true and no checker: they must skip, not error
    with pytest.raises(pytest.skip.Exception):
        support.resolve(RUNNER_ONLY, (3, 13, 0))
    with pytest.raises(pytest.skip.Exception):
        support.resolve(RUNNER_ONLY, (3, 10, 14))


def test_the_fetch_step_output_carries_the_switch(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(support, "fetch_verifier",
                        lambda d: tmp_path / "vitni-verify")
    assert support._main(["env", str(tmp_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert f"{support.ENV_REQUIRED}={support.REQUIRED}" in lines
    assert any(x.startswith(support.ENV_CHECKER + "=") for x in lines)
    assert any(x.startswith(support.ENV_VERIFIER + "=") for x in lines)


def test_the_workflow_never_sets_the_switch_for_every_job():
    assert support.ENV_REQUIRED not in _ci_text()


def test_a_missing_piece_skips_on_a_developer_machine_and_names_the_variables():
    with pytest.raises(pytest.skip.Exception) as info:
        support.resolve({}, (3, 13, 0))
    assert support.ENV_CHECKER in str(info.value)
    assert support.ENV_VERIFIER in str(info.value)


def test_python_3_10_skips_by_design_even_when_demanded():
    with pytest.raises(pytest.skip.Exception, match="by design"):
        support.resolve(CI_ENV, (3, 10, 14))


def test_a_complete_environment_resolves(tmp_path):
    verifier = tmp_path / "vitni-verify"
    verifier.write_text("#!/bin/sh\n")
    verifier.chmod(verifier.stat().st_mode | stat.S_IXUSR)
    got = support.resolve(
        {**CI_ENV, support.ENV_CHECKER: "uvx --from 'a b' tool",
         support.ENV_VERIFIER: str(verifier)}, (3, 13, 0))
    assert got == support.Contract(["uvx", "--from", "a b", "tool"],
                                   str(verifier))


def _contract_functions():
    tree = ast.parse((HERE / "test_handsal_contract.py").read_text("utf-8"))
    return tree, [n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name.startswith("test_")]


def test_every_contract_test_goes_through_the_guard():
    tree, tests = _contract_functions()
    assert tests
    for fn in tests:
        assert "contract" in [a.arg for a in fn.args.args], fn.name


def test_the_contract_file_has_no_skip_of_its_own():
    text = (HERE / "test_handsal_contract.py").read_text("utf-8")
    for banned in ("pytest.mark.skip", "pytest.skip(", "importorskip",
                   "pytest.mark.xfail"):
        assert banned not in text


# ---- a run that gives no answer ----------------------------------------------

def _fake(code):
    return support.Contract([sys.executable, "-c", code], "/unused")


@pytest.mark.parametrize("code,failure", [
    ("import sys; sys.exit(3)", authority.CheckerFailure.NONZERO_EXIT),
    ("print('not json')", authority.CheckerFailure.UNPARSEABLE),
    ("print('[1]')", authority.CheckerFailure.UNPARSEABLE),
    ("print('{}'); print('{}')", authority.CheckerFailure.UNPARSEABLE),
    ("", authority.CheckerFailure.UNPARSEABLE),
])
def test_a_run_without_an_answer_is_a_checker_failure(code, failure):
    got = support.run_checker(_fake(code), "check-act", {"a": 1})
    assert got is failure
    outcome = authority.recheck_outcome(got, "act")
    assert isinstance(outcome, authority.Undetermined)
    assert outcome.fault is authority._FAILURE_FAULT[failure]


def test_a_missing_checker_binary_is_binary_missing():
    got = support.run_checker(
        support.Contract(["/no/such/checker"], "/unused"), "check-act", {})
    assert got is authority.CheckerFailure.BINARY_MISSING


def test_the_answer_line_is_parsed_and_the_input_is_sent():
    code = ("import sys, json; d = json.loads(sys.stdin.read());"
            "print(json.dumps({'got': d, 'argv': sys.argv[1:]}))")
    got = support.run_checker(_fake(code), "render", {"a": "é"})
    assert got == {"got": {"a": "é"},
                   "argv": ["--vitni", "/unused", "render"]}


# ---- one pin ------------------------------------------------------------------

def _ci_text():
    return (HERE.parents[1] / ".github" / "workflows" / "ci.yml").read_text(
        "utf-8")


def test_the_workflow_holds_no_copy_of_the_pin():
    text = _ci_text()
    assert authority.HANDSAL_PIN.commit not in text
    assert support.RELEASE_TAR_SHA256 not in text
    assert not re.search(r"\b[0-9a-f]{64}\b", text)
    assert support.RELEASE_TAR not in text


def test_the_fetch_step_runs_in_the_py3_13_job_before_the_tests():
    text = _ci_text()
    step = re.search(
        r"      - name: Fetch the handsal verifier\n(?P<body>(?:        .*\n)+)",
        text)
    assert step, "the fetch step is missing from ci.yml"
    body = step.group("body")
    assert "matrix.python-version == '3.13'" in body
    assert "tests/_handsal_contract.py env" in body
    assert "GITHUB_ENV" in body
    assert text.index("Fetch the handsal verifier") < text.index("Run tests")
    assert os.path.exists(HERE / "_handsal_contract.py")


# ---- acts.json is not stale (no checker needed) --------------------------------

def test_the_committed_acts_match_what_daimon_builds_and_the_generator_inputs():
    """What daimon's side of acts.json can drift on, checked in every job:
    the pin, the rows, and that the signed payload is over exactly what
    `build_decision` hands over now. The signatures themselves are checked
    against the generator in the contract job."""
    import base64
    import hashlib
    import importlib.util
    import json

    data = json.loads((HERE / "handsal_contract_data" / "acts.json")
                      .read_text("utf-8"))
    assert data["handsal_commit"] == authority.HANDSAL_PIN.commit
    spec = importlib.util.spec_from_file_location(
        "make_acts", HERE / "handsal_contract_data" / "make_acts.py")
    make_acts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make_acts)
    assert {c[0] for c in make_acts.CASES} == set(data["cases"])
    for case, verb, row, _id_field, _text_field in make_acts.CASES:
        entry = data["cases"][case]
        assert (entry["verb"], entry["row"]) == (verb, row)
        built = authority.build_decision(verb, row)
        body = entry["jws"].split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        assert payload["ext"]["dev.handsal/act"]["body"] == {
            "action": built["action"], "consumer": built["consumer"],
            "subject": built["subject"]}
        digest = b"\x12\x20" + hashlib.sha256(
            base64.b64decode(built["decision_b64"])).digest()
        assert payload["inputs_hash"] == "u" + base64.urlsafe_b64encode(
            digest).rstrip(b"=").decode()
