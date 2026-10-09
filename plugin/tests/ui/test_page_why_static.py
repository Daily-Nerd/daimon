"""The why view's withheld branches read the `reason` (#1132 PR 11a).

A static scan of `render.js` (no server, no browser), plus a real run of the
helpers under node when it is installed. The scan asserts three things: each
of the three withheld branches (item text, stored quote, transcript window)
reads the reason through a helper, no helper or branch spells a count, and the
sentence about a forget tombstone appears only where the reason is "forgotten".
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import daimon_ui

STATIC = Path(daimon_ui.__file__).parent / "static"
RENDER = (STATIC / "render.js").read_text(encoding="utf-8")


def _function(name):
    head = f"export function {name}("
    body = RENDER.split(head, 1)[1]
    return body.split("\n  export function ", 1)[0]


def test_the_three_branches_go_through_a_reason_helper():
    view_src = _function("renderWhyView")
    assert "withheldItemWords(item.text)" in view_src
    assert "withheldItemWords(item.quote)" in view_src
    assert "withheldSourceWords(src)" in view_src


def test_the_helpers_read_the_reason_and_never_a_count():
    for name in ("withheldItemWords", "withheldSourceWords"):
        body = _function(name)
        assert "w.reason" in body or "w && w.reason" in body, name
        assert "forgotten:" not in body and ".forgotten" not in body, name
    assert "src.forgotten" not in _function("renderWhyView")


def test_the_forget_tombstone_sentence_lives_only_in_the_forgotten_branches():
    for name, reason in (("withheldItemWords", '"forgotten"'),
                         ("withheldSourceWords", '"forgotten-set"')):
        body = _function(name)
        assert body.count("forget tombstone") == 1, name
        before = body.split("forget tombstone", 1)[0]
        assert before.rstrip().splitlines()[-2].strip().startswith(
            f"if (reason === {reason}"), (name, before[-120:])
    assert "forget tombstone" not in _function("renderWhyView")


def test_a_withheld_item_has_no_object_render_path():
    code = "\n".join(line for line in _function("renderWhyView").splitlines()
                     if not line.strip().startswith("//"))
    assert "escapeHtml(item.text)" not in code
    assert 'escapeHtml(item.text || "")' in code            # the plain branch


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_helpers_run_under_node(tmp_path):
    script = tmp_path / "run.mjs"
    script.write_text(
        'import { withheldItemWords as i, withheldSourceWords as s,'
        ' renderWhyView as v } from ' + json.dumps(
            (STATIC / "render.js").as_uri()) + ';\n'
        'const out = {\n'
        '  forgotten: i({state: "withheld", reason: "forgotten"}),\n'
        '  quarantine: i({state: "withheld", reason: "quarantine",'
        ' quarantine_id: "tr-abc"}),\n'
        '  closed: i({state: "withheld", reason: "closed"}),\n'
        '  unknown: i({state: "withheld", reason: "something-new"}),\n'
        '  none: i(null),\n'
        '  setForgotten: s({reason: "forgotten-set"}),\n'
        '  setQuarantine: s({reason: "quarantine-set"}),\n'
        '  srcClosed: s({reason: "closed"}),\n'
        '  item: s({reason: "withheld-item"}),\n'
        '  page: v({item: {item_id: "o-aaaaaa", kind: "decision",'
        ' text: {state: "withheld", reason: "quarantine",'
        ' quarantine_id: "tr-abc"}, quote: {state: "withheld",'
        ' reason: "quarantine"}, trust: null, origin_session: null,'
        ' session_id: null, occurrences: 0}, axes: {lifecycle: "active"},'
        ' corroboration: {count: 0, references: []},'
        ' source_excerpt: {state: "withheld", reason: "forgotten-set"}}),\n'
        '};\n'
        'console.log(JSON.stringify(out));\n', encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True,
                         timeout=60)
    assert run.returncode == 0, run.stderr
    got = json.loads(run.stdout)
    assert "forget tombstone" in got["forgotten"]
    assert "tr-abc" in got["quarantine"] and "tombstone" not in got["quarantine"]
    assert "ledger" in got["closed"]
    assert got["unknown"] == "withheld" == got["none"]
    assert "machine" in got["setForgotten"]
    assert "quarantine" in got["setQuarantine"]
    assert "ledger" in got["srcClosed"] and got["item"] == "this item is withheld"
    page = got["page"]
    assert "[object Object]" not in page and "undefined" not in page
    assert "a person quarantined this value (tr-abc)" in page
    assert "transcript predates any forgetting" in page
    assert "no quote stored" not in page
