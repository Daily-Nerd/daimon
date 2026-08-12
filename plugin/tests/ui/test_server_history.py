import json
import urllib.request
import urllib.error
import pytest
from tests.ui.conftest import make_checkpoint

def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers.get_content_type(), r.read()

@pytest.fixture
def srv_with_history(flat_history):
    """Server with flat_history fixture (3 sessions in -tmp-proj)."""
    import threading
    from daimon_ui import server
    d, slug = flat_history
    # flat_history creates session files but not bucket structure
    # Add latest.json to bucket so list_buckets discovers it
    bucket = d / slug
    bucket.mkdir(parents=True, exist_ok=True)
    (bucket / "latest.json").write_text(json.dumps(make_checkpoint()))
    srv = server.make_server(d, slug, "Test Project", port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()

@pytest.fixture
def srv_with_biography_items(tmp_path):
    """Server with sessions that have items with ids for biography testing."""
    import threading
    from daimon_ui import server
    d = tmp_path / "checkpoints"
    d.mkdir(parents=True)
    slug = "-tmp-proj"
    bucket = d / slug
    bucket.mkdir()

    BIO_ID = "o-abc123abc123"

    # Session 1: item born
    cp_1 = make_checkpoint(
        created="2026-08-04T10:00:00Z", topic="day one", session_id="aaaa-1111",
        open_questions=[
            {"text": "test question", "id": BIO_ID, "trust": "verbatim", "quote": "test quote"},
        ],
    )
    cp_1["project_slug"] = slug
    (d / "aaaa-1111.json").write_text(json.dumps(cp_1))

    # Session 2: item carried
    cp_2 = make_checkpoint(
        created="2026-08-05T10:00:00Z", topic="day two", session_id="bbbb-2222",
        open_questions=[
            {"text": "test question", "id": BIO_ID, "trust": "verbatim", "quote": "test quote"},
        ],
    )
    cp_2["project_slug"] = slug
    (d / "bbbb-2222.json").write_text(json.dumps(cp_2))

    # Create bucket pointer so list_buckets discovers the bucket
    (bucket / "latest.json").write_text(json.dumps(make_checkpoint()))

    srv = server.make_server(d, slug, "Test Project", port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", BIO_ID
    srv.shutdown()

# Tests for /api/history

def test_api_history_happy_path(srv_with_history):
    status, ctype, body = _get(srv_with_history + "/api/history")
    data = json.loads(body)
    assert status == 200 and ctype == "application/json"
    assert "project" in data
    assert "sessions" in data
    assert len(data["sessions"]) == 3
    assert "unreadable" in data
    assert data["sessions"][0]["session_id"] == "rollout-2026-08-06-cccc"

def test_api_history_with_project_param(srv_with_history):
    status, ctype, body = _get(srv_with_history + "/api/history?project=-tmp-proj")
    data = json.loads(body)
    assert status == 200
    assert data["project"] == "Test Project"
    assert len(data["sessions"]) == 3

def test_api_history_invalid_slug(srv_with_history):
    status, ctype, body = _get(srv_with_history + "/api/history?project=nonexistent")
    data = json.loads(body)
    assert status == 200
    assert data["ok"] is False
    assert "error" in data

def test_api_history_traversal_attempt_rejected(srv_with_history):
    status, ctype, body = _get(srv_with_history + "/api/history?project=..%2Fevil")
    data = json.loads(body)
    assert status == 200
    assert data["ok"] is False
    assert "error" in data

# Tests for /api/diff

def test_api_diff_with_explicit_sids(srv_with_history):
    """Diff between two specific sessions."""
    status, ctype, body = _get(srv_with_history + "/api/diff?project=-tmp-proj&a=aaaa-1111&b=bbbb-2222")
    data = json.loads(body)
    assert status == 200 and ctype == "application/json"
    assert data["ok"] is True
    assert data["a"]["session_id"] == "aaaa-1111"
    assert data["b"]["session_id"] == "bbbb-2222"

def test_api_diff_default_uses_newest_two_sessions(srv_with_history):
    """Without a/b params, diff should use the two newest sessions."""
    status, ctype, body = _get(srv_with_history + "/api/diff?project=-tmp-proj")
    data = json.loads(body)
    assert status == 200
    assert data["ok"] is True
    # Two newest are: rollout-2026-08-06-cccc and bbbb-2222
    # a (older) should be bbbb-2222, b (newer) should be rollout-2026-08-06-cccc
    assert data["a"]["session_id"] == "bbbb-2222"
    assert data["b"]["session_id"] == "rollout-2026-08-06-cccc"

def test_api_diff_single_session_no_explicit_ab_returns_empty_state(tmp_path):
    """With fewer than 2 sessions and no explicit a/b, return the empty state, not an error."""
    import threading
    from daimon_ui import server
    d = tmp_path / "checkpoints"
    d.mkdir(parents=True)
    slug = "-tmp-proj"
    bucket = d / slug
    bucket.mkdir()

    cp = make_checkpoint(created="2026-08-04T10:00:00Z", topic="day one", session_id="only-session")
    cp["project_slug"] = slug
    (d / "only-session.json").write_text(json.dumps(cp))

    # Create bucket pointer so list_buckets discovers the bucket
    (bucket / "latest.json").write_text(json.dumps(make_checkpoint()))

    srv = server.make_server(d, slug, "Test Project", port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        base_url = f"http://127.0.0.1:{srv.server_address[1]}"
        status, ctype, body = _get(base_url + "/api/diff?project=-tmp-proj")
        data = json.loads(body)
        assert status == 200
        assert data["ok"] is True
        assert data["empty"] == "single_checkpoint"
        assert data["sessions"] == 1
    finally:
        srv.shutdown()

def test_api_diff_invalid_project_param(srv_with_history):
    status, ctype, body = _get(srv_with_history + "/api/diff?project=nonexistent")
    data = json.loads(body)
    assert status == 200
    assert data["ok"] is False
    assert "error" in data

def test_api_diff_traversal_attempt_in_a_param(srv_with_history):
    """Traversal in a param should be caught by reader's session validation."""
    status, ctype, body = _get(srv_with_history + "/api/diff?project=-tmp-proj&a=../../etc&b=bbbb-2222")
    data = json.loads(body)
    assert status == 200
    assert data["ok"] is False
    assert "error" in data

def test_api_diff_traversal_attempt_in_b_param(srv_with_history):
    """Traversal in b param should be caught by reader's session validation."""
    status, ctype, body = _get(srv_with_history + "/api/diff?project=-tmp-proj&a=aaaa-1111&b=../../etc/passwd")
    data = json.loads(body)
    assert status == 200
    assert data["ok"] is False
    assert "error" in data

def test_api_diff_partial_param_a_only_uses_default_b(srv_with_history):
    """When only a is supplied, b should still default to newest session."""
    status, ctype, body = _get(srv_with_history + "/api/diff?project=-tmp-proj&a=aaaa-1111")
    data = json.loads(body)
    assert status == 200
    # Intentional behavior: when either a or b is missing, both default to the two newest
    assert data["ok"] is True
    assert data["a"]["session_id"] == "bbbb-2222"
    assert data["b"]["session_id"] == "rollout-2026-08-06-cccc"

# Tests for /api/biography

def test_api_biography_happy_path(srv_with_biography_items):
    base_url, item_id = srv_with_biography_items
    status, ctype, body = _get(base_url + f"/api/biography?project=-tmp-proj&id={item_id}")
    data = json.loads(body)
    assert status == 200 and ctype == "application/json"
    assert data["ok"] is True
    assert "item" in data
    assert "events" in data
    assert data["item"]["id"] == item_id

def test_api_biography_bad_item_id_format(srv_with_biography_items):
    base_url, _ = srv_with_biography_items
    status, ctype, body = _get(base_url + "/api/biography?project=-tmp-proj&id=invalid-id-format")
    data = json.loads(body)
    assert status == 200
    assert data["ok"] is False
    assert "error" in data

def test_api_biography_nonexistent_item(srv_with_biography_items):
    base_url, _ = srv_with_biography_items
    status, ctype, body = _get(base_url + "/api/biography?project=-tmp-proj&id=o-aaa111aaa111")
    data = json.loads(body)
    assert status == 200
    assert data["ok"] is False
    assert "error" in data

def test_api_biography_traversal_attempt_in_id_param(srv_with_biography_items):
    """Traversal in id param should be caught by reader's format validation."""
    base_url, _ = srv_with_biography_items
    status, ctype, body = _get(base_url + "/api/biography?project=-tmp-proj&id=../../etc")
    data = json.loads(body)
    assert status == 200
    assert data["ok"] is False
    assert "error" in data

def test_api_biography_invalid_project_param(srv_with_biography_items):
    base_url, item_id = srv_with_biography_items
    status, ctype, body = _get(base_url + f"/api/biography?project=nonexistent&id={item_id}")
    data = json.loads(body)
    assert status == 200
    assert data["ok"] is False
    assert "error" in data
