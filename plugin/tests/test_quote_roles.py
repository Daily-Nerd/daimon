"""#1237: the receipt names whose words a verified quote is, by message role.

Every verified verbatim item already carries `quote_provenance.binding`.
Those bindings resolve to transcript rows with a role (user, assistant,
tool), and no surface read the role back. This file pins the record:
`binding.roles` is the sorted, de-duplicated list of roles behind the quote,
derived at verification from the same per-message views the quote was
checked against. A message-ids binding takes the roles of the messages it
names. A transcript-scan binding names no message, so it takes the channels
that can account for the quote, narrowest first (serializer._scan_roles).
Absent on every receipt written before the field and on a receipt stamped
with no per-message transcript. Role is the message channel, never a human
identity.

The fixtures run the SHIPPING loaders and writer: transcript.from_file on
the Claude Code jsonl shape (where every tool row is flagged `tool_result`
and #359 keeps its id out of quote bindings, so tool output always verifies
by scan) and on markdown and plain-text files (no message ids at all), then
serializer.verify_quotes. The validator tests at the end pin
reject-not-coerce: a role list the serializer did not derive is refused,
never tidied.
"""
import json

import pytest

from daimon_briefing import field_table, provenance, serializer, transcript

_HASH = "a" * 64

# The Claude Code session shape: user and assistant rows carry a uuid, and a
# tool result is a user-typed row whose only payload is tool_result blocks.
_CLAUDE_ROWS = [
    {"type": "user", "uuid": "u-1",
     "message": {"role": "user", "content": "the alpha premise stands firm"}},
    {"type": "assistant", "uuid": "a-2",
     "message": {"role": "assistant", "content": [
         {"type": "text", "text": "the omega conclusion follows cleanly"}]}},
    {"type": "user", "uuid": "t-3",
     "message": {"role": "user", "content": [
         {"type": "tool_result", "tool_use_id": "TU-1",
          "content": "exit status zero and the build is green"}]}},
    {"type": "assistant", "uuid": "a-4",
     "message": {"role": "assistant", "content": [
         {"type": "text", "text": "the beta corollary holds as well"}]}},
]


@pytest.fixture
def claude_messages(tmp_path):
    p = tmp_path / "session.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in _CLAUDE_ROWS) + "\n",
                 encoding="utf-8")
    messages = transcript.from_file(p)
    # The shape the rest of this file relies on: ids on every row, the tool
    # row flagged, so a quote of it can never be bound by id (#359).
    assert [m["id"] for m in messages] == ["u-1", "a-2", "t-3", "a-4"]
    assert messages[2] == {"role": "tool", "id": "t-3", "tool_result": True,
                           "content": "exit status zero and the build is green"}
    return messages


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


def _verify(quote, messages, ids=None):
    item = {"text": "claim", "trust": "verbatim", "quote": quote}
    if ids is not None:
        item["source_message_ids"] = list(ids)
    cp = _checkpoint(item)
    serializer.verify_quotes(
        cp, serializer._render_transcript(messages), messages,
        source_ref=_source(), transcript_hash=_HASH)
    return item


# ---- message-ids bindings: the roles of the messages the ids name -----------


def test_a_quote_bound_to_one_user_message_names_the_user_role(claude_messages):
    item = _verify("the alpha premise stands firm", claude_messages, ["u-1"])
    assert item["quote_verified"] is True
    assert item["quote_provenance"]["binding"] == {
        "mode": "message-ids", "message_ids": ["u-1"], "roles": ["user"]}


def test_a_quote_bound_to_one_assistant_message_names_the_assistant_role(
        claude_messages):
    item = _verify("the omega conclusion follows cleanly", claude_messages,
                   ["a-2"])
    assert item["quote_provenance"]["binding"]["roles"] == ["assistant"]


def test_two_messages_of_one_role_collapse_to_that_one_role(claude_messages):
    """De-duplicated: the list says which channels, not how many turns.
    The quote is stitched across turns, so #974 demotes it, and the roles
    are recorded all the same: the receipt is about the bytes witnessed."""
    item = _verify("omega conclusion ... beta corollary", claude_messages,
                   ["a-2", "a-4"])
    receipt = item["quote_provenance"]
    assert receipt["outcome"] == "verified"
    assert receipt["binding"]["message_ids"] == ["a-2", "a-4"]
    assert receipt["binding"]["roles"] == ["assistant"]
    assert item["trust"] == "inferred"


def test_a_quote_spanning_user_and_assistant_names_both_in_sorted_order(
        claude_messages):
    item = _verify("alpha premise stands ... omega conclusion follows",
                   claude_messages, ["u-1", "a-2"])
    assert item["quote_provenance"]["binding"]["roles"] == ["assistant", "user"]


def test_roles_are_sorted_whatever_order_the_ids_were_cited_in(claude_messages):
    """The list is sorted, not in citation order: the same two channels
    read the same whichever message the extractor cited first. The scoped
    check itself reads the cited messages in citation order (#358), so the
    backward quote cites backward too."""
    forward = _verify("alpha premise stands ... omega conclusion follows",
                      claude_messages, ["u-1", "a-2"])
    backward = _verify("omega conclusion follows ... alpha premise stands",
                       claude_messages, ["a-2", "u-1"])
    assert backward["quote_provenance"]["binding"]["message_ids"] == ["a-2", "u-1"]
    assert (forward["quote_provenance"]["binding"]["roles"]
            == backward["quote_provenance"]["binding"]["roles"]
            == ["assistant", "user"])


def test_roles_survive_the_stitching_demotion_intact(claude_messages):
    """#974 moves only `trust`; the binding, roles included, stays as
    recorded and still validates."""
    item = _verify("alpha premise stands ... omega conclusion follows",
                   claude_messages, ["u-1", "a-2"])
    assert item["trust"] == "inferred"
    receipt = item["quote_provenance"]
    assert receipt["binding"]["roles"] == ["assistant", "user"]
    assert provenance.valid_quote_receipt(receipt)


# ---- transcript-scan bindings: the channels that account for the quote ------


def test_a_quote_of_a_flagged_tool_result_verifies_by_scan_and_names_tool(
        claude_messages):
    """The shape every shipped parser emits: the tool row is flagged, #359
    keeps its id out of the quote binding, the quote verifies by scan, and
    the scan's own attribution says the tool channel holds it."""
    item = _verify("exit status zero and the build is green", claude_messages,
                   ["t-3"])
    assert item["quote_verified"] is True
    assert item["trust"] == "verbatim"
    assert item["quote_provenance"]["binding"] == {
        "mode": "transcript-scan", "message_ids": [], "roles": ["tool"]}


def test_a_quote_stitched_from_assistant_and_tool_names_both_by_scan(
        claude_messages):
    """Cited with the assistant row: the tool id is dropped as a signal, the
    quote no longer fits the assistant row alone, so it falls to scan, where
    no single channel holds every fragment and the roles are the channels
    the fragments were found in. #974 demotes the stitch; the roles stay."""
    item = _verify("omega conclusion ... build is green", claude_messages,
                   ["a-2", "t-3"])
    receipt = item["quote_provenance"]
    assert receipt["binding"]["mode"] == "transcript-scan"
    assert receipt["binding"]["roles"] == ["assistant", "tool"]
    assert receipt["stitching"]["cross_role"] is True
    assert item["trust"] == "inferred"


def test_a_plain_text_file_quote_names_the_user_role(tmp_path):
    """`daimon serialize <file>`: a plain-text file is one id-less user-role
    message, so its quotes can only verify by scan, and the scan names the
    one channel there is."""
    p = tmp_path / "notes.txt"
    p.write_text("the alpha premise stands firm, and that is the whole plan\n",
                 encoding="utf-8")
    messages = transcript.from_file(p)
    assert messages == [{"role": "user", "content": p.read_text().strip()}]
    item = _verify("the alpha premise stands firm", messages)
    assert item["quote_provenance"]["binding"] == {
        "mode": "transcript-scan", "message_ids": [], "roles": ["user"]}


def test_a_markdown_file_quote_names_the_channel_that_said_it(tmp_path):
    p = tmp_path / "chat.md"
    p.write_text("user: the alpha premise stands firm\n"
                 "assistant: the omega conclusion follows cleanly\n",
                 encoding="utf-8")
    messages = transcript.from_file(p)
    assert all("id" not in m for m in messages)
    said_by_assistant = _verify("the omega conclusion follows cleanly",
                                messages)
    assert said_by_assistant["quote_provenance"]["binding"]["roles"] == [
        "assistant"]
    stitched = _verify("alpha premise stands ... omega conclusion follows",
                       messages)
    assert stitched["quote_provenance"]["binding"]["roles"] == [
        "assistant", "user"]
    assert stitched["trust"] == "inferred"


def test_a_sentence_both_channels_said_names_both(tmp_path):
    """An echo across channels: one message in EACH channel holds the whole
    quote, so both can account for it and both are recorded. The stats fold
    reads two channels as mixed; the receipt does not pick one."""
    p = tmp_path / "chat.md"
    p.write_text("user: ship the pin freeze today\n"
                 "assistant: ship the pin freeze today, understood\n",
                 encoding="utf-8")
    item = _verify("ship the pin freeze today", transcript.from_file(p))
    assert item["quote_provenance"]["binding"]["roles"] == ["assistant", "user"]


def test_a_quote_stitched_across_turns_of_one_channel_names_that_channel(
        tmp_path):
    """No single message holds every fragment, but one channel's messages
    joined in transcript order do: the second level of _scan_roles."""
    p = tmp_path / "chat.md"
    p.write_text("assistant: the omega conclusion follows cleanly\n"
                 "user: noted\n"
                 "assistant: the beta corollary holds as well\n",
                 encoding="utf-8")
    item = _verify("omega conclusion ... beta corollary",
                   transcript.from_file(p))
    receipt = item["quote_provenance"]
    assert receipt["stitching"] == {"cross_message": True, "cross_role": False}
    assert receipt["binding"]["roles"] == ["assistant"]


def test_a_host_role_outside_the_three_channels_is_recorded_as_written(
        tmp_path):
    """The record is the transcript's own role string, str()-coerced the
    way stripped_message_views coerces it. Nothing here maps a host's name
    for a channel onto one of the three; stats buckets it as `other`."""
    p = tmp_path / "chat.md"
    p.write_text("system: the gamma directive applies here\n"
                 "user: fine\n", encoding="utf-8")
    item = _verify("the gamma directive applies here", transcript.from_file(p))
    assert item["quote_provenance"]["binding"]["roles"] == ["system"]


def test_a_disproven_binding_falls_to_scan_and_keeps_the_scan_roles(
        claude_messages):
    """#358: a real quote cited against the wrong message loses the binding
    and verifies by whole-transcript scan. The roles then describe the scan,
    the channel that actually holds the quote, not the message the extractor
    wrongly cited."""
    item = _verify("the alpha premise stands firm", claude_messages, ["a-2"])
    assert item["quote_verified"] is True
    assert item["quote_provenance"]["binding"] == {
        "mode": "transcript-scan", "message_ids": [], "roles": ["user"]}


def test_a_not_verified_receipt_carries_no_roles(claude_messages):
    item = _verify("a sentence the transcript never said", claude_messages,
                   ["u-1"])
    assert item["quote_verified"] is False
    binding = item["quote_provenance"]["binding"]
    assert binding == {"mode": "transcript-scan", "message_ids": []}


def test_a_transcript_less_verification_carries_no_roles():
    """The legacy two-arg call (no `messages`): there is no per-message view
    to read a role from, so the receipt stays roles-less. Absent, not an
    empty list: the stats fold reads it as unbound, beside the mix."""
    item = {"text": "claim", "trust": "verbatim",
            "quote": "the alpha premise stands firm"}
    cp = _checkpoint(item)
    serializer.verify_quotes(
        cp, "user: the alpha premise stands firm",
        source_ref=_source(), transcript_hash=_HASH)
    assert item["quote_verified"] is True
    assert item["quote_provenance"]["binding"] == {
        "mode": "transcript-scan", "message_ids": []}


def test_a_fragment_that_only_matches_across_a_line_seam_yields_no_roles(
        tmp_path):
    """The fourth outcome of _scan_roles: the scan matched the joined
    haystack, but no row holds even one fragment on its own, so there is
    no channel to name. The quote straddles the seam between two rendered
    lines, role label included. The bytes were witnessed (quote_verified
    stays true) and the receipt stays roles-less: absent, never an empty
    list, and the stats fold reports it as unbound beside the mix."""
    p = tmp_path / "chat.md"
    p.write_text("user: the alpha premise stands\n"
                 "assistant: firm and final\n", encoding="utf-8")
    messages = transcript.from_file(p)
    item = _verify("premise stands assistant: firm and", messages)
    assert item["quote_verified"] is True
    receipt = item["quote_provenance"]
    assert receipt["binding"] == {"mode": "transcript-scan", "message_ids": []}
    assert provenance.binding_roles(receipt) is None


def test_one_message_holding_the_whole_quote_wins_over_a_channel_join(
        tmp_path):
    """Narrowest first. The user said the whole quote in one message; the
    assistant said it in two turns whose join also holds every fragment.
    Step 1 answers and step 2 is never asked, so the roles name the user
    channel alone, not both."""
    p = tmp_path / "chat.md"
    p.write_text("user: the alpha premise stands firm\n"
                 "assistant: the alpha premise\n"
                 "assistant: stands firm\n", encoding="utf-8")
    messages = transcript.from_file(p)
    quote = "the alpha premise ... stands firm"
    # Both levels would match: pin that the assistant join holds every
    # fragment in order, so the single-channel answer below is a choice the
    # code made, not an accident of the fixture.
    fragments = serializer._quote_fragments(quote)
    _per_message, role_joins = serializer._attribution_views(
        [(m["role"], serializer._render_line(i, m, m["content"]))
         for i, m in enumerate(messages)])
    assert serializer._fragments_in_order(fragments, role_joins["assistant"])
    item = _verify(quote, messages)
    assert item["quote_provenance"]["binding"]["roles"] == ["user"]


def test_scan_roles_is_none_when_attribution_is_impossible():
    """The same None contract _stitching_flags has (its own test pins it the
    same way): no usable fragments, or no per-message text survived
    stripping, is "cannot tell", never an empty list. verify_quotes cannot
    reach this through a verified scan (a verified scan always has a
    fragment and a non-empty haystack), so the function's contract is
    pinned directly, as the sibling's is."""
    fragments = serializer._quote_fragments("the alpha premise stands firm")
    assert serializer._scan_roles([], [("user", "the alpha premise")],
                                  {"user": "the alpha premise"}) is None
    assert serializer._scan_roles(fragments, [("user", ""), ("tool", "")],
                                  {"user": "", "tool": ""}) is None


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


def test_quote_receipt_stores_roles_on_a_scan_binding_too():
    receipt = _receipt(binding_mode="transcript-scan", message_ids=(),
                       roles=["tool"])
    assert receipt is not None
    assert receipt["binding"] == {"mode": "transcript-scan",
                                  "message_ids": [], "roles": ["tool"]}
    assert provenance.binding_roles(receipt) == ["tool"]


def test_quote_receipt_without_roles_stays_bare():
    """The pre-field shape, still the shape of every receipt written before
    #1237: valid, and read back as "cannot tell", never as an empty list."""
    for kw in ({}, {"binding_mode": "transcript-scan", "message_ids": ()}):
        receipt = _receipt(**kw)
        assert receipt is not None
        assert "roles" not in receipt["binding"]
        assert provenance.valid_quote_receipt(receipt)
        assert provenance.binding_roles(receipt) is None


def test_binding_roles_is_none_for_junk():
    assert provenance.binding_roles({"binding": {"roles": ["user"]}}) is None


@pytest.mark.parametrize("roles", [
    ["user", "assistant"],      # unsorted
    ["user", "user"],           # duplicated
    [],                         # empty: a verified quote has at least one role
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


def test_a_scan_binding_has_no_id_ceiling_on_its_roles():
    """The never-more-roles-than-ids rule is about bound messages; a scan
    binding has none and its roles come from the whole transcript."""
    receipt = _receipt(binding_mode="transcript-scan", message_ids=(),
                       roles=["assistant", "tool", "user"])
    assert receipt is not None


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
    assert "transcript-scan binding, the channels that can account" in shape
