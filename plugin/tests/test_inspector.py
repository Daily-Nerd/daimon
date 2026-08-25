"""Trust Inspector evidence axes, privacy boundary, and CLI contract (#502)."""

import hashlib
import json
from pathlib import Path

import pytest

from daimon_briefing import (cli, config, inspector, provenance, recall,
                              redact, store, transcript)


_PROJECT = "/p/A"
_ITEM_ID = "o-abcdef"
_CHECKED_AT = "2026-08-05T10:00:00Z"
_GOLDEN = Path(__file__).parent / "golden" / "why_bound.json"


def _source(session_id="S-source", *, host="claude-code", locator="managed"):
    return {
        "version": provenance.SOURCE_REF_VERSION,
        "host": host,
        "session_id": session_id,
        "locator": locator,
        "author": "alice",
    }


def _receipt(source, digest, *, outcome="verified", mode="message-ids",
             message_ids=("u-1",), scope="raw-file"):
    return provenance.quote_receipt(
        source,
        {"algorithm": "sha256", "scope": scope, "value": digest},
        outcome=outcome,
        checked_at=_CHECKED_AT,
        binding_mode=mode,
        message_ids=message_ids,
    )


def _item(text="durable trust decision", *, item_id=_ITEM_ID,
          quote="durable trust decision", receipt=None, **extra):
    value = {
        "id": item_id,
        "text": text,
        "trust": "verbatim" if quote else "inferred",
    }
    if quote:
        value["quote"] = quote
    if receipt is not None:
        value["quote_provenance"] = receipt
        value["quote_verified"] = receipt["outcome"] == "verified"
    value.update(extra)
    return value


def _checkpoint(session_id, items, created="2026-08-05T10:00:00Z"):
    return {
        "session_id": session_id,
        "created": created,
        "author": "alice",
        "working_context": {
            "active_topic": {"text": "trust inspection", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": items,
        },
        "epistemic_snapshot": {
            "strong_beliefs": [],
            "uncertainties": [],
        },
    }


def _write_checkpoint(session_id, items, *, project=_PROJECT,
                      created="2026-08-05T10:00:00Z"):
    checkpoint = _checkpoint(session_id, items, created)
    assert store.write_checkpoint(session_id, checkpoint, project_dir=project)
    return checkpoint


def _write_team_checkpoint(session_id, items, *, project=_PROJECT,
                           author="alice", created="2026-08-05T10:00:00Z"):
    """A checkpoint that exists ONLY in the team mirror (#674) — no local flat
    file, no local bucket — the shape `why`'s own walk (store.project_surfaces)
    structurally cannot reach, but recall's index (team-scan, #111) does."""
    checkpoint = _checkpoint(session_id, items, created)
    checkpoint["author"] = author
    checkpoint["project_slug"] = store.project_slug(project)
    d = config.team_dir() / "local" / "authors" / author
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{session_id}.json").write_text(
        json.dumps(checkpoint), encoding="utf-8")
    return checkpoint


def _write_stampless_pointer_checkpoint(session_id, items, *, project=_PROJECT,
                                        created="2026-08-05T10:00:00Z"):
    """A LOCAL flat checkpoint with no embedded project_slug stamp, attributed
    to `project` only via the per-project bucket pointer (#674) — the exact
    legacy shape recall._bucket_slugs resolves but project_surfaces's
    membership test (physical bucket residence or an OWN stamp) does not."""
    checkpoint = _checkpoint(session_id, items, created)
    d = config.checkpoint_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{session_id}.json").write_text(
        json.dumps(checkpoint), encoding="utf-8")
    slug = store.project_slug(project)
    bucket = d / slug
    bucket.mkdir(parents=True, exist_ok=True)
    (bucket / "latest.json").write_text(
        json.dumps({"session_id": session_id}), encoding="utf-8")
    return checkpoint


def _write_claude(projects_dir, session_id, messages, *, bucket="bucket"):
    path = projects_dir / bucket / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for role, content, message_id in messages:
        rows.append(json.dumps({
            "type": role,
            "uuid": message_id,
            "message": {"role": role, "content": content},
        }))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _resolver(tmp_path, projects_dir):
    return provenance.SourceResolver(
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_projects=projects_dir,
        current_author="alice",
    )


@pytest.mark.parametrize("value, expected", [
    ("o-abcdef", True),
    ("q-0123456789abcdef-2", True),
    ("o-abcde", False),
    ("o-" + "a" * 41, False),
    ("O-abcdef", False),
    ("o-abcdeg", False),
    ("../o-abcdef", False),
])
def test_item_id_validation_is_bounded_and_exact(value, expected):
    assert inspector.valid_item_id(value) is expected


def test_checkpoint_scan_skips_corrupt_and_sessionless_surfaces(
    tmp_checkpoint_dir, monkeypatch
):
    tmp_checkpoint_dir.mkdir(parents=True, exist_ok=True)
    corrupt = tmp_checkpoint_dir / "corrupt.json"
    corrupt.write_text("{not-json", encoding="utf-8")
    sessionless = tmp_checkpoint_dir / "sessionless.json"
    sessionless.write_text(json.dumps({"created": _CHECKED_AT}), encoding="utf-8")
    valid = tmp_checkpoint_dir / "S-valid.json"
    valid.write_text(
        json.dumps(_checkpoint("S-valid", [])), encoding="utf-8")
    monkeypatch.setattr(
        store, "project_surfaces",
        lambda _project: [corrupt, sessionless, valid])

    checkpoints = inspector._project_checkpoints(_PROJECT)

    assert [checkpoint["session_id"] for checkpoint in checkpoints] == ["S-valid"]


def test_bound_receipt_reports_changed_bytes_and_message_support(
    tmp_checkpoint_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    projects = tmp_path / ".claude" / "projects"
    path = _write_claude(projects, "S-source", [
        ("user", "the durable trust decision remains supported", "u-1"),
    ])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = _receipt(_source(), digest)
    _write_checkpoint("S-containing", [
        _item(quote="durable trust decision remains supported", receipt=receipt),
    ])
    _write_claude(projects, "S-source", [
        ("user", "the durable trust decision remains supported after editing", "u-1"),
    ])

    result = inspector.inspect_item(
        _PROJECT, _ITEM_ID, resolver=_resolver(tmp_path, projects))

    assert result["axes"] == {
        "capture": "verified",
        "provenance": "bound",
        "locator": "resolved",
        "bytes": "changed",
        "current_support": "message-id-match",
        "verifier_comparison": "same-version",
        "lifecycle": "active",
    }


def test_axes_keep_unchanged_bytes_separate_from_not_reproduced_quote(
    tmp_checkpoint_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    projects = tmp_path / ".claude" / "projects"
    path = _write_claude(projects, "S-source", [
        ("user", "a completely unrelated message", "u-1"),
    ])
    receipt = _receipt(
        _source(), hashlib.sha256(path.read_bytes()).hexdigest())
    _write_checkpoint("S-containing", [
        _item(quote="durable trust decision is absent", receipt=receipt),
    ])

    result = inspector.inspect_item(
        _PROJECT, _ITEM_ID, resolver=_resolver(tmp_path, projects))

    assert result["axes"]["bytes"] == "unchanged"
    assert result["axes"]["current_support"] == "not-reproduced"
    assert "fabricated" not in "\n".join(inspector.human_lines(result)).lower()
    assert "invalid" not in "\n".join(inspector.human_lines(result)).lower()


def test_rendered_digest_and_older_verifier_are_independent_axes(
    tmp_checkpoint_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    projects = tmp_path / ".claude" / "projects"
    path = _write_claude(projects, "S-source", [
        ("user", "durable trust decision", "u-1"),
    ])
    digest = inspector._rendered_digest(transcript.from_file(path))
    receipt = _receipt(_source(), digest, scope="rendered-transcript")
    receipt["verifier"]["version"] += 1
    _write_checkpoint("S-containing", [_item(receipt=receipt)])

    result = inspector.inspect_item(
        _PROJECT, _ITEM_ID, resolver=_resolver(tmp_path, projects))

    assert result["axes"]["bytes"] == "unchanged"
    assert result["axes"]["current_support"] == "message-id-match"
    assert result["axes"]["verifier_comparison"] == "different-version"


def test_unreadable_rendered_source_degrades_to_unknown(
    tmp_checkpoint_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    projects = tmp_path / ".claude" / "projects"
    _write_claude(projects, "S-source", [
        ("user", "durable trust decision", "u-1"),
    ])
    receipt = _receipt(
        _source(), "a" * 64, scope="rendered-transcript")
    _write_checkpoint("S-containing", [_item(receipt=receipt)])

    def unreadable(_path):
        raise ValueError("malformed host transcript")

    monkeypatch.setattr(transcript, "from_file", unreadable)
    result = inspector.inspect_item(
        _PROJECT, _ITEM_ID, resolver=_resolver(tmp_path, projects))

    assert result["axes"]["locator"] == "resolved"
    assert result["axes"]["bytes"] == "unknown"
    assert result["axes"]["current_support"] == "not-checked"


def test_legacy_inferred_and_unbound_items_degrade_explicitly(
    tmp_checkpoint_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    projects = tmp_path / ".claude" / "projects"
    _write_claude(projects, "S-legacy", [
        ("user", "the legacy quote still appears", "u-legacy"),
    ])
    legacy_id = "o-aaaaaa"
    unbound_id = "o-bbbbbb"
    _write_checkpoint("S-containing", [
        _item(item_id=legacy_id, quote="legacy quote still appears",
              origin_session="S-legacy", origin_author="alice"),
    ])
    # Pre-origin legacy surface: modern write_checkpoint code-owns and stamps
    # origin_session, so a true legacy-unbound fixture must predate that gate.
    unbound_cp = _checkpoint(
        "S-unbound", [_item(item_id=unbound_id, quote="unbound stored quote")],
        created="2026-08-05T09:00:00Z")
    unbound_cp["project_slug"] = store.project_slug(_PROJECT)
    (tmp_checkpoint_dir / "S-unbound.json").write_text(
        json.dumps(unbound_cp), encoding="utf-8")
    resolver = _resolver(tmp_path, projects)

    legacy = inspector.inspect_item(_PROJECT, legacy_id, resolver=resolver)
    unbound = inspector.inspect_item(_PROJECT, unbound_id, resolver=resolver)

    assert legacy["axes"]["provenance"] == "legacy-inferred"
    assert legacy["axes"]["capture"] == "unknown"
    assert legacy["axes"]["locator"] == "resolved"
    assert legacy["axes"]["current_support"] == "transcript-scan-match"
    assert unbound["axes"]["provenance"] == "legacy-unbound"
    assert unbound["axes"]["locator"] == "unsupported"
    assert unbound["axes"]["current_support"] == "not-checked"


def test_legacy_item_prefers_origin_checkpoint_source_ref(
    tmp_checkpoint_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    projects = tmp_path / ".claude" / "projects"
    source = _source("S-raw-origin")
    origin = _checkpoint("S-origin", [])
    origin["source_ref"] = source
    assert store.write_checkpoint("S-origin", origin, project_dir=_PROJECT)
    _write_checkpoint("S-containing", [
        _item(origin_session="S-origin", origin_author="alice"),
    ])

    result = inspector.inspect_item(
        _PROJECT, _ITEM_ID, resolver=_resolver(tmp_path, projects))

    assert result["axes"]["provenance"] == "legacy-inferred"
    assert result["source"] == source
    assert result["axes"]["locator"] == "absent-local"


def test_locator_states_distinguish_absent_unsupported_and_ambiguous(
    tmp_checkpoint_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    projects = tmp_path / ".claude" / "projects"
    absent_id = "o-aaaaaa"
    unsupported_id = "o-bbbbbb"
    ambiguous_id = "o-cccccc"
    digest = "a" * 64
    _write_checkpoint("S-containing", [
        _item(item_id=absent_id,
              receipt=_receipt(_source("S-absent"), digest)),
        _item(item_id=unsupported_id,
              receipt=_receipt(_source("S-gemini", host="gemini",
                                      locator="unsupported"), digest)),
        _item(item_id=ambiguous_id,
              receipt=_receipt(_source("S-duplicate"), digest)),
    ])
    _write_claude(projects, "S-duplicate", [
        ("user", "first", "u-1")], bucket="one")
    _write_claude(projects, "S-duplicate", [
        ("user", "second", "u-2")], bucket="two")
    resolver = _resolver(tmp_path, projects)

    assert inspector.inspect_item(
        _PROJECT, absent_id, resolver=resolver)["axes"]["locator"] == "absent-local"
    assert inspector.inspect_item(
        _PROJECT, unsupported_id, resolver=resolver)["axes"]["locator"] == "unsupported"
    assert inspector.inspect_item(
        _PROJECT, ambiguous_id, resolver=resolver)["axes"]["locator"] == "ambiguous"


def test_lookup_deduplicates_carried_snapshots_and_never_crosses_projects(
    tmp_checkpoint_dir, monkeypatch
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    item_id = "o-abc123"
    _write_checkpoint("S-old", [
        _item("older wording", item_id=item_id, quote=None)],
        created="2026-08-05T09:00:00Z")
    _write_checkpoint("S-new", [
        _item("newest current wording", item_id=item_id, quote=None)],
        created="2026-08-05T11:00:00Z")
    _write_checkpoint("S-other", [
        _item("other project wording", item_id=item_id, quote=None)],
        project="/p/B", created="2026-08-05T12:00:00Z")

    local = inspector.inspect_item(_PROJECT, item_id)
    other = inspector.inspect_item("/p/B", item_id)

    assert local["item"]["text"] == "newest current wording"
    assert local["item"]["occurrences"] == 2
    assert other["item"]["text"] == "other project wording"
    assert other["item"]["occurrences"] == 1


def test_lifecycle_and_corroboration_fold_without_mutating_evidence(
    tmp_checkpoint_dir, monkeypatch
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    _write_checkpoint("S-containing", [
        _item(receipt=_receipt(_source(), "a" * 64)),
    ])
    slug = store.project_slug(_PROJECT)
    events = tmp_checkpoint_dir / slug / "events.jsonl"
    events.write_text("\n".join(json.dumps(row) for row in (
        {"ts": "2026-08-05T10:00:00Z", "kind": "resolution",
         "item_ref": _ITEM_ID, "status": "resolved", "source": "cli"},
        {"ts": "2026-08-05T10:01:00Z", "kind": "resolution",
         "item_ref": store.corroboration_ref(_ITEM_ID),
         "status": "corroborated-by:S-witness-1", "source": "capture"},
        {"ts": "2026-08-05T10:02:00Z", "kind": "resolution",
         "item_ref": store.corroboration_ref(_ITEM_ID),
         "status": "corroborated-by:S-witness-2", "source": "capture"},
    )) + "\n", encoding="utf-8")

    result = inspector.inspect_item(_PROJECT, _ITEM_ID)

    assert result["axes"]["lifecycle"] == "resolved"
    assert result["axes"]["capture"] == "verified"
    assert result["corroboration"] == {
        "count": 2,
        "references": ["S-witness-1", "S-witness-2"],
    }


@pytest.mark.parametrize("status, expected", [
    ("resolved", "resolved"),
    ("superseded-by:o-fedcba", "superseded"),
    ("forgotten:abc", "forgotten"),
    ("reopen", "active"),
    ("resolving-candidate", "active"),
])
def test_lifecycle_axis_values(status, expected):
    assert inspector._lifecycle({"status": status}) == expected


def test_forgotten_event_remains_inspectable_after_plaintext_is_gone(
    tmp_checkpoint_dir, monkeypatch
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    assert store.append_event(
        _ITEM_ID, "forgotten:" + "a" * 64,
        project_dir=_PROJECT, allow_disabled=True)

    result = inspector.inspect_item(_PROJECT, _ITEM_ID)

    assert result["item"]["text"] is None
    assert result["axes"]["lifecycle"] == "forgotten"
    assert result["axes"]["provenance"] == "legacy-unbound"


def test_source_disclosure_is_bounded_redacted_once_and_path_free(
    tmp_checkpoint_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    projects = tmp_path / ".claude" / "projects"
    secret = "sk-proj-" + "A" * 24
    # Put the credential across the old pre-redaction cutoff. Truncating raw
    # text first would leave a secret prefix that no longer matches the token
    # pattern; redacting once before the display cap must remove it whole.
    content = "opening evidence " + "x" * 560 + " " + secret + " closing evidence"
    path = _write_claude(projects, "S-source", [
        ("user", content, "u-1"),
    ])
    receipt = _receipt(
        _source(), hashlib.sha256(path.read_bytes()).hexdigest())
    _write_checkpoint("S-containing", [
        _item(quote="opening evidence ... closing evidence", receipt=receipt),
    ])
    real_redact = redact.redact_text
    calls = []

    def counted(value):
        calls.append(value)
        return real_redact(value)

    monkeypatch.setattr(redact, "redact_text", counted)
    resolver = _resolver(tmp_path, projects)
    default = inspector.inspect_item(_PROJECT, _ITEM_ID, resolver=resolver)
    disclosed = inspector.inspect_item(
        _PROJECT, _ITEM_ID, include_source=True, resolver=resolver)

    assert "source_excerpt" not in default
    assert len(calls) == 1
    excerpt = disclosed["source_excerpt"]
    assert excerpt["kind"] == "message-window"
    assert excerpt["truncated"] is True
    assert secret not in excerpt["text"]
    assert "[redacted:openai-key]" in excerpt["text"]
    assert len(excerpt["text"]) <= inspector._SOURCE_CHAR_LIMIT
    assert str(tmp_path) not in json.dumps(disclosed)


def test_source_disclosure_does_not_redact_stored_quote_twice(
    tmp_checkpoint_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    projects = tmp_path / ".claude" / "projects"
    path = _write_claude(projects, "S-source", [
        ("user", "stored quote evidence", "u-1"),
    ])
    receipt = _receipt(
        _source(), hashlib.sha256(path.read_bytes()).hexdigest(),
        mode="transcript-scan", message_ids=())
    _write_checkpoint("S-containing", [
        _item(quote="stored quote evidence", receipt=receipt),
    ])

    def must_not_run(_value):
        raise AssertionError("stored quote crossed redaction twice")

    monkeypatch.setattr(redact, "redact_text", must_not_run)
    result = inspector.inspect_item(
        _PROJECT, _ITEM_ID, include_source=True,
        resolver=_resolver(tmp_path, projects))

    assert result["source_excerpt"]["kind"] == "stored-quote"
    assert result["source_excerpt"]["text"] == "stored quote evidence"


def test_source_disclosure_caps_message_count_and_reports_unavailable(
    tmp_checkpoint_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    projects = tmp_path / ".claude" / "projects"
    messages = [("user", f"evidence {n}", f"u-{n}") for n in range(1, 5)]
    path = _write_claude(projects, "S-source", messages)
    receipt = _receipt(
        _source(), hashlib.sha256(path.read_bytes()).hexdigest(),
        message_ids=tuple(message_id for _, _, message_id in messages))
    _write_checkpoint("S-containing", [_item(receipt=receipt)])

    disclosed = inspector.inspect_item(
        _PROJECT, _ITEM_ID, include_source=True,
        resolver=_resolver(tmp_path, projects))

    assert disclosed["source_excerpt"]["message_ids"] == ["u-1", "u-2", "u-3"]
    assert disclosed["source_excerpt"]["truncated"] is True

    assert store.append_event(
        "o-fedcba", "forgotten:" + "a" * 64,
        project_dir=_PROJECT, allow_disabled=True)
    unavailable = inspector.inspect_item(
        _PROJECT, "o-fedcba", include_source=True,
        resolver=_resolver(tmp_path, projects))
    assert unavailable["source_excerpt"]["kind"] == "unavailable"
    human = "\n".join(inspector.human_lines(unavailable))
    assert "Source excerpt: (unavailable)" in human
    assert "no bounded source excerpt is available" in human


def test_source_helpers_ignore_malformed_messages_and_cap_plain_text():
    assert inspector._message_by_id([
        None,
        {"id": "u-1", "role": "user", "content": "evidence"},
    ]) == {
        "u-1": {"id": "u-1", "role": "user", "content": "evidence"},
    }

    value = "x" * (inspector._SOURCE_CHAR_LIMIT + 20)
    capped, truncated = inspector._cap_disclosed_source(value)

    assert truncated is True
    assert capped == "x" * (inspector._SOURCE_CHAR_LIMIT - 1) + "…"


def test_why_json_matches_golden_and_command_is_read_only_except_usage(
    tmp_checkpoint_dir, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    monkeypatch.setenv(
        "DAIMON_CLAUDE_PROJECTS_DIR", str(tmp_path / "no-transcripts"))
    _write_checkpoint("S-bound", [
        _item(receipt=_receipt(_source(), "a" * 64)),
    ])
    before = {
        str(path.relative_to(tmp_checkpoint_dir)): path.read_bytes()
        for path in tmp_checkpoint_dir.rglob("*") if path.is_file()
    }

    assert cli.main(["why", _ITEM_ID, "--project", _PROJECT, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    after = {
        str(path.relative_to(tmp_checkpoint_dir)): path.read_bytes()
        for path in tmp_checkpoint_dir.rglob("*") if path.is_file()
    }

    assert payload == json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert "summary" not in payload
    assert before == after
    usage = (tmp_path / ".daimon" / "logs" / "usage.log").read_text(
        encoding="utf-8")
    assert usage.rstrip().endswith(" why")


def test_why_exit_codes_and_human_axes(tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    _write_checkpoint("S-bound", [
        _item(receipt=_receipt(_source(), "a" * 64)),
    ])

    assert cli.main(["why", "bad-id", "--project", _PROJECT]) == 2
    assert "invalid item id" in capsys.readouterr().err
    assert cli.main(["why", "o-fedcba", "--project", _PROJECT]) == 1
    assert "no item" in capsys.readouterr().err
    assert cli.main(["why", _ITEM_ID, "--project", _PROJECT]) == 0
    out = capsys.readouterr().out
    assert "Now: capture verified" in out
    assert "Capture: verified" in out
    assert "Provenance: bound" in out
    assert "Current support: not-checked" in out
    assert cli.main([
        "why", _ITEM_ID, "--project", _PROJECT, "--slug", "p-A",
    ]) == 2


def test_recall_exposes_item_id_in_json_and_human_output(
    tmp_checkpoint_dir, monkeypatch, capsys
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    _write_checkpoint("S-recall", [
        _item("quorint trust inspector decision", quote=None),
    ])

    assert cli.main([
        "recall", "quorint", "--project", _PROJECT, "--json",
    ]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["item_id"] == _ITEM_ID

    assert cli.main(["recall", "quorint", "--project", _PROJECT]) == 0
    assert f"[{_ITEM_ID}]" in capsys.readouterr().out
    # The proactive backend has its own SELECT/output contract; adding ids to
    # deliberate recall must not leak them into injected suggestion rows.
    suggestions = recall.suggest(
        "review the quorint trust inspector decision again",
        project_dir=_PROJECT, current_session="S-now")
    assert suggestions
    assert all("item_id" not in row for row in suggestions)


# ---- #674: why falls back to the recall index when its own walk misses ----


def test_why_answers_a_team_mirror_only_item_from_the_index(
    tmp_checkpoint_dir, monkeypatch
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    _write_team_checkpoint("S-team-only", [_item(quote=None)])

    result = inspector.inspect_item(_PROJECT, _ITEM_ID)

    assert result is not None
    assert result["item"]["text"] == "durable trust decision"
    assert result["item"]["session_id"] == "S-team-only"
    assert result["item"]["occurrences"] == 0
    assert result["axes"]["provenance"] == "legacy-unbound"
    assert result["axes"]["bytes"] == "unknown"
    assert result["axes"]["current_support"] == "not-checked"
    assert result["index_only"]["reason"] == "team-mirror"
    human = "\n".join(inspector.human_lines(result))
    assert "recall index" in human.lower()


def test_why_answers_a_stampless_pointer_attributed_item_from_the_index(
    tmp_checkpoint_dir, monkeypatch
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    _write_stampless_pointer_checkpoint("S-stampless", [_item(quote=None)])
    # Sanity per the investigation: why's own walk sees only the bucket's
    # pointer file (no item content), never the stampless flat file.
    surfaces = store.project_surfaces(_PROJECT)
    assert all(p.name != "S-stampless.json" for p in surfaces)

    result = inspector.inspect_item(_PROJECT, _ITEM_ID)

    assert result is not None
    assert result["item"]["session_id"] == "S-stampless"
    assert result["index_only"]["reason"] == "pointer-attributed-legacy"


def test_why_still_refuses_an_id_in_neither_surfaces_nor_index(
    tmp_checkpoint_dir, monkeypatch, capsys
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    # A populated index for this SAME project, but never for this id — proves
    # the fallback is precise, not "any index activity counts as a hit".
    _write_team_checkpoint("S-other", [_item(item_id="o-other000000", quote=None)])

    assert inspector.inspect_item(_PROJECT, _ITEM_ID) is None
    assert cli.main(["why", _ITEM_ID, "--project", _PROJECT]) == 1
    assert "no item" in capsys.readouterr().err


def test_forgotten_team_only_item_is_not_resurrected(
    tmp_checkpoint_dir, monkeypatch
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    _write_team_checkpoint("S-team-forgotten", [_item(quote=None)])
    assert store.append_event(
        _ITEM_ID, "forgotten:" + "a" * 64,
        project_dir=_PROJECT, allow_disabled=True)

    result = inspector.inspect_item(_PROJECT, _ITEM_ID)

    assert result is not None
    assert result["axes"]["lifecycle"] == "forgotten"
    assert "index_only" not in result
    # The row itself is gone too (recall's own rebuild-time scrub) — the
    # fallback finding nothing is not what protects this; the scrub is.
    hits = recall.search("durable trust", project_dir=_PROJECT, all_projects=True)
    assert not any(h.get("item_id") == _ITEM_ID for h in hits)


def test_local_surface_answer_is_unperturbed_by_the_index_fallback(
    tmp_checkpoint_dir, monkeypatch, tmp_path, capsys
):
    # Parity: an item why already answers from its own surfaces must never
    # gain the new index_only key or otherwise change shape. This mirrors
    # test_why_json_matches_golden_and_command_is_read_only_except_usage,
    # which already asserts full dict equality against the golden file —
    # that test staying green after this change IS the parity proof; this
    # test adds the explicit negative assertion for readability.
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")
    monkeypatch.setenv(
        "DAIMON_CLAUDE_PROJECTS_DIR", str(tmp_path / "no-transcripts"))
    _write_checkpoint("S-bound", [
        _item(receipt=_receipt(_source(), "a" * 64)),
    ])

    result = inspector.inspect_item(_PROJECT, _ITEM_ID)

    assert "index_only" not in result


def test_index_fallback_answers_a_freshly_synced_item_without_manual_rebuild(
    tmp_checkpoint_dir, monkeypatch
):
    monkeypatch.setenv("DAIMON_AUTHOR", "alice")

    assert inspector.inspect_item(_PROJECT, _ITEM_ID) is None

    _write_team_checkpoint("S-fresh", [_item(quote=None)])

    result = inspector.inspect_item(_PROJECT, _ITEM_ID)
    assert result is not None
    assert result["item"]["session_id"] == "S-fresh"
