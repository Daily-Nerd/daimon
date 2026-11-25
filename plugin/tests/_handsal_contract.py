"""What the handsal contract test and the CI step that feeds it share (#1379).

One definition of everything about the pinned checker that is not already in
`authority.HANDSAL_PIN`: where the verifier release lives and its sha256, the
command line of the checker, the two environment variables that carry both to
pytest, the guard that turns a missing piece into a failure where the job demands it, and the one
function that runs the checker. The workflow calls `python
tests/_handsal_contract.py env DIR` and appends its output to `$GITHUB_ENV`, so
no hash or commit is written in the YAML.

The release tar and its sha256 are here and not in `authority.py` because only
the test and CI use them: the package never runs a verifier.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tarfile
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

import pytest

from daimon_briefing import authority

HANDSAL_REPO = "Daily-Nerd/handsal"
# The release the verifier comes from. The tag only builds the download URL;
# what is trusted is the sha256 of the file it serves.
RELEASE_TAG = "v0.4.2"
RELEASE_TAR = "handsal-v0.4.2-linux-amd64.tar"
# From the release's SHA256SUMS, checked when the tar is fetched.
RELEASE_TAR_SHA256 = (
    "85c08bda0962d75b701ca2e64e510a1fe95bc63d781243ea846d7a4d5b3a0ea7")
VERIFIER_MEMBER = "vitni-verify"

# The checker command (shell-quoted words, ending in the conformance entry
# point) and the absolute path of the real verifier. Both or the test cannot run.
ENV_CHECKER = "HANDSAL_CHECK_CONFORMANCE"
ENV_VERIFIER = "HANDSAL_VITNI"
# Set by the one CI step that installs the checker (the py3.13 job), never by
# the runner itself: GitHub sets CI=true in every job, including the ones that
# do not fetch the checker, so CI cannot be the trigger. With this set to
# "required" a missing piece FAILS; without it the contract test skips.
ENV_REQUIRED = "DAIMON_HANDSAL_CONTRACT"
REQUIRED = "required"

# handsal's checker needs Python 3.11; daimon supports 3.10.
MIN_PYTHON = (3, 11)
CALL_TIMEOUT = 300   # the first call builds the checker from source
FETCH_TIMEOUT = 120


def checker_words() -> list[str]:
    """The checker as a program, from the public repo at the pinned commit.
    Never a dependency: nothing about it is in pyproject.toml or uv.lock."""
    source = (f"git+https://github.com/{HANDSAL_REPO}"
              f"@{authority.HANDSAL_PIN.commit}#subdirectory=checker/python")
    return ["uvx", "--from", source, "handsal-check-conformance"]


def reason_classes_url() -> str:
    return (f"https://raw.githubusercontent.com/{HANDSAL_REPO}/"
            f"{authority.HANDSAL_PIN.commit}/conformance/reason-classes.json")


def source_tar_url() -> str:
    """The whole handsal tree at the pinned commit (for the generator check)."""
    return (f"https://codeload.github.com/{HANDSAL_REPO}/tar.gz/"
            f"{authority.HANDSAL_PIN.commit}")


def fetch_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as resp:  # noqa: S310
        return resp.read()


def fetch_verifier(directory: Path) -> Path:
    """Download the release tar, refuse it unless its sha256 is the recorded
    one, and extract the verifier. Returns the verifier's absolute path."""
    url = (f"https://github.com/{HANDSAL_REPO}/releases/download/"
           f"{RELEASE_TAG}/{RELEASE_TAR}")
    directory.mkdir(parents=True, exist_ok=True)
    tar_path = directory / RELEASE_TAR
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as resp, \
            open(tar_path, "wb") as out:  # noqa: S310
        while chunk := resp.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != RELEASE_TAR_SHA256:
        tar_path.unlink()
        raise SystemExit(
            f"{RELEASE_TAR} has sha256 {digest.hexdigest()}, expected "
            f"{RELEASE_TAR_SHA256}")
    target = directory / VERIFIER_MEMBER
    with tarfile.open(tar_path) as tar:
        member = tar.getmember(VERIFIER_MEMBER)
        if not member.isreg():
            raise SystemExit(f"{VERIFIER_MEMBER} is not a regular file")
        source = tar.extractfile(member)
        assert source is not None
        target.write_bytes(source.read())
    target.chmod(0o755)
    tar_path.unlink()
    return target.resolve()


class Contract(NamedTuple):
    checker: list[str]
    verifier: str


def required(environ: Mapping[str, str]) -> bool:
    return environ.get(ENV_REQUIRED) == REQUIRED


def resolve(environ: Mapping[str, str], version: tuple[int, ...]) -> Contract:
    """The checker and verifier to run, or the end of the test: a skip when
    they are not there and the job did not demand them, a FAILURE when it did
    (`DAIMON_HANDSAL_CONTRACT=required`) on a Python that can run them. The only place that decides it."""
    if tuple(version[:2]) < MIN_PYTHON:
        pytest.skip(
            "by design: handsal-check needs Python 3.11 or newer, so the "
            "contract test runs in the py3.13 job only")
    problems = []
    checker = shlex.split(environ.get(ENV_CHECKER, ""))
    if not checker:
        problems.append(f"{ENV_CHECKER} is not set")
    verifier = environ.get(ENV_VERIFIER, "")
    if not verifier:
        problems.append(f"{ENV_VERIFIER} is not set")
    elif not (os.path.isabs(verifier) and os.path.isfile(verifier)
              and os.access(verifier, os.X_OK)):
        problems.append(
            f"{ENV_VERIFIER}={verifier!r} is not an absolute path to an "
            "executable file")
    if problems:
        message = "; ".join(problems)
        if required(environ):
            pytest.fail(
                f"the handsal contract cannot run, and {ENV_REQUIRED}="
                f"{REQUIRED} says this job must run it: {message}. A skip "
                "here would be a quiet pass.", pytrace=False)
        pytest.skip(
            f"handsal contract needs {ENV_CHECKER} (the checker command) and "
            f"{ENV_VERIFIER} (a real vitni-verify): {message}")
    return Contract(checker, verifier)


def run_checker(contract: Contract, command: str, payload: dict):
    """One checker run: the parsed answer (a dict), or the `CheckerFailure`
    that gave none. A hung run is a test error, not an answer."""
    argv = [*contract.checker, "--vitni", contract.verifier, command]
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    try:
        proc = subprocess.run(
            argv, input=data.encode("utf-8") + b"\n", capture_output=True,
            timeout=CALL_TIMEOUT)
    except FileNotFoundError:
        return authority.CheckerFailure.BINARY_MISSING
    if proc.returncode != 0:
        return authority.CheckerFailure.NONZERO_EXIT
    try:
        answer = json.loads(proc.stdout.decode("utf-8"))
    except ValueError:
        return authority.CheckerFailure.UNPARSEABLE
    if not isinstance(answer, dict) or proc.stdout.count(b"\n") != 1:
        return authority.CheckerFailure.UNPARSEABLE
    return answer


def _main(argv: list[str]) -> int:
    """`env DIR`: fetch the verifier into DIR and print the variable
    assignments, one per line, for `$GITHUB_ENV`. The switch is printed here
    so that only the job that fetched the checker demands it."""
    if len(argv) != 2 or argv[0] != "env":
        print("usage: _handsal_contract.py env DIR", file=sys.stderr)
        return 2
    verifier = fetch_verifier(Path(argv[1]))
    print(f"{ENV_VERIFIER}={verifier}")
    print(f"{ENV_CHECKER}={shlex.join(checker_words())}")
    print(f"{ENV_REQUIRED}={REQUIRED}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
