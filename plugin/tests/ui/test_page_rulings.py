"""Rulings lane frontend (#693 PR 2): the refutations view splits into two
lanes. The rulings lane renders the VERDICT (the rule text, cli._print_ruling's
contract) under the CLI's own glyphs — § active, × retired, ? candidate — and
an overturned ruling reads "retired". Nothing is coined at the render layer."""
import urllib.request


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers.get_content_type(), r.read()


def test_lane_glyphs_are_the_cli_words(srv):
    _, _, body = _get(srv + "/static/render.js")
    js = body.decode()
    src = js.split("RULING_MARKS", 1)[1].split("}", 1)[0]
    for pair in ('active: "§"', 'overturned: "×"', 'candidate: "?"'):
        assert pair in src, pair


def test_overturned_ruling_reads_retired(srv):
    _, _, body = _get(srv + "/static/render.js")
    js = body.decode()
    assert '"retired"' in js


def test_ruling_row_renders_the_verdict(srv):
    _, _, body = _get(srv + "/static/render.js")
    js = body.decode()
    row = js.split("renderRulingRow", 1)[1].split("renderRefutationsView", 1)[0]
    assert "verdict" in row
    assert "r.subject" not in row  # verdict, never subject — the inversion trap


def test_rulings_lane_carries_its_own_heading(srv):
    _, _, body = _get(srv + "/static/render.js")
    js = body.decode()
    assert "Standing rulings" in js


def test_lane_chrome_never_asserts_ratification_over_candidates(srv):
    # The lane serves EVERY state (it mirrors `daimon ruling list`), so its
    # section chrome must speak in status terms: an agent-proposed candidate
    # sitting under a "human-ratified" banner asserts an approval no human
    # gave.
    _, _, body = _get(srv + "/static/render.js")
    js = body.decode()
    lane = js.split("Standing rulings", 1)[1].split("Refutations</h1>", 1)[0]
    assert "human-ratified" not in lane
    assert "never decay" not in lane


def test_rulings_lane_never_sits_under_the_refutations_heading(srv):
    # The negative-polarity page chrome ("recorded against entries · status,
    # never judgment") must not visually govern the rulings table — that is
    # the presentational form of the inversion this lane exists to prevent.
    _, _, body = _get(srv + "/static/render.js")
    js = body.decode()
    view = js.split("renderRefutationsView", 1)[1]
    assert view.index("Standing rulings") < view.index(">Refutations</h1>")
