"""#1237: the receipt names whose words a verified quote is, by message role.

Every verified verbatim item already carries `quote_provenance.binding.
message_ids`. Those ids resolve to transcript rows with a role (user,
assistant, tool), and no surface read the role back. This file pins the
record: `binding.roles` is the sorted, de-duplicated list of roles behind
the bound ids, derived at verification from the same per-message views the
quote was checked against, present only on a message-ids binding with ids,
absent on a transcript-scan binding and on every receipt written before the
field. Role is the message channel, never a human identity.

The fixtures run the SHIPPING writer, serializer.verify_quotes, so no
hand-shaped receipt can hide a shape the pipeline would write differently.
The validator tests at the end pin reject-not-coerce: a role list the
serializer did not derive is refused, never tidied.
"""
import pytest

from daimon_briefing import field_table, provenance, serializer

_HASH = "a" * 64

# A tool row WITHOUT the `tool_result` flag: a markdown transcript's "tool:"
# row, which is a quotable message (signal_message_ids keys on the flag, so
# this one is never excluded from a quote binding).
_MESSAGES = [
    {"role": "user", "content": "the alpha premise stands firm", "id": "u-1"},
    {"role": "assistant", "content": "the omega conclusion follows cleanly",
     "id": "a-2"},
    {"role": "tool", "content": "exit status zero and the build is green",
     "id": "t-3"},
    {"role": "assistant", "content": "the beta corollary holds as well",
     "id": "a-4"},
]


def _source(session="S-new"):
    return {
        "version": provenance.SOURCE_REF_VERSION,
        "host": "claude-code",
        "session_id": session,
        "locator": "managed",
        "author": "alice",
    }


def _checkpoint(item):
    return {
        "session_id": "S-new",
        "working_context": {
            "active_topic": {"text": "topic", "trust": "inferred"},
            "open_questions": [item],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }


def _verify(quote, ids=None, messages=_MESSAGES):
    item = {"text": "claim", "trust": "verbatim", "quote": quote}
    if ids is not None:
        item["source_message_ids"] = list(ids)
    cp = _checkpoint(item)
    serializer.verify_quotes(
        cp, serializer._render_transcript(messages), messages,
        source_ref=_source(), transcript_hash=_HASH)
    return item


# ---- the record, through the shipping writer -------------------------------


def test_a_quote_bound_to_one_user_message_names_the_user_role():
    item = _verify("the alpha premise stands firm", ["u-1"])
    assert item["quote_verified"] is True
    assert item["quote_provenance"]["binding"] == {
        "mode": "message-ids", "message_ids": ["u-1"], "roles": ["user"]}


def test_a_quote_bound_to_one_assistant_message_names_the_assistant_role():
    item = _verify("the omega conclusion follows cleanly", ["a-2"])
    assert item["quote_provenance"]["binding"]["roles"] == ["assistant"]


def test_two_messages_of_one_role_collapse_to_that_one_role():
    """De-duplicated: the list says which channels, not how many turns.
    The quote is stitched across turns, so #974 demotes it, and the roles
    are recorded all the same: the receipt is about the bytes witnessed."""
    item = _verify("omega conclusion ... beta corollary", ["a-2", "a-4"])
    receipt = item["quote_provenance"]
    assert receipt["outcome"] == "verified"
    assert receipt["binding"]["message_ids"] == ["a-2", "a-4"]
    assert receipt["binding"]["roles"] == ["assistant"]
    assert item["trust"] == "inferred"


def test_a_quote_bound_to_a_tool_row_names_the_tool_role():
    item = _verify("exit status zero and the build is green", ["t-3"])
    assert item["quote_provenance"]["binding"]["roles"] == ["tool"]


def test_a_quote_spanning_assistant_and_tool_names_both():
    item = _verify("omega conclusion ... build is green", ["a-2", "t-3"])
    assert item["quote_provenance"]["binding"]["roles"] == ["assistant", "tool"]


def test_a_quote_spanning_user_and_assistant_names_both_in_sorted_order():
    item = _verify("alpha premise stands ... omega conclusion follows",
                   ["u-1", "a-2"])
    assert item["quote_provenance"]["binding"]["roles"] == ["assistant", "user"]


def test_roles_are_sorted_whatever_order_the_ids_were_cited_in():
    """The list is sorted, not in citation order: the same two channels
    read the same whichever message the extractor cited first. The scoped
    check itself reads the cited messages in citation order (#358), so the
    backward quote cites backward too."""
    forward = _verify("alpha premise stands ... omega conclusion follows",
                      ["u-1", "a-2"])
    backward = _verify("omega conclusion follows ... alpha premise stands",
                       ["a-2", "u-1"])
    assert backward["quote_provenance"]["binding"]["message_ids"] == ["a-2", "u-1"]
    assert (forward["quote_provenance"]["binding"]["roles"]
            == backward["quote_provenance"]["binding"]["roles"]
            == ["assistant", "user"])


def test_a_transcript_scan_binding_carries_no_roles():
    """No cited message means no message to read a role from. Absent, not
    an empty list: the stats fold reads absent-on-transcript-scan as
    `unbound`, a different answer from pre-field."""
    item = _verify("the alpha premise stands firm")
    binding = item["quote_provenance"]["binding"]
    assert binding == {"mode": "transcript-scan", "message_ids": []}


def test_a_disproven_binding_drops_its_roles_with_its_ids():
    """#358: a real quote cited against the wrong message loses the binding
    and verifies by whole-transcript scan. The roles go with the ids they
    described; a role list on a scan receipt would be a claim about
    messages the receipt no longer names."""
    item = _verify("the alpha premise stands firm", ["a-2"])
    assert item["quote_verified"] is True
    binding = item["quote_provenance"]["binding"]
    assert binding["mode"] == "transcript-scan"
    assert "roles" not in binding


def test_a_not_verified_receipt_carries_no_roles():
    item = _verify("a sentence the transcript never said", ["u-1"])
    assert item["quote_verified"] is False
    binding = item["quote_provenance"]["binding"]
    assert binding == {"mode": "transcript-scan", "message_ids": []}


def test_a_host_role_outside_the_three_channels_is_recorded_as_written():
    """The record is the transcript's own role string, str()-coerced the
    way stripped_message_views coerces it. Nothing here maps a host's name
    for a channel onto one of the three; stats buckets it as `other`."""
    messages = _MESSAGES + [
        {"role": "system", "content": "the gamma directive applies here",
         "id": "s-5"}]
    item = _verify("the gamma directive applies here", ["s-5"], messages)
    assert item["quote_provenance"]["binding"]["roles"] == ["system"]


def test_roles_survive_the_stitching_demotion_intact():
    """#974 moves only `trust`; the binding, roles included, stays as
    recorded and still validates."""
    item = _verify("alpha premise stands ... omega conclusion follows",
                   ["u-1", "a-2"])
    assert item["trust"] == "inferred"
    receipt = item["quote_provenance"]
    assert receipt["binding"]["roles"] == ["assistant", "user"]
    assert provenance.valid_quote_receipt(receipt)


# ---- the receipt builder and the validator ---------------------------------


def _receipt(**kw):
    kw.setdefault("binding_mode", "message-ids")
    kw.setdefault("message_ids", ["u-1", "a-2"])
    return provenance.quote_receipt(
        _source(), {"algorithm": "sha256", "scope": "raw-file", "value": _HASH},
        outcome="verified", checked_at="2026-10-26T10:00:00Z", **kw)


def test_quote_receipt_stores_the_roles_it_is_handed():
    receipt = _receipt(roles=["assistant", "user"])
    assert receipt is not None
    assert receipt["binding"]["roles"] == ["assistant", "user"]
    assert provenance.binding_roles(receipt) == ["assistant", "user"]


def test_quote_receipt_without_roles_stays_bare():
    """The pre-field shape, still the shape of every receipt written before
    #1237: valid, and read back as "cannot tell", never as an empty list."""
    receipt = _receipt()
    assert receipt is not None
    assert "roles" not in receipt["binding"]
    assert provenance.valid_quote_receipt(receipt)
    assert provenance.binding_roles(receipt) is None


def test_binding_roles_is_none_for_a_scan_binding_and_for_junk():
    scan = _receipt(binding_mode="transcript-scan", message_ids=())
    assert scan is not None
    assert provenance.binding_roles(scan) is None
    assert provenance.binding_roles({"binding": {"roles": ["user"]}}) is None


@pytest.mark.parametrize("roles", [
    ["user", "assistant"],      # unsorted
    ["user", "user"],           # duplicated
    [],                         # empty: a bound quote has at least one role
    "user",                     # not a list
    ["user", 3],                # not all strings
    ["user", ""],               # an empty role name
    ["assistant", "tool", "user"],  # more roles than bound messages
])
def test_quote_receipt_refuses_a_role_list_it_did_not_derive(roles):
    """Reject-not-coerce, the #831 posture stitching set: the raw value goes
    to the validator and the validator refuses it. Sorting or de-duplicating
    here would mint a list the serializer never derived."""
    assert _receipt(roles=roles) is None


def test_roles_are_refused_on_a_transcript_scan_binding():
    assert _receipt(binding_mode="transcript-scan", message_ids=(),
                    roles=["user"]) is None


def test_roles_are_refused_on_a_message_ids_binding_with_no_ids():
    assert _receipt(message_ids=(), roles=["user"]) is None


def test_valid_quote_receipt_refuses_a_hand_edited_role_list():
    receipt = _receipt(roles=["assistant", "user"])
    assert receipt is not None
    receipt["binding"]["roles"] = ["user", "assistant"]
    assert not provenance.valid_quote_receipt(receipt)
    receipt["binding"]["roles"] = ["assistant", "user"]
    assert provenance.valid_quote_receipt(receipt)


def test_an_explicit_null_role_list_is_not_absent():
    """`roles=None` to the builder means "not handed one", the pre-field
    shape. A stored `"roles": null` is a different thing: a value nobody
    derived, and the validator refuses it the way it refuses a null
    stitching verdict."""
    receipt = _receipt()
    assert receipt is not None
    receipt["binding"]["roles"] = None
    assert not provenance.valid_quote_receipt(receipt)
    assert provenance.binding_roles(receipt) is None


def test_field_table_documents_the_roles_shape():
    row = field_table.rule("item", "quote_provenance")
    shape = " ".join(dict(row.constraints)["shape"])
    assert "roles?: array of string" in shape
    assert "never on transcript-scan" in shape
