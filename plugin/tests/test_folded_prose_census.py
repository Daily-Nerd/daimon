"""Completeness proof for `Surface.folded_prose` (#1132 PR 11c).

A printer shows the FOLDED record, whose prose copies sit outside the row
paths. This plants a sentinel through every field a real writer can reach,
folds, walks every string leaf of the folded dict and asserts that each
sentinel leaf's path is declared in `prose` or `folded_prose`. A new fold key
that carries a person's text cannot ship undeclared.
"""

from daimon_briefing import refutations, requests, store, surfaces

PROJECT = "/p/foldedprose"
MARK = "zqcensus"


def S(name):
    return f"ZQCENSUS {name}"


def _leaves(node, path=()):
    """(canonical path, string) for every string leaf. A list under key `k`
    contributes the segment `k[]` whether its members are strings or dicts."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, list):
                for member in value:
                    yield from _leaves(member, (*path, key + "[]"))
            else:
                yield from _leaves(value, (*path, key))
    elif isinstance(node, str):
        yield ".".join(path), node


def _declared(name):
    s = surfaces.bucket_ledger(name)
    return {surfaces.path_string(fp) for fp in s.prose + s.folded_prose}


def _sentinel_paths(records):
    found = set()
    for record in records:
        for path, value in _leaves(record):
            if MARK in value.lower():
                found.add(path)
    return found


def _ruling(subject, **extra):
    return refutations.assert_ruling(
        subject=S(subject), verdict=S(subject + ".verdict"),
        scope=S(subject + ".scope"), evidence=["issue:" + MARK + "1"],
        anchors=["issue:" + MARK.upper() + "2"],
        revisit_when=S(subject + ".revisit"), channel="cli-agent",
        project_dir=PROJECT, **extra)


def _check(tag):
    return {"match": MARK + tag, "body": f"echo {MARK}{tag}\nexit 0\n",
            "intent": "warn"}


def test_every_sentinel_in_a_folded_refutation_is_a_declared_path(
        tmp_checkpoint_dir):
    # a ruling carrying a check, activated by a person with a note
    a = _ruling("a", check=_check("a"))
    pinned = refutations.get(a, project_dir=PROJECT)["check"]["sha256"]
    refutations.ratify(a, channel="cli-tty", note=S("ratify.note"),
                       check_sha256=pinned, project_dir=PROJECT)
    # an agent proposes a revision of the active ruling (text, evidence, check)
    refutations.revise(a, channel="cli-agent", evidence=["issue:" + MARK + "3"],
                       subject=S("a.subject2"), verdict=S("a.verdict2"),
                       check=_check("a2"), project_dir=PROJECT)
    # an agent proposes to retire another active ruling
    b = _ruling("b")
    refutations.ratify(b, channel="cli-tty", project_dir=PROJECT)
    refutations.retire(b, channel="cli-agent",
                       evidence=["issue:" + MARK + "4"],
                       note=S("b.overturn.note"), project_dir=PROJECT)
    # a person retires a third: the overturn lands
    c = _ruling("c")
    refutations.ratify(c, channel="cli-tty", project_dir=PROJECT)
    refutations.retire(c, channel="cli-tty", evidence=["issue:" + MARK + "5"],
                       note=S("c.overturn.note"), project_dir=PROJECT)
    # a refutation overturned by a person
    r = refutations.assert_refutation(
        subject=S("r"), verdict=S("r.verdict"), scope=S("r.scope"),
        evidence=["issue:" + MARK + "6"], anchors=["issue:" + MARK + "7"],
        channel="cli-agent", project_dir=PROJECT)
    refutations.ratify(r, channel="cli-tty", project_dir=PROJECT)
    refutations.overturn(r, channel="cli-tty", evidence=["issue:" + MARK + "8"],
                         note=S("r.overturn.note"), project_dir=PROJECT)
    d = refutations.assert_refutation(
        subject=S("d"), verdict=S("d.verdict"), scope=S("d.scope"),
        evidence=["issue:" + MARK + "9"], anchors=["issue:" + MARK + "10"],
        channel="cli-agent", project_dir=PROJECT)
    refutations.ratify(d, channel="cli-tty", project_dir=PROJECT)

    folded = list(refutations.records(project_dir=PROJECT).values())
    guarded = refutations.guard("issue:" + MARK + "10",
                                anchors=["issue:" + MARK + "10"],
                                project_dir=PROJECT)
    assert guarded, "the guard must match the planted anchor"
    seen = _sentinel_paths(folded + guarded)
    assert seen - _declared("refutations.jsonl") == set(), sorted(
        seen - _declared("refutations.jsonl"))
    # anti-vacuity: the folded-only copies really did carry the sentinel
    for path in ("revision_proposed.subject", "revision_proposed.verdict",
                 "revision_proposed.evidence[]", "revision_proposed.check.match",
                 "revision_proposed.check.body", "overturn_proposed.note",
                 "overturn_proposed.evidence[]", "overturn_note",
                 "overturn_evidence[]", "guard_match.anchors[]"):
        assert path in seen, path


def test_every_sentinel_in_a_folded_request_is_a_declared_path(
        tmp_checkpoint_dir):
    slug = store.project_slug(PROJECT)
    rid = requests.open_request(
        to=slug, ask=S("ask"), why=S("why"), evidence=S("evidence"),
        channel="ui", author=S("opened.author"), project_dir=PROJECT)
    requests.accept(rid, channel="ui", note=S("accept.note"),
                    author=S("verdict.author"), project_dir=PROJECT)
    requests.reply(rid, S("reply.note"), S("reply.evidence"), channel="ui",
                   author=S("reply.author"), project_dir=PROJECT)
    requests.done(rid, channel="ui", evidence=S("done.evidence"),
                  author=S("done.author"), project_dir=PROJECT)
    folded = list(requests.records(project_dir=PROJECT).values())
    inbox = requests.inbox(project_dir=PROJECT).rows
    seen = _sentinel_paths(folded + list(inbox))
    assert seen - _declared("requests.jsonl") == set(), sorted(
        seen - _declared("requests.jsonl"))
    for path in ("done_evidence", "replies[].note", "replies[].evidence",
                 "replies[].act_author", "opened_act_author",
                 "verdict_act_author", "done_act_author"):
        assert path in seen, path
