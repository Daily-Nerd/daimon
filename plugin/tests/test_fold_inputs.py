"""#1338: every surface that folds requests gets its inputs from ONE builder
(`requests.fold_inputs`), `fold` takes the resulting bundle with no defaults,
and a fold that fails on `daimon decide` says so instead of vanishing."""

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

from daimon_briefing import pending, refutations, requests, store
from daimon_briefing.surfaces import Writer

PKG = Path(requests.__file__).parent
ASK = "review the retrieval bar proposal before Friday"
WHY = "it blocks the release note we owe the team"


@pytest.fixture
def project(tmp_checkpoint_dir):
    return "/p/fold-inputs-sender"


def _seed_bucket(project_dir, session="S-fold-inputs"):
    store.write_checkpoint(session, {
        "session_id": session, "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "something happened here", "trust": "inferred"}]},
    }, project_dir=project_dir, writer=Writer.HUMAN)
    return store.project_slug(project_dir)


def _cover(recipient_project, sender_slug):
    return refutations.assert_ruling(
        subject=f"work asks from {sender_slug}",
        verdict=f"agent may accept work asks from {sender_slug}",
        scope="cross-project requests", evidence=["issue:961"],
        channel="cli-tty", ratified=True,
        request_policy={"sender": sender_slug, "kind": "work",
                        "verb": "accept", "by": "agent"},
        project_dir=recipient_project)


# ---- structure: the bundle is required, never rebuilt by hand -----------


def test_fold_requires_the_bundle_and_has_no_defaults():
    params = list(inspect.signature(requests.fold).parameters.values())
    assert [p.name for p in params] == ["rows", "inputs"]
    assert all(p.default is inspect.Parameter.empty for p in params)


def test_the_bundle_is_immutable():
    inputs = requests.fold_inputs(
        [], perspective=requests.Perspective.LOCAL, project_dir="/p/x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        inputs.own_slug = "other"  # type: ignore[misc]


def _is_fold_call(node: ast.Call, filename: str) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "fold" and filename == "requests.py"
    return (isinstance(func, ast.Attribute) and func.attr == "fold"
            and isinstance(func.value, ast.Name) and func.value.id == "requests")


def _calls_fold_inputs(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call)
            and ((isinstance(node.func, ast.Name)
                  and node.func.id == "fold_inputs")
                 or (isinstance(node.func, ast.Attribute)
                     and node.func.attr == "fold_inputs")))


def _enclosing(tree: ast.AST):
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def test_every_fold_call_takes_its_inputs_from_fold_inputs():
    """The census: no caller builds the bundle (or the loose pieces) itself."""
    seen = 0
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = _enclosing(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and _is_fold_call(node, path.name)):
                continue
            seen += 1
            where = f"{path.name}:{node.lineno}"
            assert not node.keywords, f"{where}: fold called with keywords"
            assert len(node.args) == 2, f"{where}: fold needs (rows, inputs)"
            arg = node.args[1]
            if _calls_fold_inputs(arg):
                continue
            assert isinstance(arg, ast.Name), (
                f"{where}: inputs must come from fold_inputs")
            fn = node
            while fn in parents and not isinstance(
                    fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = parents[fn]
            bound = [a for a in ast.walk(fn) if isinstance(a, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == arg.id
                             for t in a.targets)]
            assert bound and all(_calls_fold_inputs(a.value) for a in bound), (
                f"{where}: `{arg.id}` is not built by fold_inputs")
    assert seen == 5, f"expected the five known fold callers, saw {seen}"


def test_only_fold_inputs_constructs_the_bundle():
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = _enclosing(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "FoldInputs"):
                continue
            fn = node
            while fn in parents and not isinstance(fn, ast.FunctionDef):
                fn = parents[fn]
            assert getattr(fn, "name", "") == "fold_inputs", (
                f"{path.name}:{node.lineno} builds FoldInputs by hand")


# ---- golden: every surface answers the same for the same ask ------------


def _covered_accept(project):
    """sender `project` -> recipient R, R's ruling covers an agent accept from
    the sender, R's agent accepts. Returns (q_id, sender_dir, recipient)."""
    recipient = _seed_bucket("/p/fold-inputs-recipient", "S-recipient")
    _seed_bucket(project, "S-sender")
    _cover(recipient, store.project_slug(project))
    q_id = requests.open_request(
        to=recipient, ask=ASK, why=WHY, channel="cli-tty",
        project_dir=project)
    requests.accept(q_id, channel="cli-agent", project_dir=recipient)
    return q_id, project, recipient


def test_all_surfaces_agree_a_covered_agent_accept_is_accepted(project):
    q_id, sender, recipient = _covered_accept(project)
    sent = requests.sender_join(project_dir=sender)[q_id]
    listed = {r["request_id"]: r
              for r in requests.listing(project_dir=sender)}[q_id]
    sent_join = requests.sent(sender).by_id[q_id]
    inbox = requests.recipient_join(project_dir=recipient)[q_id]
    assert {r["state"] for r in (sent, listed, sent_join, inbox)} == {
        "accepted"}
    assert {r["accepted_by"] for r in (sent, listed, sent_join, inbox)} == {
        "agent"}
    # the fleet tally folds the same rows: an accepted ask is not waiting
    assert pending._foreign_request_counts(None).get(recipient, 0) == 0


def test_all_surfaces_agree_an_uncovered_agent_accept_stays_open(project):
    recipient = _seed_bucket("/p/fold-inputs-recipient-b", "S-recipient")
    _seed_bucket(project, "S-sender")
    q_id = requests.open_request(
        to=recipient, ask=ASK, why=WHY, channel="cli-tty",
        project_dir=project)
    forged = requests._stamp("accepted", q_id, "cli-agent")
    assert requests.append(forged, project_dir=recipient, writer=Writer.HUMAN)
    sent = requests.sender_join(project_dir=project)[q_id]
    inbox = requests.recipient_join(project_dir=recipient)[q_id]
    assert sent["state"] == inbox["state"] == "open"
    assert pending._foreign_request_counts(None).get(recipient, 0) == 1


@pytest.mark.xfail(strict=True, reason=(
    "#1338 finding: the FLEET perspective reads the ruling ledger by the raw "
    "`to`, with no alias forwarding, so it disagrees with sender-side "
    "surfaces for an ask addressed to a pre-migration alias. Preserved as "
    "found; fixing it changes the decide footer count, owner to decide."))
def test_fleet_tally_resolves_an_aliased_recipient_like_the_other_surfaces(
        project, monkeypatch):
    """An ask addressed to a pre-migration alias of the recipient bucket: the
    sender-side surfaces resolve its ruling ledger forward through the alias
    map, so the fleet tally must too, or it counts a decided ask as waiting."""
    recipient = _seed_bucket("/p/fold-inputs-recipient-c", "S-recipient")
    _seed_bucket(project, "S-sender")
    old = "-p-fold-inputs-old-alias"
    monkeypatch.setattr(requests.buckets, "alias_map",
                        lambda: {old: recipient})
    _cover(recipient, store.project_slug(project))
    q_id = requests.open_request(
        to=old, ask=ASK, why=WHY, channel="cli-tty", project_dir=project)
    forged = requests._stamp("accepted", q_id, "cli-agent")
    # the covering ruling is looked up by id + sha, as accept() would stamp
    ruling = next(iter(refutations.request_policy_history(
        project_dir=recipient)))
    forged["under_ruling"], forged["policy_sha256"] = ruling[5], ruling[6]
    assert requests.append(forged, project_dir=recipient, writer=Writer.HUMAN)
    sender_state = requests.sender_join(project_dir=project)[q_id]["state"]
    counts = pending._foreign_request_counts(None)
    waiting = counts.get(old, 0) + counts.get(recipient, 0)
    assert (sender_state == "accepted") == (waiting == 0)


# ---- a fold failure on decide is visible --------------------------------


def test_a_fold_failure_on_decide_names_the_origin_and_the_error_class(
        project, monkeypatch):
    recipient = _seed_bucket("/p/fold-inputs-recipient-d", "S-recipient")
    sender_slug = _seed_bucket(project, "S-sender")
    requests.open_request(
        to=recipient, ask=ASK, why=WHY, channel="cli-tty",
        project_dir=project)

    def _boom(rows, inputs):
        raise ValueError("boom")

    monkeypatch.setattr(requests, "fold", _boom)
    got = pending.foreign_counts_typed(project_dir=recipient)
    assert got.counts == {}
    joined = "\n".join(got.notes)
    assert sender_slug in joined
    assert "ValueError" in joined


def test_a_fold_failure_with_no_tally_to_say_it_in_is_raised(
        project, monkeypatch):
    recipient = _seed_bucket("/p/fold-inputs-recipient-e", "S-recipient")
    _seed_bucket(project, "S-sender")
    requests.open_request(
        to=recipient, ask=ASK, why=WHY, channel="cli-tty",
        project_dir=project)
    monkeypatch.setattr(
        requests, "fold", lambda rows, inputs: (_ for _ in ()).throw(
            ValueError("boom")))
    with pytest.raises(ValueError):
        pending._foreign_request_counts(None)


def test_the_fold_failed_note_names_nothing_on_a_tenant_scoped_home():
    from daimon_briefing import display
    named = display.elsewhere_fold_failed_note([("-p-x", "ValueError")], False)
    scoped = display.elsewhere_fold_failed_note([("-p-x", "ValueError")], True)
    assert "-p-x" in named and "ValueError" in named
    assert "-p-x" not in scoped and "ValueError" not in scoped
