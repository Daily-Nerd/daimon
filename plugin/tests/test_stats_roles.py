"""#1237: `daimon stats` reports whose words the verbatim quotes are, by
message role.

The receipt records `binding.roles` (test_quote_roles.py). This file pins the
read: the population (FRESH verified quotes, carried copies excluded), the
six buckets, the shares over receipts that carry a role list, the two
unanswerable populations named beside them, the zero-denominator wordings,
and the --json tail.

Fixtures are built with the SHIPPING writers, the same way
test_stats_stitching.py builds its own: serializer.verify_quotes stamps the
receipts, policy.stamp_item_ids stamps the ids, store.write_checkpoint
persists and stamps the project slug.
"""
import json

import pytest

from daimon_briefing import cli, policy, provenance, serializer, store
from daimon_briefing.surfaces import Writer

_HASH = "a" * 64

# A tool row WITHOUT the `tool_result` flag (a markdown transcript's "tool:"
# row): quotable, so a quote bound to it carries the tool role.
_MESSAGES = [
    {"role": "user", "content": "the alpha premise stands firm", "id": "u-1"},
    {"role": "assistant", "content": "the omega conclusion follows cleanly",
     "id": "a-2"},
    {"role": "tool", "content": "exit status zero and the build is green",
     "id": "t-3"},
    {"role": "system", "content": "the gamma directive applies here",
     "id": "s-4"},
]
_USER = ("the alpha premise stands firm", ["u-1"])
_ASSISTANT = ("the omega conclusion follows cleanly", ["a-2"])
_TOOL = ("exit status zero and the build is green", ["t-3"])
_MIXED = ("alpha premise stands ... omega conclusion follows", ["u-1", "a-2"])
_OTHER = ("the gamma directive applies here", ["s-4"])
_UNBOUND = ("the alpha premise stands firm", None)


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


def _write(session, items, project_dir, messages=_MESSAGES):
    """Verify, stamp ids, persist: the same order serialize_strict runs in."""
    cp = _checkpoint(session, items)
    serializer.verify_quotes(
        cp, serializer._render_transcript(messages), messages,
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


# ---- the mix itself ---------------------------------------------------------


def test_stats_buckets_every_fresh_verified_quote_by_role(project, capsys):
    _write("S1", [_item("user words", _USER),
                  _item("assistant words", _ASSISTANT),
                  _item("tool output", _TOOL),
                  _item("a stitched pair", _MIXED),
                  _item("scanned", _UNBOUND)], project)

    r = _stats_json(capsys)["roles"]
    assert r == {
        "verified": 5, "measured": 4,
        "user_only": 1, "assistant_only": 1, "tool": 1, "mixed": 1,
        "other": 0, "unbound": 1, "pre_field": 0,
        "shares_pct": {"user_only": 25.0, "assistant_only": 25.0,
                       "tool": 25.0, "mixed": 25.0, "other": 0.0}}


def test_a_tool_row_anywhere_in_the_binding_reads_as_tool_involved(project,
                                                                   capsys):
    """Tool wins over mixed: a quote that spans the assistant and a tool row
    is "tool involved", because that is the fact a reader of the mix wants
    told apart from two humans-and-agent channels."""
    _write("S1", [_item("spans", ("omega conclusion ... build is green",
                                   ["a-2", "t-3"]))], project)
    r = _stats_json(capsys)["roles"]
    assert r["tool"] == 1
    assert r["mixed"] == 0


def test_a_single_role_outside_the_three_channels_is_other_not_mixed(project,
                                                                     capsys):
    """One channel is not a mix. A host that names its channels differently
    gets its own bucket, and the line names it only when it is nonzero."""
    _write("S1", [_item("directive", _OTHER), _item("user words", _USER)],
           project)
    r = _stats_json(capsys)["roles"]
    assert r["other"] == 1
    assert r["mixed"] == 0
    assert r["shares_pct"]["other"] == 50.0
    assert "other role 1 (50.0%)" in _stats_text(capsys)


def test_the_mix_is_over_the_receipt_not_the_trust_tag(project, capsys):
    """#974 demotes the stitched pair to `inferred`. The role mix reads the
    RECEIPT, so a demoted quote still counts in its bucket; keying on
    `trust == "verbatim"` would drop every mixed binding by construction and
    report a mix with no mixed bucket."""
    cp = _write("S1", [_item("a stitched pair", _MIXED)], project)
    assert cp["working_context"]["open_questions"][0]["trust"] == "inferred"
    assert _stats_json(capsys)["roles"]["mixed"] == 1


# ---- populations that must not leak in ---------------------------------------


def test_a_carried_copy_is_not_a_fresh_quote(project, capsys):
    """A carried item's receipt was minted in the session it came from. It
    is excluded on `carried_from`, the marker carry stamps on the plain-carry
    path, even under a different id, so the fresh-only population does not
    weigh one session's words once per session they survived."""
    first = _write("S1", [_item("user words", _USER)], project)
    carried = json.loads(json.dumps(
        first["working_context"]["open_questions"][0]))
    carried["id"] = "another-id"
    carried["carried_from"] = "S1"
    second = _checkpoint("S2", [carried])
    assert store.write_checkpoint("S2", second, project_dir=project,
                                  writer=Writer.HUMAN)

    r = _stats_json(capsys)["roles"]
    assert r["verified"] == 1
    assert r["user_only"] == 1


def test_a_copy_that_kept_its_id_counts_once_even_without_the_marker(project,
                                                                     capsys):
    """The walk's once-per-id rule backs the marker up: a copy that lost
    `carried_from` but kept its stable id is still one quote."""
    first = _write("S1", [_item("user words", _USER)], project)
    carried = json.loads(json.dumps(
        first["working_context"]["open_questions"][0]))
    second = _checkpoint("S2", [carried])
    assert store.write_checkpoint("S2", second, project_dir=project,
                                  writer=Writer.HUMAN)

    assert _stats_json(capsys)["roles"]["verified"] == 1


def test_a_receipt_written_before_the_field_is_counted_as_pre_field(project,
                                                                     capsys):
    """A message-ids receipt with no `roles` list is the shape every receipt
    had before #1237. It is bound, so it is not `unbound`; it cannot answer,
    so it is not in the mix; it is counted and named beside the mix."""
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
    _write("S2", [_item("user words", _USER)], project)

    r = _stats_json(capsys)["roles"]
    assert r["verified"] == 2
    assert r["measured"] == 1
    assert r["pre_field"] == 1
    assert r["user_only"] == 1
    assert r["shares_pct"]["user_only"] == 100.0
    assert "; 1 pre-field" in _stats_text(capsys)


def test_a_not_verified_receipt_is_not_a_verified_quote(project, capsys):
    _write("S1", [_item("absent", ("a sentence the transcript never said",
                                   ["u-1"])),
                  _item("user words", _USER)], project)
    r = _stats_json(capsys)["roles"]
    assert r["verified"] == 1
    assert r["user_only"] == 1


def test_another_projects_quotes_never_reach_this_projects_mix(project,
                                                               tmp_path,
                                                               capsys):
    other = tmp_path / "elsewhere"
    other.mkdir()
    _write("S-other", [_item("tool output", _TOOL)], other)
    _write("S-mine", [_item("user words", _USER)], project)

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
    _write("S1", [_item("scanned", _UNBOUND)], project)
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


def test_the_plain_render_names_the_counts_and_shares(project, capsys):
    _write("S1", [_item("user words", _USER),
                  _item("assistant words", _ASSISTANT),
                  _item("tool output", _TOOL),
                  _item("a stitched pair", _MIXED),
                  _item("scanned", _UNBOUND)], project)
    out = _stats_text(capsys)
    assert "quote roles (this project):" in out
    assert _MIX_LINE in out
    assert "other role" not in out


def test_the_rich_render_carries_the_same_wording(project, capsys,
                                                  monkeypatch):
    monkeypatch.setattr("daimon_briefing.render.supports_rich", lambda: True)
    monkeypatch.setenv("COLUMNS", "400")
    _write("S1", [_item("user words", _USER),
                  _item("assistant words", _ASSISTANT),
                  _item("tool output", _TOOL),
                  _item("a stitched pair", _MIXED),
                  _item("scanned", _UNBOUND)], project)
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


@pytest.mark.parametrize("junk", ["{not json", "[1, 2, 3]"])
def test_an_unreadable_checkpoint_never_takes_stats_down(junk, project,
                                                         capsys):
    _write("S1", [_item("user words", _USER)], project)
    from daimon_briefing import config

    (config.checkpoint_dir() / "junk.json").write_text(junk, encoding="utf-8")
    assert _stats_json(capsys)["roles"]["user_only"] == 1
