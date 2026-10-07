"""#1132 PR 8a: the viewer's ROUTES table and its one runner.

Every route is a row (path, handler, owning read-pipeline stage). One runner
sets the store scope for the request inside the handler thread and turns any
raise into a generic HTTP 500."""

import ast
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from daimon_briefing import config
from daimon_ui import reader, server

EXPECTED = {
    "/": None,
    "/api/projects": "8b-1",
    "/api/checkpoints": "8b-1",
    "/api/checkpoint/": "8b-1",
    "/api/history": "8b-1",
    "/api/diff": "8b-1",
    "/api/biography": "8b-2",
    "/api/recall": "9",
    "/api/why": "11",
    "/api/grid": "8b-2",
    "/api/refutations": "11",
    "/api/relations": "11",
    "/api/ledger": "8b-2",
    "/api/session": "8b-2",
    "/api/activity": "8b-2",
    "/static/": None,
}


def _serve(data_dir, slug="-tmp-proj"):
    s = server.make_server(data_dir, slug, "lbl", port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f"http://127.0.0.1:{s.server_address[1]}"


def _fetch(base, path):
    try:
        with urllib.request.urlopen(base + path) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_routes_is_the_sixteen_with_their_owning_stage():
    assert {k: r.owner for k, r in server.ROUTES.items()} == EXPECTED
    assert all(k == r.path and callable(r.handler)
               for k, r in server.ROUTES.items())


def test_only_the_two_trailing_slash_routes_are_prefixes():
    assert {k for k, r in server.ROUTES.items() if r.prefix} == {
        "/api/checkpoint/", "/static/"}


def test_do_get_dispatches_through_the_table_not_a_chain():
    tree = ast.parse(Path(server.__file__).read_text(encoding="utf-8"))
    do_get = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "do_GET")
    compares = [n for n in ast.walk(do_get)
                if isinstance(n, ast.Compare)
                and isinstance(n.left, ast.Name) and n.left.id == "path"]
    assert compares == []


def test_an_unknown_path_is_404(srv):
    assert _fetch(srv, "/api/nope")[0] == 404
    assert _fetch(srv, "/static/nope.js")[0] == 404


def test_the_prefix_route_serves_but_a_near_name_is_not_the_prefix(srv):
    assert _fetch(srv, "/api/checkpoint/latest")[0] == 200
    assert json.loads(_fetch(srv, "/api/checkpoints")[1])["checkpoints"]


def test_the_request_thread_sees_the_data_dir_as_the_store(tmp_path):
    """A ContextVar set in the main thread never reaches a ThreadingHTTPServer
    request thread, so the runner sets the override per request. The main thread
    here sets nothing: a handler that sees the data dir got it from the runner."""
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert config.checkpoint_dir() != other
    s, base = _serve(other)
    try:
        route = server.Route("/api/_where", lambda h, path, params: h._json(
            {"store": str(config.checkpoint_dir())}), owner=None)
        server.ROUTES[route.path] = route
        try:
            status, body = _fetch(base, "/api/_where")
        finally:
            del server.ROUTES[route.path]
    finally:
        s.shutdown()
        s.server_close()
    assert status == 200
    assert json.loads(body)["store"] == str(other)


def test_the_override_is_gone_after_the_request(tmp_path):
    before = config.checkpoint_dir()
    s, base = _serve(tmp_path)
    try:
        _fetch(base, "/api/projects")
    finally:
        s.shutdown()
        s.server_close()
    assert config.checkpoint_dir() == before


def test_a_context_override_set_in_the_main_thread_does_not_reach_a_thread(
        tmp_path):
    """The fact the runner exists for."""
    seen = []
    with config.checkpoint_dir_override(tmp_path / "main-only"):
        t = threading.Thread(target=lambda: seen.append(config.checkpoint_dir()))
        t.start()
        t.join()
    assert seen == [config.checkpoint_dir()]
    assert seen != [tmp_path / "main-only"]


def test_a_raise_in_a_handler_is_a_generic_500(bucket):
    def boom(*_a, **_k):
        raise RuntimeError("SECRET-ITEM-TEXT at /private/path")

    s, base = _serve(bucket.parent)
    try:
        orig = reader.list_buckets
        reader.list_buckets = boom
        try:
            status, body = _fetch(base, "/api/projects")
        finally:
            reader.list_buckets = orig
        again = _fetch(base, "/api/projects")[0]
    finally:
        s.shutdown()
        s.server_close()
    assert status == 500
    payload = json.loads(body)
    assert payload["ok"] is False
    assert "RuntimeError" in json.dumps(payload)
    assert b"SECRET" not in body and b"/private" not in body
    assert again == 200, "the server survives a raise"


def test_a_failed_500_write_is_swallowed(bucket):
    h = object.__new__(server._Handler)
    h.data_dir, h.default_slug, h.project_label = bucket.parent, "x", "x"
    h.headers = {"Host": "127.0.0.1"}
    h.path = "/api/projects"

    def broken(*_a):
        raise BrokenPipeError

    h._send = broken
    h.do_GET()  # must not raise


def test_the_package_exposes_no_resolve_data_dir():
    assert not hasattr(reader, "resolve_data_dir")
