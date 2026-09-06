"""#943 slice 3: the shared host-adapter core.

`checks_host.py` is what the per-host pre-action scripts call. It runs in
whatever interpreter the host launched, so this file loads it the way a hook
will: by FILE LOCATION, under its own module name, from the canonical package
copy. A relative import or a `daimon_briefing` import in it fails here rather
than on someone's machine.

The point of the module is that a host is a ROW, not a pipeline. Every test
below that names a host names it through `PROFILES`, so adding one is adding
a row and these tests are what say whether the row is complete.
"""

import ast
import importlib.util
from pathlib import Path

import pytest

CANONICAL = (Path(__file__).parents[1] / "daimon_briefing" / "checks_host.py")


def _host():
    spec = importlib.util.spec_from_file_location(
        "_checks_host_under_test", CANONICAL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ch = _host()


# ---- stdlib only, and the runtime by file location (scar 0049) ------------


def test_the_host_core_imports_nothing_from_the_package():
    """Scar 0049. This module ships into `hook/` and `_hooks/` and is loaded
    by a script with no venv and no `daimon_briefing` on sys.path, so a
    package import breaks every host at once and passes every local test."""
    tree = ast.parse(CANONICAL.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"relative import: {ast.dump(node)}"
            assert not str(node.module or "").startswith("daimon_briefing"), \
                f"package import: {node.module}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("daimon_briefing"), \
                    f"package import: {alias.name}"
                assert alias.name != "checks_runtime", (
                    "the runtime must load by file location from this file's "
                    "own directory, never by name off sys.path")


def test_the_runtime_loads_from_this_files_own_directory():
    """Same-directory, file-location load — the `_load_redact` shape. By name
    it would depend on sys.path state and could bind an unrelated top-level
    module of the same name."""
    assert ch.runtime() is not None
    assert ch.runtime().MANIFEST_NAME == "manifest.json"


def test_a_missing_runtime_sibling_is_none_and_never_a_raise(monkeypatch,
                                                             tmp_path):
    """The stale-install shape: checks_host.py present, checks_runtime.py not.
    A raise here fires before every shell action on the host."""
    stray = tmp_path / "stray"
    stray.mkdir()
    (stray / "checks_host.py").write_bytes(CANONICAL.read_bytes())
    spec = importlib.util.spec_from_file_location(
        "_checks_host_no_runtime", stray / "checks_host.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.runtime() is None


# ---- profiles: a host is a row --------------------------------------------


def test_every_declared_host_has_a_profile():
    assert set(ch.PROFILES) == {"claude-code", "codex", "windsurf"}


@pytest.mark.parametrize("host", ["claude-code", "codex", "windsurf"])
def test_a_profile_row_is_complete(host):
    """Every field the pipeline reads, present on every row. A row missing
    one is a host that half-works: the adapter reads the payload, finds
    nothing, and allows in silence."""
    p = ch.PROFILES[host]
    assert p.host == host
    assert isinstance(p.event, str) and p.event
    assert isinstance(p.tool_names, frozenset)
    assert isinstance(p.command_path, tuple) and p.command_path
    assert all(isinstance(k, str) and k for k in p.command_path)
    assert isinstance(p.cwd_key, str) and p.cwd_key
    assert p.encoder in ch.ENCODERS
    assert set(p.mode_caps) == set(ch.INTENTS)
    assert isinstance(p.matcher, str)
    assert isinstance(p.install_timeout, int) and p.install_timeout > 0


def test_the_payload_shapes_are_the_measured_ones():
    """Spec section 4. Claude Code and Codex read `tool_input.command` under
    `PreToolUse`; Windsurf reads `tool_info.command_line` under
    `pre_run_command`, which is a different field name and not a typo."""
    assert ch.PROFILES["claude-code"].event == "PreToolUse"
    assert ch.PROFILES["claude-code"].command_path == ("tool_input", "command")
    assert ch.PROFILES["claude-code"].tool_names == frozenset({"Bash"})
    assert ch.PROFILES["claude-code"].matcher == "Bash"

    assert ch.PROFILES["codex"].event == "PreToolUse"
    assert ch.PROFILES["codex"].command_path == ("tool_input", "command")
    assert ch.PROFILES["codex"].tool_names == frozenset({"Bash", "shell"})
    assert ch.PROFILES["codex"].matcher == "Bash|shell"

    assert ch.PROFILES["windsurf"].event == "pre_run_command"
    assert ch.PROFILES["windsurf"].command_path == ("tool_info",
                                                    "command_line")


def test_the_host_label_is_what_reaches_the_firing_log():
    """`host` on the row is the value `log_firing` records, so a reader can
    attribute liveness per host. Two rows sharing a label would make the
    per-host column unmeasurable, which is the thing this field exists for."""
    labels = [p.host for p in ch.PROFILES.values()]
    assert len(labels) == len(set(labels))
    assert set(labels) == set(ch.PROFILES)


# ---- the mode table (spec section 5), cell by cell ------------------------


@pytest.mark.parametrize("host,intent,mode", [
    ("claude-code", "enforce", "enforce"),
    ("claude-code", "warn", "warn"),
    ("claude-code", "record-only", "record-only"),
    ("codex", "enforce", "enforce"),
    # Codex documents no warn channel. Degrading to record-only is the whole
    # point of the cap: a warn that silently vanishes reads as a check that
    # never fired.
    ("codex", "warn", "record-only"),
    ("codex", "record-only", "record-only"),
    # The whole Windsurf column stays unsupported until a live probe. An
    # enforce intent shows unsupported there, never registered.
    ("windsurf", "enforce", "unsupported"),
    ("windsurf", "warn", "unsupported"),
    ("windsurf", "record-only", "unsupported"),
])
def test_the_mode_table_cell_by_cell(host, intent, mode):
    assert ch.mode_for(ch.PROFILES[host], intent) == mode


@pytest.mark.parametrize("host", ["claude-code", "codex", "windsurf"])
@pytest.mark.parametrize("intent", ["enforce", "warn", "record-only"])
def test_a_mode_is_never_stronger_than_the_intent_that_asked(host, intent):
    """The property behind the table: actual mode is the WEAKER of what the
    author asked for and what the host can deliver. A cell that came out
    stronger would have daimon enforcing something nobody armed."""
    rank = ch.MODES.index
    assert rank(ch.mode_for(ch.PROFILES[host], intent)) <= rank(intent)


@pytest.mark.parametrize("intent", ["", "ENFORCE", "block", None, 7, "deny"])
def test_an_unknown_intent_is_never_stronger_than_record_only(intent):
    """A manifest is a file on disk and can carry an intent this build never
    heard of. Reading it as the weakest real mode keeps an unknown word from
    becoming an enforcement, and keeps it from crashing the hook."""
    for host in ch.PROFILES:
        mode = ch.mode_for(ch.PROFILES[host], intent)
        assert mode in ch.MODES
        assert ch.MODES.index(mode) <= ch.MODES.index("record-only")


def test_modes_run_weakest_to_strongest():
    """The order every aggregation and cap reads. Written down once so a
    comparison cannot be spelled two ways."""
    assert ch.MODES == ("unsupported", "record-only", "warn", "enforce")
    assert ch.INTENTS == ("enforce", "warn", "record-only")


def test_mode_for_survives_a_profile_that_is_not_one():
    """`main` catches everything, but a function that fires before every
    shell action earns its own answer rather than relying on the net."""
    assert ch.mode_for(None, "enforce") == "unsupported"
    assert ch.mode_for(object(), "warn") == "unsupported"


# ---- encoders (spec section 4), byte for byte ----------------------------

REASON = "voice gate: no em-dash in a public body"


def test_the_json_deny_is_byte_exact():
    """A literal, not a parsed dict. The host reads these key names and a
    rename is a deny that silently becomes an allow: a parsed comparison
    would pass on a dict that spelled every key differently."""
    out = ch.encode(ch.PROFILES["claude-code"], "deny", REASON)
    assert out.stdout == (
        '{"hookSpecificOutput": {"hookEventName": "PreToolUse", '
        '"permissionDecision": "deny", "permissionDecisionReason": '
        '"voice gate: no em-dash in a public body"}}')
    assert out.stderr == ""
    assert out.exit_code == 0


def test_the_json_warn_is_an_allow_carrying_a_message():
    """Byte-exact for the same reason. A warn that came out as a deny is an
    action blocked by a check nobody armed to block."""
    out = ch.encode(ch.PROFILES["claude-code"], "warn", REASON)
    assert out.stdout == (
        '{"hookSpecificOutput": {"hookEventName": "PreToolUse", '
        '"permissionDecision": "allow"}, "systemMessage": '
        '"voice gate: no em-dash in a public body"}')
    assert out.stderr == ""
    assert out.exit_code == 0


def test_a_silent_allow_writes_nothing_at_all():
    """Not an empty JSON object: the hosts treat absent output as no opinion,
    and an object claiming a decision is a decision daimon did not make."""
    for host in ("claude-code", "codex"):
        out = ch.encode(ch.PROFILES[host], "allow", REASON)
        assert out.stdout == ""
        assert out.stderr == ""
        assert out.exit_code == 0


def test_a_warn_with_nothing_to_say_is_a_silent_allow():
    out = ch.encode(ch.PROFILES["claude-code"], "warn", "")
    assert out.stdout == ""


def test_the_event_name_comes_from_the_profile_not_a_literal():
    """The proof that the encoder is shared rather than copied per host: two
    profiles, one function, and the event name is read off the row."""
    import json as _json
    for host in ("claude-code", "codex"):
        data = _json.loads(ch.encode(ch.PROFILES[host], "deny", REASON).stdout)
        assert (data["hookSpecificOutput"]["hookEventName"]
                == ch.PROFILES[host].event)


@pytest.mark.parametrize("host", ["claude-code", "codex"])
@pytest.mark.parametrize("decision", ["deny", "warn", "allow"])
def test_the_json_encoder_writes_one_line_and_exits_zero(host, decision):
    """One JSON object or nothing, on one line, with no trailing text. The
    hosts parse stdout, so a second line or a stray newline is a decision
    they cannot read."""
    out = ch.encode(ch.PROFILES[host], decision, REASON)
    assert out.exit_code == 0
    assert out.stderr == ""
    assert "\n" not in out.stdout
    if out.stdout:
        import json as _json
        _json.loads(out.stdout)


def test_a_non_ascii_reason_survives_as_escaped_ascii():
    """A check's stderr is arbitrary text. Escaping keeps the payload
    readable on a host whose pipe is not UTF-8 without losing the reason."""
    import json as _json
    out = ch.encode(ch.PROFILES["claude-code"], "deny", "café ✅")
    assert out.stdout.isascii()
    data = _json.loads(out.stdout)
    assert (data["hookSpecificOutput"]["permissionDecisionReason"]
            == "café ✅")


def test_the_windsurf_encoder_uses_the_documented_exit_two_channel():
    """The row is complete so the profile is a row and not a special case.
    With every mode unsupported nothing in the pipeline reaches a deny here,
    which is why the shape is pinned by a test rather than by a live host."""
    out = ch.encode(ch.PROFILES["windsurf"], "deny", REASON)
    assert out.stdout == ""
    assert out.stderr == REASON + "\n"
    assert out.exit_code == 2

    warn = ch.encode(ch.PROFILES["windsurf"], "warn", REASON)
    assert warn.exit_code == 0
    assert warn.stderr == REASON + "\n"

    allow = ch.encode(ch.PROFILES["windsurf"], "allow", REASON)
    assert (allow.stdout, allow.stderr, allow.exit_code) == ("", "", 0)


def test_an_unknown_decision_word_is_a_silent_allow():
    """Never a raise and never a deny. A word this build does not know must
    not become the strongest thing the host can do."""
    for host in ch.PROFILES:
        out = ch.encode(ch.PROFILES[host], "block", REASON)
        assert (out.stdout, out.stderr, out.exit_code) == ("", "", 0)


def test_an_unknown_encoder_name_is_a_silent_allow():
    broken = ch.PROFILES["claude-code"]._replace(encoder="telepathy")
    out = ch.encode(broken, "deny", REASON)
    assert (out.stdout, out.stderr, out.exit_code) == ("", "", 0)


def test_the_decision_vocabulary_is_what_the_firing_log_records():
    assert ch.DECISIONS == ("allow", "warn", "deny")


# ---- decide: one pipeline, driven by a manifest the ledger wrote ----------
#
# Every manifest below is written by `checks.sync` from a ruling ratified
# through `refutations` on a human channel. A hand-written manifest would
# test the adapter against a shape nobody produces, and the seam this slice
# adds is exactly the one between what the ledger writes and what the hook
# reads.

import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402

from daimon_briefing import refutations  # noqa: E402

CLEAN = "#!/bin/sh\nexit 0\n"
VIOLATION = "#!/bin/sh\necho 'no em-dash in a public body' >&2\nexit 1\n"
CRASHES = "#!/bin/sh\nexit 3\n"
MATCH = "gh pr create"


def _sha(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _arm(project, *, body=VIOLATION, match=MATCH, intent="warn",
         subject="public posts", scope="publishing"):
    """Arm one check the way a human does: an agent proposes, a human
    ratifies through an in-process human channel, and the ratify syncs."""
    ruling_id = refutations.assert_ruling(
        subject=subject, verdict=f"the rule for {subject} in {scope}",
        scope=scope, evidence=["issue:943"], channel="cli-agent",
        check={"match": match, "body": body, "intent": intent},
        project_dir=str(project))
    refutations.ratify(ruling_id, channel="ui", check_sha256=_sha(body),
                       project_dir=str(project))
    return ruling_id


def _payload(command, cwd, tool="Bash"):
    return {"tool_name": tool, "tool_input": {"command": command},
            "cwd": str(cwd)}


CC = "claude-code"


def test_a_tool_that_is_not_a_shell_action_is_a_silent_allow(tmp_path):
    """No row either: nothing ran and nothing declined to run. A row here
    would report a firing on every file edit in the session."""
    _arm(tmp_path, intent="enforce")
    d = ch.decide(ch.PROFILES[CC],
                  {"tool_name": "Edit", "tool_input": {"command": MATCH},
                   "cwd": str(tmp_path)})
    assert (d.stdout, d.stderr, d.exit_code, d.rows) == ("", "", 0, [])


@pytest.mark.parametrize("payload", [
    {},
    {"tool_name": "Bash", "cwd": "."},
    {"tool_name": "Bash", "tool_input": {}, "cwd": "."},
    {"tool_name": "Bash", "tool_input": {"command": None}, "cwd": "."},
    {"tool_name": "Bash", "tool_input": {"command": 7}, "cwd": "."},
    {"tool_name": "Bash", "tool_input": "gh pr create", "cwd": "."},
    {"tool_name": "Bash", "tool_input": {"command": ""}, "cwd": "."},
])
def test_a_payload_without_a_command_is_a_silent_allow_and_no_row(payload,
                                                                  tmp_path):
    """Scar 0068 generalised: a host payload field is a CLAIM. A missing or
    non-string command is an action daimon cannot see, which is an allow with
    nothing to record, never a crash."""
    _arm(tmp_path, intent="enforce")
    payload = dict(payload)
    if "cwd" in payload:
        payload["cwd"] = str(tmp_path)
    d = ch.decide(ch.PROFILES[CC], payload)
    assert (d.stdout, d.exit_code, d.rows) == ("", 0, [])


def test_no_manifest_allows_and_records_one_row_saying_so(tmp_path):
    """Spec 3.1: the manifest is the wired signal, and "nothing is armed
    here" has to be countable so the stats surface can say "armed, never
    fired" instead of "clean"."""
    d = ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path))
    assert d.stdout == ""
    assert len(d.rows) == 1
    row = d.rows[0]
    assert row["cause"] == "no-manifest"
    assert row["decision_emitted"] == "allow"
    assert row["host"] == "claude-code"
    assert row["ruling_id"] == ""
    assert row["outcome"] == ""


def test_an_unreadable_manifest_is_its_own_cause(tmp_path):
    """An install that armed nothing and a manifest daimon wrote and can no
    longer parse are different facts; folding them reports the first as the
    second."""
    from daimon_briefing import config
    base = config.checks_dir()
    base.mkdir(parents=True, exist_ok=True)
    (base / "manifest.json").write_text("{not json", encoding="utf-8")
    d = ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path))
    assert d.stdout == ""
    assert [r["cause"] for r in d.rows] == ["manifest-unreadable"]


def test_a_manifest_that_arms_another_project_is_no_match(tmp_path):
    """The prefix test is the tenancy boundary. A ruling made for one project
    must not govern the next directory over, and the row says the action was
    seen and nothing here was armed for it."""
    other = tmp_path / "other"
    here = tmp_path / "here"
    other.mkdir()
    here.mkdir()
    _arm(other, intent="enforce")
    d = ch.decide(ch.PROFILES[CC], _payload(MATCH, here))
    assert d.stdout == ""
    assert [r["cause"] for r in d.rows] == ["no-match"]
    assert d.rows[0]["decision_emitted"] == "allow"


def test_an_armed_check_whose_pattern_misses_records_nothing(tmp_path):
    """The prefilter is the whole reason a hook on every Bash call is
    affordable. A row per non-matching command would make the firing log a
    transcript of the session, and spec 3.1 names only the two project-level
    causes."""
    _arm(tmp_path, intent="enforce")
    d = ch.decide(ch.PROFILES[CC], _payload("ls -la", tmp_path))
    assert (d.stdout, d.rows) == ("", [])


def test_a_clean_check_is_a_silent_allow_with_a_row(tmp_path):
    ruling_id = _arm(tmp_path, body=CLEAN, intent="enforce")
    d = ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path))
    assert (d.stdout, d.stderr, d.exit_code) == ("", "", 0)
    assert len(d.rows) == 1
    row = d.rows[0]
    assert row["ruling_id"] == ruling_id
    assert row["outcome"] == "clean"
    assert row["cause"] == ""
    assert row["mode"] == "enforce"
    assert row["decision_emitted"] == "allow"
    assert row["duration_ms"] >= 0


def test_a_violation_under_enforce_denies_and_names_the_ruling(tmp_path):
    ruling_id = _arm(tmp_path, intent="enforce")
    d = ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path))
    data = json.loads(d.stdout)
    out = data["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert out["permissionDecisionReason"] == \
        f"{ruling_id}: no em-dash in a public body"
    assert d.exit_code == 0
    assert d.rows[0]["mode"] == "enforce"
    assert d.rows[0]["outcome"] == "violation"
    assert d.rows[0]["decision_emitted"] == "deny"


def test_the_same_violation_under_warn_allows_and_says_so(tmp_path):
    ruling_id = _arm(tmp_path, intent="warn")
    d = ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path))
    data = json.loads(d.stdout)
    assert data["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert data["systemMessage"] == f"{ruling_id}: no em-dash in a public body"
    assert d.rows[0]["mode"] == "warn"
    assert d.rows[0]["decision_emitted"] == "warn"


def test_the_same_violation_under_record_only_is_silent(tmp_path):
    """The run still happened and the log still says so. Record-only is the
    mode where the operator learns from the log rather than from the host."""
    _arm(tmp_path, intent="record-only")
    d = ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path))
    assert d.stdout == ""
    assert d.rows[0]["outcome"] == "violation"
    assert d.rows[0]["mode"] == "record-only"
    assert d.rows[0]["decision_emitted"] == "allow"


def test_the_codex_cap_turns_the_same_warn_into_a_silent_record(tmp_path):
    """One ruling, one manifest, two hosts. The only thing that differs is
    the row, which is the claim this slice makes: Codex documents no warn
    channel, so a warn there is recorded and not shown."""
    _arm(tmp_path, intent="warn")
    payload = _payload(MATCH, tmp_path, tool="shell")
    cc = ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path))
    cx = ch.decide(ch.PROFILES["codex"], payload)
    assert cc.rows[0]["mode"] == "warn" and cc.stdout != ""
    assert cx.rows[0]["mode"] == "record-only" and cx.stdout == ""
    assert cx.rows[0]["host"] == "codex"
    assert cx.rows[0]["decision_emitted"] == "allow"


def test_an_enforce_check_on_windsurf_records_and_never_denies(tmp_path):
    """The unsupported column, end to end. An enforce intent shows up as a
    logged run and no decision at all, which is the honest reading of a host
    whose block path has never been measured."""
    _arm(tmp_path, intent="enforce")
    d = ch.decide(ch.PROFILES["windsurf"],
                  {"tool_info": {"command_line": MATCH}, "cwd": str(tmp_path)})
    assert (d.stdout, d.stderr, d.exit_code) == ("", "", 0)
    assert d.rows[0]["mode"] == "unsupported"
    assert d.rows[0]["outcome"] == "violation"
    assert d.rows[0]["decision_emitted"] == "allow"


def test_an_unresolved_subject_denies_under_enforce_and_names_the_cause(
        tmp_path):
    """Spec 2.3 and the 2026-09-05 decision: unresolved is never rendered as
    clean. daimon could not read what the action sends, so under enforce it
    cannot let it through."""
    ruling_id = _arm(tmp_path, body=CLEAN, intent="enforce")
    command = f"{MATCH} --body-file missing.md"
    d = ch.decide(ch.PROFILES[CC], _payload(command, tmp_path))
    reason = json.loads(d.stdout)["hookSpecificOutput"][
        "permissionDecisionReason"]
    assert reason.startswith(f"{ruling_id}: file-missing: ")
    assert "missing.md" in reason
    assert d.rows[0]["outcome"] == "unresolved"
    assert d.rows[0]["cause"] == "file-missing"


def test_a_check_that_crashes_is_unresolved_and_not_a_violation(tmp_path):
    _arm(tmp_path, body=CRASHES, intent="enforce")
    d = ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path))
    assert d.rows[0]["outcome"] == "unresolved"
    assert d.rows[0]["cause"] == "check-crashed"
    assert json.loads(d.stdout)["hookSpecificOutput"][
        "permissionDecision"] == "deny"


def test_two_matching_checks_aggregate_to_the_strongest_failing_mode(tmp_path):
    """The deny lists both, because the human fixing this needs the whole
    picture in the one message the host will show."""
    hard = _arm(tmp_path, intent="enforce", subject="public posts",
                scope="publishing")
    soft = _arm(tmp_path, intent="warn", subject="release notes",
                scope="publishing")
    d = ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path))
    reason = json.loads(d.stdout)["hookSpecificOutput"][
        "permissionDecisionReason"]
    assert sorted(reason.splitlines()) == sorted([
        f"{hard}: no em-dash in a public body",
        f"{soft}: no em-dash in a public body"])
    assert {r["mode"] for r in d.rows} == {"enforce", "warn"}
    # One action, one decision: every row records the decision the hook
    # actually emitted, not the one its own mode would have produced alone.
    assert {r["decision_emitted"] for r in d.rows} == {"deny"}


def test_a_clean_enforce_check_does_not_deny_for_a_warn_neighbour(tmp_path):
    """The strongest FAILING mode decides, not the strongest mode present. An
    enforce check that passed has nothing to say about a warn check that
    did not, and reading it the other way blocks actions nobody armed."""
    _arm(tmp_path, body=CLEAN, intent="enforce", subject="public posts",
         scope="publishing")
    soft = _arm(tmp_path, intent="warn", subject="release notes",
                scope="publishing")
    d = ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path))
    data = json.loads(d.stdout)
    assert data["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert data["systemMessage"] == f"{soft}: no em-dash in a public body"


def test_the_subject_is_resolved_once_for_every_matching_check(tmp_path,
                                                              monkeypatch):
    """Resolving reads every file argument off disk. Doing it per entry pays
    that cost again for a subject that cannot have changed, inside a budget
    the host will not extend."""
    _arm(tmp_path, body=CLEAN, intent="warn", subject="a", scope="publishing")
    _arm(tmp_path, body=CLEAN, intent="warn", subject="b", scope="publishing")
    rt_mod = ch.runtime()
    calls = []
    real = rt_mod.resolve
    monkeypatch.setattr(rt_mod, "resolve",
                        lambda *a, **k: (calls.append(a), real(*a, **k))[1])
    d = ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path))
    assert len(d.rows) == 2
    assert len(calls) == 1


def test_the_subject_file_is_discarded_even_when_the_runner_explodes(
        tmp_path, monkeypatch):
    """The temp subject holds the command and every file it named. A run that
    raises must not leave it behind, and `finally` is the only placement that
    survives an exception nobody predicted."""
    _arm(tmp_path, body=CLEAN, intent="warn")
    rt_mod = ch.runtime()
    seen = []
    monkeypatch.setattr(rt_mod, "run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    real_discard = rt_mod.discard
    monkeypatch.setattr(rt_mod, "discard",
                        lambda s: (seen.append(getattr(s, "path", "")),
                                   real_discard(s))[1])
    d = ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path))
    assert seen and seen[0]
    assert not os.path.exists(seen[0])
    assert d.stdout == ""


@pytest.mark.parametrize("name", ["load_manifest", "armed_for", "matches",
                                  "resolve", "run", "discard"])
def test_decide_never_raises_whatever_the_runtime_does(tmp_path, monkeypatch,
                                                       name):
    """The one promise this module makes. It fires before every shell action,
    and on the hosts measured so far a hook that crashes is fail-open: the
    action proceeds and nothing anywhere says why."""
    _arm(tmp_path, body=CLEAN, intent="enforce")
    rt_mod = ch.runtime()
    monkeypatch.setattr(rt_mod, name,
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError(name)))
    d = ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path))
    assert d.exit_code == 0
    assert d.stdout == "" or json.loads(d.stdout)
    assert isinstance(d.rows, list)


def test_a_payload_with_no_cwd_falls_back_to_the_process_directory(tmp_path,
                                                                   monkeypatch):
    """The host is supposed to send it. When it does not, the process the
    host launched is standing in the action's directory anyway, and guessing
    nothing would disarm every check for that action in silence."""
    _arm(tmp_path, body=CLEAN, intent="enforce")
    monkeypatch.chdir(tmp_path)
    d = ch.decide(ch.PROFILES[CC],
                  {"tool_name": "Bash", "tool_input": {"command": MATCH}})
    assert [r["outcome"] for r in d.rows] == ["clean"]


def test_every_row_of_one_action_shares_one_timestamp(tmp_path):
    _arm(tmp_path, body=CLEAN, intent="warn", subject="a", scope="publishing")
    _arm(tmp_path, body=CLEAN, intent="warn", subject="b", scope="publishing")
    d = ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path),
                  now="2026-09-06T00:00:00Z")
    assert {r["ts"] for r in d.rows} == {"2026-09-06T00:00:00Z"}


def test_every_row_carries_exactly_the_declared_firing_keys(tmp_path):
    """`log_firing` projects onto FIRING_KEYS, so a row with a stray field
    loses it silently. Building the row to the declared shape here is what
    keeps the two ends one declaration."""
    _arm(tmp_path, body=CLEAN, intent="warn")
    d = ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path))
    for row in d.rows:
        assert set(row) == set(ch.runtime().FIRING_KEYS)
        assert row["cause"] in ch.runtime().LOG_CAUSES or row["cause"] == ""


def test_the_runner_budget_comes_from_the_configured_timeout(tmp_path,
                                                             monkeypatch):
    """The mirror added for this slice, actually reached. A hook running on
    the runtime's bare default would ignore an operator who lowered the
    budget to fit a slow machine."""
    _arm(tmp_path, body=CLEAN, intent="warn")
    rt_mod = ch.runtime()
    seen = {}
    real = rt_mod.run
    monkeypatch.setattr(rt_mod, "run",
                        lambda *a, **k: (seen.update(k), real(*a, **k))[1])
    monkeypatch.setenv("DAIMON_CHECK_TIMEOUT", "1.5")
    ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path))
    assert seen["timeout"] == 1.5
    ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path), timeout=0.75)
    assert seen["timeout"] == 0.75


def test_a_missing_runtime_decides_nothing_and_says_nothing(tmp_path,
                                                            monkeypatch):
    """A stale install: this module shipped, its sibling did not. There is
    nothing to write a row WITH, so the caller owns the diagnostic."""
    monkeypatch.setattr(ch, "_RUNTIME", None)
    d = ch.decide(ch.PROFILES[CC], _payload(MATCH, tmp_path))
    assert (d.stdout, d.stderr, d.exit_code, d.rows) == ("", "", 0, [])
