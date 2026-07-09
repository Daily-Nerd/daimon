from daimon_briefing import anchor

_SRC = '''
def foo(x):
    return x + 1


class Bar:
    def baz(self):
        return 2
'''


def _write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_resolve_top_level_function(tmp_path):
    _write(tmp_path, "m.py", _SRC)
    a = anchor.resolve(tmp_path, "m.py", "foo")
    assert a["qualified_name"] == "m.py::foo"
    assert a["file"] == "m.py" and a["symbol"] == "foo"
    assert isinstance(a["body_hash"], str) and len(a["body_hash"]) == 64


def test_resolve_class_method(tmp_path):
    _write(tmp_path, "m.py", _SRC)
    a = anchor.resolve(tmp_path, "m.py", "Bar.baz")
    assert a["qualified_name"] == "m.py::Bar.baz"


def test_resolve_missing_symbol_returns_none(tmp_path):
    _write(tmp_path, "m.py", _SRC)
    assert anchor.resolve(tmp_path, "m.py", "nope") is None


def test_check_live_when_unchanged(tmp_path):
    _write(tmp_path, "m.py", _SRC)
    a = anchor.resolve(tmp_path, "m.py", "foo")
    assert anchor.check(a, tmp_path) == "live"


def test_check_soft_when_body_changes(tmp_path):
    _write(tmp_path, "m.py", _SRC)
    a = anchor.resolve(tmp_path, "m.py", "foo")
    _write(tmp_path, "m.py", _SRC.replace("return x + 1", "return x + 2"))
    assert anchor.check(a, tmp_path) == "soft"


def test_check_live_ignores_formatting_and_comments(tmp_path):
    _write(tmp_path, "m.py", _SRC)
    a = anchor.resolve(tmp_path, "m.py", "foo")
    _write(tmp_path, "m.py", _SRC.replace("def foo(x):", "def foo(x):  # a comment"))
    assert anchor.check(a, tmp_path) == "live"


def test_check_hard_when_symbol_removed(tmp_path):
    _write(tmp_path, "m.py", _SRC)
    a = anchor.resolve(tmp_path, "m.py", "foo")
    _write(tmp_path, "m.py", "def other():\n    return 0\n")
    assert anchor.check(a, tmp_path) == "hard"


def test_check_hard_when_file_gone(tmp_path):
    _write(tmp_path, "m.py", _SRC)
    a = anchor.resolve(tmp_path, "m.py", "foo")
    (tmp_path / "m.py").unlink()
    assert anchor.check(a, tmp_path) == "hard"


def test_drifted_collects_only_non_live_anchored_items(tmp_path):
    _write(tmp_path, "m.py", _SRC)
    live = anchor.resolve(tmp_path, "m.py", "foo")
    gone = {"qualified_name": "m.py::ghost", "file": "m.py",
            "symbol": "ghost", "body_hash": "deadbeef"}
    checkpoint = {
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [{"text": "q-live", "trust": "inferred", "anchored_to": live}],
            "recent_decisions": [{"text": "d-gone", "trust": "inferred", "anchored_to": gone}],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [{"text": "b-no-anchor", "trust": "inferred"}],
            "uncertainties": [],
        },
    }
    out = anchor.drifted(checkpoint, tmp_path)
    texts = {d["item"]["text"]: d["kind"] for d in out}
    assert texts == {"d-gone": "hard"}  # live + unanchored omitted


def test_drifted_handles_missing_sections(tmp_path):
    assert anchor.drifted({}, tmp_path) == []


def test_check_hard_on_malformed_anchor_missing_keys(tmp_path):
    # A hand-pasted anchor missing required keys must NOT raise — it degrades to "hard".
    assert anchor.check({"file": "m.py"}, tmp_path) == "hard"          # no symbol
    assert anchor.check({"symbol": "foo"}, tmp_path) == "hard"         # no file
    assert anchor.check({}, tmp_path) == "hard"                        # empty


def test_drifted_does_not_raise_on_malformed_anchor(tmp_path):
    checkpoint = {
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [
                {"text": "broken", "trust": "inferred", "anchored_to": {"file": "m.py"}}
            ],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }
    out = anchor.drifted(checkpoint, tmp_path)  # must not raise
    assert [d["item"]["text"] for d in out] == ["broken"]
    assert out[0]["kind"] == "hard"


# ---- target file with syntax errors (hot path: drift scan runs on every brief) ----

_BROKEN_SRC = "def foo(:\n    return x + 1\n"  # unparseable on any Python version


def test_check_hard_when_target_file_has_syntax_error(tmp_path):
    # An anchored file that no longer parses is unverifiable — degrade to "hard"
    # (fail toward verify-before-trusting), never raise.
    _write(tmp_path, "m.py", _SRC)
    a = anchor.resolve(tmp_path, "m.py", "foo")
    _write(tmp_path, "m.py", _BROKEN_SRC)
    assert anchor.check(a, tmp_path) == "hard"


def test_body_hash_of_returns_none_on_syntax_error():
    assert anchor.body_hash_of(_BROKEN_SRC, "foo") is None


def test_drifted_degrades_on_syntax_error_file(tmp_path):
    _write(tmp_path, "m.py", _SRC)
    a = anchor.resolve(tmp_path, "m.py", "foo")
    _write(tmp_path, "m.py", _BROKEN_SRC)
    checkpoint = {
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [
                {"text": "q-broken-file", "trust": "inferred", "anchored_to": a}
            ],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }
    out = anchor.drifted(checkpoint, tmp_path)  # must not raise
    assert [(d["item"]["text"], d["kind"]) for d in out] == [("q-broken-file", "hard")]


def test_brief_render_survives_syntax_error_in_anchored_file(tmp_path, capsys):
    # End-to-end pin: brief render over a drift scan that hit an unparseable
    # file must not traceback — the item surfaces as GONE drift.
    from daimon_briefing import render

    _write(tmp_path, "m.py", _SRC)
    a = anchor.resolve(tmp_path, "m.py", "foo")
    _write(tmp_path, "m.py", _BROKEN_SRC)
    checkpoint = {
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [
                {"text": "q-broken-file", "trust": "inferred", "anchored_to": a}
            ],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }
    drift = anchor.drifted(checkpoint, tmp_path)
    render.render_brief(checkpoint, drift=drift)  # must not raise
    out = capsys.readouterr().out
    assert "CODE DRIFT" in out
    assert "[GONE] q-broken-file" in out


def test_drifted_collects_soft_drift(tmp_path):
    _write(tmp_path, "m.py", _SRC)
    a = anchor.resolve(tmp_path, "m.py", "foo")
    _write(tmp_path, "m.py", _SRC.replace("return x + 1", "return x + 99"))
    checkpoint = {
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [{"text": "soft-one", "trust": "inferred", "anchored_to": a}],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }
    out = anchor.drifted(checkpoint, tmp_path)
    assert [(d["item"]["text"], d["kind"]) for d in out] == [("soft-one", "soft")]
