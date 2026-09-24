"""Pin for the #1109 Slice 0 extraction: `refutations.py` and `relations.py`
each held an independent `CHANNEL_AUTHORITY`/`CHANNEL_LABEL`/`CHANNELS` copy
that had drifted (relations carries `serializer`/`lab-import`, refutations
carries `mechanical`; both agree on the shared four). These tests pin each
module's EFFECTIVE mapping before and after the extraction to `channels.py`
lands, so the move is provably behavior-preserving.
"""

from daimon_briefing import channels, refutations, relations


def test_refutations_channel_authority_unchanged():
    assert refutations.CHANNEL_AUTHORITY == {
        "cli-agent": "agent",
        "cli-tty": "human",
        "ui": "human",
        "signed": "human",
        "mechanical": "mechanical",
    }
    assert refutations.CHANNELS == frozenset(refutations.CHANNEL_AUTHORITY)


def test_refutations_channel_label_unchanged():
    assert refutations.CHANNEL_LABEL == {
        "cli-agent": "agent-proposed",
        "cli-tty": "ratified (interactive)",
        "ui": "ratified (ui)",
        "signed": "ratified (signed)",
        "mechanical": "mechanically-activated",
    }


def test_relations_channel_authority_unchanged():
    assert relations.CHANNEL_AUTHORITY == {
        "serializer": "agent",
        "lab-import": "agent",
        "cli-agent": "agent",
        "cli-tty": "human",
        "ui": "human",
        "signed": "human",
    }
    assert relations.CHANNELS == frozenset(relations.CHANNEL_AUTHORITY)


def test_relations_has_no_channel_label():
    """Never duplicated: only refutations.py ever rendered a label map."""
    assert not hasattr(relations, "CHANNEL_LABEL")


def test_the_drift_is_exactly_the_documented_two_sets():
    """The two ledgers' effective channel sets differ by design, not by
    accident — refutations carries `mechanical`, relations carries
    `serializer` and `lab-import`; both agree on the shared base four."""
    assert (set(refutations.CHANNEL_AUTHORITY)
            - set(channels.BASE_CHANNEL_AUTHORITY)) == {"mechanical"}
    assert (set(relations.CHANNEL_AUTHORITY)
            - set(channels.BASE_CHANNEL_AUTHORITY)) == {
                "serializer", "lab-import"}
    assert set(channels.BASE_CHANNEL_AUTHORITY) <= set(
        refutations.CHANNEL_AUTHORITY)
    assert set(channels.BASE_CHANNEL_AUTHORITY) <= set(
        relations.CHANNEL_AUTHORITY)


def test_merged_authority_returns_a_fresh_dict_each_call():
    """No two ledgers may ever share the same mutable mapping object."""
    a = channels.merged_authority({"x": "agent"})
    b = channels.merged_authority({"y": "agent"})
    assert "y" not in a
    assert "x" not in b
    assert a is not channels.BASE_CHANNEL_AUTHORITY
