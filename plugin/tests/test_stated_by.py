"""#890: an item can name whose statement it records, distinct from `author`.

`author` is a machine identity and a KEY: it names a team sidecar directory,
gates foreign-content admission, and is half of two dedup keys. A checkpoint
written by a long-lived process that observed several people cannot say so,
because every item inherits the one checkpoint-level value.

`stated_by` is the subject identity and is rendered only. It is never a key,
never a directory name, never an admission input, which is what keeps it from
accumulating the load `author` carries.

The rule most easily got wrong: an ABSENT `stated_by` means unknown, never
"the reader". Implicit "no speaker equals mine" is the collapse this exists to
fix, and it would be reintroduced for every legacy row by a fix that guessed.
"""

from daimon_briefing import cli, field_table, recall, store


def _cp(sid, items):
    return {
        "session_id": sid,
        "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": items},
    }


# ---- the field rule --------------------------------------------------------


def test_stated_by_is_a_registered_item_field():
    rule = field_table._ITEM_BY_NAME.get("stated_by")
    assert rule is not None, "stated_by is not in the generated field table"
    assert rule.scope == "item"
    assert rule.type == "string"
    assert rule.optional is True


def test_stated_by_is_code_owned_and_stripped_from_model_output():
    """The wedge principle: no feature may let an agent assert.

    A model emitting `stated_by` would be attributing a claim to a person who
    may never have made it, and the field would carry more authority than any
    other while being the least verifiable. So it matches `author`: code-owned,
    stripped at serialize, set by the host after the model is done."""
    rule = field_table._ITEM_BY_NAME["stated_by"]
    assert rule.owner == "code"
    assert rule.disposition == "strip"
    assert dict(rule.constraints).get("stripped_at_serialize") is True


# ---- the index -------------------------------------------------------------


def test_an_item_carries_its_own_stated_by(tmp_checkpoint_dir, tmp_path,
                                           monkeypatch):
    """The whole point: one checkpoint, two speakers."""
    proj = str((tmp_path / "p-two-speakers").resolve())
    monkeypatch.setenv("DAIMON_AUTHOR", "the-host")
    store.write_checkpoint("S-a", _cp("S-a", [
        {"text": "lemurs are nocturnal", "trust": "inferred",
         "stated_by": "ana"},
        {"text": "lemurs are diurnal", "trust": "inferred",
         "stated_by": "ben"},
    ]), project_dir=proj)
    rows = recall.search("lemurs", project_dir=proj, limit=10)
    got = {r["text"]: r.get("stated_by") for r in rows}
    assert got["lemurs are nocturnal"] == "ana"
    assert got["lemurs are diurnal"] == "ben"
    # `author` is untouched and still the machine identity for both.
    assert {r["author"] for r in rows} == {"the-host"}


def test_an_item_without_stated_by_is_null_not_the_author(
        tmp_checkpoint_dir, tmp_path, monkeypatch):
    """Absent means UNKNOWN. Defaulting to the checkpoint author would make
    every legacy row a first-person claim, which is the bug this fixes."""
    proj = str((tmp_path / "p-absent").resolve())
    monkeypatch.setenv("DAIMON_AUTHOR", "the-host")
    store.write_checkpoint("S-a", _cp("S-a", [
        {"text": "lemur with no speaker", "trust": "inferred"},
    ]), project_dir=proj)
    row = recall.search("lemur", project_dir=proj, limit=10)[0]
    assert row.get("stated_by") is None
    assert row["author"] == "the-host"


def test_stated_by_is_not_a_dedup_key():
    """`author` is half the result dedup key on purpose: the same words from
    two authors is attribution, not duplication. `stated_by` must NOT join
    that key, or one speaker's echo of another would stop collapsing."""
    rows = [{"kind": "decision", "author": "a", "text": "same words",
             "stated_by": "ana", "created": 1.0},
            {"kind": "decision", "author": "a", "text": "same words",
             "stated_by": "ben", "created": 2.0}]
    assert len(recall._dedupe_rows(rows, 10)) == 1


def test_the_schema_version_moved(tmp_checkpoint_dir):
    """A new column means every existing index rebuilds deterministically
    rather than waiting for an unrelated fingerprint change."""
    assert recall._SCHEMA_VERSION != "8"


# ---- the render ------------------------------------------------------------


def test_cli_recall_names_the_stater(tmp_checkpoint_dir, tmp_path,
                                     monkeypatch, capsys):
    proj = str((tmp_path / "p-render").resolve())
    monkeypatch.setenv("DAIMON_AUTHOR", "the-host")
    store.write_checkpoint("S-a", _cp("S-a", [
        {"text": "lemur claim", "trust": "inferred", "stated_by": "ana"},
    ]), project_dir=proj)
    assert cli.main(["recall", "lemur", "--project", proj]) == 0
    assert "stated by ana" in capsys.readouterr().out


def test_cli_recall_says_nothing_when_no_one_is_named(
        tmp_checkpoint_dir, tmp_path, monkeypatch, capsys):
    """Silence, not a guess and not a placeholder. The common single-speaker
    case pays no noise, same posture as the #889 scope marker."""
    proj = str((tmp_path / "p-render-absent").resolve())
    monkeypatch.setenv("DAIMON_AUTHOR", "the-host")
    store.write_checkpoint("S-a", _cp("S-a", [
        {"text": "lemur claim", "trust": "inferred"},
    ]), project_dir=proj)
    assert cli.main(["recall", "lemur", "--project", proj]) == 0
    out = capsys.readouterr().out
    assert "lemur claim" in out
    assert "stated by" not in out


def test_suggest_line_names_the_stater():
    row = {"kind": "decision", "session_id": "S-x", "text": "a claim",
           "trust": "inferred", "created": 0, "stated_by": "ana"}
    assert "stated by ana" in cli._suggest_line(row, ["lemur"], 0.0)


def test_suggest_line_is_silent_without_one():
    row = {"kind": "decision", "session_id": "S-x", "text": "a claim",
           "trust": "inferred", "created": 0}
    assert "stated by" not in cli._suggest_line(row, ["lemur"], 0.0)
