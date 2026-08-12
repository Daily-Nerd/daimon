"""Check strip frontend (#670 slice 3): a check strip pill joins the nav in
the frozen order (briefing · ledger · check strip · refutations), the client
renders /api/grid, marks open their entry with the column's rung lit, and the
legend prints the frozen words. No check-now control ships — §7.5.4's
disposition is undecided and this surface stays read-only."""
import urllib.request


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers.get_content_type(), r.read()


def _js(srv, name):
    _, _, body = _get(srv + "/static/" + name)
    return body.decode()


def test_nav_pill_order_is_the_frozen_chrome(srv):
    js = _js(srv, "app.js")
    pills = js.split("Pill order is the frozen chrome", 1)[1].split("forEach", 1)[0]
    order = [pills.index('"briefing"'), pills.index('"ledger"'),
             pills.index('"check strip"'), pills.index('"refutations"')]
    assert order == sorted(order)


def test_client_renders_the_grid_endpoint(srv):
    js = _js(srv, "app.js")
    assert "/api/grid" in js


def test_strip_carries_the_frozen_wording(srv):
    js = _js(srv, "render.js")
    assert "one column per checkpoint · click a mark to open that event" in js
    for legend in ("written / carried", "quote check", "rejection recorded",
                   "no longer carried"):
        assert legend in js, legend


def test_strip_ships_no_check_now_control(srv):
    for name in ("render.js", "app.js"):
        js = _js(srv, name)
        assert "check now" not in js, name


def test_marks_open_the_entry_with_the_rung_lit(srv):
    js = _js(srv, "app.js")
    wiring = js.split("wireStripMarks", 1)
    assert len(wiring) == 2, "strip marks have no wiring"
    assert "whyHighlightSid" in wiring[1].split("}", 2)[-2] or "whyHighlightSid" in wiring[1]
