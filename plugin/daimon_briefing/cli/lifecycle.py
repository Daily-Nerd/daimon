"""Item lifecycle verbs — `resolve`, `forget`, `reverify`, and `loops` (#708 move).

Shared helpers that remain in the package `__init__` are reached through the
module object (`_cli.<name>`) so the `cli.<name>` seam tests and hosts patch
keeps working on moved code.
"""

import sys

import daimon_briefing.cli as _cli

from .. import (
    amendments,
    anchor,
    briefing,
    carry,
    config,
    normalize,
    refutations,
    relations,
    render,
    requests,
    serializer,
    store,
)


def _cmd_resolve(args) -> int:
    """Append a resolution event for ONE checkpoint item (#102). Exact id
    first; else a fuzzy query that must match uniquely — an ambiguous bind
    is refused with candidates listed, because a wrong bind silently
    suppresses a live memory (the false-merge lesson, #13).

    `--dry-run` runs this SAME bind and stops right before the event write
    (#304) — the matcher a preview would use is otherwise inherently
    different from the one doing the write (recall: FTS5 over history
    across checkpoints; resolve: carry._same_item over this checkpoint
    only), and a preview that disagrees with the writer is worse than none.
    Ambiguous/no-match refusals return before this point either way, so
    their output is identical with or without the flag.

    #480 slice 2 — the agent write path: `--by agent --evidence "<quote>"`
    appends a `resolving-candidate` event, `source="agent"`, instead of the
    human path's immediate `resolved`/`source="cli"`. Evidence is mandatory
    with `--by agent` (mirrors #103 reverify's evidence gate on the reopen
    side) — a call without it is refused before any checkpoint I/O, nothing
    written, logged as `resolve:no-evidence` (extends #303). `--evidence`
    without `--by agent` is rejected too: it is not a `--note` synonym on
    the human path. The bind (exact id, unique fuzzy match, ambiguous/
    no-match refusal, --dry-run) is identical on both paths — only what gets
    written at the end differs. `resolving-candidate` is deliberately NOT
    'resolved': store.is_resolved/_tie_rank exempt it exactly as they exempt
    #14's supersede-candidate, so an agent's claim never withholds the item
    on its own (the #13 false-merge lesson) until slice 3's serializer
    verifies the quote or a human confirms."""
    by_agent = getattr(args, "by", None) == "agent"
    raw_evidence = getattr(args, "evidence", None)
    if raw_evidence is not None and not by_agent:
        print("--evidence requires --by agent — the human path vouches by "
              "calling resolve directly, no evidence needed")
        return 1
    evidence = (raw_evidence or "").strip()
    if by_agent and not evidence:
        # Same #303 stance as the ambiguous/no-match refusals below: a
        # refused attempt must leave its own trace, so "no agent ever tried"
        # and "an agent tried and was refused for lacking evidence" stay
        # distinguishable in `daimon stats`.
        _cli._note_usage("resolve:no-evidence")
        print("--by agent requires --evidence \"<verbatim transcript quote>\" "
              "— refused, nothing written")
        return 1
    project = _cli._resolve_project(args.project)
    checkpoint = store.read_latest(project_dir=project, fallback=False)
    if not isinstance(checkpoint, dict):
        print("no checkpoint for this project yet — nothing to resolve")
        return 1
    items = []
    for section, key in store._ITEM_LISTS:
        for item in ((checkpoint.get(section) or {}).get(key) or []):
            if isinstance(item, dict) and item.get("id"):
                items.append((key, item))
    target = next((it for _, it in items if it["id"] == args.target), None)
    if target is None:
        texts = [str(it.get("text") or "") for _, it in items]
        generic = carry._generic_terms(texts)
        hits = [(key, it) for key, it in items
                if carry._same_item(args.target, str(it.get("text") or ""), generic)]
        if len(hits) == 1:
            target = hits[0][1]
        else:
            # #303: a refused attempt must leave its own trace — otherwise
            # "no agent ever tried resolve" and "an agent tried and was
            # refused" are indistinguishable in `daimon stats`, and the two
            # have opposite fixes (teaching vs UX).
            _cli._note_usage("resolve:no-match" if not hits else "resolve:ambiguous")
            label = "no item matches" if not hits else "ambiguous — matches"
            print(f"{label} {args.target!r}; candidates:")
            listing = hits or items
            for key, it in listing:
                print(f"  {it['id']}  [{key}] {it.get('text', '')}")
            print("resolve by exact id: daimon resolve <id>")
            return 1
    # #480 slice 2: the agent path writes a candidate status, credited to
    # source="agent" — NOT args.status/"cli", which stay exactly what the
    # human path has always written (byte-identical human behavior).
    effective_status = "resolving-candidate" if by_agent else args.status
    if getattr(args, "dry_run", False):
        # A distinct tag, not "resolve": nothing was written, so folding this
        # into the success counter would inflate it with attempts that never
        # touched the ledger — corrupting the exact refusal-rate signal #303
        # exists to expose. Not silent either: the tag still shows up in
        # `daimon stats` usage counts, just apart from resolve/resolve:*.
        _cli._note_usage("resolve:dry-run")
        render.render_lifecycle_lines(
            [f"would resolve {target['id']}: {target.get('text', '')} [{effective_status}]"])
        return 0
    ok = store.append_event(
        target["id"], effective_status,
        note=(evidence if by_agent else (args.note or "")),
        source=("agent" if by_agent else "cli"),
        project_dir=project, item_text=str(target.get("text") or ""))
    if not ok:
        print("event not written (daimon disabled or project unknown)")
        return 1
    if by_agent:
        _cli._note_usage("resolve:agent")
        render.render_lifecycle_lines(
            [f"claim recorded {target['id']}: {target.get('text', '')} "
             "— pending verification at session end"])
        return 0
    _cli._note_usage("resolve")
    render.render_lifecycle_lines(
        [f"resolved {target['id']}: {target.get('text', '')} [{args.status}]"])
    return 0

def _cmd_forget(args) -> int:
    """Deliberate item removal (#321): append a tombstone event whose status
    carries a content HASH, never the text — removal means the content leaves
    the audit trail too — then rewrite the live checkpoint without the value.
    Tombstone first (#418): the rewrite's _drop_forgotten reads the ledger, so
    the key must land before the write scrubs sibling ids of the same value.
    Binding is _cmd_resolve's never-guess contract verbatim: exact id
    first, else a fuzzy query that must match exactly one item. The tombstone
    rides the resolutions fold, so withhold, carry suppression, and the
    recall index deletion all inherit it with no new plumbing. The rewritten
    checkpoint re-mints its receipt, so the post-removal state is signed."""
    project = _cli._resolve_project(args.project)
    checkpoint = store.read_latest(project_dir=project, fallback=False)
    # #578: the refutation ledger is a second plaintext store, so a value can
    # live there with no checkpoint at all. Bailing on a missing checkpoint
    # would leave that value permanently unreachable.
    # Every plaintext value the record has EVER carried, not only the folded
    # ones: a revision rewrites fields, so the forgotten value can sit in an
    # earlier row that nothing renders. #698: the field walk is the module's
    # OWN declaration (plaintext_values, scalars only — a shared anchor or
    # evidence token must never become a by-value target), never a hand-read
    # `subject`. One entry per record, so a record whose old and new values
    # both match is one hit; `text` stays the latest subject for display.
    ledger_texts: dict[str, list[str]] = {}
    ledger_display: dict[str, str] = {}
    for row in refutations.events(project_dir=project):
        ref_id = str(row.get("refutation_id") or "")
        if not ref_id:
            continue
        for value in refutations.plaintext_values(row):
            ledger_texts.setdefault(ref_id, [])
            if value not in ledger_texts[ref_id]:
                ledger_texts[ref_id].append(value)
        subject = str(row.get("subject") or "")
        if subject:
            ledger_display[ref_id] = subject
    # #693: the ledger holds BOTH polarities; every forget surface (pool
    # label, dry-run, gate, receipt) names a ruling as a ruling. Snapshot
    # taken BEFORE any deletion.
    ledger_meta = {
        rid: (rec.get("polarity") or "refutation", rec.get("state"))
        for rid, rec in refutations.records(project_dir=project).items()}
    ledger = [
        (None, ledger_meta.get(ref_id, ("refutation", None))[0],
         {"id": ref_id,
          "text": ledger_display.get(ref_id, texts[0]),
          "_texts": texts})
        for ref_id, texts in ledger_texts.items()
    ]
    # #691: the amendment ledger is another plaintext store — evidence quotes
    # and human notes. Same every-row posture as the refutation subjects
    # above: a value can sit in any historical row of a record. The field
    # walk is the module's OWN declaration (plaintext_values), never a
    # hand-copied tuple, and `text` is the FIRST value (evidence before
    # note) — the content hash downstream must never key on a note when the
    # user named the evidence. `_target` carries the amended item's id so
    # the fuzzy branch can drop amendment hits that merely orbit an item
    # the query already matched.
    amend_texts: dict[str, list[str]] = {}
    amend_targets: dict[str, str] = {}
    for row in amendments.events(project_dir=project):
        # events() admits only rows whose amendment_id matched the id shape,
        # so the key is present and non-empty by contract.
        a_id = str(row["amendment_id"])
        target_id = str(row.get("item_id") or "")
        if target_id:
            amend_targets.setdefault(a_id, target_id)
        for value in amendments.plaintext_values(row):
            amend_texts.setdefault(a_id, [])
            if value not in amend_texts[a_id]:
                amend_texts[a_id].append(value)
    amend_pool = [
        (None, "amendment", {"id": a_id, "text": texts[0], "_texts": texts,
                             "_target": amend_targets.get(a_id, "")})
        for a_id, texts in amend_texts.items()
    ]
    # #694: the request ledger is the fifth plaintext store — an ask, its
    # rationale, a verdict note, a completion quote. Same every-row posture
    # as the two pools above (a revision rewrites `ask`, so the forgotten
    # wording can sit in a row nothing renders), and the field walk is again
    # the module's OWN declaration. No `_target`: a request names a PROJECT,
    # never a checkpoint item, so there is no orbiting-hit class to drop.
    request_texts: dict[str, list[str]] = {}
    for row in requests.events(project_dir=project):
        # events() admits only rows whose request_id matched the id shape.
        q_id = str(row["request_id"])
        for value in requests.plaintext_values(row):
            request_texts.setdefault(q_id, [])
            if value not in request_texts[q_id]:
                request_texts[q_id].append(value)
    request_pool = [
        (None, "request", {"id": q_id, "text": texts[0], "_texts": texts})
        for q_id, texts in request_texts.items()
    ]
    if (not isinstance(checkpoint, dict) and not ledger and not amend_pool
            and not request_pool):
        print("no checkpoint for this project yet — nothing to forget")
        return 1
    # Every surface this project holds, not just the live checkpoint (#419
    # scope): a value that had been superseded still sits in prev-N and in its
    # session file, and resolving only against `latest` made forget answer "no
    # item matches" about plaintext that was demonstrably on disk.
    seen_ids: set[str] = set()
    items = []
    for _path, section, key, item in store.items_for_project(project):
        if item["id"] in seen_ids:
            continue          # one item, several surfaces — not several items
        seen_ids.add(item["id"])
        items.append((section, key, item))
    # Refutation ids and checkpoint `recent_decisions` ids SHARE the namespace
    # `r-<12 hex>`, so an exact-id lookup can legitimately hit both surfaces.
    # forget's never-guess contract decides it: an ambiguous id is refused, not
    # resolved by preferring a store.
    candidates = items + ledger + amend_pool + request_pool
    exact = [it for _, _, it in candidates if it["id"] == args.target]
    target = exact[0] if len(exact) == 1 else None
    if target is None:
        # `_generic_terms` is a DOCUMENT-FREQUENCY statistic over texts "of one
        # kind" (carry.py's own wording): terms shared by >= _GENERIC_DF of them
        # are that kind's boilerplate and are subtracted from the matcher.
        # Counting the checkpoint and the ledger together mixes two corpora, so
        # recording refutations inflated the frequency until the query's own
        # terms read as generic — and `forget "<text>"` stopped reaching a
        # checkpoint item it had deleted a moment earlier, reporting "no item
        # matches" about plaintext on disk. Each store is counted on its own
        # and each candidate matched against its own store's vocabulary.
        def _texts_of(it):
            return it.get("_texts") or [str(it.get("text") or "")]

        # #698 review: document frequency is per CANDIDATE, never per field.
        # A record's subject/verdict/scope restate each other by construction,
        # so counting them as separate documents lets one record push its own
        # terms over the generic threshold and hide itself from its exact
        # subject. One concatenated document per candidate keeps the statistic
        # meaning what carry.py says it means: terms shared by >= k ITEMS.
        pools = [(pool, carry._generic_terms(
            [" ".join(_texts_of(it)) for _, _, it in pool]))
            for pool in (items, ledger, amend_pool, request_pool)]
        query_key = normalize.content_key(args.target)

        def _matched_value(it, generic):
            # The value the QUERY named. The exact canonical match is a rail
            # the GENERIC FILTER can never subtract, no matter how common the
            # value's terms have become (#698 review) — the never-guess
            # ambiguity gate below may still refuse it when several values
            # match. The fuzzy fallback matches each value against the
            # candidate's own store vocabulary.
            for t in _texts_of(it):
                if normalize.content_key(t) == query_key:
                    return t
            for t in _texts_of(it):
                if carry._same_item(args.target, t, generic):
                    return t
            return None

        # Hits carry the value they matched IN the tuple — a side table keyed
        # on object identity would silently degrade to display text if a
        # refactor ever copied a candidate dict (#698 review).
        if len(exact) > 1:
            hits = [(s, k, it, None) for s, k, it in candidates
                    if it["id"] == args.target]
        else:
            hits = [(s, k, it, value)
                    for pool, generic in pools
                    for s, k, it in pool
                    for value in [_matched_value(it, generic)]
                    if value is not None]
        # #691: an amendment's evidence is by construction ABOUT its item, so
        # a query matching the item routinely fuzzy-matches the amendment too
        # — a false-ambiguity surface, not a real second value. When an item
        # hit exists, amendment hits that merely target one of the hit items
        # are dropped: forgetting the item removes its amendments anyway
        # (forget_item_id below), so nothing becomes unreachable.
        item_hit_ids = {it["id"] for _, k, it, _ in hits
                        if k not in ("refutation", "amendment", "request")}
        if item_hit_ids:
            hits = [(s, k, it, m) for s, k, it, m in hits
                    if not (k == "amendment"
                            and it.get("_target") in item_hit_ids)]
        # Ambiguity is about distinct MATCHED values, not hit count and never
        # the display text. The same sentence carried by sibling ids, held on
        # several surfaces, or held in both the checkpoint and the refutation
        # ledger is one thing to forget; #418 already splices sibling ids from
        # a single key. But two records matched on DIFFERENT values behind one
        # shared display subject are two things — collapsing them over the
        # display text silently under-deleted (#698 review). Only genuinely
        # different matched values leave the user a choice, and there
        # never-guess still refuses.
        distinct = {normalize.content_key(
            m if m is not None else str(it.get("text") or ""))
            for _, _, it, m in hits}
        if len(distinct) == 1:
            _, _, target, matched = hits[0]
            # #691: a record can hold several values (evidence, note,
            # historical rows). The tombstone must key on the value the QUERY
            # named, never on an arbitrary field — rebind `text` to the
            # matched value before the hash below derives from it.
            if matched is not None and matched != target.get("text"):
                target = dict(target, text=matched)
        else:
            _cli._note_usage("forget:no-match" if not hits else "forget:ambiguous")
            label = "no item matches" if not hits else "ambiguous — matches"
            print(f"{label} {args.target!r}; candidates:")
            # A never-guess refusal is only useful if the user can make the
            # choice: when the matched value differs from the display text,
            # show it, or two candidates separated by their verdicts render
            # as identical lines (#698 review).
            rows = hits or [(s, k, it, None) for s, k, it in candidates]
            for _, key, it, m in rows:
                line = f"  {it['id']}  [{key}] {it.get('text', '')}"
                if m is not None and m != it.get("text"):
                    line += f" — matched: {m}"
                print(line)
            print("forget by exact id: daimon forget <id>")
            return 1
    if getattr(args, "dry_run", False):
        _cli._note_usage("forget:dry-run")
        # #698 review: forget is irreversible and this preview is the only
        # pre-deletion check. The deleters reach EVERY record whose any
        # declared field folds to the value's key — including list fields the
        # selector deliberately never offers — so the preview enumerates that
        # reach non-destructively instead of naming only the selected target.
        value_key = normalize.content_key(str(target.get("text") or ""))
        # The checkpoint splice takes every item holding the value, whatever
        # its id — and amendments die with their item BY ID, carrying prose
        # that is not the forgotten value, so both must be previewed too
        # (#698 review). Same computation as the destructive path below,
        # against the in-memory checkpoint, writing nothing — INCLUDING the
        # seed: a target superseded out of the live checkpoint (or ledger-
        # only) never appears in the walk, but its own id still reaches the
        # amendments keyed on it.
        spliced = {str(target["id"] or "")}
        if isinstance(checkpoint, dict):
            for section, key in store._ITEM_LISTS:
                for i in (checkpoint.get(section) or {}).get(key) or []:
                    if isinstance(i, dict) and (
                            i.get("id") == target["id"]
                            or normalize.content_key(i.get("text") or "")
                            == value_key):
                        spliced.add(str(i.get("id") or ""))
        spliced.discard("")
        ref_reach = sorted({str(row.get("refutation_id") or "")
                            for row in refutations.events(project_dir=project)
                            if value_key in refutations.row_content_keys(row)})
        amend_reach = sorted({
            str(row.get("amendment_id") or "")
            for row in amendments.events(project_dir=project)
            if value_key in amendments.row_content_keys(row)
            or str(row.get("item_id") or "") in spliced})
        request_reach = sorted({
            str(row.get("request_id") or "")
            for row in requests.events(project_dir=project)
            if value_key in requests.row_content_keys(row)})
        preview = [f"would forget {target['id']}: {target.get('text', '')}"]
        active_rulings = sorted(
            rid for rid in set(ref_reach) | {str(target["id"])}
            if ledger_meta.get(rid) == ("ruling", "active"))
        if active_rulings:
            preview.append("WARNING — this removes ACTIVE ruling(s): "
                           + ", ".join(active_rulings))
        sibling_ids = sorted(spliced - {str(target["id"])})
        if sibling_ids:
            preview.append("also removed — checkpoint siblings holding the "
                           "value: " + ", ".join(sibling_ids))
        also = [rid for rid in ref_reach + amend_reach + request_reach
                if rid and rid != target["id"]]
        if also:
            preview.append("also removed — ledger records reached by value "
                           "or by doomed item id: " + ", ".join(also))
        render.render_lifecycle_lines(preview)
        return 0
    # #402: key the tombstone on the CANONICAL value (normalize.content_key),
    # not the raw bytes — so a later re-extraction of the same claim (different
    # case, invisible chars, a look-alike glyph) folds to the same key and is
    # suppressed at capture. Still a hash, never the text: removal means the
    # content leaves the audit trail too (#321).
    content_hash = normalize.content_key(target.get("text") or "")
    # #693: forget takes no --by and asks no confirmation, so without this
    # gate it is an agent path that un-renders a human-ratified standing
    # constraint — the one thing no agent path may do. A human at a terminal
    # proceeds (deletion stays the user's call, #421 posture unchanged); a
    # non-interactive caller is pointed at the human.
    doomed_rulings = sorted(
        rid for rid in ({
            str(row.get("refutation_id") or "")
            for row in refutations.events(project_dir=project)
            if content_hash in refutations.row_content_keys(row)}
            | {str(target["id"])})
        if ledger_meta.get(rid) == ("ruling", "active"))
    if doomed_rulings:
        if not sys.stdin.isatty():
            print("refused: this would remove ACTIVE ruling(s) "
                  + ", ".join(doomed_rulings)
                  + " — a human decision. Run it from a terminal, or ask the "
                  "user; `daimon ruling retire` records the verdict instead.")
            return 1
        # Deleting what renders is strictly more power than rewriting it,
        # and rewriting confirms. A human can still say y (#421 unchanged).
        render.render_lifecycle_lines(
            ["WARNING — this removes ACTIVE ruling(s): "
             + ", ".join(doomed_rulings)])
        answer = input("Remove? [y/N]: ").strip().casefold()
        if answer not in ("y", "yes"):
            print("not removed")
            return 1
    sid = str((checkpoint or {}).get("session_id") or "")
    # A checkpoint-bearing project still needs its session id to rewrite. A
    # ledger-only value has no checkpoint to rewrite, so the missing id is not
    # an error there.
    if isinstance(checkpoint, dict) and not sid:
        print("checkpoint has no session_id — cannot rewrite")
        return 1
    # Tombstone BEFORE the rewrite (#418): write_checkpoint's forget gate
    # consults the ledger during the write — appended after, the new key is
    # invisible to that scrub and sibling ids carrying the same value survive.
    # Failing here leaves the checkpoint untouched: no half-removal without an
    # audit-trail record. allow_disabled (#421): forget is the ratified
    # deletion exemption to the kill switch — the tombstone (and the rewrite
    # below, which #418 chains to it) must land even while daimon is disabled.
    ok = store.append_event(target["id"], f"forgotten:{content_hash}",
                            note=args.reason or "", kind="tombstone",
                            project_dir=project, allow_disabled=True)
    if not ok:
        print("tombstone event not written (project unknown or ledger unwritable)")
        return 1
    # Splice by VALUE, not only id (#418): one value can hold sibling ids —
    # the same sentence in two sections, or a widened hash within one
    # (store._stamp_item_ids). Removal is content removal, so every item
    # folding to the tombstoned key goes. Id kept in the predicate as a belt
    # for non-string text, which content_key canonicalizes to "".
    spliced_ids = {str(target["id"] or "")}
    if isinstance(checkpoint, dict):
        for section, key in store._ITEM_LISTS:
            lst = (checkpoint.get(section) or {}).get(key)
            if isinstance(lst, list):
                doomed = [i for i in lst
                          if isinstance(i, dict)
                          and (i.get("id") == target["id"]
                               or normalize.content_key(i.get("text") or "")
                               == content_hash)]
                # #691: every spliced sibling id, not only the named target —
                # amendments are keyed by item id, and an amendment about a
                # sibling holding the same value is plaintext ABOUT the
                # forgotten content (the #418 sibling rule, extended to the
                # ledger that references the siblings).
                spliced_ids.update(str(i.get("id") or "") for i in doomed)
                lst[:] = [i for i in lst if i not in doomed]
        # allow_disabled (#421): the ONE write_checkpoint call that may run under
        # the kill switch — the rewrite that makes the deletion real on disk.
        # rotate=False: rotation copies the CURRENT latest into prev-1 before
        # writing, and the current latest is the PRE-forget bytes. Rotating here
        # made the deletion manufacture a fresh copy of the value it was asked to
        # remove, in a file that did not exist when the user ran the command.
        store.write_checkpoint(sid, checkpoint, project_dir=project,
                               allow_disabled=True, rotate=False)
    # The live checkpoint is one surface of several. prev-N and superseded
    # session files hold the same plaintext and were never in the contract
    # (#419: plaintext is what puts a file inside it, not its role). Runs even
    # with no live checkpoint: a ledger-only project still has these surfaces.
    store.scrub_content_key(content_hash, project_dir=project)
    # #600 slice A: the author's own team-mirror copies are plaintext this
    # machine owns (#419) — scrubbed here; teammates' copies and upstream
    # git history are the sync protocol's to converge (tombstone
    # propagation), not a local rewrite's.
    team_scrubbed = store.scrub_team_copies(content_hash,
                                            project_dir=project)
    # #600 slice B: publish the deletion itself (hash only) so teammates can
    # suppress the value without waiting to pull the scrubbed file — and so
    # a copy THEY extracted independently can be acted on at all.
    store.publish_tombstone(content_hash, project_dir=project)
    # #599: rows appended BEFORE this forget can carry the value in
    # `item_text`/`status`/`note` — redacted in place, rows never dropped
    # (the one ratified rewrite of the append-only ledger).
    events_scrubbed = store.scrub_event_fields(content_hash,
                                               project_dir=project)
    # #578: same value, second plaintext store. The ledger splices on the SAME
    # canonical key for the same reason the checkpoint splices on it rather than
    # on the id — removal is content removal, so a refutation asserting the
    # forgotten value goes with it whether or not it was the named target.
    forgotten_refutations = refutations.forget_content_key(
        content_hash, project_dir=project)
    # #678 fork A: relation rows hold no text, but an edge touching this item
    # is an equivalence CLAIM about its content (an `exact-text` rail against
    # a surviving twin re-derives what the value was), and post-forget the
    # relations ledger would be the only surface binding the forgotten id to
    # its sessions, kind, and revision-chain length. The scrub is id-keyed —
    # the tombstone above landed on this exact id — and the audit's
    # relations-ledger scan is what proves it reached the edges.
    forgotten_relations = relations.forget_item_id(
        target["id"], project_dir=project)
    # #691: same value, another plaintext store — and unlike relations, amend
    # rows DO carry prose, so records targeting a forgotten item (or any of
    # its spliced siblings) go with it — their evidence may paraphrase the
    # removed content — and records holding the value in any plaintext field
    # go regardless of target.
    spliced_ids.discard("")
    forgotten_amendment_set = set(
        amendments.forget_content_key(content_hash, project_dir=project))
    for doomed_id in sorted(spliced_ids):
        forgotten_amendment_set.update(
            amendments.forget_item_id(doomed_id, project_dir=project))
    forgotten_amendments = sorted(forgotten_amendment_set)
    # #694: the fifth plaintext store, and the only one whose prose was
    # written FOR another project. Value-keyed only — a request names a
    # project, never a checkpoint item, so there is no target-id sweep to
    # pair with this one. Nothing enforces this dispatch; the surface
    # registry declares the deleter but cannot call it, which is why the
    # deletion contract for a new ledger is a hand-wired line here.
    forgotten_requests = requests.forget_content_key(content_hash,
                                                     project_dir=project)
    # #422: the serializer chunk cache holds PRE-redaction extraction output
    # (quote verification forbids redacting before caching, #125), keyed by
    # chunk text — the forgotten value cannot be located selectively, so the
    # purge is WHOLESALE and default-on. Never fatal: the belief-state
    # deletion above is the primary contract; a failed purge is reported
    # honestly below, and the age reaper still bounds any survivor at
    # chunk_cache_days.
    try:
        purged, purge_err = serializer.purge_chunk_cache()
    except Exception as e:  # belt: purge_chunk_cache itself never raises
        purged, purge_err = 0, str(e)
    # #607: the Windsurf adapter writes its own transcripts when Cascade
    # gives it none — daimon-authored plaintext, so inside the contract.
    # Wholesale for the same reason as the chunk cache: the tombstone is a
    # hash, so a value inside prose cannot be located to remove selectively.
    try:
        ws_purged, ws_err = store.purge_windsurf_state()
    except Exception as e:  # belt: purge_windsurf_state never raises
        ws_purged, ws_err = 0, str(e)
    # #605: serialize-crash.log is the detached child's RAW stderr — an
    # uncaught traceback carries whatever the crashing frame held, and the
    # bytes were never scrubbed (status redacted its tail on READ, #513, over
    # a file nothing deleted). Wholesale like the two purges above: the
    # tombstone is a hash, so a value inside a traceback cannot be located.
    try:
        crash_purged, crash_err = store.purge_crash_log()
    except Exception as e:  # belt: purge_crash_log never raises
        crash_purged, crash_err = 0, str(e)
    # #616: backend-stderr.log holds backend stderr/stdout, which CLI
    # backends can seed with transcript text (#141). Wholesale like the
    # crash sink: prose diagnostics, hash tombstone, value unlocatable.
    try:
        backend_purged, backend_err = store.purge_backend_stderr_log()
    except Exception as e:  # belt: purge_backend_stderr_log never raises
        backend_purged, backend_err = 0, str(e)
    # #616: pre-fix downgrade lines logged item text into serialize.log.
    # Shape-targeted scrub, NOT a purge — serialize.log is also the ledger
    # `status` parses, and the capture record must survive a forget.
    try:
        scrubbed_lines, scrub_err = store.scrub_serialize_log()
    except Exception as e:  # belt: scrub_serialize_log never raises
        scrubbed_lines, scrub_err = 0, str(e)
    _cli._note_usage("forget")
    surfaces = []
    if isinstance(checkpoint, dict):
        surfaces.append("the live checkpoint")
    if forgotten_refutations:
        gone_rulings = [r for r in forgotten_refutations
                        if ledger_meta.get(r, ("refutation", None))[0]
                        == "ruling"]
        gone_refuts = [r for r in forgotten_refutations
                       if r not in gone_rulings]
        if gone_refuts:
            surfaces.append(f"{len(gone_refuts)} refutation(s) "
                            f"({', '.join(gone_refuts)})")
        if gone_rulings:
            surfaces.append(f"{len(gone_rulings)} ruling(s) "
                            f"({', '.join(gone_rulings)})")
    if forgotten_relations:
        surfaces.append(f"{len(forgotten_relations)} relation(s) "
                        f"({', '.join(forgotten_relations)})")
    if forgotten_amendments:
        surfaces.append(f"{len(forgotten_amendments)} amendment(s) "
                        f"({', '.join(forgotten_amendments)})")
    if forgotten_requests:
        surfaces.append(f"{len(forgotten_requests)} request(s) "
                        f"({', '.join(forgotten_requests)})")
    report = [f"forgot {target['id']} (content hash {content_hash}) — "
              f"removed from {' and '.join(surfaces) or 'no store'}; "
              "tombstone recorded"]
    if team_scrubbed:
        report.append(
            f"scrubbed {len(team_scrubbed)} own team-mirror cop(y/ies) — "
            "run `daimon team sync` to publish; teammates' copies and "
            "upstream git history remain until tombstone propagation")
    if events_scrubbed:
        report.append(f"redacted {events_scrubbed} event-ledger field(s) "
                      "carrying the value (rows kept, field replaced)")
    if purge_err is not None:
        report.append(f"warning: chunk cache purge failed: {purge_err} — "
                      "cached pre-redaction chunks may persist up to "
                      f"{config.chunk_cache_days()} day(s) (age reaper)")
    else:
        report.append(f"purged {purged} cached chunk extraction(s) "
                      "(pre-redaction serializer cache)")
    if ws_err is not None:
        report.append(
            f"warning: windsurf transcript purge failed: {ws_err} — "
            "daimon-authored conversation text may persist up to "
            f"{config.windsurf_state_days()} day(s) (age reaper)")
    else:
        # Always reported, like the chunk-cache line: a silent zero is how a
        # misconfigured store (writer and deleter disagreeing about the
        # directory) stays invisible. The scope note is not decoration —
        # the store is keyed by trajectory and carries no project
        # attribution, so this purge is machine-wide by construction.
        report.append(
            f"purged {ws_purged} daimon-authored windsurf transcript "
            "file(s) across all projects (machine-wide: the store is keyed "
            "by trajectory, not project); host-authored transcripts are "
            "untouched")
    if crash_err is not None:
        report.append(
            f"warning: crash log purge failed: {crash_err} — serializer "
            "tracebacks may persist (bounded to a trimmed tail at the next "
            "spawn, never removed)")
    else:
        # Reported even at zero, like the two lines above: a silent zero is
        # how a writer and a deleter disagreeing about DAIMON_LOG_DIR stays
        # invisible. One crash log per machine, so the scope note is the
        # same honesty the windsurf line owes.
        report.append(
            f"purged {crash_purged} serializer crash log(s) across all "
            "projects (machine-wide: the log is raw child stderr, not "
            "keyed by project)")
    if backend_err is not None:
        report.append(
            f"warning: backend log purge failed: {backend_err} — backend "
            "echo of transcript text may persist (byte-bounded at the "
            "write seam, never removed)")
    else:
        # Same silent-zero rule as the three lines above.
        report.append(
            f"purged {backend_purged} backend stderr log(s) across all "
            "projects (machine-wide: backend diagnostics are not keyed "
            "by project)")
    if scrub_err is not None:
        report.append(
            f"warning: serialize.log scrub failed: {scrub_err} — legacy "
            "downgrade lines may still carry item text")
    else:
        # Reported even at zero, same rule again: a scrubber resolving a
        # different log dir than the writer must not read as "nothing left".
        report.append(
            f"scrubbed {scrubbed_lines} legacy downgrade line(s) in "
            "serialize.log (payload replaced; ledger lines kept)")
    render.render_lifecycle_lines(report)
    return 0

def _is_supersede_candidate(item_id: str, project) -> bool:
    """True when the item's LATEST lifecycle event is a serializer-authored
    supersede-candidate — an unconfirmed machine SUGGESTION (#14) that has
    never withheld anything. This is the one reopen target that needs no
    evidence: rejecting a guess is not vouching for a claim (#111). The
    source gate mirrors `_emit_supersede_candidates` — only the serializer
    ever writes candidates, so a human verdict already latest reads as
    not-a-candidate and keeps the evidence gate. Fails closed: a broken
    events fold means 'not a candidate', so the evidence gate still applies."""
    try:
        prior = store.resolutions(project_dir=project).get(item_id)
    except Exception:
        return False
    if not isinstance(prior, dict):
        return False
    status = str(prior.get("status") or "").lower()
    source = str(prior.get("source") or "")
    return status.startswith("supersede-candidate") and source == "serializer"

def _cmd_reverify(args) -> int:
    """Evidence-gated reopen (#103): re-stamping a resolved item without
    evidence would mark an unchecked claim verified — the one thing this
    tool must never do to its own audit trail. Reopen is allowed only when
    the original anchor still checks out live (the code proved itself) or
    the caller supplies --evidence (a human vouches for it); otherwise the
    refusal appends nothing. Exact id only — no fuzzy match, because
    reopening is a deliberate act; find ids via `daimon status --suppressed`.

    #111 exception: when the target is an unconfirmed supersede CANDIDATE
    (a machine guess that has never withheld anything), reopen is allowed
    with no evidence — rejecting a suggestion is not vouching for a claim.
    The reopened event is a human verdict, so re-detection stays silent."""
    project = _cli._resolve_project(args.project)
    checkpoint = store.read_latest(project_dir=project, fallback=False)
    if not isinstance(checkpoint, dict):
        print("no checkpoint for this project yet — nothing to reverify")
        return 1
    item = None
    for section, key in store._ITEM_LISTS:
        for it in ((checkpoint.get(section) or {}).get(key) or []):
            if isinstance(it, dict) and it.get("id") == args.target:
                item = it
                break
        if item is not None:
            break
    if item is None:
        print(f"no item found with id {args.target!r}")
        return 1
    evidence = args.evidence or ""
    a = item.get("anchored_to")
    if isinstance(a, dict) and anchor.check(a, project) == "live":
        note = "reverified: anchor live"
        if evidence:
            note += "; " + evidence
    elif evidence:
        note = f"evidence: {evidence}"
    elif _is_supersede_candidate(item["id"], project):
        # #111: the target is an unconfirmed machine SUGGESTION, never a
        # withheld/suppressed item — rejecting a guess needs no proof. The
        # reopened event below is a human verdict, so the human-speaks-once
        # gate (#14) silences re-detection of this candidate permanently.
        note = "candidate rejected"
    else:
        print("re-stamping without evidence would mark an unchecked claim "
              "verified — supply --evidence, or fix the anchored code and retry")
        return 1
    ok = store.append_event(item["id"], "reopened", note=note,
                            item_text=item.get("text", ""), project_dir=project)
    if not ok:
        print("event not written (daimon disabled or project unknown)")
        return 1
    # A typed baseline for this verb. Without it reverify's count is
    # structurally zero, and a zero that measures missing instrumentation
    # cannot be compared against a UI channel's count later.
    _cli._note_usage("reverify")
    render.render_lifecycle_lines(
        [f"reopened {item['id']}: {item.get('text', '')}"])
    return 0

def _cmd_loops(args) -> int:
    """`daimon loops` (#480 slice 1): list open, briefable loop items with
    ids for this project — the read-only counterpart to the id handles
    briefing._line now renders inline (an agent, or a human, needs something
    to pass to `daimon resolve`).

    Reuses the SAME item walk `_cmd_resolve` builds (store._ITEM_LISTS over
    the latest checkpoint) and the SAME withhold classification `daimon
    status --suppressed` uses (briefing.withhold over store.resolutions) —
    the resolved/live split must stay in exactly one place, or this listing
    could show an item the briefing itself would withhold (an agent would
    then "resolve" a ghost).

    Briefable = briefing.BRIEFABLE_ITEM_KEYS (open_questions — external and
    non-external both — plus uncertainties). Decisions/beliefs/
    contradictions are valid `daimon resolve` targets too, but are not
    loop-shaped; listing them here would invite resolving settled facts
    (#480 scope guard, mirrors briefing._line's new suffix)."""
    _cli._note_usage("loops")
    project = _cli._resolve_project(args.project)
    checkpoint = store.read_latest(project_dir=project, fallback=False)
    if not isinstance(checkpoint, dict):
        print("no checkpoint for this project yet — nothing to list")
        return 0
    try:
        events = store.resolutions(project_dir=project)
        # #691: amendments ride along so the listing agrees with the
        # briefing — an agent discovering targets here must see that an
        # item is already amended, or its cheapest probe is a duplicate
        # proposal.
        checkpoint, _, _ = briefing.withhold(
            checkpoint, events,
            amendments=amendments.renderable(project_dir=project))
    except Exception:
        pass  # fail-open, same stance as _print_suppressed
    rows = []
    for section, key in store._ITEM_LISTS:
        if key not in briefing.BRIEFABLE_ITEM_KEYS:
            continue
        for item in ((checkpoint.get(section) or {}).get(key) or []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            if item.get("_agent_claim"):
                # #480 slice 4: the listing agrees with the briefing — an
                # item carrying a still-pending, unverified agent claim
                # (briefing.withhold's transient stamp) is marked here too.
                text += " (agent claim pending)"
            amend_stamp = item.get("_amend")
            if isinstance(amend_stamp, dict):
                n = (len(amend_stamp.get("rows") or [])
                     + (amend_stamp.get("overflow") or 0))
                if n:
                    text += f" (amended ×{n})"
            rows.append((item["id"], key, text, briefing._mark(item)))
    if not rows:
        render.render_lifecycle_lines(["no open loops"])
        return 0
    render.render_lifecycle_lines(
        [f"  {item_id}  [{key}] [{mark}] {text}"
         for item_id, key, text, mark in rows])
    return 0


def register(sub, fmt) -> None:
    """Register the `lifecycle` parser family on the top-level subparsers."""
    p_resolve = sub.add_parser(
        "resolve", help="mark a checkpoint item resolved — append-only event, "
        "folds at read so the item stops carrying (#102)",
        epilog="Examples:\n  daimon resolve o-3f8a2c\n"
               "  daimon resolve \"release pipeline approval\" --note \"shipped in 0.9\"\n",
    )
    p_resolve.add_argument("target", help="item id (exact) or a query that must match exactly one item")
    p_resolve.add_argument("--status", default="resolved",
                           help="free-form lifecycle status (default: resolved; "
                                "a status starting with 'reopen' revives the item)")
    p_resolve.add_argument("--note", help="optional context recorded on the event")
    p_resolve.add_argument(
        "--by", choices=["agent"],
        help="declare an agent-initiated claim (requires --evidence); "
             "omit for the human path (default)")
    p_resolve.add_argument(
        "--evidence",
        help="verbatim transcript quote proving the claim — required with "
             "--by agent, rejected without it")
    p_resolve.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    p_resolve.add_argument(
        "--dry-run", action="store_true",
        help="show what would resolve without writing an event — look before the write (#304)")
    p_resolve.set_defaults(func=_cli._cmd_resolve)

    p_forget = sub.add_parser(
        "forget", help="remove ONE item from the live checkpoint and record a "
        "signed-state tombstone — content leaves disk and index; the event "
        "carries only a hash (#321)",
        epilog="Examples:\n  daimon forget o-3f8a2c --reason \"contains client name\"\n"
               "  daimon forget \"wrong belief about retry nonce\" --dry-run\n",
    )
    p_forget.add_argument("target", help="item id (exact) or a query that must match exactly one item")
    p_forget.add_argument("--reason", help="recorded on the tombstone event (redacted like any note)")
    p_forget.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    p_forget.add_argument(
        "--dry-run", action="store_true",
        help="show what would be forgotten without writing — look before a destructive op")
    p_forget.set_defaults(func=_cli._cmd_forget)

    p_reverify = sub.add_parser(
        "reverify", help="evidence-gated reopen of a resolved item (#103) — "
        "refuses without proof, so a claim can't get re-verified for free",
        epilog="Examples:\n"
               "  daimon reverify o-3f8a2c --evidence \"checked release page\"\n"
               "  daimon reverify o-3f8a2c   # reopens only if the anchor still checks live\n"
               "  daimon reverify o-3f8a2c   # rejects an unconfirmed supersede candidate — no evidence needed\n",
    )
    p_reverify.add_argument("target", help="item id (exact only — reverify is deliberate, no fuzzy match)")
    p_reverify.add_argument("--evidence", help="why this claim can be trusted again")
    p_reverify.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    p_reverify.set_defaults(func=_cli._cmd_reverify)

    p_loops = sub.add_parser(
        "loops", help="list open, briefable loop items with ids for this "
        "project (#480) — the read counterpart to daimon resolve's write path",
        epilog="Examples:\n  daimon loops\n  daimon loops --project .\n",
    )
    p_loops.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    p_loops.set_defaults(func=_cli._cmd_loops)
