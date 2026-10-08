"""The term functions live in `terms`, a leaf module (#1132 PR 9a D9.0)."""

import ast
from pathlib import Path

from daimon_briefing import carry, recall, terms


def test_terms_imports_nothing_from_daimon():
    tree = ast.parse(Path(terms.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import in terms.py"
            assert not (node.module or "").startswith("daimon"), node.module
        elif isinstance(node, ast.Import):
            assert not any(a.name.startswith("daimon") for a in node.names)


def test_recall_still_exports_the_three_public_names():
    assert recall.salient_terms is terms.salient_terms
    assert recall.is_machine_prompt is terms.is_machine_prompt
    assert recall.credited_terms is terms.credited_terms


def test_min_overlap_stays_in_recall():
    assert recall._MIN_OVERLAP == 2
    assert not hasattr(terms, "_MIN_OVERLAP")


def test_carry_takes_its_terms_from_the_leaf_module():
    assert carry.terms is terms
    assert not hasattr(carry, "recall")


def test_salient_terms_behaves_as_before():
    assert terms.salient_terms("please fix the auth_token refresh") == [
        "auth_token", "refresh"]
    assert terms.salient_terms("hi") == []


def test_salient_terms_stop_at_the_cap():
    prompt = " ".join(f"token{chr(97 + i // 26)}{chr(97 + i % 26)}x"
                      for i in range(40))
    got = terms.salient_terms(prompt)
    assert len(got) == terms._TERM_CAP == 24
    assert got[0] == "tokenaax"
