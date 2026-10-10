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


# Evidence is a typed source (`artifact:<path>`), so a whole value in an
# evidence column is `artifact:<sentinel text>`. Each is quarantined by its own
# record, which is what makes the column's value withheld.
EVIDENCE = {kind: f"artifact:{TEXTS[kind]}" for kind in QUARANTINED}


# A seventh sentinel that is not a schema kind: a decision whose ID is
# tombstoned while no tombstone key matches its VALUE (a sibling id, or a
# value redacted differently at capture). Only the id rule withholds it.
ID_TOKEN = token("idforgot")
ID_TEXT = f"{ID_TOKEN} the id-forgotten sentinel text"


# Words only the peer project quarantines (see `_write_peer_requests`).
PEER_ASK = f"{TEXTS['question']} as the peer worded its ask"
PEER_NOTE = f"{TEXTS['topic']} as the peer worded its note"


# A value forgotten by ANOTHER project (a tombstone in the peer's events
# ledger) after this project's ledgers already hold it. Writers re-scrub a
# forgotten value as they append (`jsonl.reaching`), so a ledger prose column
# keeps a whole forgotten value only when the forget happened elsewhere AFTER
# the write. The tombstone key is in the peer's bucket, not this one, so it is
# not one of KEY_TOKENS.
PEER_TOKEN = token("peerforgot")
PEER_TEXT = f"{PEER_TOKEN} the peer-forgotten sentinel text"


# A value a TEAMMATE quarantined (PR 13, D6): every checkpoint the world
# writes holds it as a decision, grace's mirrored copy included, and grace
# quarantines it through the real writer on a machine of its own. This reader
# never sees grace's trust ledger: only the hash-only row grace published.
FOREIGNQ_TOKEN = token("foreignq")
FOREIGNQ_TEXT = f"{FOREIGNQ_TOKEN} the teammate-quarantined sentinel text"


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
    found = {kind for kind, tok in TOKENS.items() if tok.lower() in low}
    if PEER_TOKEN.lower() in low:
        found.add("peerforgot")
    if FOREIGNQ_TOKEN.lower() in low:
        found.add("foreignq")
    return found


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
                {"text": FOREIGNQ_TEXT, "trust": "inferred"},
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
    peer: str = ""                                # a second project's directory
    foreignq_id: str = ""                         # grace's quarantine id
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
    # The census lives under the tmp home it sets below. An exported store,
    # team, log, recall or env-file location would point it at the machine's
    # own files, so none of them may be inherited.
    for name in ("DAIMON_CHECKPOINT_DIR", "DAIMON_TEAM_DIR", "DAIMON_LOG_DIR",
                 "DAIMON_RECALL_DB", "DAIMON_ENV_FILE"):
        monkeypatch.delenv(name, raising=False)
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
        if k == "decision" and text == FOREIGNQ_TEXT:
            world.ids["foreignq"] = item_id
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
    for kind in QUARANTINED:       # the evidence-shaped copies, quarantined
        trust.propose(
            text=EVIDENCE[kind], kind=kind, reason=TEXTS["contradiction"],
            evidence=["issue:1"], channel="cli-tty", project_dir=project)
    for kind in QUARANTINED:
        world.quarantine_ids[kind] = trust.propose(
            text=TEXTS[kind], kind=kind, reason=TEXTS["contradiction"],
            evidence=["issue:1", EVIDENCE["topic"]], channel="cli-tty",
            project_dir=project)

    _write_ledger_prose(world, project)
    _write_folded_prose(world, project)
    _write_peer_requests(world, tmp_path, project)
    _write_forgotten_prose(world, project)
    _plant_quarantined_line(world)
    _teammate_quarantines(world, monkeypatch)
    return world


def _teammate_quarantines(world: World, monkeypatch) -> None:
    """grace quarantines FOREIGNQ on a machine of its own, through the real
    writer.

    The proposal comes from the peer project, so ada's own `trust.jsonl` never
    holds the record; the publisher routes the hash-only row into grace's author
    directory of the same sidecar (the env grant routes the peer project there
    too). The peer bucket is latched locally as a side effect, which is why
    assertions cover ada's surfaces and the visible controls, never the peer
    bucket. Then ada is back."""
    monkeypatch.setenv("DAIMON_AUTHOR", "grace")
    try:
        world.foreignq_id = trust.propose(
            text=FOREIGNQ_TEXT, kind="decision", reason="grace's reason",
            evidence=["issue:1"], channel="cli-tty", project_dir=world.peer)
    finally:
        monkeypatch.setenv("DAIMON_AUTHOR", "ada")


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


def _write_folded_prose(world: World, project: str) -> None:
    """The prose a FOLD derives outside the row paths (11c): a ruling's
    pending revision and retirement, an overturn's note, a refutation's
    anchors and evidence, a request's replies, completion and per-act
    authors. Every one is a whole quarantined value, planted by the real
    writer. `revision_proposed.note` has no writer (`revise` takes no note);
    `from_label` is the sender's directory name, which would put the
    sentinel into the bucket name itself (see tests/test_request_masking.py)."""
    t, q, c = TEXTS["topic"], TEXTS["question"], TEXTS["contradiction"]
    ev = EVIDENCE
    # the standing ruling takes an agent's pending revision: subject, verdict,
    # evidence and a check, every one a quarantined whole value
    refutations.revise(
        world.ruling_id, channel="cli-agent", evidence=[ev["question"]],
        subject=t, verdict=q,
        check={"match": t, "body": q, "intent": "warn"}, project_dir=project)
    # a second standing ruling an agent proposes to retire
    pending_retire = refutations.assert_ruling(
        subject="census ruling kept", verdict="a standing rule that stays",
        scope="census-kept", evidence=["issue:693"], channel="cli-tty",
        ratified=False, project_dir=project)
    refutations.ratify(pending_retire, channel="cli-tty", project_dir=project)
    refutations.retire(pending_retire, channel="cli-agent",
                       evidence=[ev["topic"]], note=c, project_dir=project)
    # a third a person retired: overturn note and evidence
    retired = refutations.assert_ruling(
        subject="census ruling retired", verdict="a rule that was retired",
        scope="census-retired", evidence=["issue:693"], channel="cli-tty",
        ratified=False, project_dir=project)
    refutations.ratify(retired, channel="cli-tty", project_dir=project)
    refutations.retire(retired, channel="cli-tty", evidence=[ev["question"]],
                       note=q, project_dir=project)
    # a candidate ruling carrying a check whose match and body are quarantined
    refutations.assert_ruling(
        subject="census ruling with a check", verdict="a rule with a check",
        scope="census-check", evidence=["issue:693"], channel="cli-agent",
        check={"match": q, "body": t, "intent": "warn"}, project_dir=project)
    # a refutation with anchors and evidence, active so the guard can match it
    anchored = refutations.assert_refutation(
        subject="census anchored refutation", verdict="it was measured",
        scope="census-anchored", evidence=[ev["question"]], anchors=[t],
        channel="cli-agent", project_dir=project)
    refutations.ratify(anchored, channel="cli-tty", project_dir=project)
    # a refutation a person overturned
    overturned = refutations.assert_refutation(
        subject="census overturned refutation", verdict="it was wrong",
        scope="census-overturned", evidence=["measurement:replay"],
        channel="cli-agent", project_dir=project)
    refutations.ratify(overturned, channel="cli-tty", project_dir=project)
    refutations.overturn(overturned, channel="cli-tty",
                         evidence=[ev["contradiction"]], note=c,
                         project_dir=project)
    # a request carrying every per-act author, a reply and a completion
    slug = store.project_slug(project)
    rid = requests.open_request(
        to=slug, ask="a second census ask", why="because", evidence="e",
        channel="ui", author=t, project_dir=project)
    requests.accept(rid, channel="ui", note="ok", author=q,
                    project_dir=project)
    requests.reply(rid, t, q, channel="ui", author=c, project_dir=project)
    requests.done(rid, channel="ui", evidence=c, author=t,
                  project_dir=project)


def _write_forgotten_prose(world: World, project: str) -> None:
    """A whole forgotten value in a ledger prose column. The deleters drop
    whole records and writers re-scrub as they append, so the only way one
    exists is a forget made in ANOTHER project after this project wrote the
    value: the rows go in first, then the peer's tombstone."""
    f = PEER_TEXT
    refutations.assert_refutation(
        subject=f, verdict=f, scope=f, evidence=["measurement:replay"],
        anchors=[f], channel="cli-agent", project_dir=project)
    rid = requests.open_request(
        to=store.project_slug(project), ask=f, why=f, evidence=f,
        channel="cli-agent", project_dir=project)
    requests.accept(rid, channel="cli-tty", note=f, project_dir=project)
    store.append_event(
        "o-peer-forgot", "forgotten:" + normalize.content_key(f),
        kind="tombstone", tombstone=True, project_dir=world.peer,
        writer=Writer.HUMAN)


def _write_peer_requests(world: World, tmp_path, project: str) -> None:
    """A second project: it sends this one an ask whose text is a quarantined
    value, and answers an ask of ours with a quarantined note. Both rows live
    in the PEER's bucket, so a reader must judge them across the join."""
    peer = tmp_path / "peer"
    peer.mkdir()
    world.peer = str(peer)
    own, theirs = store.project_slug(project), store.project_slug(world.peer)
    requests.open_request(
        to=own, ask=TEXTS["question"], why=TEXTS["topic"],
        evidence=TEXTS["contradiction"], channel="cli-agent",
        project_dir=world.peer)
    ours = requests.open_request(
        to=theirs, ask="please look at the census", why="it blocks us",
        channel="cli-tty", project_dir=project)
    requests.accept(ours, channel="cli-tty", note=TEXTS["contradiction"],
                    project_dir=world.peer)
    # Two texts only the PEER quarantined (its bucket holds the record, this
    # one holds none): the ask it sends us, and the note it answers ours with.
    # This project's own snapshot cannot mask either, so a reader that judges
    # a joined row by the own snapshot alone leaks them.
    requests.open_request(
        to=own, ask=PEER_ASK, why="the peer's own words", channel="cli-agent",
        project_dir=world.peer)
    ours_two = requests.open_request(
        to=theirs, ask="please look at the second census ask", why="again",
        channel="cli-tty", project_dir=project)
    requests.accept(ours_two, channel="cli-tty", note=PEER_NOTE,
                    project_dir=world.peer)
    for text, kind in ((PEER_ASK, "question"), (PEER_NOTE, "topic")):
        trust.propose(text=text, kind=kind, reason=TEXTS["contradiction"],
                      evidence=["issue:1"], channel="cli-tty",
                      project_dir=world.peer)


def _plant_quarantined_line(world: World) -> None:
    """A torn trust line carrying a sentinel, repaired by the real verb into
    the `*.quarantined-lines` sidecar."""
    path = world.bucket / "forget-hits.jsonl"
    store.record_forget_hits([{"text": "x"}], project_dir=world.project)
    with open(path, "ab") as handle:     # independent byte writer: the torn tail
        handle.write(('{"note": "' + TEXTS["question"]).encode())
    assert cli.main(["ledger", "repair", "forget-hits",
                     "--project", world.project]) == 0
