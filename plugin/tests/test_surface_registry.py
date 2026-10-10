"""#601: the declared surface registry — new stores must declare a delete
strategy.

Four shipped defects of one class (#583, quote/scene #599, events item_text
#599, team copies #600-pending) came from the same structural hole: three
hand-maintained parallel lists (store._plaintext_surfaces, privacy's
exemptions, recall._fingerprint) each answered "what files exist and what may
they hold" differently, and a new file shape silently inherited a hole in
whichever list its author forgot. The registry is the single declaration:
every file shape daimon writes under ~/.daimon states whether it can hold
item plaintext, how deletion reaches it, and who owns it. The auditor derives
its exemptions from it; the write-audit guard refuses shapes it has never
seen declared; a plaintext shape with no reachable deletion must name the
tracking issue for its gap.
"""
import json
import os
import time

from daimon_briefing import cli, config, privacy, recall, store, surfaces
from daimon_briefing.surfaces import Writer


# ---- declaration hygiene --------------------------------------------------


def test_every_entry_declares_the_full_contract():
    assert surfaces.SURFACES, "registry must not be empty"
    seen = set()
    for s in surfaces.SURFACES:
        assert s.shape and s.owner, f"underspecified entry: {s}"
        assert s.shape not in seen, f"duplicate shape: {s.shape}"
        seen.add(s.shape)
        assert s.delete in surfaces.DELETE_STRATEGIES, \
            f"{s.shape}: unknown delete strategy {s.delete!r}"


def test_plaintext_never_pairs_with_exempt():
    for s in surfaces.SURFACES:
        if s.plaintext:
            assert s.delete != "exempt-no-plaintext", \
                f"{s.shape} holds plaintext but claims the exemption"
        else:
            assert s.delete == "exempt-no-plaintext", \
                f"{s.shape} holds no plaintext yet declares {s.delete}"


def test_known_gaps_name_their_tracking_issue():
    gaps = [s for s in surfaces.SURFACES if s.delete == "known-gap"]
    assert gaps, "the team mirror gap (#600) must be declared, not hidden"
    for s in gaps:
        assert s.issue, f"{s.shape}: a known gap must cite its issue"


def test_the_load_bearing_shapes_are_registered():
    for shape in ("checkpoints/{slug}/events.jsonl",
                  "checkpoints/{slug}/refutations.jsonl",
                  "checkpoints/{slug}/relations.jsonl",
                  "checkpoints/{slug}/verification.jsonl",
                  "checkpoints/{slug}/forget-hits.jsonl",
                  "checkpoints/.chunk-cache/*",
                  "recall.db",
                  "recall.db.{pid}.tmp*",
                  "recall/{hash}.db",
                  "recall/{hash}.db.{pid}.tmp*"):
        assert surfaces.match(shape) is not None, f"unregistered: {shape}"


def test_windsurf_state_is_declared_not_invisible():
    """Adversarial finding (BLOCKER): the windsurf adapter accumulates FULL
    RAW TRANSCRIPTS under ~/.daimon/windsurf/transcripts — the largest
    plaintext store daimon writes, and the registry claimed completeness
    without it. #607 brought it inside the contract (wholesale purge on
    forget, age reaper on heal); the activity stamps and installed hook
    copies hold no item text."""
    t = surfaces.match("windsurf/transcripts/traj-1.md")
    assert t is not None and t.delete == "wholesale-purge"
    u = surfaces.match("windsurf/unparsed-post_cascade_response-123.json")
    assert u is not None and u.delete == "wholesale-purge"
    assert surfaces.match("windsurf/traj-1.last-activity").delete \
        == "exempt-no-plaintext"
    assert surfaces.match("windsurf/traj-1.last-serialize").delete \
        == "exempt-no-plaintext"
    assert surfaces.match("hooks/daimon-windsurf-hooks.py").delete \
        == "exempt-no-plaintext"


def test_pid_placeholder_matches_digits_only():
    """{pid} must not degrade to a bare wildcard: recall.db.bak.tmp and
    recall.db.tmp are a user's own files and must stay UNDECLARED so the
    registry-derived reaper cannot touch them."""
    reap = surfaces.match("recall.db.44594.tmp")
    assert reap is not None and reap.delete == "reap"
    assert surfaces.match("recall.db.44594.tmp-journal").delete == "reap"
    # the staging name of a current rebuild: pid, then a random token
    staged = surfaces.match("recall.db.44594.tmp.a1b2c3d4e5f6")
    assert staged is not None and staged.delete == "reap"
    assert surfaces.match(
        "recall.db.44594.tmp.a1b2c3d4e5f6-journal").delete == "reap"
    assert surfaces.match("recall.db.bak.tmp") is None
    assert surfaces.match("recall.db.tmp") is None
    assert surfaces.match("recall.db.tmpfoo") is None


def test_recall_db_declares_lazy_rebuild_not_rewrite():
    """Nothing rewrites recall.db at forget time — rows leave at the next
    fingerprint-triggered rebuild, and if no recall command ever runs the
    plaintext persists. The strategy name must say so."""
    assert surfaces.match("recall.db").delete == "lazy-rebuild"


# ---- pattern matching -----------------------------------------------------


def test_match_classifies_write_audit_patterns():
    assert surfaces.match("checkpoints/{slug}/events.jsonl").delete \
        == "append-tombstone"
    assert surfaces.match("checkpoints/{hash}.json").plaintext is True
    assert surfaces.match("checkpoints/{slug}/S1.json").plaintext is True
    assert surfaces.match("team/{remote}/README.md").delete \
        == "exempt-no-plaintext"
    assert surfaces.match("team/{remote}/projects/p/authors/a/S1.json").delete \
        == "known-gap"
    assert surfaces.match("checkpoints/latest.json.bak-1782874461") is None
    assert surfaces.match("somewhere/unregistered.bin") is None


# ---- derivations ----------------------------------------------------------


def test_exempt_suffix_refuses_a_registry_without_one(monkeypatch):
    """A registry stripped of its suffix exemption must fail loudly — a
    silent empty string would quietly un-exempt every receipt sidecar and
    flood the audit with false residue."""
    import pytest

    monkeypatch.setattr(surfaces, "SURFACES", ())
    with pytest.raises(LookupError):
        surfaces.exempt_suffix()


def test_exempt_suffix_refuses_two_suffix_exemptions(monkeypatch):
    """privacy._is_plaintext_free compares against exactly ONE suffix —
    declaration order silently deciding the winner is the guess the
    registry exists to forbid."""
    import pytest

    two = (surfaces.Surface("a/*.receipt", "x", False,
                            "exempt-no-plaintext", "none", audit_exempt=True),
           surfaces.Surface("b/*.sidecar", "y", False,
                            "exempt-no-plaintext", "none", audit_exempt=True))
    monkeypatch.setattr(surfaces, "SURFACES", two)
    with pytest.raises(LookupError):
        surfaces.exempt_suffix()


def test_reap_is_registry_derived_not_a_parallel_predicate(
        tmp_path, monkeypatch):
    """The reaper's filter IS surfaces.match() — remove the reap declaration
    and the reaper must delete nothing, proving there is no second
    hand-written predicate (the parallel-list defect this issue exists to
    kill, reintroduced in the one path that deletes files)."""
    dead, journal, _fresh = _plant_orphans()
    no_reap = tuple(s for s in surfaces.SURFACES if s.delete != "reap")
    monkeypatch.setattr(surfaces, "SURFACES", no_reap)
    assert recall.reap_dead_snapshots() == []
    assert dead.exists() and journal.exists()


def test_privacy_exemptions_derive_from_the_registry():
    """The auditor's name-based exemption set is a VIEW of the registry, not
    a fourth parallel list."""
    assert privacy._EXEMPT_NAMES == surfaces.exempt_names()
    assert privacy._EXEMPT_SUFFIX == surfaces.exempt_suffix()
    # The registry hardcodes the lock filename in a shape string — this pin
    # is what breaks if store._LOCK_NAME is ever renamed without the
    # registry following (the shape is a copy, not a reference).
    assert store._LOCK_NAME in surfaces.exempt_names()


# ---- the reap verb --------------------------------------------------------


def _plant_orphans():
    db = config.recall_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    dead = db.parent / f"{db.name}.44594.tmp"
    journal = db.parent / f"{db.name}.44594.tmp-journal"
    dead.write_text("dead snapshot bytes")
    journal.write_text("journal bytes")
    old = time.time() - 2 * 3600
    os.utime(dead, (old, old))
    os.utime(journal, (old, old))
    fresh = db.parent / f"{db.name}.{os.getpid()}.tmp"
    fresh.write_text("live rebuild in flight")
    return dead, journal, fresh


def test_reap_removes_dead_snapshots_and_spares_fresh_ones(tmp_path):
    dead, journal, fresh = _plant_orphans()
    reaped = recall.reap_dead_snapshots()
    assert sorted(p.name for p in reaped) == [dead.name, journal.name]
    assert not dead.exists() and not journal.exists()
    assert fresh.exists(), "a fresh in-flight rebuild tmp must survive"


def test_reap_takes_the_token_named_staging_files_of_a_crashed_rebuild():
    db = config.recall_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    staged = db.parent / f"{db.name}.44594.tmp.a1b2c3d4e5f6"
    journal = db.parent / f"{db.name}.44594.tmp.a1b2c3d4e5f6-journal"
    live = db.parent / f"{db.name}.{os.getpid()}.tmp.0f0f0f0f0f0f"
    for p in (staged, journal, live):
        p.write_text("staging bytes")
    old = time.time() - 2 * 3600
    os.utime(staged, (old, old))
    os.utime(journal, (old, old))
    assert sorted(p.name for p in recall.reap_dead_snapshots()) == [
        staged.name, journal.name]
    assert live.exists(), "a fresh in-flight rebuild file must survive"


def test_reap_skips_non_tmp_siblings_directories_and_glob_errors(
        tmp_path, monkeypatch):
    db = config.recall_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    backup = db.parent / f"{db.name}.backup"      # no .tmp — never a target
    backup.write_text("user's own backup")
    dirlike = db.parent / f"{db.name}.44594.tmp"   # directory, not a file
    dirlike.mkdir()
    old = time.time() - 2 * 3600
    os.utime(backup, (old, old))
    os.utime(dirlike, (old, old))
    assert recall.reap_dead_snapshots() == []
    assert backup.exists() and dirlike.is_dir()


def test_reap_takes_only_pid_shaped_strands_never_user_files(
        tmp_path, monkeypatch):
    """Adversarial finding: the first filter (`.tmp` substring) was a strict
    superset of the declared shape `recall.db.{pid}.tmp*` — a user backup
    named recall.db.tmp / recall.db.bak.tmp / recall.db.tmp.gz beside a
    DAIMON_RECALL_DB override would have been silently deleted. The reaper
    deletes ONLY what rebuild() manufactures: <db>.<digits>.tmp plus its
    sqlite dash-sidecars."""
    db = config.recall_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    old = time.time() - 2 * 3600
    spared = []
    for name in (f"{db.name}.tmp", f"{db.name}.bak.tmp",
                 f"{db.name}.tmp.gz", f"{db.name}.tmpl"):
        p = db.parent / name
        p.write_text("not daimon's to delete")
        os.utime(p, (old, old))
        spared.append(p)
    dead = db.parent / f"{db.name}.44594.tmp-journal"
    dead.write_text("strand")
    os.utime(dead, (old, old))
    assert [p.name for p in recall.reap_dead_snapshots()] == [dead.name]
    for p in spared:
        assert p.exists(), f"{p.name} is a user file, not a strand"
    # an undeletable strand is skipped, not fatal, and not reported reaped
    dead, journal, _fresh = _plant_orphans()
    real_unlink = type(dead).unlink

    def deny_dead(self, missing_ok=False):
        if self.name == dead.name:
            raise OSError("EPERM")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(type(dead), "unlink", deny_dead)
    assert [p.name for p in recall.reap_dead_snapshots()] == [journal.name]
    monkeypatch.undo()
    # an unreadable parent aborts quietly with nothing reaped
    monkeypatch.setattr(type(db.parent), "glob",
                        lambda self, pat: (_ for _ in ()).throw(OSError()))
    assert recall.reap_dead_snapshots() == []


def test_reap_never_touches_the_live_db(tmp_path):
    db = config.recall_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_text("the live derived index")
    _plant_orphans()
    recall.reap_dead_snapshots()
    assert db.exists()


def test_heal_reaps_orphans_and_dry_run_only_lists(tmp_path, capsys):
    dead, journal, fresh = _plant_orphans()
    assert cli.main(["heal", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert dead.name in out
    assert dead.exists() and journal.exists(), "--dry-run must not delete"
    assert cli.main(["heal"]) == 0
    out = capsys.readouterr().out
    assert dead.name in out
    assert not dead.exists() and not journal.exists()
    assert fresh.exists()


def test_reap_clears_the_audit_unscannable_class(tmp_path):
    """The four dead -journal sidecars were what pinned a real install at
    exit 3 (cannot-prove): unopenable as databases, so the audit honestly
    refused to certify. After the reap they are gone, not explained away."""
    _write_min_checkpoint()
    dead, journal, fresh = _plant_orphans()
    fresh.unlink()          # leave only the dead pair
    before = privacy.audit_project(project_dir=_P)
    assert any(journal.name in u for u in before["unscannable"])
    recall.reap_dead_snapshots()
    after = privacy.audit_project(project_dir=_P)
    assert not any(journal.name in u for u in after["unscannable"])


_P = "/p/surface-registry"


def _write_min_checkpoint():
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "a decision that stays", "trust": "inferred"}]},
    }, project_dir=_P, writer=Writer.HUMAN)


# ---- ledger columns (#1132) ------------------------------------------------


def _bucket_jsonl():
    # The quarantine sidecar is JSONL too: one envelope row per line.
    return [s for s in surfaces.SURFACES
            if s.shape.startswith("checkpoints/{slug}/")
            and s.shape.endswith((".jsonl", surfaces.QUARANTINE_SIDECAR_SUFFIX))]


def test_field_paths_are_nonempty_tuples_of_str():
    for s in surfaces.SURFACES:
        for fp in s.prose:
            assert isinstance(fp, surfaces.FieldPath), s.shape
            assert isinstance(fp.path, tuple) and fp.path, s.shape
            assert all(isinstance(p, str) and p for p in fp.path), s.shape
            assert isinstance(fp.is_list, bool)
            if fp.is_list:
                assert len(fp.path) == 1, \
                    f"{s.shape}: a list field is a top-level key"


def test_no_ledger_column_is_filled_on_a_non_jsonl_shape():
    jsonl = {s.shape for s in _bucket_jsonl()}
    for s in surfaces.SURFACES:
        filled = (s.fold or s.prose or s.read or s.index_content
                  or s.mergeable or s.deleter or s.phase)
        if filled:
            assert s.shape in jsonl, f"{s.shape}: ledger column on a non-ledger"
    # The write column also governs the two append-only ledgers outside a
    # bucket (the own team tombstones, the recall delivery log).
    outside = {"team/{remote}/**/tombstones.jsonl",
               "team/{remote}/**/quarantines.jsonl",
               "logs/recall-delivery.jsonl"}
    for s in surfaces.SURFACES:
        if s.write:
            assert s.shape in jsonl | outside, (
                f"{s.shape}: write column on a non-ledger")


def test_every_nonempty_fold_resolves_to_a_callable():
    import importlib

    seen = 0
    for s in surfaces.SURFACES:
        if not s.fold:
            continue
        module, _, attr = s.fold.rpartition(".")
        assert module and attr, f"{s.shape}: fold {s.fold!r} is not dotted"
        fn = getattr(importlib.import_module(f"daimon_briefing.{module}"),
                     attr, None)
        assert callable(fn), f"{s.shape}: {s.fold} does not resolve"
        seen += 1
    assert seen, "at least one ledger declares a fold"


def test_every_plaintext_value_keyed_bucket_ledger_declares_prose():
    """relations.jsonl forgets by item id, so it is the one plaintext
    ledger with a deleter and no prose columns."""
    by_id = {"relations.jsonl"}
    for s in _bucket_jsonl():
        name = s.shape.rsplit("/", 1)[-1]
        if s.plaintext and s.delete in ("rewrite", "append-tombstone") \
                and name not in by_id:
            assert s.prose, f"{s.shape}: value-keyed deleter, no prose"
    assert surfaces.bucket_ledger("relations.jsonl").prose == ()


def test_the_mergeable_ledgers_are_pinned_in_order():
    """A literal on purpose: a silent registry edit must fail here."""
    assert surfaces.mergeable_ledgers() == (
        "events.jsonl", "refutations.jsonl", "amendments.jsonl",
        "requests.jsonl", "verification.jsonl", "forget-hits.jsonl",
        "relations.jsonl", "trust.jsonl", "request_policy_tombstones.jsonl")


def test_the_index_content_ledgers_are_pinned_and_cover_what_recall_folds():
    """recall folds events (resolutions), verification (invalidated_by) and
    trust (quarantine withholding) into the index. Scar 0107."""
    assert surfaces.index_content_ledgers() == frozenset(
        {"events.jsonl", "verification.jsonl", "trust.jsonl"})


def test_events_are_walked_by_forget_through_the_ratified_carve_out():
    """store.scrub_event_fields is what cli forget calls on events.jsonl."""
    assert surfaces.bucket_ledger("events.jsonl").walker == "forget"


def test_bucket_ledger_lookup_refuses_an_undeclared_name():
    import pytest

    with pytest.raises(LookupError):
        surfaces.bucket_ledger("nonesuch.jsonl")
    assert surfaces.bucket_ledger("trust.jsonl").fold == "trust.fold"


def test_the_prose_declarations_are_pinned():
    fp = surfaces.FieldPath

    def scalars(*names):
        return tuple(fp((n,)) for n in names)

    assert surfaces.bucket_ledger("refutations.jsonl").prose == (
        scalars("subject", "verdict", "scope", "revisit_when", "note")
        + (fp(("anchors",), True), fp(("evidence",), True),
           fp(("check", "match")), fp(("check", "body"))))
    assert surfaces.bucket_ledger("amendments.jsonl").prose == scalars(
        "evidence", "note")
    assert surfaces.bucket_ledger("trust.jsonl").prose == (
        fp(("reason",)), fp(("evidence",), True))
    assert surfaces.bucket_ledger("requests.jsonl").prose == scalars(
        "ask", "why", "note", "evidence", "from_label", "act_author")
    assert surfaces.bucket_ledger("events.jsonl").prose == scalars(
        "note", "item_text", "status")


def test_the_folded_prose_declarations_are_pinned():
    """Prose that sits in a FOLDED record outside the row paths (11c): a
    proposal's copy of the text, an overturn's note, a reply's note. Only the
    two ledgers whose fold derives such keys declare any."""
    fp = surfaces.FieldPath
    assert surfaces.bucket_ledger("refutations.jsonl").folded_prose == (
        fp(("revision_proposed", "subject")),
        fp(("revision_proposed", "verdict")),
        fp(("revision_proposed", "note")),
        fp(("revision_proposed", "evidence"), True),
        fp(("revision_proposed", "check", "match")),
        fp(("revision_proposed", "check", "body")),
        fp(("overturn_proposed", "note")),
        fp(("overturn_proposed", "evidence"), True),
        fp(("overturn_note",)),
        fp(("overturn_evidence",), True),
        fp(("guard_match", "anchors"), True))
    assert surfaces.bucket_ledger("requests.jsonl").folded_prose == (
        fp(("done_evidence",)),
        fp(("replies[]", "note")),
        fp(("replies[]", "evidence")),
        fp(("replies[]", "act_author")),
        fp(("opened_act_author",)),
        fp(("verdict_act_author",)),
        fp(("done_act_author",)))
    for name in ("events.jsonl", "amendments.jsonl", "trust.jsonl",
                 "relations.jsonl", "verification.jsonl"):
        assert surfaces.bucket_ledger(name).folded_prose == (), name


# ---- every prose consumer reads the registry (#1132) ----------------------


def _with_prose(monkeypatch, name, prose):
    monkeypatch.setattr(surfaces, "SURFACES", tuple(
        s._replace(prose=prose) if s.shape.endswith("/" + name) else s
        for s in surfaces.SURFACES))


def test_prose_values_walks_scalars_lists_and_nested_paths():
    fp = surfaces.FieldPath
    ledger = (fp(("a",)), fp(("b",), True), fp(("c", "d")))
    row = {"a": " x ", "b": ["y", "", 3, "z"], "c": {"d": "w"},
           "e": "ignored"}
    assert surfaces.prose_values(ledger, row) == [" x ", "y", "z", "w"]
    assert surfaces.prose_values(ledger, row, scalars_only=True) == [" x "]
    assert surfaces.prose_values(ledger, {"a": "  ", "c": "notadict"}) == []


def test_scalar_prose_fields_are_the_top_level_non_list_keys():
    assert surfaces.scalar_prose_fields("requests.jsonl") == (
        "ask", "why", "note", "evidence", "from_label", "act_author")
    assert surfaces.scalar_prose_fields("refutations.jsonl") == (
        "subject", "verdict", "scope", "revisit_when", "note")
    assert surfaces.scalar_prose_fields("trust.jsonl") == ("reason",)
    assert surfaces.scalar_prose_fields("relations.jsonl") == ()


def test_each_ledger_module_reads_its_own_registry_row(monkeypatch):
    from daimon_briefing import amendments, normalize, refutations, requests, trust

    row = {"zz": "probe text", "ask": "old ask", "reason": "old reason",
           "subject": "old subject", "evidence": "old ev"}
    for mod, name in ((refutations, "refutations.jsonl"),
                      (amendments, "amendments.jsonl"),
                      (trust, "trust.jsonl"),
                      (requests, "requests.jsonl")):
        assert mod.plaintext_values(row) != ["probe text"], name
        with monkeypatch.context() as m:
            _with_prose(m, name, (surfaces.FieldPath(("zz",)),))
            assert mod.plaintext_values(row) == ["probe text"], name
            assert mod.row_content_keys(row) == {
                normalize.content_key("probe text")}, name


def test_pending_strips_exactly_the_registry_request_prose(monkeypatch):
    from daimon_briefing import pending

    row = {"ask": "a", "why": "b", "zz": "c", "other": "kept"}
    assert pending._strip_plaintext(row)["zz"] == "c"
    _with_prose(monkeypatch, "requests.jsonl", (surfaces.FieldPath(("zz",)),))
    out = pending._strip_plaintext(row)
    assert out == {"ask": "a", "why": "b", "zz": "x", "other": "kept"}


def test_privacy_candidates_exclude_the_registry_plaintext_ledgers(
        tmp_path, monkeypatch):
    """A plaintext jsonl ledger declared in the registry is not an UNKNOWN
    bucket file: no hand-kept tuple to forget (scar 0105)."""
    monkeypatch.setattr(surfaces, "SURFACES", surfaces.SURFACES + (
        surfaces.Surface("checkpoints/{slug}/zz-new.jsonl", "x.y", True,
                         "rewrite", "forget"),))
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path))
    bucket = tmp_path / "some-bucket"
    bucket.mkdir()
    (bucket / "zz-new.jsonl").write_text("{}\n", encoding="utf-8")
    (bucket / "stranger.jsonl").write_text("{}\n", encoding="utf-8")
    _known, unknown = privacy._checkpoint_candidates()
    assert [p.name for p, _slug in unknown] == ["stranger.jsonl"]


def test_event_scrub_redacts_exactly_the_registry_event_prose(
        tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import normalize

    value = "an event note that must go"
    store.append_event("o-111aaa", "resolved", note=value, project_dir=_P, writer=Writer.HUMAN)
    _with_prose(monkeypatch, "events.jsonl", ())
    assert store.scrub_event_fields(normalize.content_key(value),
                                    project_dir=_P) == 0


def test_every_plaintext_bucket_ledger_has_an_audit_scan_block():
    """privacy no longer lists these names by hand, so a plaintext ledger
    the registry declares but audit_project never scans would pass the audit
    in silence. Its own scan block must name the file."""
    import ast
    import inspect
    import textwrap

    # Real Name nodes, never a substring: a comment naming the constant must
    # not satisfy this (scar 0054).
    tree = ast.parse(textwrap.dedent(inspect.getsource(privacy.audit_project)))
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names = {"events.jsonl": "_EVENTS_NAME",
             "refutations.jsonl": "_REFUTATIONS_NAME",
             "amendments.jsonl": "_AMENDMENTS_NAME",
             "requests.jsonl": "_REQUESTS_NAME",
             "relations.jsonl": "_RELATIONS_NAME",
             "trust.jsonl": "_TRUST_NAME"}
    assert set(names) == set(surfaces.bucket_ledger_names(plaintext=True))
    for const in names.values():
        assert const in used, f"audit_project never scans {const}"


def test_bucket_ledger_lookups_skip_globbed_and_nested_jsonl_shapes(
        monkeypatch):
    """Only fixed-name ledgers directly under the bucket are ledgers: a
    wildcard or deeper jsonl shape must not become a lookup name."""
    monkeypatch.setattr(surfaces, "SURFACES", (
        surfaces.Surface("checkpoints/{slug}/*.jsonl", "x.y", True,
                         "rewrite", "forget", mergeable=True),
        surfaces.Surface("checkpoints/{slug}/sub/deep.jsonl", "x.y", True,
                         "rewrite", "forget", mergeable=True),
        surfaces.Surface("checkpoints/{slug}/ok.jsonl", "x.y", True,
                         "rewrite", "forget", mergeable=True)))
    assert surfaces.bucket_ledger_names() == ("ok.jsonl",)
    assert surfaces.mergeable_ledgers() == ("ok.jsonl",)



# ---- request_policy_tombstones.jsonl is declared (#1132) -------------------

_TOMBSTONE_ROW = {
    "sender": "a-bucket", "to": "b-bucket", "kind": "info", "verb": "open",
    "by": "human", "ruling_id": "r-0123456789ab",
    "policy_sha256": "0" * 64, "active_from": 1, "active_until": 2}


def test_request_policy_tombstones_are_declared_structural_and_mergeable():
    row = surfaces.bucket_ledger("request_policy_tombstones.jsonl")
    assert row.owner == "refutations._write_policy_tombstones"
    assert row.plaintext is False and row.audit_exempt is True
    assert row.delete == "exempt-no-plaintext"
    assert row.mergeable is True and row.index_content is False
    assert row.prose == ()
    # every field is a bucket slug, a closed-enum string, an opaque id, a
    # hash or an integer stamp: nothing a forget could be asked to reach
    assert surfaces.match("checkpoints/{slug}/request_policy_tombstones.jsonl")


def test_the_audit_does_not_classify_policy_tombstones_as_unknown(
        tmp_checkpoint_dir):
    from daimon_briefing import refutations

    _write_min_checkpoint()
    path = refutations._tombstone_path(_P)
    path.write_text(json.dumps(_TOMBSTONE_ROW) + "\n", encoding="utf-8")
    result = privacy.audit_project(project_dir=_P)
    assert not any(path.name in u for u in result["unscannable"])


def test_per_store_recall_indexes_are_declared():
    """D9.6: a non-default store keeps `recall/<hash>.db`; its cache is
    lazily rebuilt and its staging twins are reaped like the legacy ones."""
    cache = surfaces.match("recall/0123456789abcdef.db")
    assert cache is not None and cache.delete == "lazy-rebuild"
    staged = surfaces.match("recall/0123456789abcdef.db.44594.tmp.a1b2c3d4e5f6")
    assert staged is not None and staged.delete == "reap"
    assert surfaces.match(
        "recall/0123456789abcdef.db.44594.tmp-journal").delete == "reap"
    # a user's own file beside it stays undeclared
    assert surfaces.match("recall/0123456789abcdef.db.bak.tmp") is None
    assert surfaces.match("recall/notes.txt") is None


# ---- PR 13: the published team quarantine ledger -----------------------------


def test_the_team_quarantine_ledger_is_declared_hash_only():
    row = surfaces.match("team/r/projects/a/authors/b/quarantines.jsonl")
    assert row is not None
    assert row.shape == "team/{remote}/**/quarantines.jsonl"
    assert row.owner == "store.publish_quarantine"
    assert row.plaintext is False
    assert row.delete == "exempt-no-plaintext"
    flat = surfaces.match("team/r/authors/b/quarantines.jsonl")
    assert flat is row
    # Declared before the `*.json` known-gap row, which would otherwise be
    # the first match for nothing but would still be the wrong neighbour.
    shapes = [s.shape for s in surfaces.SURFACES]
    assert (shapes.index("team/{remote}/**/quarantines.jsonl")
            < shapes.index("team/{remote}/**/*.json"))


def test_quarantines_jsonl_is_in_no_bucket_registry():
    from daimon_briefing import buckets

    assert "quarantines.jsonl" not in surfaces.bucket_ledger_names()
    assert "quarantines.jsonl" not in surfaces.mergeable_files()
    assert "quarantines.jsonl" not in surfaces.index_content_ledgers()
    assert "quarantines.jsonl" not in buckets._REMOVABLE
