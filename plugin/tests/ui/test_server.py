import json
import urllib.request
import pytest

def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers.get_content_type(), r.read()

def test_root_serves_html(srv):
    status, ctype, body = _get(srv + "/")
    assert status == 200 and ctype == "text/html" and b"daimon" in body

def test_api_checkpoints(srv):
    status, ctype, body = _get(srv + "/api/checkpoints")
    data = json.loads(body)
    assert status == 200 and ctype == "application/json"
    assert data["project"] == "daimon-ui-init"
    assert [c["ref"] for c in data["checkpoints"]] == ["latest", "prev-1", "prev-2"]

def test_api_checkpoint_ok(srv):
    _, _, body = _get(srv + "/api/checkpoint/latest")
    assert json.loads(body)["ok"] is True

def test_api_checkpoint_bad_ref(srv):
    _, _, body = _get(srv + "/api/checkpoint/..%2f..%2fetc")
    assert json.loads(body)["ok"] is False

def test_unknown_route_404(srv):
    import urllib.error
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(srv + "/nope")
    assert e.value.code == 404

def test_spoofed_host_header_rejected(srv):
    import urllib.error
    req = urllib.request.Request(srv + "/", headers={"Host": "evil.example.com"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 403

def test_api_projects_lists_discovered_and_current(srv):
    status, ctype, body = _get(srv + "/api/projects")
    data = json.loads(body)
    assert status == 200 and ctype == "application/json"
    assert data["current"] == "-tmp-proj"
    assert "-tmp-proj" in [p["slug"] for p in data["projects"]]

def test_api_projects_multi(srv_multi):
    _, _, body = _get(srv_multi + "/api/projects")
    data = json.loads(body)
    assert sorted(p["slug"] for p in data["projects"]) == ["-other-proj", "-tmp-proj"]

def test_checkpoints_project_param_switches_bucket(srv_multi):
    _, _, body = _get(srv_multi + "/api/checkpoints?project=-other-proj")
    data = json.loads(body)
    assert data["project"] == "-other-proj"
    assert [c["ref"] for c in data["checkpoints"]] == ["latest"]

def test_checkpoints_missing_param_uses_default(srv_multi):
    _, _, body = _get(srv_multi + "/api/checkpoints")
    data = json.loads(body)
    assert data["project"] == "daimon-ui-init"
    assert [c["ref"] for c in data["checkpoints"]] == ["latest", "prev-1", "prev-2"]

def test_checkpoints_invalid_slug_traversal(srv):
    _, _, body = _get(srv + "/api/checkpoints?project=..%2Fevil")
    data = json.loads(body)
    assert data["ok"] is False and "error" in data

def test_checkpoints_nonexistent_slug(srv):
    _, _, body = _get(srv + "/api/checkpoints?project=nonexistent")
    data = json.loads(body)
    assert data["ok"] is False

def test_checkpoint_ref_project_param_switches_bucket(srv_multi):
    _, _, body = _get(srv_multi + "/api/checkpoint/latest?project=-other-proj")
    data = json.loads(body)
    assert data["ok"] is True
    assert data["meta"]["active_topic"] == "Other project topic"

def test_checkpoint_ref_invalid_slug(srv):
    _, _, body = _get(srv + "/api/checkpoint/latest?project=nonexistent")
    data = json.loads(body)
    assert data["ok"] is False

def test_checkpoints_reports_how_many_sessions_exist_beyond_the_window(srv_flat):
    """The sidebar serves pointer files only (latest, prev-N); History serves every
    session file for the slug. When the two disagree the screen shows two answers to
    "how much history is there" with nothing explaining the gap, so the endpoint that
    feeds the sidebar has to carry the total the sidebar is a window onto."""
    _, _, body = _get(srv_flat + "/api/checkpoints?project=-tmp-proj")
    data = json.loads(body)
    assert [c["ref"] for c in data["checkpoints"]] == ["latest"]
    assert data["sessions_total"] == 3

def test_checkpoints_total_counts_only_the_requested_project(srv_flat):
    """flat_history plants a session owned by -other. A total that swept the whole
    root directory would read 4 and overstate this project's history."""
    _, _, body = _get(srv_flat + "/api/checkpoints?project=-tmp-proj")
    assert json.loads(body)["sessions_total"] == 3
