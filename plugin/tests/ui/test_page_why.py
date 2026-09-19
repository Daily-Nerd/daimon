"""Why view source disclosure (#1065): `/api/why?source=1` can answer with two
shapes for `source_excerpt` — the disclosed `{kind, text, truncated}` window, or
the withheld `{state: "withheld", forgotten: N}` refusal a live forget tombstone
produces. Gating the render on `src.text` alone renders NOTHING for the withheld
shape, the exact silent-empty failure #1065 exists to prevent."""
import urllib.request


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers.get_content_type(), r.read()


def _js(srv, name):
    _, _, body = _get(srv + "/static/" + name)
    return body.decode()


def _render_why_view_src(srv):
    js = _js(srv, "render.js")
    return js.split("export function renderWhyView", 1)[1].split(
        "\n  export function ", 1)[0]


def test_why_view_renders_a_visible_line_when_the_source_is_withheld(srv):
    src = _render_why_view_src(srv)
    assert 'src.state === "withheld"' in src
    assert "forget tombstone" in src


def test_why_view_withheld_branch_carries_the_count_and_stays_out_of_a_pre(srv):
    """The withheld line is a stated refusal, not a transcript excerpt: it must
    show the tombstone count and must not render inside a <pre>, which is
    reserved for text Daimon is actually disclosing."""
    src = _render_why_view_src(srv)
    branch = src.split('src.state === "withheld"', 1)[1]
    branch = branch.split("} else if", 1)[0] if "} else if" in branch else branch.split("}", 1)[0]
    assert "src.forgotten" in branch
    assert "<pre>" not in branch


def test_why_view_still_renders_a_disclosed_window(srv):
    """The pre-#1065 disclosed shape ({kind, text, truncated}) must still render
    exactly as before when nothing was withheld."""
    src = _render_why_view_src(srv)
    assert "src.text" in src
    assert "<pre>" in src


# ---- #1070: the item itself can also carry a withheld state --------------
#
# `why <id>` withholds the item's text/quote (not just --source's transcript
# window) whenever the item's own content matches a live forget tombstone,
# local or a teammate's. `item.text`/`item.quote` then carry {state:
# "withheld"} instead of a string, and gating on truthiness alone would print
# "[object Object]" via escapeHtml, the same silent-wrong-render class #1065
# exists to prevent, one level up.


def test_why_view_renders_a_visible_line_when_item_text_is_withheld(srv):
    src = _render_why_view_src(srv)
    assert 'item.text.state === "withheld"' in src
    assert "forget tombstone" in src


def test_why_view_item_text_withheld_branch_never_reaches_escapeHtml_of_the_object(srv):
    """The withheld branch must render its own stated line, not fall through
    to `escapeHtml(item.text)`, which would stringify the object."""
    src = _render_why_view_src(srv)
    branch = src.split('item.text.state === "withheld"', 1)[1]
    branch = branch.split("} else", 1)[0]
    assert "why-none" in branch
    assert "escapeHtml(item.text" not in branch


def test_why_view_still_renders_a_plain_item_text(srv):
    src = _render_why_view_src(srv)
    assert 'escapeHtml(item.text || "")' in src


def test_why_view_renders_a_visible_line_when_item_quote_is_withheld(srv):
    src = _render_why_view_src(srv)
    assert 'item.quote.state === "withheld"' in src


def test_why_view_item_quote_withheld_is_not_rendered_as_a_blockquote(srv):
    src = _render_why_view_src(srv)
    branch = src.split('item.quote.state === "withheld"', 1)[1]
    branch = branch.split("} else if", 1)[0]
    assert "<blockquote>" not in branch
    assert "why-none" in branch


def test_why_view_still_renders_a_stored_quote_when_not_withheld(srv):
    src = _render_why_view_src(srv)
    assert "<blockquote>" in src
    assert "no quote stored" in src
