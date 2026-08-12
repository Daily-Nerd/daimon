import json
import threading
import urllib.request
from daimon_ui import server


def _get(base, path):
    req = urllib.request.Request(base + path)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())


def _serve(data_dir, slug):
    s = server.make_server(data_dir, slug, "test-proj", port=0)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    return s, f"http://127.0.0.1:{s.server_address[1]}"


def test_activity_route_returns_rows(flat_history):
    d, slug = flat_history
    s, base = _serve(d, slug)
    try:
        got = _get(base, "/api/activity?project=" + slug)
        assert got["ok"] is True
        kinds = {r["kind"] for r in got["rows"]}
        assert "session" in kinds
    finally:
        s.shutdown()


def test_activity_route_rejects_unknown_slug(flat_history):
    d, slug = flat_history
    s, base = _serve(d, slug)
    try:
        got = _get(base, "/api/activity?project=-nope-")
        assert got["ok"] is False
        assert set(got["error"]) == {"what", "why", "fix"}
    finally:
        s.shutdown()


def test_activity_route_rejects_traversal_slug(flat_history):
    d, slug = flat_history
    s, base = _serve(d, slug)
    try:
        got = _get(base, "/api/activity?project=..%2Fx")
        assert got["ok"] is False
    finally:
        s.shutdown()
