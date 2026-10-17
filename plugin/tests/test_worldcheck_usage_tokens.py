"""The worldcheck class tokens in usage.log name their project (#1184).

`worldcheck:<class>:<outcome>[:<slug>]`: the slug is the LAST colon field of
the one command token, so the line keeps its two whitespace fields (scar 0153)
and matches the receipt-probe precedent (`worldcheck:receipt-probe:<label>:
<slug>`). Lines written before this carry no slug and stay valid."""

import pytest

from daimon_briefing import cli, effects_commit, store, worldcheck
from daimon_briefing.worldcheck import parse_usage_token, usage_token

CLASSES = (worldcheck.PR_STATE, worldcheck.BRANCH_STATE,
           worldcheck.FILE_EXISTS, worldcheck.DEPENDENCY_VERSION,
           worldcheck.RECEIPT_VALIDITY)
OUTCOMES = ("confirmed", "contradicted", "skipped", "skipped-cap",
            "skipped-no-gh", "skipped-exit-nonzero")
# every shape the slug transform allows: word chars (Unicode), '_', '-'
ODD_PATHS = ("/Users/a b/proj", "/tmp/a.b/c_d", "/p/café/日本", "/p/x:y/z",
             "/p/tab\there", "/p/new\nline", "/p/a b", "/p/a\u0085b",
             "/p/" + "x" * 300)


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("outcome", OUTCOMES)
def test_a_class_token_ends_with_the_slug(cls, outcome):
    assert usage_token(f"{cls}:{outcome}", "-p-proj") == (
        f"worldcheck:{cls}:{outcome}:-p-proj")


def test_an_aggregate_token_has_no_slug():
    for tag in ("confirmed", "contradicted", "skipped", "fired"):
        assert usage_token(tag, "-p-proj") == f"worldcheck:{tag}"


def test_no_slug_leaves_the_token_as_it_was():
    assert usage_token("pr-state:confirmed", None) == "worldcheck:pr-state:confirmed"
    assert usage_token("pr-state:confirmed", "") == "worldcheck:pr-state:confirmed"


@pytest.mark.parametrize("path", ODD_PATHS)
def test_every_slug_the_transform_makes_round_trips_on_one_field(path):
    slug = store.project_slug(path)
    command = usage_token("pr-state:skipped-cap", slug)
    assert len(f"2026-10-17T00:00:00Z {command}".split()) == 2
    assert parse_usage_token(command) == worldcheck.UsageToken(
        "pr-state", "skipped-cap", slug)


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("outcome", OUTCOMES)
def test_the_old_form_parses_without_a_slug(cls, outcome):
    got = parse_usage_token(f"worldcheck:{cls}:{outcome}")
    assert got == worldcheck.UsageToken(cls, outcome, None)
    assert got.key == f"worldcheck:{cls}:{outcome}"


def test_the_new_form_has_the_old_forms_key():
    got = parse_usage_token("worldcheck:file-exists:confirmed:-p-proj")
    assert got.key == "worldcheck:file-exists:confirmed"


@pytest.mark.parametrize("command", [
    "worldcheck:confirmed", "worldcheck:fired", "worldcheck:receipt-probe:attempted:-p",
    "worldcheck:pr-state", "worldcheck:pr-state:", "worldcheck:pr-state:bogus",
    "worldcheck:nope:confirmed", "brief", "worldcheck:pr-state:skipped-"])
def test_anything_else_is_not_a_class_token(command):
    assert parse_usage_token(command) is None


def test_the_commit_writes_class_tokens_with_the_slug_and_aggregates_without(
        tmp_checkpoint_dir, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_note_usage", seen.append)
    monkeypatch.setattr(cli, "_write_worldcheck_ledger", lambda rows, route: None)
    monkeypatch.setattr(cli, "_write_worldcheck_outcomes", lambda rows, route: None)
    monkeypatch.setattr(cli, "_note_receipt_probe_usage", lambda p, s: None)
    slug = store.project_slug("/p/tok")
    effects_commit.commit(effects_commit.worldcheck_effects(
        "/p/tok", "/p/tok",
        {"confirmed": 1, "pr-state:confirmed": 1, "skipped": 1,
         "file-exists:skipped": 1}, (), skips=("file-exists:skipped-cap",)))
    assert sorted(seen) == sorted([
        "worldcheck:confirmed", "worldcheck:skipped",
        f"worldcheck:pr-state:confirmed:{slug}",
        f"worldcheck:file-exists:skipped:{slug}",
        f"worldcheck:file-exists:skipped-cap:{slug}"])


def test_the_token_slug_is_the_slug_of_the_outcomes_bucket(
        tmp_checkpoint_dir, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_note_usage", seen.append)
    monkeypatch.setattr(cli, "_write_worldcheck_ledger", lambda rows, route: None)
    monkeypatch.setattr(cli, "_write_worldcheck_outcomes", lambda rows, route: None)
    monkeypatch.setattr(cli, "_note_receipt_probe_usage", lambda p, s: None)
    effects_commit.commit(effects_commit.worldcheck_effects(
        "/p/same", "/p/same", {"pr-state:confirmed": 1}, ()))
    assert seen == ["worldcheck:pr-state:confirmed:"
                    + store._outcomes_path("/p/same").parent.name]


def test_the_machine_wide_usage_table_counts_both_forms_under_one_key(
        tmp_path, monkeypatch):
    from daimon_briefing import config
    log = config.log_dir() / "usage.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "2026-07-30T02:35:46Z worldcheck:pr-state:confirmed\n"
        "2026-10-17T00:00:00Z worldcheck:pr-state:confirmed:-p-a\n"
        "2026-10-17T00:00:01Z worldcheck:pr-state:confirmed:-p-b\n"
        "2026-10-17T00:00:02Z worldcheck:pr-state:skipped-cap:-p-a\n"
        "2026-10-17T00:00:03Z worldcheck:confirmed\n"
        "2026-10-17T00:00:04Z worldcheck:receipt-probe:attempted:-p-a\n",
        encoding="utf-8")
    got = cli._stats_usage()
    assert got["worldcheck:pr-state:confirmed"] == 3
    assert got["worldcheck:pr-state:skipped-cap"] == 1
    assert got["worldcheck:confirmed"] == 1
    # the receipt-probe counters were already slug-keyed and stay that way
    assert got["worldcheck:receipt-probe:attempted:-p-a"] == 1
