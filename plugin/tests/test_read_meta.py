"""`store.read_meta`: the envelope of a pointer or session file and nothing
else (#1132 PR 6a). Fixtures come from the real writer."""

import json

from daimon_briefing import config, field_table, store

PROJECT = "/p/read-meta"
ENVELOPE = ("session_id", "created", "author", "format_version",
            "project_slug", "project_name", "git_branch", "source",
            "transcript_hash", "receipts", "team_project")


def _write(sid="S-meta", **extra):
    cp = {"session_id": sid, "source": "introspection",
          "working_context": {
              "active_topic": {"text": "SENTINEL-TOPIC"},
              "open_questions": [{"text": "SENTINEL-QUESTION"}],
              "recent_decisions": []},
          "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                                 "contradictions_flagged": ["SENTINEL-BARE"]},
          **extra}
    store.write_checkpoint(sid, cp, project_dir=PROJECT)
    return (config.checkpoint_dir() / store.project_slug(PROJECT))


def test_meta_fields_are_the_code_owned_envelope_keys():
    assert store.Meta._fields == ENVELOPE
    code_owned = {r.name for r in field_table.ENVELOPE_RULES
                  if r.owner == "code"}
    assert set(ENVELOPE) - {"session_id"} <= code_owned


def test_a_pointer_reads_as_its_envelope(tmp_checkpoint_dir):
    bucket = _write()
    meta = store.read_meta(bucket / "latest.json")
    assert meta.session_id == "S-meta"
    assert meta.project_slug == store.project_slug(PROJECT)
    assert meta.source == "introspection"
    assert meta.created and meta.format_version
    assert meta.transcript_hash is None


def test_a_session_file_reads_as_its_envelope(tmp_checkpoint_dir):
    _write()
    meta = store.read_meta(config.checkpoint_dir() / "S-meta.json")
    assert meta is not None and meta.session_id == "S-meta"


def test_meta_never_carries_an_item_field(tmp_checkpoint_dir):
    bucket = _write()
    meta = store.read_meta(bucket / "latest.json")
    assert "SENTINEL" not in json.dumps(meta._asdict())
    assert not set(meta._fields) & {"working_context", "epistemic_snapshot",
                                    "worker_queue", "text", "quote", "scene"}


def test_a_missing_torn_or_non_object_file_reads_as_none(tmp_path):
    assert store.read_meta(tmp_path / "absent.json") is None
    torn = tmp_path / "torn.json"
    torn.write_text("{not json")
    assert store.read_meta(torn) is None
    scalar = tmp_path / "scalar.json"
    scalar.write_text("[1, 2]")
    assert store.read_meta(scalar) is None
    (tmp_path / "dir.json").mkdir()
    assert store.read_meta(tmp_path / "dir.json") is None


def test_a_file_missing_envelope_keys_reads_them_as_none(tmp_path):
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps({"session_id": "S-bare"}))
    meta = store.read_meta(bare)
    assert meta.session_id == "S-bare" and meta.created is None
    assert meta.receipts is None and meta.team_project is None
