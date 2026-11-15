"""Every `DAIMON_*` variable the code reads is in the reference, and the
reference names no variable the code does not read.

No single registry in the code owns the variables yet (that is a separate
change), so the reference page is hand-written, and without a check it drifts
both ways: a new variable ships undocumented, and a renamed one lingers on the
page. This test is the check. It reads the quoted `DAIMON_*` names in the
package and the host hooks, and the names on every English page under
`reference/`, and compares the two sets. The Spanish tree must name the same
set, so a variable added to one language cannot be missing from the other.

A name that is deliberately on only one side goes in an allowlist below, with
the reason beside it.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CODE_ROOTS = (REPO / "plugin" / "daimon_briefing", REPO / "hook")
DOCS = {
    "en": REPO / "website" / "docs" / "reference",
    "es": REPO / "website" / "i18n" / "es" / "docusaurus-plugin-content-docs"
    / "current" / "reference",
}

_QUOTED = re.compile(r"""["'](DAIMON_[A-Z0-9]+(?:_[A-Z0-9]+)*)["']""")
_NAME = re.compile(r"\bDAIMON_[A-Z0-9]+(?:_[A-Z0-9]+)*\b")

# Read by the code and absent from the reference, each with the reason.
UNDOCUMENTED = {}

# On the reference and not read by the code, each with the reason.
NOT_READ = {}


def _code_names() -> set[str]:
    names = set()
    for root in CODE_ROOTS:
        for path in root.rglob("*.py"):
            if ".venv" in path.parts:
                continue
            names |= set(_QUOTED.findall(path.read_text(encoding="utf-8")))
    return names


def _doc_names(lang: str) -> set[str]:
    names = set()
    for path in DOCS[lang].rglob("*.md"):
        names |= set(_NAME.findall(path.read_text(encoding="utf-8")))
    return names


def test_every_variable_the_code_reads_is_in_the_reference():
    missing = _code_names() - _doc_names("en") - set(UNDOCUMENTED)
    assert not missing, (
        "read by the code, missing from website/docs/reference: "
        + ", ".join(sorted(missing)))


def test_the_reference_names_no_variable_the_code_does_not_read():
    stale = _doc_names("en") - _code_names() - set(NOT_READ)
    assert not stale, (
        "on website/docs/reference, read by no code: "
        + ", ".join(sorted(stale)))


def test_the_spanish_reference_names_the_same_variables():
    en, es = _doc_names("en"), _doc_names("es")
    assert en == es, (sorted(en - es), sorted(es - en))


def test_an_allowlist_entry_is_still_needed():
    code = _code_names()
    docs = _doc_names("en")
    assert not [n for n in UNDOCUMENTED if n not in code or n in docs]
    assert not [n for n in NOT_READ if n in code or n not in docs]
