"""The closed-trust policy of `view.masked`, one table (#1132 PR 11c, B2).

Every declared prose and folded_prose path of every bucket ledger has exactly
one entry, and every entry is a declared path, so a new prose column cannot
ship unpoliced. `True` marks a value copy (it masks under a closed trust
ledger); everything a person authored stays readable.
"""

from daimon_briefing import surfaces, view

VALUE_COPIES = {("events.jsonl", "item_text"), ("trust.jsonl", "reason"),
                ("trust.jsonl", "evidence[]"),
                ("amendments.jsonl", "evidence")}


def _declared():
    out = set()
    for name in surfaces.bucket_ledger_names():
        s = surfaces.bucket_ledger(name)
        for fp in s.prose + s.folded_prose:
            out.add((name, surfaces.path_string(fp)))
    return out


def test_the_policy_is_two_way_against_the_registry():
    declared = _declared()
    assert declared - set(view.MASK_POLICY) == set()
    assert set(view.MASK_POLICY) - declared == set()


def test_no_path_is_declared_twice_in_one_ledger():
    for name in surfaces.bucket_ledger_names():
        s = surfaces.bucket_ledger(name)
        paths = [surfaces.path_string(fp) for fp in s.prose + s.folded_prose]
        assert len(paths) == len(set(paths)), name


def test_only_value_copies_mask_under_a_closed_ledger():
    assert {k for k, closed in view.MASK_POLICY.items() if closed} == (
        VALUE_COPIES)


def test_policy_values_are_booleans():
    assert all(isinstance(v, bool) for v in view.MASK_POLICY.values())
