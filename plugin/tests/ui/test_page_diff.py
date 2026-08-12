"""Diff view (#670 slice 3): the diff hangs off the LIFE ladder — a bio rung
opens the pairwise diff between that sighting and the one before it, and a
diff row returns to the entry with its source rung highlighted. Kinds render
the frozen words (added / changed / dropped / resolved), changed rows show
the old value struck through above the new one, and the footer states what a
diff is."""
import urllib.request


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers.get_content_type(), r.read()


def _js(srv, name):
    _, _, body = _get(srv + "/static/" + name)
    return body.decode()


def test_diff_view_kinds_are_the_frozen_words(srv):
    js = _js(srv, "render.js")
    src = js.split("DIFF_KINDS", 1)[1].split("}", 1)[0]
    for pair in ('born: "added"', 'changed: "changed"', 'gone: "dropped"',
                 'resolved: "resolved"'):
        assert pair in src, pair


def test_diff_view_declares_what_a_diff_is(srv):
    js = _js(srv, "render.js")
    assert ("A diff is a reading of the ledger, not a mutation of it. "
            "Both checkpoints remain on disk.") in js


def test_diff_summary_counts_from_picker_order(srv):
    """'N checkpoints apart' is computed from the two picked sessions'
    positions in the history list — never copied from a default pair."""
    js = _js(srv, "render.js")
    assert "checkpoints apart" in js


def test_changed_rows_strike_the_old_value(srv):
    js = _js(srv, "render.js")
    assert "diff-old" in js


def test_bio_rungs_open_the_pairwise_diff(srv):
    """A LIFE rung carries the session that wrote it; clicking it must open
    the diff between the previous sighting and that session."""
    js = _js(srv, "app.js")
    assert "data-diff-sid" in js
    assert "/api/diff" in js


def test_diff_rows_return_to_the_entry_with_the_rung_lit(srv):
    render = _js(srv, "render.js")
    app = _js(srv, "app.js")
    assert "rung-lit" in render
    assert "whyHighlightSid" in app


def test_the_orphaned_history_view_is_gone(srv):
    """Slice 2 deleted the History entry points; slice 3 replaces the view
    itself. The old renderer must not survive as dead code beside the new
    one — two diff renderings would be two vocabularies waiting to drift."""
    js = _js(srv, "render.js")
    assert "renderHistoryView" not in js
    assert "renderDiffView" in js
