"""Pins the request furniture of `daimon brief` as it prints today (#1402).

The four request panels, the decision count line, the version note and the
withheld trailer are built in three places and printed by one host. This file
holds that host's exact bytes in every state (a body, no checkpoint, an empty
body, a closed view) and with the budget cut, so the refactor that builds
them once cannot change what the CLI prints. Ids and the overdue age are
normalized (`_furniture_seed.normalize`); everything else is verbatim."""
from daimon_briefing import briefing, cli, requests, serializer, store
from tests import _furniture_seed as seed

NOTE = (f"⚠ checkpoint format 0.0.1 != current {serializer.PROMPT_VERSION} "
        "— schema changed; some sections may render partially.")
COUNT = "1 decision waiting on you here (1 elsewhere) - daimon decide"
PANELS = [
    "Requests waiting on you (from other projects):",
    "→ q-request  publish the schema  [blocking]",
    "  From: -p-furn-sender",
    "  High: the release is blocked on it",
    "",
    "Decisions on requests you sent:",
    "✓ accepted  q-verdict  share the fixture set",
    "  To: -p-furn-other",
    "  Note: on it",
    "",
    "Requests you accepted and still owe:",
    "✓ q-owed  port the corroboration model",
    "  From: -p-furn-sender",
    "  Accepted and owed. Close one now, no human needed: "
    "`daimon request done <id> --evidence …`",
    "",
    "Requests you sent that nobody is moving:",
    "! q-overdue  review the cutover plan  [open, Nd, to -p-furn-other2]",
]
NO_CHECKPOINT = ("No checkpoint yet — nothing to brief. Checkpoints are "
                 "written automatically at session end; to backfill one "
                 "manually, run `daimon serialize <transcript>`.")
TRAILER = [
    "1 resolved item(s) withheld — `daimon status --suppressed` to list",
    "1 quarantined item(s) withheld — `daimon trust list` shows them",
]


def _brief(capsys, monkeypatch, ids):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", seed.PROJECT)
    assert cli.main(["brief"]) == 0
    return seed.normalize(capsys.readouterr().out, ids).split("\n")


def test_a_body_prints_the_note_count_panels_and_trailer_exactly(
        tmp_checkpoint_dir, monkeypatch, capsys):
    ids = seed.seed()
    assert _brief(capsys, monkeypatch, ids) == [
        NOTE, briefing.GREETING, "", COUNT, "", *PANELS, "",
        "Decisions made:",
        f"- [~ inferred] {seed.KEPT}",
        *TRAILER, ""]


def test_no_checkpoint_prints_the_panels_before_the_pointer(
        tmp_checkpoint_dir, monkeypatch, capsys):
    ids = seed.seed(checkpoint_for_project=False, buckets=False)
    assert _brief(capsys, monkeypatch, ids) == [
        COUNT, "", *PANELS, "", NO_CHECKPOINT, ""]


def test_an_empty_body_prints_the_panels_before_the_pointer(
        tmp_checkpoint_dir, monkeypatch, capsys):
    ids = seed.seed(buckets=False, trailer=False, decisions=(), loops=())
    assert _brief(capsys, monkeypatch, ids) == [
        COUNT, "", *PANELS, "", NO_CHECKPOINT, ""]


def test_a_closed_view_prints_the_panels_before_the_closed_line(
        tmp_checkpoint_dir, monkeypatch, capsys):
    ids = seed.seed(trailer=False)
    bucket = tmp_checkpoint_dir / store.project_slug(seed.PROJECT)
    (bucket / "trust.jsonl").write_bytes(b"<<<<<<< HEAD\n")
    assert _brief(capsys, monkeypatch, ids) == [
        briefing.GREETING, "",
        "⚠ trust.jsonl is unreadable (garbage); run: daimon trust repair",
        "", COUNT, "", *PANELS, "", briefing.CLOSED_LINE, ""]


def test_a_tight_budget_collapses_every_panel_to_a_count_and_stamps_nothing(
        tmp_checkpoint_dir, monkeypatch, capsys):
    ids = seed.seed()
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "900")
    lines = _brief(capsys, monkeypatch, ids)
    collapsed = [
        "Requests waiting on you (from other projects):",
        "  (1 cut for budget, see: daimon request inbox)", "",
        "Decisions on requests you sent:",
        "  (1 cut for budget, see: daimon request list)", "",
        "Requests you accepted and still owe:",
        "  (1 cut for budget, see: daimon request inbox)", "",
        "Requests you sent that nobody is moving:",
        "  (1 cut for budget, see: daimon request list)", ""]
    start = lines.index(collapsed[0])
    assert lines[start:start + len(collapsed)] == collapsed
    assert lines[start - 2:start] == [COUNT, ""]
    # a card the reader never saw is never stamped as surfaced
    record = requests.recipient_join(project_dir=seed.PROJECT)[ids["request"]]
    assert requests.needs_surfaced_stamp(record) is True


def test_a_printed_card_is_stamped_once_and_the_next_read_prints_the_same(
        tmp_checkpoint_dir, monkeypatch, capsys):
    ids = seed.seed()
    first = _brief(capsys, monkeypatch, ids)
    record = requests.recipient_join(project_dir=seed.PROJECT)[ids["request"]]
    assert requests.needs_surfaced_stamp(record) is False
    assert _brief(capsys, monkeypatch, ids) == first
    record = requests.recipient_join(project_dir=seed.PROJECT)[ids["request"]]
    assert len(record["surfaced"]) == 1
