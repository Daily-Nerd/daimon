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
