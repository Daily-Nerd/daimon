"""#1424: `request open` says, after the ask is recorded, when the recipient
bucket has been quiet and a similarly named bucket next to it was active
since. Advisory only: the ask, its stdout and its exit code are unchanged, and
nothing here ever claims the two buckets are one project."""

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from daimon_briefing import (buckets, clock, cli, config, recipient_hint,
                             requests, store)
from daimon_briefing.surfaces import Writer

PARENT = "/p/work"
SENDER = "/p/other/sender"
ASK = "review the retrieval bar proposal before Friday"
WHY = "it blocks the release note we owe the team"
NOW = "2026-11-25T12:00:00Z"
DAY = 86_400


def _ns(stamp):
    return int(datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc).timestamp()) * 10 ** 9


def _bucket(name, created, parent=PARENT):
    path = f"{parent}/{name}"
    store.write_checkpoint(f"S-{name}", {
        "session_id": f"S-{name}", "created": created,
    }, project_dir=path, writer=Writer.HUMAN)
    return path


def _slug(path):
    return store.project_slug(path)


def _open(to_path, *, sender=SENDER, now=NOW, extra=()):
    with clock.use(clock.StepClock(_ns(now))):
        return cli.main(["request", "open", f"--to={_slug(to_path)}",
                         "--ask", ASK, "--why", WHY, "--by", "agent",
                         "--project", sender, *extra])


def _note(out):
    """The advisory note's lines, or [] when the output carries none."""
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("note: nothing has been saved or shown"):
            return lines[i:]
    return []


@pytest.fixture
def acme(tmp_checkpoint_dir):
    """The motivating shape: a quiet workspace folder and the active repo
    next to it."""
    quiet = _bucket("Acme", "2026-10-09T07:31:00Z")
    active = _bucket("AcmeV2", "2026-11-24T23:47:00Z")
    return quiet, active


# ---- the note ---------------------------------------------------------------

def test_a_quiet_recipient_with_an_active_sibling_gets_the_note(acme, capsys):
    quiet, active = acme
    assert _open(quiet) == 0
    out = capsys.readouterr().out
    assert _note(out) == [
        f"note: nothing has been saved or shown in {_slug(quiet)} since "
        "2026-10-09 (47 days ago).",
        f"  {_slug(active)} sits next to it with a similar name and was "
        "active on 2026-11-24.",
        "  If they are one project, ask the owner to merge them "
        "(`daimon bucket merge`), so asks reach one inbox. If not, ignore "
        "this.",
    ]


def test_the_ask_is_recorded_exactly_as_without_a_note(acme, capsys):
    quiet, active = acme
    assert _open(active) == 0
    quiet_out = capsys.readouterr().out
    assert _note(quiet_out) == []
    assert _open(quiet, now="2026-11-25T13:00:00Z") == 0
    out = capsys.readouterr().out
    note = _note(out)
    assert note
    head = out.splitlines()[:len(out.splitlines()) - len(note)]
    # the ask's own output is untouched and comes first, the note trails it
    assert len(head) == len(quiet_out.splitlines())
    sent = requests.records(project_dir=SENDER)
    assert sorted(r["to"] for r in sent.values()) == sorted(
        [_slug(active), _slug(quiet)])
    assert all(r["ask"] == ASK for r in sent.values())


def test_sending_to_the_active_sibling_prints_nothing_extra(acme, capsys):
    _, active = acme
    assert _open(active) == 0
    assert _note(capsys.readouterr().out) == []


# ---- the gap ----------------------------------------------------------------

@pytest.mark.parametrize("recipient_created, fires", [
    ("2026-11-10T23:47:00Z", True),    # exactly 14 days before the sibling
    ("2026-11-10T23:47:01Z", False),   # one second short of the bar
    ("2026-11-20T00:00:00Z", False),   # well inside the gap
])
def test_the_gap_bar_is_fourteen_days_and_inclusive(
        tmp_checkpoint_dir, capsys, recipient_created, fires):
    quiet = _bucket("Acme", recipient_created)
    _bucket("AcmeV2", "2026-11-24T23:47:00Z")
    assert _open(quiet) == 0
    assert bool(_note(capsys.readouterr().out)) is fires


def test_decisions_made_from_the_recipient_folder_do_not_make_it_active(
        acme, capsys):
    """BrandMe's ledger moved on 11-24 only because a person and an agent
    decided asks while standing in that folder. No session was shown
    anything, so the bucket is still quiet."""
    quiet, active = acme
    with clock.use(clock.StepClock(_ns("2026-11-01T00:00:00Z"))):
        q_id = requests.open_request(
            to=_slug(quiet), ask=ASK, why=WHY, channel="cli-agent",
            project_dir=SENDER)
    with clock.use(clock.StepClock(_ns("2026-11-24T10:00:00Z"))):
        requests.accept(q_id, channel="cli-tty", project_dir=quiet)
        requests.done(q_id, channel="cli-agent", evidence="x",
                      project_dir=quiet)
    capsys.readouterr()
    assert _open(quiet) == 0
    note = _note(capsys.readouterr().out)
    assert note and "since 2026-10-09" in note[0]


def test_a_mechanical_stamp_counts_as_the_recipient_being_seen(acme, capsys):
    quiet, active = acme
    with clock.use(clock.StepClock(_ns("2026-10-01T00:00:00Z"))):
        q_id = requests.open_request(
            to=_slug(quiet), ask=ASK, why=WHY, channel="cli-agent",
            project_dir=SENDER)
    with clock.use(clock.StepClock(_ns("2026-11-20T00:00:00Z"))):
        requests.stamp_surfaced(q_id, project_dir=quiet)
    capsys.readouterr()
    assert _open(quiet) == 0   # seen 11-20, sibling 11-24: 4 days apart
    assert _note(capsys.readouterr().out) == []


def test_the_last_stamp_date_is_the_date_the_note_states(acme, capsys):
    quiet, _ = acme
    with clock.use(clock.StepClock(_ns("2026-10-10T00:00:00Z"))):
        q_id = requests.open_request(
            to=_slug(quiet), ask=ASK, why=WHY, channel="cli-agent",
            project_dir=SENDER)
        requests.stamp_surfaced(q_id, project_dir=quiet)
    capsys.readouterr()
    assert _open(quiet) == 0
    note = _note(capsys.readouterr().out)
    assert note and "since 2026-10-10" in note[0]


def test_a_ledger_file_touched_today_is_not_a_signal(acme, capsys):
    import os
    import time
    quiet, _ = acme
    ledger = store.ledger_file(quiet, "requests.jsonl")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("", encoding="utf-8")
    os.utime(ledger, (time.time(), time.time()))
    assert _open(quiet) == 0
    assert _note(capsys.readouterr().out)


# ---- the name relation ------------------------------------------------------

def test_unrelated_names_under_one_parent_stay_silent(
        tmp_checkpoint_dir, capsys):
    quiet = _bucket("daimon", "2026-10-09T00:00:00Z")
    _bucket("handsal", "2026-11-24T00:00:00Z")
    assert _open(quiet) == 0
    assert _note(capsys.readouterr().out) == []


def test_a_layer_folder_and_its_project_stay_silent(tmp_checkpoint_dir,
                                                    capsys):
    layer = _bucket("Daily-Nerd", "2026-10-09T00:00:00Z", parent="/p")
    _bucket("daimon", "2026-11-24T00:00:00Z", parent="/p/Daily-Nerd")
    assert _open(layer) == 0
    assert _note(capsys.readouterr().out) == []


def test_the_same_name_under_another_parent_stays_silent(
        tmp_checkpoint_dir, capsys):
    quiet = _bucket("Acme", "2026-10-09T00:00:00Z", parent="/p/a")
    _bucket("AcmeV2", "2026-11-24T00:00:00Z", parent="/p/b")
    assert _open(quiet) == 0
    assert _note(capsys.readouterr().out) == []


def test_a_name_shorter_than_four_characters_never_relates(
        tmp_checkpoint_dir, capsys):
    quiet = _bucket("rb", "2026-10-09T00:00:00Z")
    _bucket("rbv2", "2026-11-24T00:00:00Z")
    assert _open(quiet) == 0
    assert _note(capsys.readouterr().out) == []


def test_the_name_prefix_ignores_case(tmp_checkpoint_dir, capsys):
    quiet = _bucket("ACME", "2026-10-09T00:00:00Z")
    _bucket("acmev2", "2026-11-24T00:00:00Z")
    assert _open(quiet) == 0
    assert _note(capsys.readouterr().out)


@pytest.mark.parametrize("a, b, expected", [
    ("Acme", "AcmeV2", True), ("AcmeV2", "Acme", True),
    ("acme", "ACMEV2", True), ("rb", "rbv2", False),
    ("daimon", "handsal", False), ("Acme", "Acme", False),
    ("Acme", "Zacme", False),
])
def test_names_related(a, b, expected):
    assert recipient_hint.names_related(a, b) is expected


# ---- what is skipped --------------------------------------------------------

def _merge(src, dst):
    """A REAL `bucket merge`: the alias row is minted by the verb itself. The
    folder is still on disk in these tests, hence force."""
    buckets.merge(_slug(src), _slug(dst), apply=True, force=True)


def _reborn(path, created):
    """A merged-away bucket whose directory shows up again (restored from a
    backup, or recreated by a host that bypasses the alias): the one way a
    merged pair has two live buckets. Written by hand because a normal
    session in that folder is routed to the merge target."""
    slug = _slug(path)
    bucket = store.make_bucket(slug, path)
    (bucket / "latest.json").write_text(json.dumps(
        {"session_id": "S-reborn", "created": created}), encoding="utf-8")


def test_a_real_merge_pair_stays_silent_in_both_directions(
        tmp_checkpoint_dir, capsys):
    quiet = _bucket("Acme", "2026-10-09T07:31:00Z")
    active = _bucket("AcmeV2", "2026-11-24T23:47:00Z")
    _merge(quiet, active)
    _reborn(quiet, "2026-10-09T07:31:00Z")
    assert buckets.alias_map() == {_slug(quiet): _slug(active)}
    assert _open(quiet) == 0
    assert _note(capsys.readouterr().out) == []


def test_a_merge_chain_is_followed_to_its_final_bucket(
        tmp_checkpoint_dir, capsys):
    """Acme -> AcmeV2 -> AcmeV3: Acme resolves to AcmeV3, so AcmeV2 (also
    resolving to AcmeV3) is already one project with it."""
    a = _bucket("Acme", "2026-10-09T07:31:00Z")
    b = _bucket("AcmeV2", "2026-11-23T07:31:00Z")
    c = _bucket("AcmeV3", "2026-11-24T23:47:00Z")
    _merge(a, b)
    _merge(b, c)
    _reborn(a, "2026-10-09T07:31:00Z")
    _reborn(b, "2026-11-23T07:31:00Z")
    assert buckets.alias_map() == {_slug(a): _slug(c), _slug(b): _slug(c)}
    assert _open(a) == 0
    assert _note(capsys.readouterr().out) == []


def test_two_buckets_merged_into_a_third_are_one_project(
        tmp_checkpoint_dir, capsys):
    a = _bucket("Acme", "2026-10-09T07:31:00Z")
    b = _bucket("AcmeV2", "2026-11-23T07:31:00Z")
    c = _bucket("AcmeV3", "2026-11-24T23:47:00Z")
    _merge(a, c)
    _merge(b, c)
    _reborn(a, "2026-10-09T07:31:00Z")
    _reborn(b, "2026-11-23T07:31:00Z")
    assert _open(a) == 0
    assert _note(capsys.readouterr().out) == []


def test_an_unrelated_merge_does_not_silence_a_real_pair(
        tmp_checkpoint_dir, capsys):
    quiet = _bucket("Acme", "2026-10-09T07:31:00Z")
    _bucket("AcmeV2", "2026-11-24T23:47:00Z")
    other = _bucket("handsal", "2026-10-09T07:31:00Z")
    target = _bucket("daimon", "2026-10-09T07:31:00Z")
    _merge(other, target)
    assert _open(quiet) == 0
    assert _note(capsys.readouterr().out)


def test_a_sibling_with_no_recorded_root_is_skipped(
        tmp_checkpoint_dir, capsys):
    quiet = _bucket("Acme", "2026-10-09T00:00:00Z")
    bare = store.bucket_dir(_slug(f"{PARENT}/AcmeV2"))
    bare.mkdir(parents=True)
    (bare / "latest.json").write_text(json.dumps(
        {"session_id": "S", "created": "2026-11-24T00:00:00Z"}),
        encoding="utf-8")
    assert store.bucket_root(bare.name) is None
    assert _open(quiet) == 0
    assert _note(capsys.readouterr().out) == []


def test_a_recipient_with_no_recorded_root_has_no_siblings():
    assert recipient_hint.related(
        "-p-work-Acme", {"-p-work-AcmeV2": "/p/work/AcmeV2"}, {}) == []


def test_a_recipient_never_seen_at_all_stays_silent(
        tmp_checkpoint_dir, capsys):
    """No checkpoint and no stamp: the existing no-bucket warning already
    covers it, and 'quiet since never' would be a made-up date."""
    path = f"{PARENT}/Acme"
    store.make_bucket(_slug(path), path)
    _bucket("AcmeV2", "2026-11-24T00:00:00Z")
    assert _open(path, extra=("--anyway",)) == 0
    out = capsys.readouterr().out
    assert "has no bucket on this machine" in out
    assert _note(out) == []


def test_a_sibling_never_seen_stays_silent(tmp_checkpoint_dir, capsys):
    quiet = _bucket("Acme", "2026-10-09T00:00:00Z")
    path = f"{PARENT}/AcmeV2"
    store.make_bucket(_slug(path), path)
    assert _open(quiet) == 0
    assert _note(capsys.readouterr().out) == []


def test_a_torn_checkpoint_pointer_is_not_a_date(tmp_checkpoint_dir, capsys):
    quiet = _bucket("Acme", "2026-10-09T00:00:00Z")
    active = _bucket("AcmeV2", "2026-11-24T00:00:00Z")
    (store.bucket_dir(_slug(active)) / "latest.json").write_text(
        "{torn", encoding="utf-8")
    assert _open(quiet) == 0
    assert _note(capsys.readouterr().out) == []


# ---- tenant scope -----------------------------------------------------------

def test_a_tenant_scoped_home_never_reads_or_names_another_bucket(
        tmp_checkpoint_dir, tmp_path, monkeypatch, capsys):
    parent = tmp_path / "work"
    quiet = _bucket("Acme", "2026-10-09T07:31:00Z", parent=str(parent))
    active = _bucket("AcmeV2", "2026-11-24T23:47:00Z", parent=str(parent))
    Path(quiet).mkdir(parents=True)
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    monkeypatch.chdir(quiet)
    touched = []
    real_root = store.bucket_root
    real_events = requests.events

    def spy_root(x):
        touched.append(str(x))
        return real_root(x)

    def spy_events(project_dir=None, **kw):
        touched.append(str(project_dir))
        return real_events(project_dir, **kw)

    real_alias = buckets.alias_map

    def spy_alias():
        touched.append("alias_map")
        return real_alias()

    monkeypatch.setattr(store, "bucket_root", spy_root)
    monkeypatch.setattr(requests, "events", spy_events)
    monkeypatch.setattr(buckets, "alias_map", spy_alias)
    with clock.use(clock.StepClock(_ns(NOW))):
        rc = cli.main(["request", "open", f"--to={_slug(quiet)}", "--ask",
                       ASK, "--why", WHY, "--by", "agent"])
    out = capsys.readouterr().out
    assert rc == 0, out   # a tenant addresses its own bucket
    assert _note(out) == []
    assert _slug(active) not in out
    assert not [t for t in touched if _slug(active) in t or active in t]
    assert config.tenant_scoped()


def test_a_tenant_scoped_hint_reads_nothing_at_all(acme, monkeypatch):
    quiet, _ = acme
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    touched = []

    def spy(name, fn):
        def wrapper(*a, **k):
            touched.append(name)
            return fn(*a, **k)
        return wrapper

    monkeypatch.setattr(buckets, "alias_map", spy("alias_map",
                                                  buckets.alias_map))
    monkeypatch.setattr(store, "bucket_root", spy("bucket_root",
                                                  store.bucket_root))
    monkeypatch.setattr(store, "list_buckets", spy("list_buckets",
                                                   store.list_buckets))
    monkeypatch.setattr(requests, "events", spy("events", requests.events))
    assert recipient_hint.hint(_slug(quiet), _slug(quiet)) is None
    assert touched == []


# ---- several siblings -------------------------------------------------------

def test_with_several_siblings_the_most_recent_is_named_with_a_count(
        acme, capsys):
    quiet, active = acme
    third = _bucket("AcmeV3", "2026-11-12T00:00:00Z")
    assert _open(quiet) == 0
    note = _note(capsys.readouterr().out)
    assert _slug(active) in note[1] and "2026-11-24" in note[1]
    assert _slug(third) not in note[1]
    assert "and 1 more" in note[1]


def test_a_sibling_inside_the_gap_is_not_counted(acme, capsys):
    quiet, active = acme
    _bucket("AcmeV3", "2026-10-12T00:00:00Z")   # 3 days after the recipient
    assert _open(quiet) == 0
    note = _note(capsys.readouterr().out)
    assert note and "more" not in note[1]


# ---- every attention stamp counts, nothing else does ------------------------

def _stamp_at(quiet, event, when, *, extra=None):
    with clock.use(clock.StepClock(_ns("2026-10-01T00:00:00Z"))):
        q_id = requests.open_request(
            to=_slug(quiet), ask=ASK, why=WHY, channel="cli-agent",
            project_dir=SENDER)
    with clock.use(clock.StepClock(_ns(when))):
        row = requests._stamp(event, q_id, "mechanical")
    row.update(extra or {})
    requests.append(row, project_dir=quiet, writer=Writer.EMITTER)


@pytest.mark.parametrize("event", sorted(requests.ATTENTION_STAMPS))
def test_every_attention_stamp_counts_as_the_recipient_being_seen(
        acme, capsys, event):
    quiet, _ = acme
    _stamp_at(quiet, event, "2026-11-20T00:00:00Z", extra={"session": "S"})
    capsys.readouterr()
    assert _open(quiet) == 0
    assert _note(capsys.readouterr().out) == []


def test_done_verified_is_a_byte_check_not_attention(acme, capsys):
    quiet, _ = acme
    _stamp_at(quiet, "done_verified", "2026-11-20T00:00:00Z")
    capsys.readouterr()
    assert _open(quiet) == 0
    assert _note(capsys.readouterr().out)


def test_the_attention_set_names_every_mechanical_stamp_requests_writes():
    """A new `_stamp(..., "mechanical")` site must be classified: attention or
    not. Otherwise the hint would silently ignore a surface that showed asks."""
    tree = ast.parse(Path(requests.__file__).read_text(encoding="utf-8"))
    mechanical = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", "")
                == "_stamp" and len(node.args) >= 3
                and isinstance(node.args[2], ast.Constant)
                and node.args[2].value == "mechanical"):
            mechanical.add(node.args[0].value)
    assert mechanical == requests.ATTENTION_STAMPS | {"done_verified"}


# ---- bad data never becomes a date or an error ------------------------------

def _plant_row(slug_path, **fields):
    with clock.use(clock.StepClock(_ns("2026-10-01T00:00:00Z"))):
        q_id = requests.open_request(
            to=_slug(slug_path), ask=f"{ASK} ({slug_path})", why=WHY,
            channel="cli-agent", project_dir=SENDER)
    row = {"version": 1, "event": "surfaced", "request_id": q_id,
           "channel": "mechanical", "ts": "2026-11-20T00:00:00Z"}
    row.update(fields)
    ledger = store.ledger_file(slug_path, "requests.jsonl")
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


@pytest.mark.parametrize("fields", [
    {"order": 10 ** 29, "ts": "x"},               # absurd magnitude
    {"order": -10 ** 29, "ts": "x"},
    {"order": "garbage", "ts": "2026-11-20T00:00:00.5Z"},  # fractional ts
    {"order": [1], "ts": 7},                       # wrong types
    {"order": 1.5e29, "ts": None},
])
def test_an_unreadable_stamp_is_no_stamp_and_never_an_error(
        acme, capsys, fields):
    quiet, active = acme
    _plant_row(active, **fields)
    _plant_row(quiet, **fields)
    capsys.readouterr()
    assert _open(quiet) == 0
    out = capsys.readouterr()
    assert out.err == ""
    note = _note(out.out)
    assert note and "since 2026-10-09" in note[0]


def test_a_stamp_from_the_far_future_is_not_a_date(acme, capsys):
    quiet, _ = acme
    _plant_row(quiet, order=_ns("2999-01-01T00:00:00Z"))
    capsys.readouterr()
    assert _open(quiet) == 0
    note = _note(capsys.readouterr().out)
    assert note and "since 2026-10-09" in note[0]


def test_a_hint_that_raises_never_changes_a_committed_open(
        acme, monkeypatch, capsys):
    quiet, active = acme
    assert _open(active) == 0
    baseline = capsys.readouterr().out

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(recipient_hint, "hint", boom)
    assert _open(quiet, now="2026-11-25T13:00:00Z") == 0
    got = capsys.readouterr()
    assert got.err == ""
    assert len(got.out.splitlines()) == len(baseline.splitlines())
    assert _note(got.out) == []
    assert sorted(r["to"] for r in requests.records(
        project_dir=SENDER).values()) == sorted([_slug(active), _slug(quiet)])
    usage = (config.log_dir() / "usage.log").read_text(encoding="utf-8")
    assert "request:open:hint-error" in usage


def test_a_note_that_fails_to_render_never_changes_a_committed_open(
        acme, monkeypatch, capsys):
    quiet, _ = acme
    monkeypatch.setattr(recipient_hint, "note_lines",
                        lambda *a, **k: 1 / 0)
    assert _open(quiet) == 0
    got = capsys.readouterr()
    assert got.err == "" and _note(got.out) == []
    assert len(requests.records(project_dir=SENDER)) == 1


# ---- a single caller --------------------------------------------------------

def test_open_request_has_one_caller_and_it_asks_for_the_hint():
    """Parity pin: every host opens an ask by shelling out to the CLI, so the
    rule lives in the shared command. A new caller of `open_request` (an MCP
    or host API verb) must call the hint too, or this fails."""
    package = Path(recipient_hint.__file__).parent
    callers = {}
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(
                fn, "id", "")
            if name == "open_request":
                callers.setdefault(path.relative_to(package).as_posix(),
                                   []).append(node.lineno)
    assert set(callers) == {"cli/request.py"}, callers
    tree = ast.parse((package / "cli" / "request.py").read_text(
        encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "_cmd_request_open")
    calls = [(n.lineno, n.func.attr if isinstance(n.func, ast.Attribute)
              else "", getattr(n.func.value, "id", "")
              if isinstance(n.func, ast.Attribute) else "")
             for n in ast.walk(fn) if isinstance(n, ast.Call)]
    opened = [line for line, name, _ in calls if name == "open_request"]
    hinted = [line for line, name, owner in calls
              if owner == "recipient_hint" and name == "hint"]
    assert opened and hinted and min(hinted) > max(opened)
