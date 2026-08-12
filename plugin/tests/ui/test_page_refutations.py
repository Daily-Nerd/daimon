"""Refutations lane frontend (#670 slice 3): a refutations pill joins the nav,
the client fetches /api/refutations, and the lane carries the frozen wording —
column heads Record / Refutes / Recorded / Origin, the standing footer, and a
density line instead of a silent cap. State markers print the CLI's own words
([? candidate · agent-proposed]); nothing is coined at the render layer."""
import urllib.request


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers.get_content_type(), r.read()


def test_nav_gains_the_refutations_pill(srv):
    _, _, body = _get(srv + "/static/app.js")
    js = body.decode()
    pills = js.split("nav-pills-slot", 1)[1]
    assert '"refutations"' in pills


def test_client_fetches_the_engine_endpoint(srv):
    _, _, body = _get(srv + "/static/app.js")
    js = body.decode()
    assert "/api/refutations" in js


def test_lane_carries_the_frozen_wording(srv):
    _, _, body = _get(srv + "/static/render.js")
    js = body.decode()
    assert "recorded against entries · status, never judgment" in js
    assert "These records live outside checkpoints and survive checkpoint expiry." in js
    for col in (">Record<", ">Refutes<", ">Recorded<", ">Origin<"):
        assert col in js, col


def test_lane_state_marker_is_the_cli_word(srv):
    """The marker glyphs are the CLI's: ✗ active, × overturned, ? candidate.
    The lane renders refute list; it must not invent a second vocabulary."""
    _, _, body = _get(srv + "/static/render.js")
    js = body.decode()
    src = js.split("REFUTATION_MARKS", 1)[1].split("}", 1)[0]
    for pair in ('active: "✗"', 'overturned: "×"', 'candidate: "?"'):
        assert pair in src, pair
