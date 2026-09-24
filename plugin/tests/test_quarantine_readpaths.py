"""#1109 PR 2: quarantine's whole reason to be value-keyed rather than
id-keyed (design §2) is that an item id is not stable across carry and
re-extraction. These tests build the id-divergence through the SHIPPING
writers — `store.write_checkpoint` (which stamps ids via
`policy.stamp_item_ids`) and `carry.merge` — never a hand-shaped fixture,
per the fix-963 lesson (hand-shaped fixtures hid three real data-loss holes
there).
"""
from daimon_briefing import briefing, carry, normalize, store, trust

_QVALUE = "the deploy key rotation runbook was fabricated by the agent"


def _qkeys(text, kind):
    return {(kind, normalize.content_key(text))}


def test_quarantine_survives_straight_carry_under_the_same_id(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    prev = {
        "session_id": "P1",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [{"text": _QVALUE, "trust": "inferred"}],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }
    store.write_checkpoint("P1", prev, project_dir="/repo/q")
    stored_prev = store.read_latest_body(project_dir="/repo/q",
                                         route=store.Route.OWN,
                                         admit=store.Admit.ANY)
    stamped_id = stored_prev["working_context"]["open_questions"][0]["id"]
    assert stamped_id  # the real writer minted one

    trust.propose(text=_QVALUE, kind="question", reason="fabricated finding",
                  evidence=["issue:1109"], channel="cli-tty",
                  project_dir="/repo/q")
    quarantine = trust.active_value_keys(project_dir="/repo/q")

    new_cp = {"session_id": "S2",
             "working_context": {"active_topic": {"text": "next", "trust": "inferred"},
                                 "open_questions": [], "recent_decisions": []},
             "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                                    "contradictions_flagged": []}}
    merged = carry.merge(new_cp, stored_prev, now=1_760_000_100.0)

    # Carry itself forwards the value UNCHANGED, under the SAME id — carry.py
    # needs no quarantine-awareness of its own (design §3's "withhold, don't
    # drop"): the value is still fully present in the merged checkpoint.
    carried = merged["working_context"]["open_questions"]
    assert any(i["text"] == _QVALUE and i["id"] == stamped_id for i in carried)

    # Only the render-time withhold check drops it, and it does so by VALUE —
    # the id happens to match here too, but this call never consults it.
    filtered, withheld, _candidates = briefing.withhold(
        merged, {}, quarantine=quarantine)
    assert filtered["working_context"]["open_questions"] == []
    assert len(withheld) == 1


def test_quarantine_survives_a_reworded_twin_with_a_different_id(
        tmp_checkpoint_dir, monkeypatch):
    # A re-extraction can restate the same fact with a different SURFACE form
    # (case, whitespace) — `policy.stamp_item_ids` hashes the RAW text, so
    # this mints a genuinely different id than the original, exactly the
    # id-divergence design §2 names. `normalize.content_key` folds case and
    # whitespace, so the two texts still share one value_key.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    reworded = "  THE Deploy Key Rotation Runbook Was Fabricated By The Agent  "
    cp = {
        "session_id": "S1",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [
                {"text": _QVALUE, "trust": "inferred"},
                {"text": reworded, "trust": "inferred"},
            ],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }
    store.write_checkpoint("S1", cp, project_dir="/repo/qr")
    stored = store.read_latest_body(project_dir="/repo/qr", route=store.Route.OWN,
                                    admit=store.Admit.ANY)
    items = stored["working_context"]["open_questions"]
    assert items[0]["id"] != items[1]["id"]  # genuinely different ids
    assert normalize.content_key(items[0]["text"]) == \
        normalize.content_key(items[1]["text"])  # same value once canonicalized

    trust.propose(text=_QVALUE, kind="question", reason="fabricated finding",
                  evidence=["issue:1109"], channel="cli-tty",
                  project_dir="/repo/qr")
    quarantine = trust.active_value_keys(project_dir="/repo/qr")

    filtered, withheld, _candidates = briefing.withhold(
        stored, {}, quarantine=quarantine)
    assert filtered["working_context"]["open_questions"] == []
    assert len(withheld) == 2  # both ids withheld — neither carries an exemption


def test_reworded_enough_to_change_canonical_text_is_not_withheld(
        tmp_checkpoint_dir, monkeypatch):
    # The documented tradeoff (design §2): canonicalization is textual, never
    # semantic — a genuine paraphrase produces a different key and is NOT
    # caught. Pinned here so a future reader sees this is the accepted gap,
    # not a surprise regression.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    paraphrase = "the runbook covering deploy key rotation was made up by the model"
    cp = {"session_id": "S1",
         "working_context": {"active_topic": {"text": "t", "trust": "inferred"},
                             "open_questions": [{"text": paraphrase, "trust": "inferred"}],
                             "recent_decisions": []},
         "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                                "contradictions_flagged": []}}
    trust.propose(text=_QVALUE, kind="question", reason="fabricated finding",
                  evidence=["issue:1109"], channel="cli-tty",
                  project_dir="/repo/qp")
    quarantine = trust.active_value_keys(project_dir="/repo/qp")
    filtered, withheld, _candidates = briefing.withhold(
        cp, {}, quarantine=quarantine)
    assert filtered is cp
    assert withheld == []
