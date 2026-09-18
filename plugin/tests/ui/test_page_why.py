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
