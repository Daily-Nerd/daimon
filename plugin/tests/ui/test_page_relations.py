"""History lane frontend (#678 Phase 3): served source carries the wiring
and the frozen wording. Same shape as test_page_refutations.py — fetch the
static bytes over HTTP, assert on source text."""

import urllib.request

from tests.ui.conftest import *  # noqa: F401,F403  (bucket/srv fixtures)


def _text(base, path):
    with urllib.request.urlopen(base + path) as r:
        return r.read().decode("utf-8")


def test_client_fetches_the_relations_endpoint(srv):
    app_js = _text(srv, "/static/app.js")
    assert "/api/relations?" in app_js
    assert "loadHistory" in app_js


def test_why_view_carries_the_history_placeholder(srv):
    render_js = _text(srv, "/static/render.js")
    assert 'id="why-history"' in render_js
    assert "renderHistoryLane" in render_js


def test_lane_carries_the_frozen_wording(srv):
    render_js = _text(srv, "/static/render.js")
    assert "confirmed by a person · candidates never shown" in render_js
    assert "no confirmed connections yet" in render_js
    assert "edge(s) withheld (erased endpoint)" in render_js
    assert "[unresolved]" in render_js


def test_lane_styles_are_served(srv):
    css = _text(srv, "/static/app.css")
    assert ".why-history" in css
    assert ".hist-chip" in css


def test_counterpart_chips_reuse_the_universal_entry_door(srv):
    render_js = _text(srv, "/static/render.js")
    lane = render_js.split("renderHistoryLane", 1)[1]
    assert 'data-open-why' in lane.split("function", 1)[0] or \
        'data-open-why' in lane[:2000]
