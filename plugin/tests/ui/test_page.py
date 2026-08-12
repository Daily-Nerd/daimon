import urllib.request

def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers.get_content_type(), r.read()

def test_page_has_regions(srv):
    _, _, body = _get(srv + "/")
    html = body.decode()
    for needle in ['id="sidebar"', 'id="main"', 'id="state"', 'id="sections"',
                   '<link rel="stylesheet" href="/static/app.css">']:
        assert needle in html, needle

def test_view_links_sit_in_a_nav_landmark(srv):
    _, _, body = _get(srv + "/")
    html = body.decode()
    assert '<nav class="view-nav"' in html, "no view-nav landmark in page"
    nav = html.split('<nav class="view-nav"', 1)[1].split("</nav>", 1)[0]
    for needle in ['aria-label="Views"', 'id="back-link-slot"',
                   'id="nav-pills-slot"']:
        assert needle in nav, needle


def _tag_carrying(html, needle):
    """The whole tag containing `needle`, both sides of it.

    Reading only the text *after* the attribute makes the assertion depend on
    attribute order: `<div id="state" aria-live="polite">` is caught while
    `<div aria-live="polite" id="state">` slips past, and the two are the same
    element to a browser. Scar 0006 fences the same blind spot in HTML."""
    before, after = html.split(needle, 1)
    return before.rsplit("<", 1)[1] + needle + after.split(">", 1)[0]


def test_live_region_is_the_status_line_not_the_content_container(srv):
    """#state receives whole rendered views via innerHTML (app.js renderActivityView,
    renderEmptyProjects, renderError). A live region there re-announces the entire
    view on every navigation. The announcement duty belongs to a dedicated, small
    #status element instead. Scoped to each element's own tag so a stray aria-live
    elsewhere in the document cannot satisfy it."""
    _, _, body = _get(srv + "/")
    html = body.decode()

    assert 'id="status"' in html, "no dedicated #status announcement element in page"
    status_tag = _tag_carrying(html, 'id="status"')
    assert 'role="status"' in status_tag, status_tag

    state_tag = _tag_carrying(html, 'id="state"')
    assert "aria-live" not in state_tag, state_tag


def test_flagged_dot_paints_differently_from_ordinary_event_dots(srv):
    """The JS side asserts quote_check picks the "flagged" dot class; that alone
    proves nothing if the class paints the same pixels as the dots it must stand
    apart from. The original defect was exactly this shape — quote_check used
    "changed", whose rule is `background: var(--accent)`, identical to
    .life-dot-born. Compares declaration bodies, not just class presence."""
    _, _, css = _get(srv + "/static/app.css")
    css = css.decode()

    def rule_for(cls):
        hits = [blk for blk in css.split("}") if f".{cls}" in blk.split("{")[0]]
        assert hits, f"no rule defines .{cls}"
        return hits[0].split("{", 1)[1].strip()

    flagged = rule_for("life-dot-flagged")
    for ordinary in ("life-dot-born", "life-dot-seen", "life-dot-resolved"):
        assert flagged != rule_for(ordinary), \
            f".life-dot-flagged paints identically to .{ordinary}: {flagged}"


def test_css_has_tokens_and_media_queries(srv):
    """The viewer commits to one dark look (the design's frozen palette), so
    there is deliberately no prefers-color-scheme block to assert on."""
    _, _, body = _get(srv + "/static/app.css")
    css = body.decode()
    for needle in ["--s1: 4px", "--s8: 32px", "--accent",
                   "prefers-reduced-motion"]:
        assert needle in css, needle
    assert "prefers-color-scheme" not in css, "single committed look — no theme fork"

def test_session_catch_clears_stale_sections(srv):
    """enterSession lives in app.js and touches the DOM, so this stays a source
    grep — a weaker test than the render.js behavior tests, kept deliberately and
    labelled as such rather than dropped silently. (enterActivity's version of
    this test left with the Activity button: slice 2 replaced the scaffold nav
    with the design's pills, and the ledger/session pages read the same rows.)"""
    _, _, body = _get(srv + "/static/app.js")
    js = body.decode()
    fn = js.split("function enterSession", 1)[1].split("\n  function ", 1)[0]
    catch_block = fn.split(".catch(function ()", 1)[1]
    assert 'sectionsEl.innerHTML = "";' in catch_block
