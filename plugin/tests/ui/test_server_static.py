import http.client
import re
import urllib.error
import urllib.request

import pytest


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def _raw_get(srv, raw_path):
    """Send raw_path verbatim. urllib normalizes '..' out of URLs before sending;
    http.client does not, so traversal probes must go through this."""
    host = srv.replace("http://", "").rstrip("/")
    conn = http.client.HTTPConnection(host)
    conn.putrequest("GET", raw_path, skip_accept_encoding=True)
    conn.endheaders()
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, body


def test_css_served_with_exact_mime(srv):
    status, ctype, body = _get(srv + "/static/app.css")
    assert status == 200
    assert ctype == "text/css; charset=utf-8"
    assert b"--s1: 4px" in body


def test_css_response_keeps_nosniff(srv):
    with urllib.request.urlopen(srv + "/static/app.css") as r:
        assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_unlisted_static_name_is_404(srv):
    try:
        _get(srv + "/static/nope.txt")
        raise AssertionError("expected 404")
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_traversal_never_returns_source(srv):
    probes = [
        "/static/../server.py",
        "/static/../../daimon_ui/server.py",
        "/static/%2e%2e/server.py",
        "/static/..%2Fserver.py",
        "/static/app.css/../../reader.py",
        "/static//etc/passwd",
        "/static/",
    ]
    for probe in probes:
        status, body = _raw_get(srv, probe)
        assert status == 404, f"{probe} returned {status}"
        assert b"def " not in body, f"{probe} leaked Python source"
        assert b"root:" not in body, f"{probe} leaked system file"




@pytest.mark.parametrize("name", ["state.js", "render.js", "app.js"])
def test_js_served_with_exact_mime(srv, name):
    status, ctype, body = _get(srv + "/static/" + name)
    assert status == 200
    assert ctype == "text/javascript; charset=utf-8"
    assert len(body) > 0


def test_app_js_imports_every_render_js_name_it_uses(srv):
    """A render.js export referenced by name in app.js (including bare references
    passed to .map()/.forEach() with no trailing '(') but missing from app.js's
    import block is undefined at runtime — a ReferenceError that only surfaces when
    that code path actually runs in a browser, not from `node --check` or any other
    syntax-only check. This walks every export render.js offers and confirms app.js
    either imports it or defines it locally before it can be referenced."""
    _, _, render_body = _get(srv + "/static/render.js")
    _, _, app_body = _get(srv + "/static/app.js")
    render_src = render_body.decode()
    app_src = app_body.decode()

    exported = sorted(set(re.findall(r"^\s*export (?:function|const)\s+(\w+)", render_src, re.MULTILINE)))
    assert exported, "no exports found in render.js — parsing broke, fix the test"

    m = re.search(r'import\s*\{(.*?)\}\s*from\s*["\']\./render\.js["\']', app_src, re.DOTALL)
    assert m, "app.js has no `import { ... } from \"./render.js\"` block"
    imported = {name.strip() for name in m.group(1).split(",") if name.strip()}

    # Body to scan for usage = app.js source with the import block itself blanked out,
    # so the import statement's own name list doesn't count as a "reference".
    body_for_usage = app_src[:m.start()] + app_src[m.end():]

    offenders = []
    for name in exported:
        word = re.compile(r"\b" + re.escape(name) + r"\b")
        if name in imported:
            continue
        if not word.search(body_for_usage):
            continue  # app.js never references this render.js export — fine
        if re.search(r"\bfunction\s+" + re.escape(name) + r"\s*\(", body_for_usage):
            continue  # shadowed by a local app.js function of the same name — fine
        if re.search(r"\bvar\s+" + re.escape(name) + r"\b", body_for_usage):
            continue  # shadowed by a local app.js var — fine
        line_no = app_src[:app_src.index(name)].count("\n") + 1 if name in app_src else "?"
        offenders.append(f"{name} (first referenced near app.js:{line_no})")

    assert not offenders, (
        "app.js references render.js export(s) not in its import block: "
        + ", ".join(offenders)
    )


def test_no_external_resources_anywhere(srv):
    """Offline, localhost-only inspector: nothing may load from a third-party origin.
    Replaces test_page_is_single_file_no_external_refs, whose <link>/src= half the
    /static/ split legitimately invalidated — this keeps the half that still holds."""
    for path in ["/", "/static/app.css", "/static/app.js",
                 "/static/render.js", "/static/state.js"]:
        _, _, body = _get(srv + path)
        text = re.sub(r'http://127\.0\.0\.1(:\d+)?(?=[/"\s]|$)', '', body.decode())
        for needle in ["https://", "http://", "@import"]:
            assert needle not in text, f"{path} references {needle}"
