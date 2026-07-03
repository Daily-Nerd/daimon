import pytest

import seed
import synth


def test_transcript_meets_min_messages_and_embeds_context():
    msgs = synth.make_transcript("BRIEFING TEXT HERE", cycle=1, arm="control")
    assert len(msgs) >= 10
    assert all(m["role"] in ("user", "assistant") for m in msgs)
    assert "BRIEFING TEXT HERE" in msgs[0]["content"]


def test_flip_turn_only_at_flip_cycle():
    def blob(cycle):
        return "\n".join(m["content"] for m in
                         synth.make_transcript("ctx", cycle=cycle, arm="control"))
    assert seed.V2_TOKEN in blob(seed.FLIP_CYCLE)
    assert seed.V2_TOKEN not in blob(seed.FLIP_CYCLE - 1)
    assert seed.V2_TOKEN not in blob(seed.FLIP_CYCLE + 1)


def test_distractors_only_in_distractor_arm():
    control = "\n".join(m["content"] for m in
                        synth.make_transcript("ctx", cycle=2, arm="control"))
    distract = "\n".join(m["content"] for m in
                         synth.make_transcript("ctx", cycle=2, arm="distractor"))
    assert not any(d in control for d in seed.DISTRACTOR_POOL)
    assert any(d in distract for d in seed.DISTRACTOR_POOL)


def test_distractor_choice_varies_by_cycle_but_deterministic():
    a = synth.make_transcript("ctx", cycle=2, arm="distractor")
    b = synth.make_transcript("ctx", cycle=2, arm="distractor")
    c = synth.make_transcript("ctx", cycle=3, arm="distractor")
    assert a == b
    assert a != c


def test_wrapper_leak_raises():
    with pytest.raises(synth.VocabLeakError):
        synth.make_transcript("ctx", cycle=1, arm="control",
                              _extra_wrapper="oops quorint-ledger")
