"""Ledger screen + session page (#670 slice 2): the nav carries the design's
pills (briefing, ledger) instead of the scaffold's History/Activity buttons,
and the client renders /api/ledger and /api/session. Wording checks pin the
frozen vocabulary — the event names render exactly as the CLI family prints
them, and the session page's footer states what the page is."""
import urllib.request


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers.get_content_type(), r.read()


def test_nav_carries_pills_not_the_scaffold_buttons(srv):
    _, _, body = _get(srv + "/")
    html = body.decode()
    nav = html.split('<nav class="view-nav"', 1)[1].split("</nav>", 1)[0]
    assert 'id="nav-pills-slot"' in nav
    for gone in ('id="history-link-slot"', 'id="activity-link-slot"'):
        assert gone not in nav, f"scaffold slot survived: {gone}"


def test_client_renders_ledger_and_session(srv):
    _, _, body = _get(srv + "/static/app.js")
    js = body.decode()
    assert "/api/ledger" in js
    assert "/api/session" in js


def test_event_labels_are_the_frozen_words(srv):
    """§8: first seen / changed / last seen / quote check / resolved — the
    ledger renders the recorded event kinds under their frozen names, nothing
    coined at the render layer."""
    _, _, body = _get(srv + "/static/render.js")
    js = body.decode()
    labels_src = js.split("LEDGER_EVENT_LABELS", 1)[1].split("}", 1)[0]
    for label in ('"first seen"', '"changed"', '"last seen"',
                  '"quote check"', '"resolved"'):
        assert label in labels_src, label


def test_session_page_declares_it_holds_nothing(srv):
    _, _, body = _get(srv + "/static/render.js")
    js = body.decode()
    assert "This page reads the ledger; it holds nothing of its own" in js


def test_ledger_catch_clears_stale_sections(srv):
    """Same defensive shape the other views carry: a failed fetch must not
    leave the previous view's sections behind an error card. Source grep,
    kept deliberately and labelled as such."""
    _, _, body = _get(srv + "/static/app.js")
    js = body.decode()
    fn = js.split("function enterLedger", 1)[1].split("\n  function ", 1)[0]
    catch_block = fn.split(".catch(function ()", 1)[1]
    assert 'sectionsEl.innerHTML = "";' in catch_block
