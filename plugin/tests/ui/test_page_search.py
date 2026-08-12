"""Search surface (#670): the page ships a search box and the client renders
/api/recall and /api/why — never a second matcher, never its own receipt logic."""
import urllib.request


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers.get_content_type(), r.read()


def test_page_has_a_search_form(srv):
    _, _, body = _get(srv + "/")
    html = body.decode()
    assert 'id="search-form"' in html
    assert 'id="search-box"' in html
    # the box announces what it searches with — recall, not a viewer matcher
    assert 'recall' in html.lower()


def test_client_renders_recall_and_why(srv):
    _, _, body = _get(srv + "/static/app.js")
    js = body.decode()
    assert "/api/recall" in js
    assert "/api/why" in js
