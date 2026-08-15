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
