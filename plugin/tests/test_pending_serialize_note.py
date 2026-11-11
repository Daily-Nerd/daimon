"""#1328: every surface that renders a briefing says, from one liveness
source, that a fresher checkpoint for this project is being written.

The fact used to have three readers and the briefing consulted the narrowest
(a slug-stamped heartbeat, on the main CLI route only). The note now belongs to
`briefing.prepare`, the preparation every entrance shares, and is built from
`serialize_liveness.serializes` rows in `LIVE_STATES`. The census below runs
ONE fixture through every entrance, so a new entrance cannot ship without it.

Every case runs in a sandbox store (temp HOME, DAIMON_LOG_DIR, checkpoint dir).
"""

import ast
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import pytest

import daimon_briefing
from daimon_briefing import (api, briefing, cli, config, hooks, ledger, marks,
                             mcp_tools, store, testing)

NOTE = "a serialize is in flight"
KEPT = "an unrelated decision that stays visible"


class Box(NamedTuple):
    project: str
    other: str


@pytest.fixture
def box(tmp_path):
    root = tmp_path.resolve() / "sbx"
    project, other = root / "p" / "pending", root / "p" / "pending-other"
    with testing.sandbox(root, settings={"DAIMON_PROJECT_DIR": str(project)}):
        yield Box(str(project), str(other))


def _seed(project):
    testing.seed_checkpoint(
        project, testing.sample_checkpoint(
            KEPT, topic="the pending topic",
            questions=("who owns the rollout of it",)))


def _slug(project):
    return store.project_slug(project)


def _stamp(box_project, session="S-live", *, age=0.0, slug=None):
    """A heartbeat stamp for `slug` (default: the project's own), `age` old."""
    ledger.touch_heartbeat(
        session, project_slug=_slug(box_project) if slug is None else slug)
    if age:
        p = ledger._heartbeat_dir() / session
        t = time.time() - age
        os.utime(p, (t, t))


def _spawn_line(project, session, seconds_ago):
    t = time.time() - seconds_ago
    ts = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log = config.log_dir() / "serialize.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(f"{ts} session-end: spawned serialize for {session} "
                f"(reason: exit, project: {project}) "
                f"(transcript: /nowhere/{session}.jsonl)\n")


# ---- every briefing entrance, one fixture ----------------------------------


def _cli(capsys, *argv):
    rc = cli.main(["brief", *argv])
    out = capsys.readouterr().out
    assert rc == 0, out
    return out


def _main(box, capsys):
    _seed(box.project)
    return _cli(capsys)


def _header_only(box, capsys):
    # Only ANOTHER project has a checkpoint: this project gets the
    # header-only orientation and never the foreign body.
    _seed(box.other)
    out = _cli(capsys)
    assert out.splitlines()  # sanity: the route really is header-only
    assert "No briefing for this project yet" in out
    return out


def _slug_route(box, capsys):
    _seed(box.project)
    return _cli(capsys, "--slug", _slug(box.project))


def _mcp_slug(box, capsys):
    _seed(box.project)
    return mcp_tools.HANDLERS["daimon_brief"]({"slug": _slug(box.project)}).text


def _mcp_project(box, capsys):
    _seed(box.project)
    return mcp_tools.HANDLERS["daimon_brief"]({"project": box.project}).text


def _machine_brief(box, capsys):
    _seed(box.project)
    return briefing.machine_brief(box.project, time.time(),
                                  loops_pointer=False).text


def _api(box, capsys):
    _seed(box.project)
    return api.render_briefing(box.project).text


def _api_no_checkpoint(box, capsys):
    return api.render_briefing(box.project).text


def _hermes(box, capsys):
    _seed(box.project)
    out = hooks.pre_llm_call(session_id="S-new", user_message="hi",
                             conversation_history=[], is_first_turn=True,
                             model="m", platform="cli")
    return out["context"]


ENTRANCES = {
    "cli-main": _main,
    "cli-header-only": _header_only,
    "cli-slug-own": _slug_route,
    "mcp-slug": _mcp_slug,
    "mcp-project": _mcp_project,
    "machine_brief": _machine_brief,
    "api.render_briefing": _api,
    "api.render_briefing-no-checkpoint": _api_no_checkpoint,
    "hermes-pre_llm_call": _hermes,
}


@pytest.fixture(params=sorted(ENTRANCES))
def entrance(request, box, capsys):
    return lambda: ENTRANCES[request.param](box, capsys)


def _warnings(text):
    return [w for w in api.parse_briefing(text).warnings if NOTE in w]


def test_a_live_heartbeat_for_this_project_is_noted_on_every_entrance(
        box, entrance):
    _stamp(box.project)
    [note] = _warnings(entrance())
    assert note.startswith(f"{marks.WARNING_MARK} a serialize")


def test_a_spawn_with_no_stamp_inside_the_ceiling_is_noted_everywhere(
        box, entrance):
    _spawn_line(box.project, "S-starting", seconds_ago=30)
    assert len(_warnings(entrance())) == 1


def test_a_spawn_past_the_hung_ceiling_is_silent_everywhere(box, entrance):
    _spawn_line(box.project, "S-hung",
                seconds_ago=config.hung_after_seconds() + 600)
    assert _warnings(entrance()) == []


def test_a_stale_heartbeat_past_the_ceiling_is_silent_everywhere(
        box, entrance):
    _stamp(box.project, age=config.hung_after_seconds() + 600)
    assert _warnings(entrance()) == []


def test_another_projects_stamp_is_silent_everywhere(box, entrance):
    _stamp(box.other, "S-elsewhere")
    assert _warnings(entrance()) == []


def test_another_projects_spawn_is_silent_everywhere(box, entrance):
    _spawn_line(box.other, "S-elsewhere", seconds_ago=30)
    assert _warnings(entrance()) == []


def test_nothing_running_is_silent_everywhere(box, entrance):
    assert _warnings(entrance()) == []


# ---- scoping rules ---------------------------------------------------------


def test_the_note_is_about_the_bucket_the_briefing_renders(box, capsys):
    # The own project has a live serialize; the reader named ANOTHER bucket.
    # The checkpoint on screen is the other bucket's, so the note is silent.
    _seed(box.other)
    _stamp(box.project)
    out = _cli(capsys, "--slug", _slug(box.other))
    assert _warnings(out) == []
    got = briefing.machine_brief(_slug(box.other), time.time(),
                                 loops_pointer=False)
    assert _warnings(got.text) == []
    # And when that bucket's own serialize is live, a named read says so.
    _stamp(box.other, "S-other")
    assert len(_warnings(_cli(capsys, "--slug", _slug(box.other)))) == 1


def test_a_project_arg_to_the_cli_is_the_own_bucket(box, capsys, monkeypatch):
    # `--project X` names the reader's project: its stamp is the own one even
    # when the working directory belongs elsewhere.
    _seed(box.other)
    _stamp(box.other, "S-other")
    out = _cli(capsys, "--project", box.other)
    assert len(_warnings(out)) == 1


def test_the_note_is_read_only(box, capsys):
    _stamp(box.project)
    _spawn_line(box.project, "S-starting", seconds_ago=30)
    def liveness_files():
        # What a liveness read could disturb: the stamps and the serialize log
        # (other logs, such as usage.log, are the CLI's own bookkeeping).
        return sorted((p.name, p.stat().st_mtime_ns, p.read_bytes())
                      for p in config.log_dir().rglob("*")
                      if p.is_file() and (p.parent.name == "heartbeats"
                                          or p.name == "serialize.log"))

    before = liveness_files()
    assert len(before) == 2
    _main(box, capsys)
    assert liveness_files() == before


def test_an_unreadable_heartbeat_dir_never_takes_the_briefing_down(
        box, monkeypatch):
    _seed(box.project)

    def boom(now=None):
        raise OSError("no listing")

    monkeypatch.setattr(ledger, "heartbeat_index", boom)
    got = api.render_briefing(box.project)
    assert got.state == "briefing" and KEPT in got.text


def test_a_liveness_read_that_raises_never_takes_the_briefing_down(
        box, monkeypatch):
    from daimon_briefing import serialize_liveness
    _seed(box.project)

    def boom(*a, **k):
        raise RuntimeError("a bug in the fold")

    monkeypatch.setattr(serialize_liveness, "serializes", boom)
    got = api.render_briefing(box.project)
    assert got.state == "briefing" and KEPT in got.text
    assert not any(NOTE in n for n in got.notes)


# ---- one owner: the shared preparation -------------------------------------


def test_the_note_is_a_property_of_prepare(box):
    _seed(box.project)
    _stamp(box.project)
    annotated = briefing.prepare(box.project, time.time())
    assert any(NOTE in n for n in annotated.notes)
    # Surfaced on the result a host reads back, not only in text.
    got = api.render_briefing(box.project)
    assert any(NOTE in n for n in got.notes)


def test_every_prepare_caller_is_a_known_entrance():
    """A new module that prepares a briefing gets the note from `prepare`
    automatically; this census makes adding one a deliberate act that also
    adds it to ENTRANCES above."""
    root = Path(daimon_briefing.__file__).parent
    callers = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and ((isinstance(node.func, ast.Attribute)
                          and node.func.attr == "prepare"
                          and isinstance(node.func.value, ast.Name)
                          and node.func.value.id == "briefing")
                         or (isinstance(node.func, ast.Name)
                             and node.func.id == "prepare"))):
                callers.add(str(path.relative_to(root)))
    assert callers == {"briefing.py", "hooks.py", "cli/brief.py",
                       "cli/lifecycle.py"}


def test_the_ad_hoc_in_flight_oracle_is_retired():
    assert not hasattr(ledger, "serialize_in_flight")
