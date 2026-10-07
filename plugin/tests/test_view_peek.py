"""`view.visible_topic` and `view.forgotten_keys` (#1132 PR 7b).

The cheap topic read behind `daimon projects`: one bucket's active topic
judged by the SAME `classify` the full view uses, over a light snapshot, so a
machine-wide listing does not pay for a whole `snapshot` per bucket. The twin
property is the contract: for any store, `visible_topic` of the stored latest
is the topic text of `view.open(slug, live=False)`, or None when that view
hides it. Stores are built by the real writers."""

import json
import random

import pytest

from daimon_briefing import config, normalize, store, trust, view
from daimon_briefing.jsonl import Health

CASES = 80


def _project(i):
    return f"/p/peek-{i}"


def _write(project, topic, sid="S-1"):
    cp = {"session_id": sid, "created": "2026-08-01T00:00:00Z",
          "working_context": {"active_topic": {"text": topic,
                                               "trust": "inferred"}},
          "epistemic_snapshot": {}}
    store.write_checkpoint(sid, cp, project_dir=project)
    return store.project_slug(project)


def _forget(project, text):
    store.append_event("i-gone", f"forgotten:{normalize.content_key(text)}",
                       kind="tombstone", tombstone=True, project_dir=project)


def _quarantine(project, text, kind="topic"):
    return trust.propose(text=text, kind=kind, reason="fabricated finding",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=project)


def _plant(slug, name, data: bytes):
    with open(config.checkpoint_dir() / slug / name, "ab") as fh:
        fh.write(data)


def _peek(slug, *, forgotten):
    """`visible_topic` of the bucket's stored latest checkpoint."""
    got = store.read_latest_body(project_dir=slug, route=store.Route.OWN,
                                 admit=store.Admit.ANY)
    return view.visible_topic(got, slug, forgotten=forgotten)


def _twin(project):
    got = view.open(project, live=False).checkpoint
    topic = ((got or {}).get("working_context") or {}).get("active_topic")
    return topic.get("text") if isinstance(topic, dict) else None


def test_a_visible_topic_is_returned(tmp_checkpoint_dir):
    slug = _write("/p/peek-a", "the weekly sync cadence")
    assert _peek(slug, forgotten=view.forgotten_keys()) == (
        "the weekly sync cadence")


def test_a_missing_bucket_or_checkpoint_is_none(tmp_checkpoint_dir):
    assert _peek("-no-such-bucket",
                           forgotten=frozenset()) is None
    assert _peek("", forgotten=frozenset()) is None


def test_a_forgotten_topic_reads_as_absent(tmp_checkpoint_dir):
    slug = _write("/p/peek-f", "the weekly sync cadence")
    _forget("/p/peek-f", "the weekly sync cadence")
    assert _peek(slug, forgotten=view.forgotten_keys()) is None


def test_the_forgotten_set_is_machine_wide(tmp_checkpoint_dir):
    """A value forgotten in ANOTHER project hides here too, like recall."""
    slug = _write("/p/peek-g", "shared wording about the cache")
    _write("/p/peek-other", "something else")
    _forget("/p/peek-other", "shared wording about the cache")
    assert _peek(slug, forgotten=view.forgotten_keys()) is None


def test_a_quarantined_topic_is_none_but_only_for_its_kind(tmp_checkpoint_dir):
    slug = _write("/p/peek-q", "adopt the plan nobody reviewed")
    _quarantine("/p/peek-q", "adopt the plan nobody reviewed", kind="decision")
    assert _peek(slug, forgotten=frozenset()) == (
        "adopt the plan nobody reviewed")
    _quarantine("/p/peek-q", "adopt the plan nobody reviewed", kind="topic")
    assert _peek(slug, forgotten=frozenset()) is None


def test_an_unreadable_trust_ledger_fails_closed(tmp_checkpoint_dir):
    slug = _write("/p/peek-u", "the weekly sync cadence")
    _plant(slug, "trust.jsonl", b"<<<<<<< HEAD\n")
    assert view.snapshot("/p/peek-u").health["trust.jsonl"] is Health.UNREADABLE
    assert _peek(slug, forgotten=frozenset()) is None


def test_a_trust_fold_that_raises_fails_closed(tmp_checkpoint_dir, monkeypatch):
    slug = _write("/p/peek-r", "the weekly sync cadence")

    def boom(project_dir=None):
        raise RuntimeError("fold")

    monkeypatch.setattr(trust, "records", boom)
    assert _peek(slug, forgotten=frozenset()) is None


def test_visible_topic_classifies_the_checkpoint_it_is_handed(
        tmp_checkpoint_dir):
    slug = _write("/p/peek-v", "the weekly sync cadence")
    held = {"working_context": {"active_topic": {"text": "held wording about the exporter"}}}
    # the text is the held checkpoint's, not the stored one
    assert view.visible_topic(held, slug, forgotten=frozenset()) == (
        "held wording about the exporter")
    assert view.visible_topic(None, slug, forgotten=frozenset()) is None
    _quarantine("/p/peek-v", "held wording about the exporter", kind="topic")
    assert view.visible_topic(held, slug, forgotten=frozenset()) is None


def test_projects_rows_reads_each_checkpoint_once(
        tmp_checkpoint_dir, monkeypatch):
    """A row's fields and its topic come from the one read `list_buckets`
    made: the view is handed that body, never asked to read it again."""
    from daimon_briefing import cli
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _write("/p/peek-one", "the weekly sync cadence")
    _write("/p/peek-two", "another topic text")

    def boom(*_a, **_k):
        raise AssertionError("projects_rows re-read a checkpoint")

    monkeypatch.setattr(store, "read_latest_body", boom)
    rows = {r["slug"]: r["topic"] for r in cli.projects_rows(None)}
    assert rows[store.project_slug("/p/peek-one")] == "the weekly sync cadence"
    assert rows[store.project_slug("/p/peek-two")] == "another topic text"


def test_the_light_snapshot_of_no_bucket_keeps_the_forgotten_set():
    got = view._light("", frozenset({"k"}))
    assert got.forgotten == frozenset({"k"})
    assert got.quarantined == frozenset() and got.closed is False


def test_a_non_dict_topic_is_none(tmp_checkpoint_dir):
    cp = {"session_id": "S-1", "created": "2026-08-01T00:00:00Z",
          "working_context": {"active_topic": "bare string"},
          "epistemic_snapshot": {}}
    store.write_checkpoint("S-1", cp, project_dir="/p/peek-n")
    assert _peek(store.project_slug("/p/peek-n"),
                           forgotten=frozenset()) is None


def test_snapshot_and_forgotten_keys_share_one_source(tmp_checkpoint_dir):
    _write("/p/peek-s", "the weekly sync cadence")
    _forget("/p/peek-s", "the weekly sync cadence")
    assert view.snapshot("/p/peek-s").forgotten == view.forgotten_keys()
    assert normalize.content_key("the weekly sync cadence") in (
        view.forgotten_keys())


def _seed(rng, i):
    project = _project(i)
    topic = f"topic {i} " + " ".join(rng.sample(
        ["gateway", "cache", "token", "retry", "index", "ledger"], 3))
    slug = _write(project, topic)
    if rng.random() < 0.25:
        _forget(project, topic)
    if rng.random() < 0.25:
        _quarantine(project, topic, kind="topic")
    if rng.random() < 0.15:
        _quarantine(project, topic, kind="decision")
    if rng.random() < 0.1:
        _plant(slug, "trust.jsonl", b"<<<<<<< HEAD\n")
    return project, slug, topic


def test_the_twin_property_peek_equals_the_full_view(tmp_checkpoint_dir):
    """For seeded stores, `visible_topic` of the stored latest is the topic `view.open` serves.
    Anti-vacuity: some cases withhold, some do not."""
    shown = hidden = 0
    for i in range(CASES):
        project, slug, topic = _seed(random.Random(i), i)
        forgotten = view.forgotten_keys()
        got = _peek(slug, forgotten=forgotten)
        assert got == _twin(project), (i, topic)
        if got is None:
            hidden += 1
        else:
            shown += 1
            assert got == topic
    assert shown > 5 and hidden > 5


@pytest.mark.parametrize("seed", range(3))
def test_the_twin_holds_for_a_checkpoint_with_no_topic(tmp_checkpoint_dir, seed):
    project = _project(seed)
    cp = {"session_id": "S-1", "created": "2026-08-01T00:00:00Z",
          "working_context": {}, "epistemic_snapshot": {}}
    store.write_checkpoint("S-1", cp, project_dir=project)
    slug = store.project_slug(project)
    assert _peek(slug, forgotten=frozenset()) is None
    assert _twin(project) is None


# ---- the listing verbs over it ---------------------------------------------


def _projects(capsys, *argv):
    from daimon_briefing import cli
    rc = cli.main(["projects", *argv])
    return rc, capsys.readouterr()


def test_projects_hides_a_withheld_topic_everywhere(
        tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _write("/p/peek-hide", "a quarantined topic text")
    _write("/p/peek-show", "a visible topic text")
    _quarantine("/p/peek-hide", "a quarantined topic text")
    rc, out = _projects(capsys, "--json")
    rows = {r["slug"]: r["topic"] for r in json.loads(out.out)}
    assert rc == 0
    assert rows[store.project_slug("/p/peek-hide")] is None
    assert rows[store.project_slug("/p/peek-show")] == "a visible topic text"
    rc, out = _projects(capsys)
    assert "quarantined topic text" not in out.out
    assert "a visible topic text" in out.out


def test_projects_lists_a_torn_bucket_without_peeking(
        tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    torn = tmp_checkpoint_dir / "-p-torn"
    torn.mkdir(parents=True)
    (torn / "latest.json").write_text("{not json")

    def boom(*_a, **_k):
        raise AssertionError("a torn bucket has no topic to peek")

    monkeypatch.setattr(view, "visible_topic", boom)
    rc, out = _projects(capsys, "--json")
    assert rc == 0 and "-p-torn" in out.out


def test_projects_fails_closed_when_the_peek_raises(
        tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _write("/p/peek-boom", "a visible topic text")

    def boom(*_a, **_k):
        raise RuntimeError("peek")

    monkeypatch.setattr(view, "visible_topic", boom)
    rc, out = _projects(capsys)
    assert rc == 2
    assert out.out == ""
    assert out.err.strip().count("\n") == 0
    assert "RuntimeError" in out.err and "nothing was rendered" in out.err


def test_the_mcp_projects_tool_raises_when_the_peek_raises(
        tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import mcp_tools
    _write("/p/peek-mcp", "a visible topic text")

    def boom(*_a, **_k):
        raise RuntimeError("peek")

    monkeypatch.setattr(view, "visible_topic", boom)
    with pytest.raises(mcp_tools.ToolError):
        mcp_tools._projects({})
