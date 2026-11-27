"""One seeded store for the request-furniture tests (#1402).

`seed` writes, with the real writers, everything a briefing can print besides
its body: a request waiting on PROJECT (blocking, high severity), a verdict on
a request PROJECT sent, a request PROJECT accepted and still owes, an overdue
request PROJECT sent, a resolved loop and a quarantined item (the withheld
trailer) and an old checkpoint format (the version note). The ids are random,
so `normalize` maps them to stable placeholders before any byte comparison."""
import re

from daimon_briefing import clock, requests, store, trust
from daimon_briefing.surfaces import Writer

PROJECT = "/p/furn-project"
SENDER = "/p/furn-sender"
OTHER = "/p/furn-other"
OTHER2 = "/p/furn-other2"
KEPT = "a decision that stays visible"
QUARANTINED = "SENTINEL-quarantined ship the migration on Friday"
LOOP = "the gateway retry loop"

T0 = 1_700_000_000 * 10 ** 9      # an old calendar: the overdue card is old
DAY = 86_400 * 10 ** 9


def checkpoint(*, old_format=True, decisions=(KEPT, QUARANTINED), loops=(LOOP,)):
    cp = {"session_id": "S-furn", "created": "2026-08-01T00:00:00Z",
          "working_context": {
              "recent_decisions": [{"text": t, "trust": "inferred"}
                                   for t in decisions],
              "open_questions": [{"text": t, "trust": "inferred"}
                                 for t in loops]},
          "epistemic_snapshot": {}}
    if old_format:
        cp["format_version"] = "0.0.1"
    return cp


def write_cp(project=PROJECT, **kw):
    store.write_checkpoint("S-furn", checkpoint(**kw), project_dir=project,
                           writer=Writer.HUMAN)


def _bucket(project):
    store.write_checkpoint("S-b", {
        "session_id": "S-b", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "x", "trust": "inferred"}]}, "epistemic_snapshot": {}},
        project_dir=project, writer=Writer.HUMAN)
    return store.project_slug(project)


def seed(*, checkpoint_for_project=True, trailer=True, buckets=True, **kw):
    """Seed the store and return the ids by role. `buckets=False` leaves the
    other projects without a checkpoint, so no global pointer exists and a
    project with no checkpoint of its own takes the no-checkpoint state."""
    if buckets:
        sender, other, other2 = (_bucket(SENDER), _bucket(OTHER),
                                 _bucket(OTHER2))
    else:
        sender, other, other2 = (store.project_slug(p)
                                 for p in (SENDER, OTHER, OTHER2))
    if checkpoint_for_project:
        write_cp(**kw)
        if trailer:
            cp = store.read_latest_body(project_dir=PROJECT,
                                        route=store.Route.OWN,
                                        admit=store.Admit.ANY)
            loop = cp["working_context"]["open_questions"][0]["id"]
            store.append_event(loop, "resolved", project_dir=PROJECT,
                               source="cli", writer=Writer.HUMAN)
            trust.propose(text=QUARANTINED, kind="decision", reason="made up",
                          evidence=["issue:1"], channel="cli-tty",
                          project_dir=PROJECT)
    ids = {}
    to_project = store.project_slug(PROJECT)
    # a request waiting on PROJECT
    ids["request"] = requests.open_request(
        to=to_project, ask="publish the schema", why="needed",
        channel="cli-agent", blocking=True, severity="high",
        severity_reason="the release is blocked on it", project_dir=sender)
    # a request PROJECT sent, decided by OTHER with a note
    ids["verdict"] = requests.open_request(
        to=other, ask="share the fixture set", why="needed",
        channel="cli-agent", project_dir=PROJECT)
    requests.accept(ids["verdict"], channel="cli-tty", note="on it",
                    project_dir=OTHER)
    # a request PROJECT accepted and still owes
    ids["owed"] = requests.open_request(
        to=to_project, ask="port the corroboration model", why="needed",
        channel="cli-agent", project_dir=sender)
    requests.accept(ids["owed"], channel="cli-tty", project_dir=PROJECT)
    # a request PROJECT sent long ago that nobody moved
    with clock.use(clock.StepClock(T0)):
        ids["overdue"] = requests.open_request(
            to=other2, ask="review the cutover plan", why="needed",
            channel="cli-agent", project_dir=PROJECT)
    return ids


_ID = re.compile(r"q-[0-9a-f]{12}")
_DAYS = re.compile(r"\b\d+d,")


def normalize(text: str, ids: dict | None = None) -> str:
    """The text with each request id mapped to its role (or `q-ID`) and the
    overdue age (a function of the real clock) to `Nd`."""
    by_id = {v: f"q-{k}" for k, v in (ids or {}).items()}
    text = _ID.sub(lambda m: by_id.get(m.group(0), "q-ID"), text)
    return _DAYS.sub("Nd,", text)


def block(text: str, header: str) -> list:
    """The lines of the block opened by `header`, up to the next blank line."""
    lines = text.splitlines()
    start = lines.index(header)
    out = []
    for line in lines[start:]:
        if not line.strip():
            break
        out.append(line)
    return out
