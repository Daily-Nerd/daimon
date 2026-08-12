"""/api/grid (#670 slice 3): the check strip's data — reader.project_grid
over the same walk every other surface reads."""
import json
import urllib.request


def _get(url):
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read())


def test_api_grid_happy_path(srv_flat):
    out = _get(srv_flat + "/api/grid")
    assert out["ok"] is True
    assert [c["session_id"] for c in out["columns"]] == [
        "aaaa-1111", "bbbb-2222", "rollout-2026-08-06-cccc"]
    assert out["columns"][-1]["is_head"] is True


def test_api_grid_rejects_unknown_slug(srv_flat):
    out = _get(srv_flat + "/api/grid?project=not-a-project")
    assert out["ok"] is False
    assert set(out["error"]) == {"what", "why", "fix"}
