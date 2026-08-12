import json
import threading
import pytest
from daimon_ui import server

def make_checkpoint(created="2026-08-06T08:05:12Z", topic="Scoping the inspector", **over):
    cp = {
        "session_id": over.get("session_id", "aaaa-bbbb"),
        "format_version": over.get("format_version", "D-018"),
        "created": created,
        "author": "ada",
        "project_slug": "-tmp-proj",
        "working_context": {
            "active_topic": {"text": topic},
            "open_questions": over.get("open_questions", []),
            "recent_decisions": over.get("recent_decisions", []),
        },
        "epistemic_snapshot": {
            "strong_beliefs": over.get("strong_beliefs", []),
            "uncertainties": over.get("uncertainties", []),
            "contradictions_flagged": over.get("contradictions_flagged", []),
        },
        "worker_queue": [{"type": "task", "id": 1, "name": "x", "status": "open", "importance": 2, "scene": ""}],
    }
    return cp

@pytest.fixture
def bucket(tmp_path):
    b = tmp_path / "checkpoints" / "-tmp-proj"
    b.mkdir(parents=True)
    (b / "latest.json").write_text(json.dumps(make_checkpoint()))
    (b / "prev-1.json").write_text(json.dumps(make_checkpoint(created="2026-08-06T02:04:00Z", topic="Option B decided")))
    (b / "prev-2.json").write_text(json.dumps(make_checkpoint(created="2026-08-05T22:00:00Z", topic="Moat strategy read")))
    (b / "latest.json.bak-123").write_text("{}")          # stray sidecar — must be ignored
    (b / "events.jsonl").write_text("")                    # append log — must be ignored
    return b

@pytest.fixture
def second_bucket(bucket):
    root = bucket.parent
    b2 = root / "-other-proj"
    b2.mkdir()
    (b2 / "latest.json").write_text(json.dumps(
        make_checkpoint(created="2026-08-04T00:00:00Z", topic="Other project topic")))
    return b2

@pytest.fixture
def srv(bucket):
    data_dir = bucket.parent
    s = server.make_server(data_dir, "-tmp-proj", "daimon-ui-init", port=0)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()

@pytest.fixture
def srv_multi(bucket, second_bucket):
    data_dir = bucket.parent
    s = server.make_server(data_dir, "-tmp-proj", "daimon-ui-init", port=0)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()

@pytest.fixture
def flat_history(tmp_path):
    d = tmp_path / "checkpoints"
    d.mkdir(parents=True)
    slug = "-tmp-proj"
    bucket = d / slug
    bucket.mkdir()
    for i, (sid, created, topic) in enumerate([
        ("aaaa-1111", "2026-08-04T10:00:00Z", "day one"),
        ("bbbb-2222", "2026-08-05T10:00:00Z", "day two"),
        ("rollout-2026-08-06-cccc", "2026-08-06T10:00:00Z", "day three"),
    ]):
        cp = make_checkpoint(created=created, topic=topic, session_id=sid)
        cp["project_slug"] = slug
        (d / f"{sid}.json").write_text(json.dumps(cp))
    other = make_checkpoint(created="2026-08-05T12:00:00Z", topic="other proj")
    other["project_slug"] = "-other"
    (d / "dddd-9999.json").write_text(json.dumps(other))
    (d / "latest.json").write_text(json.dumps(make_checkpoint()))   # pointer: excluded
    (d / "torn.json").write_text("{nope")                            # unreadable: counted
    (d / "x.tmp").write_text("{}")                                   # excluded
    (bucket / "latest.json").write_text(json.dumps(make_checkpoint()))  # for server bucket discovery
    return d, slug

@pytest.fixture
def srv_flat(flat_history):
    """A project whose sidebar window is narrower than its history: the bucket
    holds one pointer, the root holds three sessions. The asymmetry is the point."""
    d, slug = flat_history
    s = server.make_server(d, slug, "daimon-ui-init", port=0)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()

@pytest.fixture
def flat_with_events(flat_history):
    d, slug = flat_history
    ev = d / slug / "events.jsonl"
    ev.write_text('\n'.join([
        json.dumps({"ts": "2026-08-06T09:00:00Z", "kind": "resolution", "item_ref": "o-aaa111aaa111", "status": "resolved", "source": "cli", "note": "closed it", "item_text": "old question"}),
        '{broken line',
        json.dumps({"ts": "2026-08-06T09:05:00Z", "kind": "other", "item_ref": "o-zzz", "status": "resolved"}),
    ]) + '\n')
    return d, slug
