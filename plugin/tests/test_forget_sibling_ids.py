"""#418: `daimon forget` must remove the VALUE, not merely the id.

One value can legitimately hold two ids: `_stamp_item_ids` hashes
`f"{key}:{text}"`, so the same sentence in two sections gets two ids, and
identical text within one section falls through to a widened hash. Pre-#418,
`_cmd_forget` spliced by id and wrote the scrubbed checkpoint BEFORE appending
the tombstone, so `_drop_forgotten` consulted a ledger that lacked the new key
and every sibling survived — live in the briefing, in recall, and verbatim on
disk, while forget reported success.

Both sibling routes are covered, each with a never-forgotten twin (T) as the
liveness control so no negative assertion can pass vacuously. Assertion order
inside each test is disk first: the raw checkpoint is the source the derived
artifacts (briefing.build, recall) are rebuilt from.
"""

import json
from datetime import datetime, timezone

from daimon_briefing import briefing, cli, config, normalize, recall, store

# Fixed clock threaded into every now-consumer (scar 0016): the checkpoints
# below carry a simulated calendar, so briefing.build must read the SAME
# clock, never wall time.
_NOW = datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()

_P = "/repo/forget-sibling-arc"

# S is forgotten; T is the never-forgotten twin. "adopt" is the hot keyword —
# it matches BOTH, so the tombstone (not the query) is what separates them.
_S = "adopt sqlite for the recall index cache"
_T = "adopt postgres for the analytics warehouse"
_HOT = "adopt"


def _item(text):
    return {"text": text, "trust": "inferred"}


def _latest_raw(project_dir):
    slug = store.project_slug(project_dir)
    return (config.checkpoint_dir() / slug / "latest.json").read_text(encoding="utf-8")


def _build_blob(project_dir):
    cp = store.read_latest(project_dir=project_dir, fallback=False)
    return json.dumps(briefing.build(cp, now=_NOW))


def _recall_texts(project_dir):
    recall.rebuild()
    return [h["text"] for h in recall.search(_HOT, project_dir=project_dir)]


def _assert_value_gone_twin_alive(project_dir):
    raw = _latest_raw(project_dir)
    assert _S not in raw                    # gone from disk under EVERY id
    assert _T in raw                        # liveness (twin still present)
    blob = _build_blob(project_dir)
    assert _S not in blob and _T in blob
    texts = _recall_texts(project_dir)
    assert _S not in texts and _T in texts


def test_forget_scrubs_same_value_sibling_in_another_section(tmp_checkpoint_dir,
                                                             monkeypatch):
    """Same sentence as a decision AND an open question — two ids, one value.
    Forgetting the decision id must take the open-question sibling with it."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _P)
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", {
        "session_id": "S1",
        "created": "2026-07-31T00:00:00Z",
        "working_context": {
            "recent_decisions": [_item(_S), _item(_T)],
            "open_questions": [_item(_S)],
        },
    }, project_dir=_P)
    stored = store.read_latest(project_dir=_P, fallback=False)
    d_id = next(i["id"] for i in stored["working_context"]["recent_decisions"]
                if i["text"] == _S)
    q_id = next(i["id"] for i in stored["working_context"]["open_questions"]
                if i["text"] == _S)
    assert d_id != q_id                     # sibling ids: two sections, one value

    assert cli.main(["forget", d_id]) == 0
    _assert_value_gone_twin_alive(_P)


def test_forget_scrubs_widened_hash_sibling_within_one_section(tmp_checkpoint_dir,
                                                               monkeypatch):
    """Same sentence twice in ONE section — the collision widens the second
    id's hash slice. Forgetting the first id must take the widened twin too."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _P)
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", {
        "session_id": "S1",
        "created": "2026-07-31T00:00:00Z",
        "working_context": {
            "recent_decisions": [_item(_S), _item(_S), _item(_T)],
        },
    }, project_dir=_P)
    stored = store.read_latest(project_dir=_P, fallback=False)
    ids = [i["id"] for i in stored["working_context"]["recent_decisions"]
           if i["text"] == _S]
    assert len(ids) == 2 and ids[0] != ids[1]   # widened-hash siblings

    assert cli.main(["forget", ids[0]]) == 0
    _assert_value_gone_twin_alive(_P)


def test_forget_sibling_path_keeps_tombstone_and_normal_output(tmp_checkpoint_dir,
                                                               monkeypatch,
                                                               capsys):
    """The sibling scrub must not change forget's contract: exit 0, the normal
    success line, and a hash-only tombstone event on the ledger."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _P)
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", {
        "session_id": "S1",
        "created": "2026-07-31T00:00:00Z",
        "working_context": {
            "recent_decisions": [_item(_S), _item(_T)],
            "open_questions": [_item(_S)],
        },
    }, project_dir=_P)
    stored = store.read_latest(project_dir=_P, fallback=False)
    d_id = next(i["id"] for i in stored["working_context"]["recent_decisions"]
                if i["text"] == _S)

    assert cli.main(["forget", d_id]) == 0
    out = capsys.readouterr().out
    key = normalize.content_key(_S)
    assert f"forgot {d_id} (content hash {key})" in out
    assert "tombstone recorded" in out

    res = store.resolutions(project_dir=_P)
    assert res[d_id]["status"] == f"forgotten:{key}"
    # Hash, never the text: removal means the content leaves the audit trail.
    events = store._events_path(_P).read_text(encoding="utf-8")
    assert _S not in events
