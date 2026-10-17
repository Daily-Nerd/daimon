"""#1131: an optional sender-set title on a request.

Invariants, each enforced where it belongs:
- the title is validated ONCE, at `open_request`/`revise` (collapse, redact,
  bound), and a too-long one is REFUSED like an over-long ask, never cut;
- the title is registered plaintext in the one surface registry, so forget,
  the audit, the masking policy and the append-time redaction all cover it
  without a second list;
- a request without a title is byte-identical to before.
"""

import json

import pytest

from daimon_briefing import normalize, requests, store, surfaces

SECRET = "AKIAIOSFODNN7EXAMPLE"


@pytest.fixture
def project(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    return "/p/A"


def _open(project, ask="publish the schema", **kw):
    return requests.open_request(
        to=store.project_slug(project), ask=ask, why="the client needs it",
        channel="cli-agent", project_dir=project, **kw)


def _rows(project):
    path = requests._path(project)
    return [json.loads(ln) for ln in path.read_text().splitlines()]


# ---- the title is stored and bounded at the edge ---------------------------


def test_title_is_stored_on_the_opened_row_and_the_record(project):
    q_id = _open(project, title="Publish schema")
    assert _rows(project)[0]["title"] == "Publish schema"
    assert requests.get(q_id, project_dir=project)["title"] == "Publish schema"


def test_no_title_leaves_row_and_record_without_the_key(project):
    q_id = _open(project)
    assert "title" not in _rows(project)[0]
    assert "title" not in requests.get(q_id, project_dir=project)


def test_title_collapses_whitespace(project):
    q_id = _open(project, title="  Publish \n\t schema  ")
    assert requests.get(q_id, project_dir=project)["title"] == "Publish schema"


def test_title_is_redacted_like_an_ask(project):
    q_id = _open(project, title=f"rotate {SECRET}")
    stored = requests.get(q_id, project_dir=project)["title"]
    assert SECRET not in stored
    assert SECRET not in requests._path(project).read_text()


def test_title_at_the_bound_is_kept(project):
    title = "t" * requests.TITLE_CHARS
    assert requests.TITLE_CHARS == 80
    q_id = _open(project, title=title)
    assert requests.get(q_id, project_dir=project)["title"] == title


def test_over_long_title_is_refused_not_cut(project):
    with pytest.raises(requests.RequestTooLong) as err:
        _open(project, title="t" * (requests.TITLE_CHARS + 1))
    assert err.value.field == "title"
    assert err.value.limit == requests.TITLE_CHARS
    assert not requests._path(project).exists()


def test_supersedes_replacement_carries_its_own_title(project):
    first = _open(project, title="First")
    second = _open(project, ask="publish it again", title="Second",
                   supersedes=first)
    assert requests.get(second, project_dir=project)["title"] == "Second"
    assert requests.get(first, project_dir=project)["title"] == "First"


# ---- revise: keep unless the caller says, "" clears -------------------------


def test_revise_without_title_keeps_it(project):
    q_id = _open(project, title="Keep me")
    requests.revise(q_id, channel="cli-agent", ask="a sharper ask",
                    project_dir=project)
    assert requests.get(q_id, project_dir=project)["title"] == "Keep me"


def test_revise_with_title_replaces_it(project):
    q_id = _open(project, title="Old")
    requests.revise(q_id, channel="cli-agent", title="New",
                    project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert record["title"] == "New"
    assert record["ask"] == "publish the schema"


def test_revise_with_empty_title_clears_it(project):
    q_id = _open(project, title="Old")
    requests.revise(q_id, channel="cli-agent", title="", project_dir=project)
    assert "title" not in requests.get(q_id, project_dir=project)


def test_revise_title_alone_is_a_revision(project):
    q_id = _open(project)
    requests.revise(q_id, channel="cli-agent", title="Added",
                    project_dir=project)
    assert requests.get(q_id, project_dir=project)["title"] == "Added"


def test_revise_over_long_title_is_refused(project):
    q_id = _open(project)
    with pytest.raises(requests.RequestTooLong):
        requests.revise(q_id, channel="cli-agent",
                        title="t" * (requests.TITLE_CHARS + 1),
                        project_dir=project)


# ---- plaintext: one registry, every consumer --------------------------------


def test_title_is_scalar_prose_in_the_registry():
    assert "title" in surfaces.scalar_prose_fields("requests.jsonl")


def test_append_redacts_every_scalar_prose_field(project, monkeypatch):
    """The class: `append`'s redaction list is the registry's scalar prose
    columns (plus `author`), so a field added to the registry cannot be
    stored unredacted."""
    from daimon_briefing import policy
    seen = {}
    real = policy.admit_row

    def spy(row, redact_fields=(), redact_fn=None):
        seen["fields"] = set(redact_fields)
        return real(row, redact_fields=redact_fields, redact_fn=redact_fn)

    monkeypatch.setattr(policy, "admit_row", spy)
    _open(project)
    assert set(surfaces.scalar_prose_fields("requests.jsonl")) <= seen["fields"]
    assert "author" in seen["fields"]


def test_forget_on_a_title_only_value_removes_the_record(project):
    q_id = _open(project, title="only-in-the-title-9f3a")
    keep = _open(project, ask="a different ask", title="another title")
    key = normalize.content_key("only-in-the-title-9f3a")
    assert q_id in requests.forget_content_key(key, project_dir=project)
    text = requests._path(project).read_text()
    assert "only-in-the-title-9f3a" not in text
    assert keep in text


# ---- the CLI edge -----------------------------------------------------------


def _cli(project, *args):
    from daimon_briefing import cli
    return cli.main(["request", *args, "--by", "agent", "--project", project])


def _cli_open(project, *extra):
    return _cli(project, "open", f"--to={store.project_slug(project)}",
                "--ask", "publish the schema", "--why", "needed", "--anyway",
                *extra)


def test_cli_open_takes_a_title(project, capsys):
    assert _cli_open(project, "--title", "Publish schema") == 0
    (record,) = requests.records(project_dir=project).values()
    assert record["title"] == "Publish schema"
    assert "Publish schema" in capsys.readouterr().out


def test_cli_open_refuses_an_over_long_title(project, capsys):
    assert _cli_open(project, "--title", "t" * 81) == 1
    out = capsys.readouterr().out
    assert "title is too long (81 > 80 characters)" in out
    assert not requests._path(project).exists()


def test_cli_revise_replaces_and_clears_the_title(project, capsys):
    q_id = _open(project, title="Old")
    assert _cli(project, "revise", q_id, "--title", "New") == 0
    assert requests.get(q_id, project_dir=project)["title"] == "New"
    assert _cli(project, "revise", q_id, "--title", "") == 0
    assert "title" not in requests.get(q_id, project_dir=project)


def test_cli_revise_without_title_keeps_it(project, capsys):
    q_id = _open(project, title="Keep")
    assert _cli(project, "revise", q_id, "--ask", "sharper") == 0
    assert requests.get(q_id, project_dir=project)["title"] == "Keep"
