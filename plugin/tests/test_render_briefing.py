"""`api.render_briefing` (#1180 PR 6, design D17 to D20).

The machine briefing, rendered in process: the same bytes the MCP brief tool
returns, a state, the notes, and never an exception. Every case runs in a
`testing.sandbox`, seeded through the public seeders, so what is read is the
store the test wrote and nothing of the developer's own."""

import hashlib

import pytest

from daimon_briefing import api, briefing, config, marks, mcp_tools, render, store, testing

KEPT = "a decision the briefing must surface"
RULING = "never ship a Friday deploy"


@pytest.fixture
def project(tmp_path):
    root = tmp_path.resolve() / "sbx"
    proj = root / "proj"
    with testing.sandbox(root, settings={"DAIMON_PROJECT_DIR": str(proj)}):
        proj.mkdir()
        yield proj


@pytest.fixture
def tight_project(tmp_path):
    """A budget small enough that open loops get cut, which is when the note
    carries the `daimon loops` pointer the loops_pointer flag controls."""
    root = tmp_path.resolve() / "sbx"
    proj = root / "proj"
    settings = {"DAIMON_PROJECT_DIR": str(proj), "DAIMON_BRIEF_MAX_BYTES": "900"}
    with testing.sandbox(root, settings=settings):
        proj.mkdir()
        yield proj


def _plant(project, name, data):
    bucket = config.checkpoint_dir() / store.project_slug(str(project))
    bucket.mkdir(parents=True, exist_ok=True)
    (bucket / name).write_bytes(data)


def _seed(project, *decisions):
    testing.seed_checkpoint(project, testing.sample_checkpoint(
        *decisions, questions=("who owns the rollout of it",)))


# ---- one case per state ----


def test_a_seeded_project_renders_a_briefing(project):
    _seed(project, KEPT)
    testing.seed_ruling(project, subject="deploys", verdict=RULING)
    got = api.render_briefing(project)
    assert got.state == "briefing" and got.notes == ()
    assert marks.GREETING in got.text and KEPT in got.text
    assert f"{marks.RULING_MARK} {RULING}" in got.text


def test_a_closed_view_renders_the_greeting_and_the_closed_line(project):
    _seed(project, KEPT)
    _plant(project, "trust.jsonl", b"<<<<<<< HEAD\n")
    got = api.render_briefing(project)
    assert got.state == "closed"
    assert got.text.endswith(briefing.CLOSED_LINE) and KEPT not in got.text
    assert any("trust.jsonl is unreadable" in n for n in got.notes)


def test_a_project_without_a_checkpoint_renders_the_orientation(project):
    got = api.render_briefing(project)
    assert got.state == "no-checkpoint"
    assert "no checkpoint for this project" in got.text


def test_a_checkpoint_with_nothing_to_surface_is_empty(project):
    testing.seed_checkpoint(project, testing.sample_checkpoint(topic=""))
    got = api.render_briefing(project)
    assert got.state == "empty"
    assert "nothing worth surfacing" in got.text


def test_a_prepare_that_raises_is_unavailable_with_the_exception_type(
        project, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(briefing, "prepare", boom)
    got = api.render_briefing(project)
    assert got.state == "unavailable" and got.text == ""
    assert got.notes == ("the briefing could not be prepared (RuntimeError)",)
    assert "secret detail" not in repr(got)


@pytest.mark.parametrize("name", ["ruling_lines", "build", "render_plain"])
def test_any_later_failure_is_unavailable_too(project, monkeypatch, name):
    _seed(project, KEPT)

    def boom(*a, **k):
        raise KeyError("x")

    monkeypatch.setattr(briefing, name, boom)
    got = api.render_briefing(project)
    assert got.state == "unavailable"
    assert got.notes == ("the briefing could not be prepared (KeyError)",)


def test_a_project_that_cannot_be_resolved_is_unavailable(project, monkeypatch):
    def boom(arg=None):
        raise OSError("no cwd")

    monkeypatch.setattr(config, "resolve_read_project", boom)
    got = api.render_briefing(None)
    assert got.state == "unavailable"
    assert got.notes == ("the briefing could not be prepared (OSError)",)


def test_briefing_text_is_the_text_of_the_render(project):
    _seed(project, KEPT)
    assert api.briefing_text(project) == api.render_briefing(project).text


# ---- the same bytes as the MCP tool ----


def test_the_text_is_byte_equal_to_the_mcp_tool_for_a_project(tight_project):
    questions = tuple(f"open question number {n} about the rollout plan"
                      for n in range(30))
    testing.seed_checkpoint(tight_project, testing.sample_checkpoint(
        KEPT, questions=questions))
    testing.seed_ruling(tight_project, subject="deploys", verdict=RULING)
    mcp = mcp_tools.HANDLERS["daimon_brief"](
        {"project": str(tight_project)}).text
    # DAIMON_PROJECT_DIR is this project, so the MCP adds the loops pointer.
    got = api.render_briefing(tight_project, loops_pointer=True)
    assert got.text == mcp
    assert "See: daimon loops" in mcp
    assert api.render_briefing(tight_project).text != mcp


def test_the_text_is_byte_equal_to_the_mcp_tool_for_a_slug(project):
    _seed(project, KEPT)
    slug = store.project_slug(str(project))
    mcp = mcp_tools.HANDLERS["daimon_brief"]({"slug": slug}).text
    assert api.render_briefing(project, loops_pointer=False).text == mcp


@pytest.mark.parametrize("state", ["closed", "no-checkpoint", "empty"])
def test_every_state_is_byte_equal_to_the_mcp_tool(project, state):
    if state == "closed":
        _seed(project, KEPT)
        _plant(project, "trust.jsonl", b"<<<<<<< HEAD\n")
    elif state == "empty":
        testing.seed_checkpoint(project, testing.sample_checkpoint(topic=""))
    mcp = mcp_tools.HANDLERS["daimon_brief"]({"project": str(project)}).text
    got = api.render_briefing(project, loops_pointer=True)
    assert got.state == state and got.text == mcp


# ---- the round trip (D19) ----


def test_a_briefing_round_trips_through_the_parser(project):
    _seed(project, KEPT)
    testing.seed_ruling(project, subject="deploys", verdict=RULING)
    _plant(project, "events.jsonl", b"\xff\xfe not utf-8\n")
    got = api.render_briefing(project)
    parsed = api.parse_briefing(got.text)
    assert got.state == "briefing" and got.notes
    assert parsed.header == marks.GREETING
    assert parsed.standing_rulings and all(
        r.startswith(marks.RULING_MARK) for r in parsed.standing_rulings)
    assert any(RULING in r for r in parsed.standing_rulings)
    assert parsed.items and all(i.startswith(marks.ITEM_MARKS)
                                for i in parsed.items)
    assert any(KEPT in i for i in parsed.items)
    for note in got.notes:
        assert note in parsed.warnings


def test_a_closed_briefing_round_trips_with_no_items(project):
    _seed(project, KEPT)
    _plant(project, "trust.jsonl", b"<<<<<<< HEAD\n")
    got = api.render_briefing(project)
    parsed = api.parse_briefing(got.text)
    assert parsed.header == marks.GREETING and parsed.items == ()
    assert got.notes and all(n in parsed.warnings for n in got.notes)


@pytest.mark.parametrize("state", ["no-checkpoint", "empty"])
def test_the_orientation_states_round_trip_with_notes_as_warnings(
        project, state):
    if state == "empty":
        testing.seed_checkpoint(project, testing.sample_checkpoint(topic=""))
    testing.seed_ruling(project, subject="deploys", verdict=RULING)
    _plant(project, "events.jsonl", b"\xff\xfe not utf-8\n")
    got = api.render_briefing(project)
    parsed = api.parse_briefing(got.text)
    assert got.state == state and got.notes
    assert parsed.header and parsed.header not in parsed.warnings
    assert not parsed.header.startswith(marks.RULING_MARK)
    assert all(n in parsed.warnings for n in got.notes)
    assert any(RULING in r for r in parsed.standing_rulings)


# ---- determinism and the machine path ----


def test_two_runs_with_the_same_now_give_the_same_bytes(project):
    _seed(project, KEPT)
    testing.seed_ruling(project, subject="deploys", verdict=RULING)
    first = api.render_briefing(project, now=1_800_000_000.0)
    second = api.render_briefing(project, now=1_800_000_000.0)
    assert first == second and first.state == "briefing"


def test_the_bytes_do_not_depend_on_the_plain_flag_or_a_terminal(
        project, monkeypatch):
    _seed(project, KEPT)
    plain = api.render_briefing(project, now=1_800_000_000.0)
    monkeypatch.delenv("DAIMON_PLAIN", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    assert api.render_briefing(project, now=1_800_000_000.0) == plain


# ---- the format version (D20) ----

_FINGERPRINT = "b4d6e9fc4a71f46246907f10bcf9644bc04544ec60c56f781ccff6ffb493324c"


def _marks_fingerprint():
    consts = sorted((n, getattr(marks, n)) for n in vars(marks)
                    if n.isupper() and n != "FORMAT_VERSION")
    return hashlib.sha256(repr(consts).encode("utf-8")).hexdigest()


def test_the_format_version_is_the_exported_integer():
    assert marks.FORMAT_VERSION == 1 == api.BRIEFING_FORMAT
    assert isinstance(api.BRIEFING_FORMAT, int)


def test_the_marks_changed_only_with_a_format_bump():
    assert _marks_fingerprint() == _FINGERPRINT, (
        "the marks constants changed: bump marks.FORMAT_VERSION (and "
        "api.BRIEFING_FORMAT follows), then update _FINGERPRINT here")
    # The version is never printed into a briefing.


def test_the_version_is_not_printed_in_a_briefing(project):
    _seed(project, KEPT)
    assert "FORMAT" not in api.render_briefing(project).text
