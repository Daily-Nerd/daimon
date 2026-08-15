"""`daimon audit` verbs — quote and privacy audits (#708 move).

Shared helpers that remain in the package `__init__` are reached through the
module object (`_cli.<name>`).
"""

import json

import daimon_briefing.cli as _cli

from .. import (
    config,
    privacy,
    provenance,
    render,
    serializer,
    store,
    transcript,
)


def _load_audit_transcript(tpath):
    """Parse one transcript file into the (haystack, texts_by_id) pair
    audit-quotes checks quotes against — the one place #503's per-session
    cache calls into transcript.from_file, so it is charged once per session
    no matter how many items or checkpoints resolve to it."""
    msgs = transcript.from_file(tpath)
    # #440: the same stripped haystacks verify_quotes used at serialize time.
    # Audit re-blesses stored items, so reading the raw render here would
    # certify exactly the echo verification rejected — and do it with the
    # CLI's authority. #512: daimon-output tool rows blank here too, for the
    # same reason and by the same rule.
    haystack = serializer.stripped_transcript(msgs)
    daimon_ids = serializer.daimon_output_ids(msgs)
    texts_by_id = {
        mid: "" if mid in daimon_ids else serializer.strip_injected(text)
        for mid, text in serializer.message_texts_by_id(msgs).items()}
    return haystack, texts_by_id

def _legacy_audit_source(session_id, author=None):
    """Explicitly inferred pre-#594 Claude candidate, never a bound receipt."""
    if not provenance.valid_session_id(session_id):
        return None
    source = {
        "version": provenance.SOURCE_REF_VERSION,
        "host": "claude-code",
        "session_id": session_id,
        "locator": "managed",
    }
    if isinstance(author, str) and author.strip():
        source["author"] = author.strip()
    return source

def _audit_item_source(item):
    """Return (source, receipt) without containing-checkpoint fallback."""
    receipt = item.get("quote_provenance")
    if provenance.valid_quote_receipt(receipt):
        return receipt["source"], receipt
    origin = item.get("origin_session")
    if not provenance.valid_session_id(origin):
        return None, None
    origin_cp = store.read_checkpoint(origin)
    if isinstance(origin_cp, dict):
        source = origin_cp.get("source_ref")
        if provenance.valid_source_ref(source):
            return source, None
    return _legacy_audit_source(origin, item.get("origin_author")), None

def _resolve_audit_source(source, resolver, cache: dict):
    """Resolve and parse one strict source once per complete source identity."""
    if not provenance.valid_source_ref(source):
        return None
    key = json.dumps(source, sort_keys=True, separators=(",", ":"))
    if key in cache:
        return cache[key]
    result = None
    resolved = resolver.resolve(source)
    if resolved.state == "resolved" and resolved.path is not None:
        try:
            result = _load_audit_transcript(resolved.path)
        except (OSError, FileNotFoundError):
            result = None
    cache[key] = result
    return result

def _cmd_audit_privacy(args) -> int:
    """Read-only tombstone residue audit — proves forget's contract instead
    of trusting it (#583: a passing test once asserted the residue). Exit 0
    proven clean / 1 residue / 3 cannot-prove; 3 exists because "could not
    check" must never look like "all clean"."""
    if getattr(args, "all_projects", False):
        results = privacy.audit_all()
    else:
        results = [privacy.audit_project(_cli._resolve_project(args.project))]
    render.render_privacy_audit(results)
    code = privacy.exit_code(results)
    # One tag per OUTCOME: "the auditor ran" and "the auditor found residue"
    # answer different questions, and folding them loses the only number that
    # says whether the deletion contract holds in the field.
    _cli._note_usage({1: "audit-privacy:residue",
                 3: "audit-privacy:unproven"}.get(code, "audit-privacy"))
    return code

def _cmd_audit_quotes_deprecated(args) -> int:
    print("note: 'daimon audit-quotes' is deprecated — use 'daimon audit quotes'")
    return _cmd_audit_quotes(args)

def _cmd_audit_quotes(args) -> int:
    """Read-only audit (#125): re-check every stored verbatim quote against its
    source transcript with the SAME tier-f matcher serialize uses, and REPORT.

    #594: a valid item receipt is authoritative for source identity and message
    binding. Legacy origin_session may form an explicitly inferred Claude
    candidate, but an absent/unresolved source NEVER falls back to the
    containing checkpoint: exact-text carry proves origin, quote evidence, and
    containing session can be three different facts.

    Never rewrites a trust tag — a blind backfill would flip a large share of
    the historical corpus. Measured (#503): the overwhelming majority of
    failures on an unpatched checker were exactly this resolution bug, not
    quotes crossing content the current renderer no longer emits — that class
    is a small minority. Default scope is the current project; --all spans
    the whole corpus."""
    project = _cli._resolve_project(args.project)
    want_slug = store.project_slug(project)
    d = config.checkpoint_dir()
    try:
        files = store._session_files(d)
    except OSError:
        files = []
    scanned = paired = unpaired = items = verified = failed = id_resolved = 0
    origin_resolved = 0
    transcripts: dict = {}
    resolver = provenance.SourceResolver(
        claude_projects=config.claude_projects_dir(),
        current_author=config.author())
    failures: list[tuple[str, str]] = []
    for f in sorted(files):
        try:
            cp = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(cp, dict):
            continue
        slug = cp.get("project_slug")
        if not args.all and want_slug is not None and slug != want_slug:
            continue
        scanned += 1
        session_id = str(cp.get("session_id") or f.stem)
        own_source = cp.get("source_ref")
        if not provenance.valid_source_ref(own_source):
            own_source = _legacy_audit_source(session_id, cp.get("author"))
        own = _resolve_audit_source(own_source, resolver, transcripts)
        if own is not None:
            paired += 1
        else:
            unpaired += 1
        for item in serializer.iter_items(cp):
            if item.get("trust") != "verbatim":
                continue
            quote = item.get("quote")
            if not isinstance(quote, str) or not quote.strip():
                continue
            source, receipt = _audit_item_source(item)
            resolved = _resolve_audit_source(source, resolver, transcripts)
            if resolved is None:
                continue
            haystack, texts_by_id = resolved
            items += 1
            if source.get("session_id") != session_id:
                origin_resolved += 1
            # #358: an item bound to source message id(s) resolves the id and
            # compares bytes against just that message. Missing/invalid ids
            # (old checkpoints, moved/truncated transcripts) fall back to the
            # whole-transcript scan — the pre-#358 verdict, byte-identical.
            checked_item = item
            if receipt is not None:
                checked_item = dict(item)
                ids = provenance.binding_message_ids(receipt)
                if ids:
                    checked_item[serializer.SOURCE_IDS_KEY] = ids
                else:
                    checked_item.pop(serializer.SOURCE_IDS_KEY, None)
            scoped = serializer.scoped_haystack(checked_item, texts_by_id)
            if scoped is not None and serializer.quote_matches(quote, scoped):
                verified += 1
                id_resolved += 1
            elif serializer.quote_matches(quote, haystack):
                verified += 1
            else:
                failed += 1
                failures.append((session_id, str(item.get("text") or "")))
    rate = (verified / items) if items else 0.0
    scope = "all projects" if args.all else project
    lines = [
        f"audit-quotes ({scope})",
        f"  checkpoints scanned: {scanned}  paired: {paired}  unpaired: {unpaired}",
        f"  verbatim quotes checked: {items}  verified: {verified}  "
        f"failed: {failed}  id-resolved: {id_resolved}  "
        f"origin-resolved: {origin_resolved}  rate: {rate:.1%}",
    ]
    if failures:
        top = max(0, args.top)
        lines.append(f"  top {min(top, len(failures))} failures (item text prefix):")
        for sid, text in failures[:top]:
            lines.append(f"    [{sid}] {text[:80]}")
    print("\n".join(lines))
    # #504: the only read-side verification verb recorded nothing, so there was
    # no evidence either way about whether anyone reaches for it. The unpaired
    # variant is a distinct event, not a detail of this one: a run that resolved
    # no transcript verified nothing, and it is also how a host whose
    # transcripts live outside the registered resolver's reach shows up at all.
    # #503: keyed on ANY resolved transcript, not on `paired`. Once resolution
    # is per item, a checkpoint whose own transcript is gone still verifies its
    # carried items through origin_session — `paired` counts containing
    # checkpoints and would report that run as silence.
    resolved_any = any(v is not None for v in transcripts.values())
    _cli._note_usage("audit-quotes" if resolved_any else "audit-quotes:unpaired")
    return 0


def register(sub, fmt) -> None:
    """Register the `audit` parser family on the top-level subparsers."""
    p_audit = sub.add_parser(
        "audit",
        help="read-only auditors that verify stored guarantees",
    )
    audit_sub = p_audit.add_subparsers(dest="audit_cmd", required=True)
    pa_quotes = audit_sub.add_parser(
        "quotes",
        help="re-check stored verbatim quotes against their source transcripts "
             "and report mismatches (read-only, never rewrites tags, #125)",
        epilog="Examples:\n"
               "  daimon audit quotes\n"
               "  daimon audit quotes --all --top 20\n",
    )
    pa_quotes.add_argument(
        "--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pa_quotes.add_argument(
        "--all", action="store_true",
        help="audit every project's checkpoints, not just the current one")
    pa_quotes.add_argument(
        "--top", type=int, default=10,
        help="how many failing quotes to list (default: 10)")
    pa_quotes.set_defaults(func=_cli._cmd_audit_quotes)
    pa_priv = audit_sub.add_parser(
        "privacy",
        help="prove no forgotten value's plaintext survives on any surface "
             "(read-only; exit 0 clean, 1 residue, 3 cannot-prove)",
        epilog="Examples:\n"
               "  daimon audit privacy\n"
               "  daimon audit privacy --all\n",
    )
    # Mutually exclusive: --project scopes to ONE bucket and --all audits every
    # bucket, so together one of them is silently ignored — and the flag that
    # loses decides which tombstone sets were checked. Fail loud instead.
    pa_priv_scope = pa_priv.add_mutually_exclusive_group()
    pa_priv_scope.add_argument(
        "--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pa_priv_scope.add_argument(
        "--all", action="store_true", dest="all_projects",
        help="audit every local project, each against its own tombstone set")
    pa_priv.set_defaults(func=_cli._cmd_audit_privacy)
    # Deprecated flat alias (#504-era name). metavar on the top-level
    # subparsers (set below) keeps it out of the usage brace list.
    p_audit_old = sub.add_parser("audit-quotes")
    p_audit_old.add_argument("--project")
    p_audit_old.add_argument("--all", action="store_true")
    p_audit_old.add_argument("--top", type=int, default=10)
    p_audit_old.set_defaults(func=_cli._cmd_audit_quotes_deprecated)
