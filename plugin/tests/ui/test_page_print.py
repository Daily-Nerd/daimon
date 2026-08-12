"""Print view (#670 slice 4): one checkpoint set as a printed record. The door
is the entry page's footer; back returns to the entry. Blocks render a hanging
monospace margin (id / trust class / event) beside the claim at reading size,
with the provenance line under it — the stored quote's presence and origin,
never the quote text itself. Renders the same session_events the session page
reads, so the two surfaces cannot disagree."""
import urllib.request


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers.get_content_type(), r.read()


def _js(srv, name):
    _, _, body = _get(srv + "/static/" + name)
    return body.decode()


def test_print_view_carries_the_frozen_wording(srv):
    js = _js(srv, "render.js")
    assert "print view · one checkpoint, set as a printed record" in js
    assert "no class asserted · no quote stored" in js
    assert "quote stored" in js
    assert "no quote stored" in js


def test_the_entry_footer_is_the_door(srv):
    js = _js(srv, "render.js")
    assert "data-open-print" in js
    assert "← entry" in js


def test_back_returns_to_the_entry(srv):
    js = _js(srv, "app.js")
    assert "data-print-back" in js


def test_print_renders_the_session_engine(srv):
    """No second walk: the print view fetches the same /api/session payload
    the session page renders."""
    js = _js(srv, "app.js")
    wiring = js.split("enterPrint", 1)
    assert len(wiring) == 2, "print view has no entry point"
    assert "/api/session" in wiring[1]
