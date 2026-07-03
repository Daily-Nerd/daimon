import json

from daimon_briefing import serializer

import seed


def test_seed_checkpoint_validates():
    cp = seed.make_seed()
    assert serializer.validate(cp)
    assert cp["session_id"] == "cycle-000"
    assert cp["created"] == seed.BASE_CREATED


def test_seed_carries_all_designed_items():
    blob = json.dumps(seed.make_seed())
    for key, tokens in seed.NONCES.items():
        for t in tokens:
            assert t in blob, f"{key} nonce {t} missing from seed"
    assert seed.V1_TOKEN in blob and seed.V2_TOKEN not in blob


def test_oq_stable_backdated_and_scored():
    cp = seed.make_seed()
    oq = cp["working_context"]["open_questions"][0]
    assert oq["importance"] == 7
    assert oq["first_seen"] == "2026-05-02T00:00:00Z"


def test_vocab_screen_catches_leak():
    # a wrapper text that leaks a grading nonce must be reported
    leaks = seed.vocab_screen(["harmless text", "mentions quorint-ledger here"])
    assert leaks == ["quorint-ledger"]
    assert seed.vocab_screen(["all clean"]) == []


def test_distractors_and_fillers_are_leak_free():
    assert seed.vocab_screen(seed.DISTRACTOR_POOL) == []
    assert seed.vocab_screen([u + " " + a for u, a in seed.FILLER_TURNS]) == []
