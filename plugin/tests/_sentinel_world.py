"""The fixture store of the read census (#1132 PR 6b).

One sentinel per `schema.ITEM_FIELDS` entry, written ONLY by the real writers
(scar 0096: a census that plants its own bytes proves the readers agree with
the plant). Three sentinels are quarantined by a human and three are forgotten
by the real `daimon forget`; copies live in the latest pointer, prev-1, flat
session files, a teammate's checkpoint in a synced sidecar, every ledger prose
field that can hold a whole value, and a built recall index.

The sentinel texts are distinctive: `SENTINEL-<kind>-<8 hex> ...`. A reader
leaks when any token appears in what it hands back.
"""

import hashlib
import io
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from daimon_briefing import (amendments, cli, config, normalize, refutations,
                             relations, requests, schema, store, trust)
from daimon_briefing.surfaces import Writer

KINDS = tuple(f.kind for f in schema.ITEM_FIELDS)
QUARANTINED = ("topic", "question", "contradiction")
FORGOTTEN = ("decision", "belief", "uncertainty")


def token(kind: str) -> str:
    return f"SENTINEL-{kind}-{hashlib.sha1(kind.encode()).hexdigest()[:8]}"


TOKENS = {kind: token(kind) for kind in KINDS}
TEXTS = {kind: f"{TOKENS[kind]} the {kind} sentinel text" for kind in KINDS}


# A seventh sentinel that is not a schema kind: a decision whose ID is
# tombstoned while no tombstone key matches its VALUE (a sibling id, or a
# value redacted differently at capture). Only the id rule withholds it.
ID_TOKEN = token("idforgot")
ID_TEXT = f"{ID_TOKEN} the id-forgotten sentinel text"


def leaked_id(blob) -> bool:
    if isinstance(blob, bytes):
        blob = blob.decode("utf-8", errors="replace")
    return ID_TOKEN.lower() in blob.lower()


# The content key of each forgotten value, and of the value a tombstoned ID
# was written under. A tombstone row's status and a scrub marker carry the key
# (scar 0119), which names the value it hashes; the value sentinel above cannot
# see it, so a reader that prints a raw status brings it back unnoticed.
KEY_TOKENS = {
    **{kind: normalize.content_key(TEXTS[kind]) for kind in FORGOTTEN},
    "idforgot": normalize.content_key("a different value"),
}


def leaked_keys(blob) -> set[str]:
    """The forgotten-value names whose content key appears in `blob` (str or
    bytes), as `key:<name>`."""
    if isinstance(blob, bytes):
        blob = blob.decode("utf-8", errors="replace")
    low = blob.lower()
    return {f"key:{name}" for name, key in KEY_TOKENS.items()
            if key.lower() in low}


def leaked_kinds(blob) -> set[str]:
    """The sentinel kinds whose token appears in `blob` (str or bytes)."""
    if isinstance(blob, bytes):
        blob = blob.decode("utf-8", errors="replace")
    low = blob.lower()
    return {kind for kind, tok in TOKENS.items() if tok.lower() in low}


class TtyStdin(io.StringIO):
    """stdin whose `isatty` answer is the channel under test."""

    def __init__(self, text="", tty=False):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


def checkpoint(sid, created):
    t = TEXTS
    return {
        "session_id": sid, "created": created,
        "working_context": {
            "active_topic": {"text": t["topic"], "trust": "inferred"},
            "open_questions": [
                {"text": t["question"], "trust": "verbatim",
                 "quote": t["question"]},
                {"text": "an unrelated open question stays visible",
                 "trust": "inferred"}],
            "recent_decisions": [
                {"text": t["decision"], "trust": "inferred"},
                {"text": ID_TEXT, "trust": "inferred"},
                {"text": "an unrelated decision stays visible",
                 "trust": "inferred"}]},
        "epistemic_snapshot": {
            "strong_beliefs": [{"text": t["belief"], "trust": "inferred"}],
            "uncertainties": [{"text": t["uncertainty"], "trust": "inferred"}],
            "contradictions_flagged": [t["contradiction"]]},
    }


@dataclass
class World:
    project: str
    bucket: Path
    ids: dict = field(default_factory=dict)       # kind -> item id (list kinds)
    quarantine_ids: dict = field(default_factory=dict)
    ruling_id: str = ""
    refutation_id: str = ""
    relation_id: str = ""
    amendment_id: str = ""
    request_id: str = ""
    root: Path = None                             # the tmp root being watched


def _plant_transcripts(world: World, tmp_path, monkeypatch) -> None:
    """A resolvable transcript for each session that does NOT hold the
    question's quote: the question is a verbatim item whose quote fails, so
    `audit quotes` has a failure to print if it reads the item at all."""
    import json
    projects = tmp_path / ".claude" / "projects"
    monkeypatch.setenv("DAIMON_CLAUDE_PROJECTS_DIR", str(projects))
    slug_dir = projects / world.bucket.name
    slug_dir.mkdir(parents=True)
    for sid in ("S-1", "S-2"):
        (slug_dir / f"{sid}.jsonl").write_text(
            json.dumps({"role": "user", "content": "an unrelated chat"}) + "\n",
            encoding="utf-8")


def _item_ids(project):
    cp = store.read_latest_body(project_dir=project, route=store.Route.OWN,
                                admit=store.Admit.ANY)
    out = {}
    for fld, item in schema.iter_items(cp or {}):
        if item.get("id"):
            out[(fld.kind, item["text"])] = item["id"]
    return out


def build_world(tmp_path, monkeypatch) -> World:
    """Build the store. Returns the World; leaves the process on the human
    channel (stdin isatty True) as author `ada` with team mirroring on."""
    if shutil.which("git") is None:
        pytest.skip("git not on PATH: the sidecar needs it")
    proj = tmp_path / "proj"
    proj.mkdir()
    # `daimon anchor mod.py fn` resolves a real symbol (the anchor verb cases)
    (proj / "mod.py").write_text("def fn():\n    return 1\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(proj))
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setattr(cli.sys, "stdin", TtyStdin("", tty=True))
    project = str(proj)

    # a synced sidecar, so a teammate's checkpoint is a FOREIGN one (gated)
    bare = tmp_path / "origin" / "team-mem.git"
    bare.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)],
                   check=True, capture_output=True, timeout=30)
    assert cli.main(["team", "init", str(bare)]) == 0
    monkeypatch.setenv("DAIMON_TEAM", "1")
    # explicit machine intent: this project syncs into the sidecar's logical
    # path, so the teammate's copy is a FOREIGN one the inbound gate judges
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "squad/census")

    monkeypatch.setenv("DAIMON_AUTHOR", "grace")
    cp = checkpoint("G-1", "2026-08-03T00:00:00Z")
    store.write_checkpoint("G-1", cp, project_dir=project, writer=Writer.HUMAN)
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")

    for sid, created in (("S-1", "2026-08-01T00:00:00Z"),
                         ("S-2", "2026-08-02T00:00:00Z")):
        store.write_checkpoint(sid, checkpoint(sid, created),
                               project_dir=project, writer=Writer.HUMAN)

    world = World(project=project,
                  bucket=config.checkpoint_dir() / store.project_slug(project),
                  root=tmp_path)
    _plant_transcripts(world, tmp_path, monkeypatch)
    by_text = _item_ids(project)
    for kind in KINDS:
        for (k, text), item_id in by_text.items():
            if k == kind and text == TEXTS[kind]:
                world.ids[kind] = item_id

    for (k, text), item_id in by_text.items():
        if text == "an unrelated open question stays visible":
            world.ids["other_question"] = item_id
        if text == "an unrelated decision stays visible":
            world.ids["other_decision"] = item_id

    # forget three values through the real verb: their own copies are scrubbed,
    # the teammate's copy and the tombstone remain
    for kind in FORGOTTEN:
        assert cli.main(["forget", world.ids[kind], "--reason", "stale",
                         "--project", project]) == 0

    # tombstone one more ID, whose value no tombstone key names
    for (k, text), item_id in by_text.items():
        if k == "decision" and text == ID_TEXT:
            store.append_event(
                item_id,
                "forgotten:" + normalize.content_key("a different value"),
                kind="tombstone", tombstone=True, project_dir=project, writer=Writer.HUMAN)
            world.ids["idforgot"] = item_id

    # quarantine three, by a human
    for kind in QUARANTINED:
        world.quarantine_ids[kind] = trust.propose(
            text=TEXTS[kind], kind=kind, reason=TEXTS["contradiction"],
            evidence=["issue:1"], channel="cli-tty", project_dir=project)

    _write_ledger_prose(world, project)
    _plant_quarantined_line(world)
    return world


def _write_ledger_prose(world: World, project: str) -> None:
    """A whole quarantined value in every prose field that can hold one."""
    q, t, c = TEXTS["question"], TEXTS["topic"], TEXTS["contradiction"]
    # events: note, item_text, status (free-form)
    assert store.append_event(world.ids["question"], "reopen", note=c,
                              item_text=q, project_dir=project, writer=Writer.HUMAN)
    assert store.append_event("i-prose-status", t, project_dir=project, writer=Writer.HUMAN)
    # refutations / rulings: subject, verdict, scope, revisit_when, note
    world.ruling_id = refutations.assert_ruling(
        subject=t, verdict=q, scope=c, evidence=["issue:693"],
        revisit_when=t, channel="cli-tty", ratified=False,
        project_dir=project)
    refutations.ratify(world.ruling_id, channel="cli-tty", note=q,
                       project_dir=project)
    world.refutation_id = refutations.assert_refutation(
        subject=t, verdict=c, scope=q, evidence=["measurement:replay"],
        channel="cli-tty", project_dir=project)
    world.relation_id = relations.propose(
        type_="revision-of",
        from_endpoint={"session_id": "S-2", "field": "open_questions",
                       "item_id": world.ids["question"]},
        to_endpoint={"session_id": "S-2", "field": "open_questions",
                     "item_id": world.ids["other_question"]},
        matched_by=["carry-absolute"], matcher_version="census-1",
        channel="lab-import", project_dir=project)
    # amendments: evidence, note
    world.amendment_id = amendments.propose(
        item_id=world.ids["question"], change="progressed", evidence=q,
        channel="cli-tty", note=c, project_dir=project)
    # requests: ask, why, note, evidence (self-addressed)
    world.request_id = requests.open_request(
        to=store.project_slug(project), ask=t, why=q, evidence=c,
        channel="cli-agent",
        project_dir=project)
    requests.accept(world.request_id, channel="cli-tty", note=c,
                    project_dir=project)
    # trust: reason and evidence of a further quarantine proposal
    trust.propose(text="an unrelated value that stays visible",
                  kind="decision", reason=q, evidence=["issue:2"],
                  channel="cli-agent", project_dir=project)


def _plant_quarantined_line(world: World) -> None:
    """A torn trust line carrying a sentinel, repaired by the real verb into
    the `*.quarantined-lines` sidecar."""
    path = world.bucket / "forget-hits.jsonl"
    store.record_forget_hits([{"text": "x"}], project_dir=world.project)
    with open(path, "ab") as handle:     # independent byte writer: the torn tail
        handle.write(('{"note": "' + TEXTS["question"]).encode())
    assert cli.main(["ledger", "repair", "forget-hits",
                     "--project", world.project]) == 0
