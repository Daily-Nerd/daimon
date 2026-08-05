"""#431: the write-side architecture guard — no CLI command may write
checkpoint/team bytes without passing through the policy seam.

The read side has its structural guard (test_scoring.py: no scoring input can
lift effective weight above the trust ceiling). This is the write-side twin:
a runtime write-audit that drives EVERY command `cli.build_parser()` knows
about and asserts each audited write is attributable to a `policy.admit_*`
admission, with an explicit two-directional ratchet (KNOWN_BYPASSES) for the
writes that are deliberately outside the seam.

Mechanism
---------
The `write_audit` fixture patches `store._atomic_write` AND
`pathlib.Path.open` (write/append/create modes), recording every write that
lands under `config.checkpoint_dir()` or `config.team_dir()` (the conftest
isolation makes those tmp-rooted, so the audit is total for Python-level file
I/O). It also wraps `policy.admit_checkpoint` and `policy.admit_row` to
register the exact objects they admitted. A recorded write is GOVERNED when:

- a `daimon_briefing.policy.admit_*` frame is live on the stack, or
- some `daimon_briefing.*` frame on the stack holds a local whose identity
  was registered by an admit call — the correlation the issue prescribes,
  because `write_checkpoint`'s pointer/mirror writes (and the ledger appends)
  happen AFTER their admit call returned. `store.write_checkpoint` still
  holds the admitted checkpoint dict, and `store.append_event` /
  `store.append_verification` write the very dict `policy.admit_row`
  returned, so the bytes on disk are the bytes that were admitted.

Ratchet semantics (both directions)
-----------------------------------
`observed_bypasses == KNOWN_BYPASSES` exactly: an ungoverned write not in the
set fails (a new ungoverned write path landed), AND a listed entry that is no
longer observed fails (stale entry — the ratchet must shrink truthfully when
a bypass is closed). Sensitivity is proven, not assumed: companion tests stub
`policy.admit_checkpoint` / `policy.admit_row` out (the #426/#420 mutation-
check precedent, test_carry_forget_adversarial.py) and show the guard trips.

Documented limitations (honest absences, not covered by this audit)
-------------------------------------------------------------------
- recall's sqlite index (DAIMON_RECALL_DB): sqlite writes happen in C, below
  Python file I/O, so they cannot be recorded by these patches, and patching
  the sqlite3 module to intercept them would test the mock, not the seam.
  The db is a DERIVED index — rebuilt at any time from checkpoint files that
  themselves passed the seam — and it lives outside the audited roots.
- git subprocess writes (team sidecar clone/commit/fetch in teamsync) happen
  in a child process, invisible to in-process patches. The sidecar's PYTHON-
  side writes (scaffolding on `team init`, the dual-write mirror) ARE audited
  — the mirror is governed, the scaffolding is ratcheted below.
- builtin open() is not routed through pathlib.Path.open; the only such
  writer under the roots is store._pointer_lock's flock sidecar dotfile,
  which carries no content (opened "a+", never written).
- the serializer chunk cache is only written on CHUNKED serializes; the
  serialize recipe forces chunking (DAIMON_CHUNK_LINES) precisely so that
  write is observed and ratcheted rather than silently uncovered.
- correlation blind spot: a hypothetical rogue write inside a daimon_briefing
  frame that still holds a reference to some previously admitted object would
  read as governed. The admitted registry is per-test and the sensitivity
  tests bound the risk; a frame-exact taint system is not worth its weight.
"""

import argparse
import inspect  # noqa: F401  (documented API of the recorded stacks)
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from daimon_briefing import cli, config, policy, refutations, store, teamsync
from tests.conftest import FIXTURES, FakeChat

# ---------------------------------------------------------------------------
# The ratchet. Every entry is (command, path pattern) with its justification.
# Delete entries as their bypass is closed — the stale-entry direction of the
# guard forces that deletion.
# ---------------------------------------------------------------------------
KNOWN_BYPASSES = frozenset({
    # serializer._save_chunk_cache: the #48 content-addressed chunk cache is
    # PRE-redaction by design (quote verification must see raw text, #125) and
    # written before store.write_checkpoint ever runs — deliberately outside
    # the admission pipeline. Its exposure is bounded elsewhere: 0600 files,
    # age reaper (chunk_cache_days), and `daimon forget` purges it wholesale
    # (#422, incl. the pre-redaction window, #429).
    ("serialize", "checkpoints/.chunk-cache/*"),
    # teamsync.init scaffolding on an EMPTY remote: a stub README and the #279
    # scope-seed daimon-team.toml. Repo furniture, not belief bytes — no
    # checkpoint content can reach either file.
    ("team init", "team/{remote}/README.md"),
    ("team init", "team/{remote}/daimon-team.toml"),
})

# Commands that genuinely cannot be driven headless would be named here with
# a reason — the guard fails on any command lacking BOTH a recipe and a
# SKIPPED entry, so a skip can never be silent. Currently every command runs.
SKIPPED: dict = {}

# Pure log sinks, allowlisted BY FILENAME if one ever lands under an audited
# root. Today they all live under config.log_dir() (outside the roots), so
# this is insurance against a future move, not an active exemption.
# Belief-bearing files — checkpoints, latest/prev pointers, events.jsonl,
# verification.jsonl, forget-hits.jsonl, receipts sidecars, the team mirror —
# are deliberately NOT here.
LOG_SINKS = frozenset({
    "usage.log", "serialize.log", "serialize-crash.log", "recall-error.log",
})

_HEX = re.compile(r"^\.?[0-9a-f]{16,}")


class WriteAudit:
    """Recorder for every Python-level write under the audited roots."""

    def __init__(self):
        self.command = "<fixture>"
        self.records = []       # (command, root_label, rel Path, governed, frames)
        self.placeholders = {}  # literal path part -> "{placeholder}"
        self._roots = []        # (label, absolute base) — raw + resolved forms
        self._admitted_ids = set()
        self._admitted_refs = []   # strong refs pin id()s against reuse
        self._local = threading.local()

    def add_root(self, label, base: Path):
        for form in {Path(os.path.abspath(base)), Path(os.path.abspath(base)).resolve()}:
            self._roots.append((label, form))

    def admit(self, obj):
        self._admitted_refs.append(obj)
        self._admitted_ids.add(id(obj))

    # -- recording ---------------------------------------------------------

    def _under_roots(self, path):
        for p in {Path(os.path.abspath(path)), Path(os.path.abspath(path)).resolve()}:
            for label, base in self._roots:
                try:
                    return label, p.relative_to(base)
                except ValueError:
                    continue
        return None

    def record(self, path):
        hit = self._under_roots(path)
        if hit is None:
            return
        label, rel = hit
        governed = False
        frames = []
        fr = sys._getframe(1)
        while fr is not None:
            mod = fr.f_globals.get("__name__", "")
            name = fr.f_code.co_name
            frames.append(f"{mod}.{name}")
            if mod == "daimon_briefing.policy" and name.startswith("admit_"):
                governed = True
            elif not governed and mod.startswith("daimon_briefing"):
                try:
                    if any(id(v) in self._admitted_ids
                           for v in fr.f_locals.values()):
                        governed = True
                except Exception:
                    pass
            fr = fr.f_back
        self.records.append((self.command, label, rel, governed, tuple(frames)))

    # -- classification ----------------------------------------------------

    def pattern(self, label, rel: Path) -> str:
        parts = rel.parts
        if label == "checkpoints" and parts and parts[0] == ".chunk-cache":
            return "checkpoints/.chunk-cache/*"
        normed = [self.placeholders.get(p, "{hash}" if _HEX.match(p) else p)
                  for p in parts]
        return "/".join([label] + normed)

    def bypasses(self) -> set:
        return {(cmd, self.pattern(label, rel))
                for cmd, label, rel, governed, _ in self.records
                if not governed and rel.name not in LOG_SINKS}

    def governed(self):
        return [(cmd, label, rel) for cmd, label, rel, governed, _
                in self.records if governed]


@pytest.fixture
def write_audit(monkeypatch):
    """Install the write audit: record every write under the checkpoint/team
    roots, and register every object the policy seam admits."""
    audit = WriteAudit()
    audit.add_root("checkpoints", config.checkpoint_dir())
    audit.add_root("team", config.team_dir())

    orig_atomic = store._atomic_write

    def rec_atomic(path, blob):
        audit.record(path)
        audit._local.in_atomic = True    # its inner write_text re-opens the
        try:                             # .tmp twin — one write, one record
            return orig_atomic(path, blob)
        finally:
            audit._local.in_atomic = False

    monkeypatch.setattr(store, "_atomic_write", rec_atomic)

    orig_open = Path.open

    def rec_open(self, mode="r", *args, **kwargs):
        if (any(c in mode for c in "wax+")
                and not getattr(audit._local, "in_atomic", False)):
            audit.record(self)
        return orig_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", rec_open)

    orig_admit_checkpoint = policy.admit_checkpoint

    def rec_admit_checkpoint(checkpoint, forgotten_keys):
        audit.admit(checkpoint)
        return orig_admit_checkpoint(checkpoint, forgotten_keys)

    monkeypatch.setattr(policy, "admit_checkpoint", rec_admit_checkpoint)

    orig_admit_row = policy.admit_row

    def rec_admit_row(row, redact_fields=(), redact_fn=None):
        audit.admit(row)
        return orig_admit_row(row, redact_fields=redact_fields,
                              redact_fn=redact_fn)

    monkeypatch.setattr(policy, "admit_row", rec_admit_row)
    return audit


# ---------------------------------------------------------------------------
# Command enumeration + drive recipes
# ---------------------------------------------------------------------------

def _iter_commands(parser, prefix=()):
    """Every leaf command tuple the parser tree registers."""
    subs = [a for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)]
    if not subs:
        yield prefix
        return
    for action in subs:
        for name, sub in action.choices.items():
            yield from _iter_commands(sub, prefix + (name,))


def _assert_ratchet(observed: set):
    new = observed - KNOWN_BYPASSES
    stale = KNOWN_BYPASSES - observed
    assert not new, (
        "UNGOVERNED WRITE(S) outside the ratchet — a write path reached "
        "checkpoint/team bytes without a policy.admit_* admission. Route it "
        f"through the seam (or, if deliberate, ratchet it): {sorted(new)}")
    assert not stale, (
        "stale KNOWN_BYPASSES entries — these bypasses were not observed, so "
        "either the bypass was closed (delete the entry: the ratchet must "
        "shrink truthfully) or the recipe stopped exercising its write path "
        f"(fix the recipe): {sorted(stale)}")


# Item texts the recipes key on.
_T_GATEWAY = "Adopt the gateway seam for all writes"
_T_FORGET = "Retire the legacy uploader path"
_T_KEEP = "Keep the ratchet honest in tests"
_T_SER = "Serialize goes through the gateway too"
_T_HEAL = "Heal rejoins the governed store"
_Q1 = "Which commands still bypass the seam"
_B1 = "The seam sees every governed write"


def _cp_json(session_id, decisions, questions=(_Q1,)):
    return json.dumps({
        "session_id": session_id,
        "working_context": {
            "active_topic": {"text": "Write-audit guard drive", "trust": "inferred"},
            "open_questions": [{"text": q, "trust": "inferred"} for q in questions],
            "recent_decisions": [{"text": d, "trust": "inferred"} for d in decisions],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [{"text": _B1, "trust": "inferred"}],
            "uncertainties": [],
        },
    })


def _setup_env(tmp_path, monkeypatch):
    """Project + HOME isolation for the drive. conftest already isolates every
    DAIMON_* path; HOME covers hooks/skill installs (they write under ~)."""
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    (proj / "mod.py").write_text("def fn():\n    return 1\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(proj))
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    return proj


def _ids_by_text(proj):
    cp = store.read_latest(project_dir=str(proj), fallback=False)
    out = {}
    for section, key in store._ITEM_LISTS:
        for item in ((cp or {}).get(section) or {}).get(key) or []:
            if isinstance(item, dict) and item.get("id"):
                out[str(item.get("text") or "")] = item["id"]
    return out


def _transcript_copy(tmp_path, name):
    dst = tmp_path / name
    dst.write_text((FIXTURES / "sample_transcript.md").read_text(encoding="utf-8"),
                   encoding="utf-8")
    return dst


def _drive_all(audit, tmp_path, monkeypatch, proj):
    """Drive every command with minimal valid args, in dependency order.
    Each recipe asserts its rc so a broken drive can never silently stop
    exercising a write path."""
    ctx = {}

    def run(argv, want_rc, stdin=None, env=(), chat=None):
        with monkeypatch.context() as m:
            for k, v in env:
                m.setenv(k, v)
            if stdin is not None:
                m.setattr(sys, "stdin", io.StringIO(stdin))
            if chat is not None:
                m.setattr(cli, "_chat", chat)
            rc = cli.main(argv)
        assert rc == want_rc, f"{argv} -> rc {rc}, wanted {want_rc}"

    def r_write_checkpoint():
        run(["write-checkpoint"], 0,
            stdin=_cp_json("S-seed", [_T_GATEWAY, _T_FORGET, _T_KEEP]))
        ctx["ids"] = _ids_by_text(proj)

    def r_team_init():
        if shutil.which("git") is None:  # matches test_teamsync's requirement
            pytest.skip("git not on PATH — team recipes need it")
        bare = tmp_path / "origin" / "team-mem.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)],
                       check=True, capture_output=True, timeout=30)
        remote = teamsync.remote_slug(str(bare))
        audit.placeholders[remote] = "{remote}"
        run(["team", "init", str(bare)], 0)
        # Team mirroring ON for the rest of the drive, so write_checkpoint's
        # dual-write path into team_dir is exercised (and must be governed).
        monkeypatch.setenv("DAIMON_TEAM", "1")

    def r_serialize():
        ts = _transcript_copy(tmp_path, "guard-serialize.md")
        # Force the CHUNKED path so the pre-admission chunk cache write is
        # observed (and ratcheted) instead of silently uncovered.
        run(["serialize", str(ts)], 0,
            env=(("DAIMON_CHUNK_LINES", "3"), ("DAIMON_CHUNK_OVERLAP", "0"),
                 ("DAIMON_CHUNK_CONCURRENCY", "1")),
            chat=FakeChat(_cp_json("x", [_T_GATEWAY, _T_FORGET, _T_KEEP, _T_SER])))
        ctx["ids"] = _ids_by_text(proj)

    def r_brief():
        run(["brief"], 0)

    def r_anchor():
        run(["anchor", "mod.py", "fn", "--attach", "gateway seam"], 0)

    def r_recall():
        run(["recall", "gateway"], 0)

    def r_projects():
        run(["projects"], 0)

    def r_refute_add():
        subject = "The original receipt design"
        scope = "carried-item receipt tiers"
        run([
            "refute", "add", "--subject", subject,
            "--verdict", "whole-file hashes do not prove span claims",
            "--scope", scope, "--anchor", "issue:502",
            "--evidence", "measurement:566/623 origin misses",
            "--by", "agent",
        ], 0)
        ctx["refutation_id"] = refutations.make_id(subject, scope)

    def r_refute_ratify():
        run(["refute", "ratify", ctx["refutation_id"]], 0)

    def r_refute_revise():
        run([
            "refute", "revise", ctx["refutation_id"],
            "--verdict", "file hashes still do not prove individual claims",
            "--evidence", "measurement:second corpus", "--by", "human",
            "--ratify",
        ], 0)

    def r_refute_overturn():
        run([
            "refute", "overturn", ctx["refutation_id"],
            "--evidence", "measurement:contrary replay", "--by", "agent",
        ], 0)

    def r_refute_show():
        run(["refute", "show", ctx["refutation_id"]], 0)

    def r_refute_list():
        run(["refute", "list"], 0)

    def r_refute_search():
        run(["refute", "search", "receipt"], 0)

    def r_refute_guard():
        run(["refute", "guard", "revisit", "#502"], 0)

    def r_resolve():
        run(["resolve", ctx["ids"][_T_KEEP], "--note", "shipped"], 0)

    def r_forget():
        run(["forget", ctx["ids"][_T_FORGET], "--reason", "stale"], 0)

    def r_reverify():
        run(["reverify", ctx["ids"][_T_KEEP], "--evidence",
             "checked the release page"], 0)

    def r_log():
        run(["log", "--text", "cut the release"], 0)

    def r_handoff():
        # #523: the baton writes a ref-less handoff event; append_event's
        # policy.admit_row is the frame. Clear writes a second event.
        run(["handoff", "ship the baton first"], 0)
        run(["handoff", "--clear"], 0)

    def r_loops():
        # #480 slice 1: pure read — lists open, briefable loop items with ids.
        # No write path of its own, but drive it anyway so a future regression
        # (e.g. it starts writing) trips this guard immediately.
        run(["loops"], 0)

    def r_recall_inject():
        run(["recall-inject", "--session", "S-live"], 0,
            stdin="anything about the gateway seam?")

    def r_status():
        run(["status"], 0)

    def r_verify_receipt():
        run(["verify-receipt"], 2)  # rc 2 = unable (receipts off — no sidecar)

    def r_audit_quotes():
        run(["audit-quotes"], 0)

    def r_heal():
        # Seed a FAILED session with the ledger's own documented line shapes
        # (spawn + error result), then heal re-serializes it for real. The
        # canned checkpoint re-asserts the forgotten value, so the forget gate
        # drops it inside write_checkpoint and the forget-hits sink is
        # exercised under audit.
        heal_md = _transcript_copy(tmp_path, "guard-heal.md")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        log_dir = config.log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "serialize.log").open("a", encoding="utf-8") as f:
            f.write(f"{stamp} session-end: spawned serialize for guard-heal "
                    f"(reason: exit, project: {proj}) (transcript: {heal_md})\n")
            f.write(f"error: canned failure (transcript: {heal_md}) after 1s\n")
        run(["heal"], 0, chat=FakeChat(_cp_json("x", [_T_HEAL, _T_FORGET])))
        stored = store.read_checkpoint("guard-heal")
        texts = json.dumps(stored)
        assert _T_HEAL in texts
        assert _T_FORGET not in texts  # forget gate dropped the re-assertion

    def r_team_sync():
        run(["team", "sync"], 0)

    def r_team_status():
        run(["team", "status"], 0)

    def r_configure():
        run(["configure", "--backend", "command", "--command", "true",
             "--output", "text"], 0)

    def r_stats():
        run(["stats"], 0)

    def r_hooks_list():
        run(["hooks", "list"], 0)

    def r_hooks_install():
        run(["hooks", "install", "windsurf"], 0)  # HOME is tmp-isolated

    def r_hooks_status():
        run(["hooks", "status"], 0)

    def r_skill_list():
        run(["skill", "list"], 0)

    def r_skill_show():
        run(["skill", "show"], 0)

    def r_skill_install():
        run(["skill", "install", "claude"], 0)

    def r_skill_uninstall():
        run(["skill", "uninstall", "claude"], 0)

    def r_mcp_serve():
        run(["mcp", "serve"], 0, stdin="")

    recipes = {
        ("write-checkpoint",): r_write_checkpoint,
        ("team", "init"): r_team_init,
        ("serialize",): r_serialize,
        ("brief",): r_brief,
        ("anchor",): r_anchor,
        ("recall",): r_recall,
        ("projects",): r_projects,
        ("refute", "add"): r_refute_add,
        ("refute", "ratify"): r_refute_ratify,
        ("refute", "revise"): r_refute_revise,
        ("refute", "overturn"): r_refute_overturn,
        ("refute", "show"): r_refute_show,
        ("refute", "list"): r_refute_list,
        ("refute", "search"): r_refute_search,
        ("refute", "guard"): r_refute_guard,
        ("resolve",): r_resolve,
        ("reverify",): r_reverify,
        ("forget",): r_forget,
        ("log",): r_log,
        ("handoff",): r_handoff,
        ("loops",): r_loops,
        ("recall-inject",): r_recall_inject,
        ("status",): r_status,
        ("verify-receipt",): r_verify_receipt,
        ("audit-quotes",): r_audit_quotes,
        ("heal",): r_heal,
        ("team", "sync"): r_team_sync,
        ("team", "status"): r_team_status,
        ("configure",): r_configure,
        ("stats",): r_stats,
        ("hooks", "list"): r_hooks_list,
        ("hooks", "install"): r_hooks_install,
        ("hooks", "status"): r_hooks_status,
        ("skill", "list"): r_skill_list,
        ("skill", "show"): r_skill_show,
        ("skill", "install"): r_skill_install,
        ("skill", "uninstall"): r_skill_uninstall,
        ("mcp", "serve"): r_mcp_serve,
    }

    # No silent skips: every registered command has a recipe or a named SKIPPED
    # entry — a new subcommand fails here until it is covered.
    registered = set(_iter_commands(cli.build_parser()))
    covered = set(recipes) | set(SKIPPED)
    assert covered == registered and not (set(recipes) & set(SKIPPED)), (
        f"command registry / recipe drift — uncovered: "
        f"{sorted(registered - covered)}; phantom recipes: "
        f"{sorted(covered - registered)}")

    for cmd, recipe in recipes.items():
        audit.command = " ".join(cmd)
        recipe()
    return ctx


# ---------------------------------------------------------------------------
# THE GUARD
# ---------------------------------------------------------------------------

def test_every_command_write_carries_an_admit_frame(
        write_audit, tmp_path, monkeypatch, capsys):
    proj = _setup_env(tmp_path, monkeypatch)
    _drive_all(write_audit, tmp_path, monkeypatch, proj)

    _assert_ratchet(write_audit.bypasses())

    # Anti-vacuity: the audit must have SEEN the belief-bearing writes it
    # exists to govern — a fixture that recorded nothing would pass the
    # ratchet trivially.
    governed = write_audit.governed()

    def saw(command, name, label=None):
        return any(cmd == command and rel.name == name
                   and (label is None or lbl == label)
                   for cmd, lbl, rel in governed)

    assert saw("write-checkpoint", "latest.json")   # pointer writes
    assert saw("serialize", "latest.json")          # serialize's store path
    assert saw("serialize", "guard-serialize.json", "team")  # team dual-write
    assert saw("resolve", "events.jsonl")           # admit_row on the ledger
    assert saw("forget", "events.jsonl")            # tombstone append
    assert saw("refute add", "refutations.jsonl")  # negative ledger append
    assert saw("heal", "forget-hits.jsonl")         # capture-time forget drop
    assert saw("anchor", "latest.json")             # --attach rewrite


# ---------------------------------------------------------------------------
# SENSITIVITY — the guard must FAIL when the seam is removed (the #420/#426
# mutation-check precedent: prove the alarm rings, don't assume it).
# ---------------------------------------------------------------------------

def test_guard_trips_when_admit_checkpoint_is_stubbed_out(
        write_audit, tmp_path, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch)
    # The mutation: the write boundary loses its admission pipeline entirely
    # (no gates run, nothing is registered as admitted).
    monkeypatch.setattr(policy, "admit_checkpoint",
                        lambda checkpoint, forgotten_keys: [])
    write_audit.command = "write-checkpoint"
    monkeypatch.setattr(sys, "stdin", io.StringIO(_cp_json("S-mut", [_T_GATEWAY])))
    assert cli.main(["write-checkpoint"]) == 0

    stray = write_audit.bypasses() - KNOWN_BYPASSES
    assert stray, "guard is blind: an unadmitted checkpoint write passed"
    assert any(pat.endswith(".json") for _, pat in stray)  # checkpoint bytes


def test_guard_trips_when_admit_row_is_stubbed_out(
        write_audit, tmp_path, monkeypatch, capsys):
    proj = _setup_env(tmp_path, monkeypatch)
    # Seed through the intact seam first, so the ledger append has a target.
    write_audit.command = "<fixture>"
    monkeypatch.setattr(sys, "stdin", io.StringIO(_cp_json("S-seed", [_T_KEEP])))
    assert cli.main(["write-checkpoint"]) == 0
    item_id = _ids_by_text(proj)[_T_KEEP]

    # The mutation: ledger rows stop passing through the admission seam.
    monkeypatch.setattr(policy, "admit_row",
                        lambda row, redact_fields=(), redact_fn=None: row)
    write_audit.command = "resolve"
    assert cli.main(["resolve", item_id, "--note", "done"]) == 0

    stray = write_audit.bypasses() - KNOWN_BYPASSES
    assert ("resolve", f"checkpoints/{store.project_slug(str(proj))}/events.jsonl") \
        in stray or any(pat.endswith("events.jsonl") for _, pat in stray), \
        "guard is blind: an unadmitted ledger append passed"


def test_guard_trips_on_a_synthetic_ungoverned_write(write_audit):
    write_audit.command = "rogue"
    p = config.checkpoint_dir() / "some-slug" / "events.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write("{}\n")
    assert ("rogue", "checkpoints/some-slug/events.jsonl") in \
        write_audit.bypasses() - KNOWN_BYPASSES


def test_ratchet_fails_in_both_directions():
    # New bypass -> fails.
    with pytest.raises(AssertionError, match="UNGOVERNED"):
        _assert_ratchet(set(KNOWN_BYPASSES) | {("rogue", "checkpoints/x.json")})
    # Stale entry -> fails (KNOWN_BYPASSES is non-empty by construction).
    with pytest.raises(AssertionError, match="stale"):
        _assert_ratchet(set())
