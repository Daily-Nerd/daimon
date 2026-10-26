"""#1237: `daimon stats` reports whose words the verbatim quotes are, by
message role.

The receipt records `binding.roles` (test_quote_roles.py). This file pins the
read: the population (FRESH verified quotes, carried copies excluded, in
whichever order their session files sort), the buckets, the shares over
receipts that carry a role list, the two unanswerable populations named
beside them, the zero-denominator wordings, and the --json tail.

Fixtures are built with the SHIPPING loaders and writers, the same way
test_stats_stitching.py builds its own: transcript.from_file on the Claude
Code jsonl shape (tool rows flagged, so tool output verifies by scan),
serializer.verify_quotes stamps the receipts, policy.stamp_item_ids stamps
the ids, store.write_checkpoint persists and stamps the project slug.
"""
import json

import pytest

from daimon_briefing import cli, policy, provenance, serializer, store, transcript
from daimon_briefing.surfaces import Writer

_HASH = "a" * 64

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
]
_USER = ("the alpha premise stands firm", ["u-1"])
_ASSISTANT = ("the omega conclusion follows cleanly", ["a-2"])
_TOOL = ("exit status zero and the build is green", ["t-3"])
_MIXED = ("alpha premise stands ... omega conclusion follows", ["u-1", "a-2"])


def _source(session):
    return {
        "version": provenance.SOURCE_REF_VERSION,
        "host": "claude-code",
        "session_id": session,
        "locator": "managed",
        "author": "alice",
    }


def _checkpoint(session, items):
    return {
        "session_id": session,
        "working_context": {
            "active_topic": {"text": "topic", "trust": "inferred"},
            "open_questions": list(items),
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }


def _item(text, spec):
    quote, ids = spec
    item = {"text": text, "trust": "verbatim", "quote": quote}
    if ids is not None:
        item["source_message_ids"] = list(ids)
    return item


@pytest.fixture
def messages(tmp_path):
    p = tmp_path / "session.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in _CLAUDE_ROWS) + "\n",
                 encoding="utf-8")
    loaded = transcript.from_file(p)
    assert loaded[2]["tool_result"] is True
    return loaded


def _write(session, items, project_dir, messages):
    """Verify, stamp ids, persist: the same order serialize_strict runs in."""
    cp = _checkpoint(session, items)
    serializer.verify_quotes(
        cp, serializer._render_transcript(messages), messages,
        source_ref=_source(session), transcript_hash=_HASH)
    policy.stamp_item_ids(cp)
    assert store.write_checkpoint(session, cp, project_dir=project_dir,
                                  writer=Writer.HUMAN)
    return cp


def _write_transcript_less(session, items, project_dir):
    """The legacy two-arg verification: no per-message view, so every
    receipt is a scan binding with no role list, the `unbound` shape."""
    cp = _checkpoint(session, items)
    serializer.verify_quotes(
        cp, "user: the alpha premise stands firm",
        source_ref=_source(session), transcript_hash=_HASH)
    policy.stamp_item_ids(cp)
    assert store.write_checkpoint(session, cp, project_dir=project_dir,
                                  writer=Writer.HUMAN)
    return cp


def _stats_json(capsys):
    assert cli.main(["stats", "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def _stats_text(capsys):
    assert cli.main(["stats"]) == 0
    return capsys.readouterr().out


@pytest.fixture
def project(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(d))
    return d


def _write_the_mix(project, messages):
    _write("S1", [_item("user words", _USER),
                  _item("assistant words", _ASSISTANT),
                  _item("tool output", _TOOL),
                  _item("a stitched pair", _MIXED)], project, messages)
    _write_transcript_less("S2", [_item("scanned blind", _USER)], project)


# ---- the mix itself ---------------------------------------------------------


def test_stats_buckets_every_fresh_verified_quote_by_role(project, messages,
                                                          capsys):
    _write_the_mix(project, messages)

    r = _stats_json(capsys)["roles"]
    assert r == {
        "verified": 5, "measured": 4,
        "user_only": 1, "assistant_only": 1, "tool": 1, "mixed": 1,
        "other": 0, "unbound": 1, "pre_field": 0,
        "shares_pct": {"user_only": 25.0, "assistant_only": 25.0,
                       "tool": 25.0, "mixed": 25.0, "other": 0.0}}


def test_tool_output_quoted_from_a_flagged_tool_row_is_tool_involved(
        project, messages, capsys):
    """The shape Claude Code and Kimi emit: the tool row is flagged, its id
    never enters a quote binding (#359), the quote verifies by scan, and the
    bucket still reads tool. A fold keyed on bound ids alone would report
    tool involved 0 forever on every shipped parser."""
    cp = _write("S1", [_item("tool output", _TOOL)], project, messages)
    receipt = cp["working_context"]["open_questions"][0]["quote_provenance"]
    assert receipt["binding"]["mode"] == "transcript-scan"
    r = _stats_json(capsys)["roles"]
    assert r["tool"] == 1
    assert r["unbound"] == 0


def test_a_tool_row_anywhere_in_the_binding_reads_as_tool_involved(
        project, messages, capsys):
    """Tool wins over mixed: a quote that spans the assistant and a tool row
    is "tool involved", because that is the fact a reader of the mix wants
    told apart from two humans-and-agent channels."""
    _write("S1", [_item("spans", ("omega conclusion ... build is green",
                                   ["a-2", "t-3"]))], project, messages)
    r = _stats_json(capsys)["roles"]
    assert r["tool"] == 1
    assert r["mixed"] == 0


def test_a_single_role_outside_the_three_channels_is_other_not_mixed(
        project, tmp_path, capsys):
    """One channel is not a mix. A host that names its channels differently
    gets its own bucket, and the line names it only when it is nonzero."""
    p = tmp_path / "chat.md"
    p.write_text("system: the gamma directive applies here\n"
                 "user: the alpha premise stands firm\n", encoding="utf-8")
    md = transcript.from_file(p)
    _write("S1", [_item("directive", ("the gamma directive applies here", None)),
                  _item("user words", (_USER[0], None))], project, md)
    r = _stats_json(capsys)["roles"]
    assert r["other"] == 1
    assert r["user_only"] == 1
    assert r["mixed"] == 0
    assert r["shares_pct"]["other"] == 50.0
    assert "other role 1 (50.0%)" in _stats_text(capsys)


def test_a_serialize_file_run_reads_as_user_only(project, tmp_path, capsys):
    """`daimon serialize <file>` on plain text: one id-less user-role
    message, quotes verified by scan, and the mix says user only."""
    p = tmp_path / "notes.txt"
    p.write_text("the alpha premise stands firm, and that is the plan\n",
                 encoding="utf-8")
    _write("S1", [_item("user words", (_USER[0], None))], project,
           transcript.from_file(p))
    r = _stats_json(capsys)["roles"]
    assert r["user_only"] == 1
    assert r["unbound"] == 0


def test_the_mix_is_over_the_receipt_not_the_trust_tag(project, messages,
                                                       capsys):
    """#974 demotes the stitched pair to `inferred`. The role mix reads the
    RECEIPT, so a demoted quote still counts in its bucket; keying on
    `trust == "verbatim"` would drop every mixed binding by construction and
    report a mix with no mixed bucket."""
    cp = _write("S1", [_item("a stitched pair", _MIXED)], project, messages)
    assert cp["working_context"]["open_questions"][0]["trust"] == "inferred"
    assert _stats_json(capsys)["roles"]["mixed"] == 1


# ---- populations that must not leak in ---------------------------------------


def _carry(cp):
    """The real carry shape (carry.merge): a deep copy that keeps the id and
    the frozen receipt, stamped carried_from."""
    copy = json.loads(json.dumps(cp["working_context"]["open_questions"][0]))
    copy["carried_from"] = cp["session_id"]
    return copy


@pytest.mark.parametrize("fresh, later", [("S1", "S2"), ("S9", "S2")])
def test_a_carried_copy_is_not_a_fresh_quote_whichever_file_sorts_first(
        fresh, later, project, messages, capsys):
    """A carried item's receipt was minted in the session it came from, so
    it is excluded on `carried_from`. Session files sort by id, not by
    time: with S9 fresh and S2 carried, the copy is walked FIRST. A fold
    that dropped the copy after the walk had already marked its id seen
    counted the fresh quote zero times; the filter now runs inside the walk,
    before the once-per-id rule."""
    first = _write(fresh, [_item("user words", _USER)], project, messages)
    second = _checkpoint(later, [_carry(first)])
    assert store.write_checkpoint(later, second, project_dir=project,
                                  writer=Writer.HUMAN)

    r = _stats_json(capsys)["roles"]
    assert r["verified"] == 1
    assert r["user_only"] == 1


def test_a_copy_that_kept_its_id_counts_once_even_without_the_marker(
        project, messages, capsys):
    """The walk's once-per-id rule backs the marker up: a copy that lost
    `carried_from` but kept its stable id is still one quote."""
    first = _write("S1", [_item("user words", _USER)], project, messages)
    carried = json.loads(json.dumps(
        first["working_context"]["open_questions"][0]))
    second = _checkpoint("S2", [carried])
    assert store.write_checkpoint("S2", second, project_dir=project,
                                  writer=Writer.HUMAN)

    assert _stats_json(capsys)["roles"]["verified"] == 1


def test_a_receipt_written_before_the_field_is_counted_as_pre_field(
        project, messages, capsys):
    """A message-ids receipt with no `roles` list is the shape every bound
    receipt had before #1237. It cannot answer, so it is not in the mix; it
    is counted and named beside the mix."""
    receipt = provenance.quote_receipt(
        _source("S1"), {"algorithm": "sha256", "scope": "raw-file",
                        "value": _HASH},
        outcome="verified", checked_at="2026-10-01T10:00:00Z",
        binding_mode="message-ids", message_ids=["u-1"])
    assert receipt is not None and "roles" not in receipt["binding"]
    old = {"text": "old words", "trust": "verbatim", "quote": _USER[0],
           "quote_verified": True, "quote_provenance": receipt}
    cp = _checkpoint("S1", [old])
    policy.stamp_item_ids(cp)
    assert store.write_checkpoint("S1", cp, project_dir=project,
                                  writer=Writer.HUMAN)
    _write("S2", [_item("user words", _USER)], project, messages)

    r = _stats_json(capsys)["roles"]
    assert r["verified"] == 2
    assert r["measured"] == 1
    assert r["pre_field"] == 1
    assert r["unbound"] == 0
    assert r["user_only"] == 1
    assert r["shares_pct"]["user_only"] == 100.0
    assert "; 1 pre-field" in _stats_text(capsys)


def test_a_not_verified_receipt_is_not_a_verified_quote(project, messages,
                                                        capsys):
    _write("S1", [_item("absent", ("a sentence the transcript never said",
                                   ["u-1"])),
                  _item("user words", _USER)], project, messages)
    r = _stats_json(capsys)["roles"]
    assert r["verified"] == 1
    assert r["user_only"] == 1


def test_another_projects_quotes_never_reach_this_projects_mix(
        project, tmp_path, messages, capsys):
    other = tmp_path / "elsewhere"
    other.mkdir()
    _write("S-other", [_item("tool output", _TOOL)], other, messages)
    _write("S-mine", [_item("user words", _USER)], project, messages)

    r = _stats_json(capsys)["roles"]
    assert r["measured"] == 1
    assert r["tool"] == 0
    assert r["user_only"] == 1


# ---- the zero denominator, in the shapes it comes in ---------------------------


def test_no_verified_quotes_renders_a_line_rather_than_a_mix_of_zeros(project,
                                                                      capsys):
    r = _stats_json(capsys)["roles"]
    assert r == {"verified": 0, "measured": 0, "user_only": 0,
                 "assistant_only": 0, "tool": 0, "mixed": 0, "other": 0,
                 "unbound": 0, "pre_field": 0, "shares_pct": None}
    assert "quote roles: no verified quotes yet" in _stats_text(capsys)


def test_a_corpus_with_no_role_list_says_which_way_it_cannot_answer(project,
                                                                    capsys):
    _write_transcript_less("S1", [_item("scanned blind", _USER)], project)
    r = _stats_json(capsys)["roles"]
    assert r["verified"] == 1
    assert r["measured"] == 0
    assert r["unbound"] == 1
    assert r["shares_pct"] is None
    assert ("quote roles: none of 1 fresh verified quotes carry a role list "
            "(1 unbound, 0 pre-field)" in _stats_text(capsys))


# ---- the two renders ------------------------------------------------------------


_MIX_LINE = ("quote roles: 4 fresh verified quotes by message role: "
             "user only 1 (25.0%), assistant only 1 (25.0%), "
             "tool involved 1 (25.0%), mixed 1 (25.0%); 1 unbound")


def test_the_plain_render_names_the_counts_and_shares(project, messages,
                                                      capsys):
    _write_the_mix(project, messages)
    out = _stats_text(capsys)
    assert "quote roles (this project):" in out
    assert _MIX_LINE in out
    assert "other role" not in out


def test_the_rich_render_carries_the_same_wording(project, messages, capsys,
                                                  monkeypatch):
    monkeypatch.setattr("daimon_briefing.render.supports_rich", lambda: True)
    monkeypatch.setenv("COLUMNS", "400")
    _write_the_mix(project, messages)
    out = _stats_text(capsys)
    assert "quote roles (this project)" in out
    assert _MIX_LINE in out


# ---- the --json contract ---------------------------------------------------------


def test_stats_json_carries_roles_at_the_tail(project, capsys):
    """Key order is part of the --json contract, so a new fact is appended."""
    payload = _stats_json(capsys)
    assert list(payload)[-4:] == ["checks", "stitching", "worldcheck", "roles"]
    assert list(payload["roles"]) == [
        "verified", "measured", "user_only", "assistant_only", "tool",
        "mixed", "other", "unbound", "pre_field", "shares_pct"]


def test_a_verified_receipt_with_no_binding_object_reads_as_unbound(
        project, messages, capsys):
    """Stats reads stored receipts raw and never raises on one it did not
    write. A receipt that says verified but carries no binding object names
    no message and no role, so it is counted as unbound beside the mix,
    and the fresh quote next to it still lands in its bucket."""
    broken = {"text": "half a receipt", "trust": "verbatim", "quote": _USER[0],
              "quote_verified": True,
              "quote_provenance": {"outcome": "verified"}}
    cp = _checkpoint("S1", [broken])
    policy.stamp_item_ids(cp)
    assert store.write_checkpoint("S1", cp, project_dir=project,
                                  writer=Writer.HUMAN)
    _write("S2", [_item("user words", _USER)], project, messages)

    r = _stats_json(capsys)["roles"]
    assert r["verified"] == 2
    assert r["unbound"] == 1
    assert r["user_only"] == 1
    assert "; 1 unbound" in _stats_text(capsys)


@pytest.mark.parametrize("junk", ["{not json", "[1, 2, 3]"])
def test_an_unreadable_checkpoint_never_takes_stats_down(junk, project,
                                                         messages, capsys):
    _write("S1", [_item("user words", _USER)], project, messages)
    from daimon_briefing import config

    (config.checkpoint_dir() / "junk.json").write_text(junk, encoding="utf-8")
    assert _stats_json(capsys)["roles"]["user_only"] == 1
