import { state } from "./state.js";

let evidenceCounter = 0;
let bioCounter = 0;

// Test-only. IDs derive from these counters, so tests reset them for determinism.
export function resetCounters() {
  evidenceCounter = 0;
  bioCounter = 0;
}

export const ACT_ITEM_ID_RE = /^[a-z]-[0-9a-f]{6,40}(-\d+)?$/;   // mirror of reader ITEM_ID_RE

  export function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  export function fmtRelative(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    var diff = Math.floor((Date.now() - d.getTime()) / 1000);
    if (diff < 60) return "just now";
    var m = Math.floor(diff / 60); if (m < 60) return m + "m ago";
    var h = Math.floor(m / 60); if (h < 24) return h + "h ago";
    var dd = Math.floor(h / 24); if (dd < 30) return dd + "d ago";
    var mo = Math.floor(dd / 30); if (mo < 12) return mo + "mo ago";
    return Math.floor(mo / 12) + "y ago";
  }
  export function fmtDate(iso) {
    if (!iso) return "unknown date";
    return String(iso).split("T")[0];
  }
  // The activity feed is chronological, so the time of day is load-bearing: this
  // project's own feed has 15 rows sharing one second and 25 sharing one date, and
  // a date-only stamp erases every ordering cue. Rendered in UTC (the ledger's own
  // zone) rather than local time — mixing a UTC date with a local clock silently
  // disagrees near midnight, and a pure string read keeps tests timezone-independent.
  // Anything that is not ISO-shaped keeps fmtDate's lenient behavior rather than
  // manufacturing a "00:00" the data never recorded.
  export function fmtDateTime(iso) {
    if (!iso) return "unknown date";
    var m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(String(iso));
    return m ? m[1] + " " + m[2] + "Z" : fmtDate(iso);
  }
  // aria-current value for a header view button. Pure so it is testable without a
  // DOM; app.js does the setAttribute. Returns the literal strings the attribute
  // takes, not booleans, so callers cannot stringify `false` into a truthy "false"
  // by accident. Views with no header button (grid, project) mark nothing current.
  export function navCurrent(view, target) {
    return view === target ? "page" : "false";
  }
  export function activityLabel(r) {
    if (r.kind === "resolution") {
      var st = (r.extra && r.extra.status) || "";
      if (st === "resolved") return "RESOLVED";
      if (st === "reopened") return "REOPENED";
      return st ? escapeHtml(spellOutEnum(st)) : "RESOLUTION";
    }
    var L = { session: "SESSION", handoff: "HANDOFF", corroboration: "CORROBORATED",
              note: "NOTE", quote_check: "QUOTE CHECK" };
    return Object.prototype.hasOwnProperty.call(L, r.kind) ?
      L[r.kind] : escapeHtml(spellOutEnum(String(r.kind)));
  }
  // Unmapped statuses and kinds arrive as machine values ("resolved-agent-verified").
  // Uppercasing alone shipped the punctuation straight to the reader. Only the
  // separators change — the words stay exactly as recorded, so nothing is renamed.
  function spellOutEnum(s) {
    return String(s).replace(/[-_]+/g, " ").toUpperCase();
  }
  // quote_check gets its own dot. It used to share "changed" with ordinary edits,
  // which made 20 recorded check reasons read as routine traffic.
  var ACT_DOTS = { session: "seen", resolution: "resolved", handoff: "born",
                   corroboration: "verified", note: "seen", quote_check: "flagged" };
  export function renderActivityRow(r) {
    var dotCls = "life-dot-" + (Object.prototype.hasOwnProperty.call(ACT_DOTS, r.kind) ?
      ACT_DOTS[r.kind] : "seen");
    var when = escapeHtml(fmtDateTime(r.ts));
    var body = "";
    if (r.kind === "handoff" && r.detail) {
      body = '<div class="life-quote">“' + escapeHtml(r.detail) + '”</div>';
    } else if (r.detail) {
      body = '<div class="life-detail">' + escapeHtml(r.detail) + "</div>";
    }
    // The server sends extra.item_text for some rows and it was being dropped, so
    // rows that could say WHICH item they concern said nothing. Only rendered when
    // it differs from detail — for corroborations the reader already folds item_text
    // into detail, and printing it twice would read as two separate facts.
    var itemText = (r.extra && r.extra.item_text) || "";
    if (itemText && itemText !== r.detail) {
      body += '<div class="life-detail act-item-text">' + escapeHtml(itemText) + "</div>";
    }
    // The ref itself is now printed, not just linked. With only a "View history"
    // button here, twenty rows about twenty different items were indistinguishable.
    var refHtml = "";
    if (r.item_ref) {
      if (ACT_ITEM_ID_RE.test(r.item_ref)) {
        refHtml = '<div class="act-ref"><code class="act-ref-id">' + escapeHtml(r.item_ref) +
          "</code> " + renderHistoryToggle(r.item_ref) + "</div>";
      } else {
        refHtml = '<div class="act-ref act-ref-raw">' + escapeHtml(r.item_ref) + "</div>";
      }
    }
    return '<li class="life-row act-row"><div class="life-rail"><span class="life-dot ' +
      dotCls + '"></span></div><div class="life-body"><span class="life-label">' +
      activityLabel(r) + '</span><span class="life-when">' + when + "</span>" +
      body + refHtml + "</div></li>";
  }
  // Counts quote_check rows by their recorded reason. Deliberately reports only what
  // the ledger literally stores: verification.jsonl carries ts/check/item_ref/reason
  // and no pass-fail verdict, so calling these rows "failures" would be our word, not
  // the data's. What was actually wrong is that twenty of them read as ambient traffic
  // — the scale never reached the viewer. Counting fixes that without interpreting.
  // A null prototype keeps a reason literally named "constructor" from tallying onto
  // an inherited function, the same bug class as activityLabel's.
  export function activityCheckSummary(rows) {
    var counts = Object.create(null);
    var total = 0;
    (rows || []).forEach(function (r) {
      if (!r || r.kind !== "quote_check") return;
      var reason = (r.extra && r.extra.reason) || "no reason recorded";
      counts[reason] = (counts[reason] || 0) + 1;
      total += 1;
    });
    if (total === 0) return null;
    var breakdown = Object.keys(counts).map(function (reason) {
      return { reason: reason, count: counts[reason] };
    });
    // Commonest first; ties broken by name so the order never depends on key insertion.
    breakdown.sort(function (x, y) {
      return y.count - x.count || (x.reason < y.reason ? -1 : x.reason > y.reason ? 1 : 0);
    });
    return { total: total, breakdown: breakdown };
  }
  function renderCheckSummary(rows) {
    var s = activityCheckSummary(rows);
    if (!s) return "";
    var parts = s.breakdown.map(function (b) {
      return b.count + " × " + escapeHtml(b.reason);
    }).join(" · ");
    return '<div class="banner banner-check"><span class="banner-icon" aria-hidden="true">⚠</span><span>' +
      s.total + (s.total === 1 ? " quote check recorded" : " quote checks recorded") +
      ": " + parts + ".</span></div>";
  }
  export function renderActivityView(data) {
    var partial = data.partial || [];
    var partialHtml = partial.map(function (p) {
      return '<p class="life-note">' + escapeHtml(p) + "</p>";
    }).join("");
    if (!data.rows || data.rows.length === 0) {
      var emptyHtml = partial.length > 0 ?
        '<div class="state-card"><p class="state-title">No readable activity</p>' +
          "<p>Some files couldn't be read; nothing readable remains to show.</p></div>" :
        '<div class="state-card"><p class="state-title">No activity recorded</p>' +
          "<p>This project's checkpoints carry no session history and its ledgers are empty.</p></div>";
      return partialHtml + emptyHtml;
    }
    return partialHtml + renderCheckSummary(data.rows) + '<ul class="life act-feed">' +
      data.rows.map(renderActivityRow).join("") + "</ul>";
  }
  // The sidebar lists pointer checkpoints only (latest, prev-N), so it is a window
  // onto the sessions History can reach, not the whole set. Unlabelled, the two
  // views give different counts and neither says why. Silent when nothing is
  // hidden, and silent if total ever arrives smaller than shown — an unreadable
  // file or a racing request must not make the sidebar claim "3 of 2".
  export function renderSidebarScope(shown, total) {
    if (!(total > shown)) return "";
    return '<p class="cp-scope">Showing the ' + shown + ' most recent of ' + total +
      ' sessions. The earlier ones are reachable in History.</p>';
  }

  // A slug is a flattened path, so the true directory name is unrecoverable —
  // the tail segment is a best-effort display name, never an identity.
  export function slugTail(slug) {
    var parts = String(slug || "").split("-").filter(Boolean);
    return parts.length ? parts[parts.length - 1] : String(slug || "");
  }
  export function renderProjCard(p) {
    var isCurrent = p.slug === state.defaultSlug;
    var torn = p.active_topic == null && p.created == null && p.item_count == null;
    var chip = isCurrent ? '<span class="proj-chip">current dir</span>' : "";
    // #672: prefer the write-time stamped name; the slug tail stays only as
    // the fallback for buckets written before the stamp existed.
    var name = '<span class="proj-name">' +
      escapeHtml(p.project_name || slugTail(p.slug)) + "</span>";
    var age = !torn && p.created
      ? '<span class="proj-age">' + escapeHtml(fmtRelative(p.created)) + "</span>" : "";
    var topic = torn ? "Unreadable checkpoint" : (p.active_topic || "(untitled)");
    var foot = torn ? "run daimon heal"
      : [p.item_count != null ? p.item_count + " items" : null]
          .filter(Boolean).join(" · ");
    return '<button type="button" class="proj-card' + (isCurrent ? " current" : "") +
      '" data-slug="' + escapeHtml(p.slug) + '">' +
      '<span class="proj-top">' + name + chip + age + "</span>" +
      '<span class="proj-topic' + (torn || !p.active_topic ? " proj-topic-dim" : "") + '">' +
      escapeHtml(topic) + "</span>" +
      '<span class="proj-foot"><span class="proj-slug">' + escapeHtml(p.slug) +
      "</span>" + (foot ? '<span class="proj-meta">' + escapeHtml(foot) + "</span>" : "") +
      "</span></button>";
  }
  export function renderEmptyProjects() {
    return '<div class="state-card state-empty"><p class="state-title">No daimon projects yet</p>' +
      "<p>Once you run <code>daimon brief</code> in a project directory, it'll appear here as a " +
      "card you can open.</p></div>";
  }
  export function skeletonHtml() {
    return '<div class="skeleton" aria-hidden="true">' +
      '<div class="skel-bar"></div><div class="skel-row"></div>' +
      '<div class="skel-row"></div><div class="skel-row"></div></div>';
  }

  // Plain descriptions of a byte comparison. NOT "verified" — the inspector does
  // not check the signature; `daimon verify-receipt` does. hasOwnProperty guards
  // the lookup so an inherited key like "constructor" cannot fabricate a label.
  //
  // The `unsigned` KEY and its "no receipt claimed" STRING differ on purpose, and
  // the mismatch is not a typo to tidy up. The key is a machine value under the
  // contract's machine-key exemption (§4, same shape as `verdict` in the debt
  // register); the string is a §8.2 composite. "unsigned" fails §8.2 condition 1
  // — no frozen head noun — and was wrong-adjacent anyway: this state fires when
  // a checkpoint never CLAIMED a receipt (reader.py:33-35), and also when the
  // payload is unreadable. Neither is a missing signature. Renaming the key buys
  // no governance and churns test_receipts.py in five places.
  // The daimon CLI still says "unsigned" for this state (receipts.py:545-547).
  // That divergence is REGISTERED in the contract's §4 naming-debt register with
  // the CLI as named successor — it is not drift, and it is not yours to
  // "reconcile" by reverting this string.
  var RECEIPT_TEXT = { match: "receipt matches", missing: "receipt missing",
                       mismatch: "receipt does not match this file",
                       unsigned: "no receipt claimed" };
  export function receiptMetaText(receipt) {
    var state = receipt && receipt.state;
    return Object.prototype.hasOwnProperty.call(RECEIPT_TEXT, state) ? RECEIPT_TEXT[state] : "";
  }

  // Only a BROKEN claim is loud. Both sentences describe what was found on disk and
  // stop: the inspector cannot tell tampering from a failed signing run from a
  // deleted file, and under fail-open a signing failure is routine.
  var RECEIPT_BANNER = {
    missing: "This checkpoint is marked as having a receipt, but no receipt file was found beside it.",
    mismatch: "The receipt beside this checkpoint records a different file than the one on disk."
  };
  export function receiptBanner(receipt) {
    var state = receipt && receipt.state;
    if (!Object.prototype.hasOwnProperty.call(RECEIPT_BANNER, state)) return "";
    var detail = receipt.detail ? " " + escapeHtml(receipt.detail) : "";
    return '<div class="banner"><span class="banner-icon" aria-hidden="true">⚠</span><span>' +
      escapeHtml(RECEIPT_BANNER[state]) + detail + "</span></div>";
  }

  export function renderSections(data) {
    var meta = data.meta || {};
    var metaParts = [meta.active_topic, fmtDate(meta.created), meta.author,
                     receiptMetaText(meta.receipt)].filter(Boolean);
    var sub = (meta.session_id ? "s-" + String(meta.session_id).slice(0, 8) + " · " : "") +
      "every line carries the object it came from";
    var html = '<div class="brief-head"><h1 class="page-heading">Briefing</h1>' +
      '<span class="brief-sub">' + escapeHtml(sub) + "</span></div>" +
      '<p class="page-meta">' + metaParts.map(escapeHtml).join(" · ") + "</p>" +
      receiptBanner(meta.receipt);
    (data.partial || []).forEach(function (p) {
      html += '<div class="banner banner-partial"><span class="banner-icon" aria-hidden="true">' +
        "ⓘ</span><span>" + escapeHtml(p) + "</span></div>";
    });
    var hasItems = data.sections.some(function (sec) { return sec.items && sec.items.length > 0; });
    if (!hasItems) {
      html += '<div class="state-card"><p class="state-title">This checkpoint has no recorded items yet</p>' +
        "<p>Items appear here as daimon captures decisions, questions, and beliefs from your sessions.</p></div>";
    } else {
      data.sections.forEach(function (sec) {
        html += renderSection(sec, data.meta);
      });
    }
    return html;
  }
  export function sortByImportance(items) {
    return items.map(function (it, i) { return [it, i]; }).sort(function (a, b) {
      var ai = a[0].importance == null ? -1 : a[0].importance;
      var bi = b[0].importance == null ? -1 : b[0].importance;
      if (ai !== bi) return bi - ai; // descending, null last
      return a[1] - b[1]; // stable tie-break on original order
    }).map(function (pair) { return pair[0]; });
  }
  export function renderSection(sec, meta) {
    var cls = sec.key === "verify_first" ? "sec sec-verify" : "sec";
    var n = sec.items ? sec.items.length : 0;
    var bodyId = "sec-" + sec.key;
    var expanded = sec.key === "verify_first" && n > 0;
    var disabled = n === 0;
    var items = sortByImportance(sec.items || []);
    var itemsHtml = items.map(function (it) { return renderItem(it, meta); }).join("");
    var marker = expanded ? "▾" : "▸";
    var btn = '<button type="button" class="sec-toggle" aria-expanded="' +
      (expanded ? "true" : "false") + '" aria-controls="sec-' + sec.key + '"' +
      (disabled ? " disabled" : "") + '>' + escapeHtml(sec.label) +
      ' <span class="count">· ' + n + '</span> <span class="disclosure" aria-hidden="true">' +
      marker + '</span></button>';
    var body = '<div id="' + bodyId + '" class="sec-body"' + (expanded ? "" : " hidden") + '>' +
      '<div class="items">' + itemsHtml + "</div></div>";
    return '<section class="' + cls + '" aria-label="' + escapeHtml(sec.label) + '">' + btn + body + "</section>";
  }
  export function trustClass(it) {
    if (it.trust === "verbatim") return "it-verbatim";
    if (it.trust === "inferred") return "it-inferred";
    return "it-untagged";
  }
  // Trust glyphs are the design's frozen legend: solid square = verbatim,
  // outlined = inferred, dashed = untagged.
  export function trustGlyph(trust) {
    var g = trust === "verbatim" ? "glyph-verbatim"
      : trust === "inferred" ? "glyph-inferred" : "glyph-untagged";
    return '<span class="glyph ' + g + '" aria-hidden="true"></span>';
  }
  export function renderItem(it, meta) {
    var carried = it.carried_from
      ? '<span class="chip-carried">⟳ carried from ' + escapeHtml(it.carried_from) + "</span>"
      : "";
    var inner = trustGlyph(it.trust) +
      '<span class="it-text">' + escapeHtml(it.text) + carried + "</span>";
    // Design contract: a briefing line carries the object it came from, and
    // clicking the line opens that entry. No inline expansion here — the entry
    // page is where evidence lives.
    if (it.id) {
      return '<div class="it-wrap"><button type="button" class="brief-row ' + trustClass(it) +
        '" data-open-why data-item-id="' + escapeHtml(it.id) + '">' + inner +
        '<code class="obj-id">' + escapeHtml(it.id) + "</code></button></div>";
    }
    return '<div class="it-wrap"><div class="brief-row brief-row-static ' + trustClass(it) + '">' +
      inner + "</div></div>";
  }
  export function renderHistoryToggle(itemId) {
    bioCounter += 1;
    var bioId = "bio-" + bioCounter;
    return '<button type="button" class="history-btn" aria-expanded="false" aria-controls="' + bioId +
      '" data-bio-toggle data-item-id="' + escapeHtml(itemId) + '">View history</button>' +
      '<div class="bio-panel" id="' + bioId + '" hidden></div>';
  }
  export function renderEvidence(it, meta) {
    var label = it.quote_verified === false
      ? "EVIDENCE — UNVERIFIED QUOTE"
      : "EVIDENCE — EXACT QUOTE · " + escapeHtml(fmtDate(meta.created));
    var because = it.because
      ? '<div class="ev-because">Because: ' + escapeHtml(it.because) + "</div>"
      : "";
    return '<div class="ev-tag">' + label + '</div><div class="ev-quote">“' +
      escapeHtml(it.quote) + '”</div>' + because;
  }
  export function compressBioEvents(events) {
    var out = [];
    var i = 0;
    while (i < events.length) {
      var e = events[i];
      if (e.kind !== "seen") { out.push(e); i += 1; continue; }
      var j = i;
      while (j < events.length && events[j].kind === "seen") j += 1;
      out.push({ kind: "seen-run", count: j - i, ts_or_created: events[j - 1].ts_or_created,
        session_id: null, detail: null });
      i = j;
    }
    return out;
  }
  export function taVal(v) {
    return (v === null || v === undefined || v === "")
      ? '<span class="ta-none">not recorded</span>'
      : escapeHtml(String(v));
  }
  export function taRow(label, valueHtml) {
    return '<div class="ta-row"><span class="ta-label">' + label +
      '</span><div class="ta-val">' + valueHtml + "</div></div>";
  }
  export function renderTrustAnatomy(data) {
    var a = data.trust_anatomy;
    if (!a) return "";
    var s = a.stored || {};
    var c = a.checks || {};
    var r = a.receipt;

    var claim = taVal(s.trust) +
      (s.quote ? ' <span class="ta-quote">"' + escapeHtml(s.quote) + '"</span>' : "") +
      " · quote check: " + (s.quote_verified === true ? "passed"
        : s.quote_verified === false ? "failed"
        : '<span class="ta-none">not recorded</span>') +
      " · last verified: " + taVal(s.last_verified);

    var receipt;
    if (r) {
      receipt = "verifier " + taVal(r.verifier) + " · outcome " + taVal(r.outcome) +
        " · checked " + taVal(r.checked_at) + " · digest " + taVal(r.digest_algorithm) +
        " · bound to " + (r.message_ids ? r.message_ids.length + " message" +
          (r.message_ids.length === 1 ? "" : "s") : '<span class="ta-none">not recorded</span>');
    } else {
      receipt = '<span class="ta-none">not recorded</span>';
    }

    var firstSeenSid = a.chain && a.chain[0] && a.chain[0].session_id;
    var originBits = "origin " + taVal(s.origin_session) +
      " · first seen " + (firstSeenSid ? escapeHtml(String(firstSeenSid)) : taVal(null)) +
      " · " +
      (c.origin_on_disk
        ? '<span class="ta-flag-ok">source file present</span>'
        : '<span class="ta-flag-warn">source file missing from disk</span>');
    if (c.quote_check_failures > 0) {
      originBits += " · " + c.quote_check_failures + " failed quote check" +
        (c.quote_check_failures === 1 ? "" : "s") +
        (c.last_check_ts ? ", last " + escapeHtml(fmtDate(c.last_check_ts)) : "");
    }

    var chainRows = (a.chain || []).map(function (link) {
      return '<div class="ta-chain-row"><span class="ta-chain-sid">' +
        escapeHtml(String(link.session_id).slice(0, 12)) + "</span><span>" +
        escapeHtml(fmtDate(link.created)) + "</span>" +
        (link.changed && link.changed.length
          ? "<span>changed: " + escapeHtml(link.changed.join(", ")) + "</span>" : "") +
        "</div>";
    }).join("");

    return '<div class="trust-anatomy">' +
      taRow("Claim", claim) +
      taRow("Receipt", receipt) +
      taRow("Origin", originBits) +
      taRow("Chain", chainRows || '<span class="ta-none">not recorded</span>') +
      "</div>";
  }
  // Contract §8 (ratified 2026-08-11): "verified" events are verification.jsonl
  // rows, and that ledger records rejections — the activity feed already calls
  // the same rows QUOTE CHECK, so the bio must not give them a second name.
  var BIO_LABELS = {
    born: "FIRST SEEN", changed: "CHANGED", verified: "QUOTE CHECK", resolved: "RESOLVED", "seen-run": "CARRIED"
  };
  export function renderBioEventRow(e) {
    var dotCls = "life-dot-" + escapeHtml(e.kind === "seen-run" ? "seen" : e.kind);
    var label = BIO_LABELS[e.kind] || escapeHtml(e.kind.toUpperCase());
    var when = e.ts_or_created ? escapeHtml(fmtDate(e.ts_or_created)) : "unknown date";
    var body = "";
    if (e.kind === "born" && e.detail) {
      body = '<div class="life-quote">“' + escapeHtml(e.detail) + '”</div>';
    } else if (e.kind === "seen-run") {
      body = '<div class="life-detail">carried, unchanged × ' + e.count +
        (e.count === 1 ? " session" : " sessions") + "</div>";
    } else if (e.detail) {
      body = '<div class="life-detail">' + escapeHtml(e.detail) + "</div>";
    }
    return '<li class="life-row"><div class="life-rail"><span class="life-dot ' + dotCls +
      '"></span></div><div class="life-body"><span class="life-label">' + label +
      '</span><span class="life-when">' + when + "</span>" + body + "</div></li>";
  }
  export function renderBioPanel(data) {
    var anatomyHtml = renderTrustAnatomy(data);
    var events = compressBioEvents(data.events || []);
    var noteHtml = data.window_note
      ? '<p class="life-note">' + escapeHtml(data.window_note) + "</p>"
      : "";
    if (events.length === 0) {
      return anatomyHtml + noteHtml +
        '<div class="state-card"><p class="state-title">No history recorded</p>' +
        "<p>This item has no recorded events yet.</p></div>";
    }
    return anatomyHtml + noteHtml + '<ul class="life">' +
      events.map(renderBioEventRow).join("") + "</ul>";
  }
  export function renderHistoryPicker() {
    var opts = state.historySessions.map(function (s) {
      return {
        id: s.session_id,
        label: (s.active_topic || "(untitled)") + " · " + (fmtRelative(s.created) || "unknown date")
      };
    });
    function selectHtml(pickKey, selected) {
      return '<select class="sess-pick" data-pick="' + pickKey + '">' +
        opts.map(function (o) {
          return '<option value="' + escapeHtml(o.id) + '"' +
            (o.id === selected ? " selected" : "") + '>' + escapeHtml(o.label) + '</option>';
        }).join("") + '</select>';
    }
    return '<div class="sess-pickbar">' +
      '<label class="sess-label">From' + selectHtml("a", state.historyPick.a) + '</label>' +
      '<label class="sess-label">To' + selectHtml("b", state.historyPick.b) + '</label>' +
      '</div>';
  }
  export function renderTagChip(cls, label) {
    return '<span class="tag-chip ' + escapeHtml(cls) + '">' + escapeHtml(label) + '</span>';
  }
  export function renderHistoryRow(item, chipCls, chipLabel, note, suffix) {
    var cls = "it " + trustClass(item);
    var suffixHtml = suffix ? '<span class="hist-suffix">' + escapeHtml(suffix) + "</span>" : "";
    var inner = renderTagChip(chipCls, chipLabel) + '<span class="it-text">' + escapeHtml(item.text) +
      "</span>" + suffixHtml;
    var noteHtml = note ? '<div class="hist-note">' + escapeHtml(note) + "</div>" : "";
    if (!item.id) {
      return '<div class="it-wrap"><div class="' + cls + '">' + inner + noteHtml + "</div></div>";
    }
    bioCounter += 1;
    var bioId = "bio-" + bioCounter;
    return '<div class="it-wrap"><button type="button" class="' + cls +
      '" aria-expanded="false" aria-controls="' + bioId + '" data-bio-toggle data-item-id="' +
      escapeHtml(item.id) + '">' + inner + "</button>" + noteHtml +
      '<div class="bio-panel" id="' + bioId + '" hidden></div></div>';
  }
  export function renderHistoryGroup(key, label, count, wantOpen, bodyHtml) {
    var expanded = wantOpen && count > 0;
    var disabled = count === 0;
    var marker = expanded ? "▾" : "▸";
    var btn = '<button type="button" class="sec-toggle" aria-expanded="' +
      (expanded ? "true" : "false") + '" aria-controls="hist-sec-' + key + '"' +
      (disabled ? " disabled" : "") + '>' + escapeHtml(label) +
      ' <span class="count">· ' + count + '</span> <span class="disclosure" aria-hidden="true">' +
      marker + '</span></button>';
    var body = '<div id="hist-sec-' + key + '" class="sec-body"' + (expanded ? "" : " hidden") + '>' +
      '<div class="items">' + bodyHtml + "</div></div>";
    return '<section class="sec" aria-label="' + escapeHtml(label) + '">' + btn + body + "</section>";
  }
  function historyReferent(meta) {
    if (!meta) return null;
    if (meta.created) return fmtDate(meta.created);
    return meta.session_id || null;
  }
  export function renderHistoryView(diffData) {
    var born = diffData.born || [];
    var resolved = diffData.resolved || [];
    var trustChanged = diffData.trust_changed || [];
    var carried = diffData.carried || [];
    var gone = diffData.gone || [];

    var aTopic = (diffData.a && diffData.a.active_topic) || "(untitled)";
    var bTopic = (diffData.b && diffData.b.active_topic) || "(untitled)";
    var html = '<h1 class="page-heading">History</h1>' +
      '<p class="page-meta">' + escapeHtml(aTopic) + " → " + escapeHtml(bTopic) + "</p>";
    html += renderHistoryPicker();

    if (state.historyUnreadable > 0) {
      html += '<div class="banner banner-partial"><span class="banner-icon" aria-hidden="true">ⓘ</span><span>' +
        escapeHtml(state.historyUnreadable + " session file(s) couldn't be read and were skipped.") + "</span></div>";
    }
    (diffData.partial || []).forEach(function (p) {
      html += '<div class="banner banner-partial"><span class="banner-icon" aria-hidden="true">ⓘ</span><span>' +
        escapeHtml(p) + "</span></div>";
    });

    var hasChanges = born.length || resolved.length || trustChanged.length || carried.length || gone.length;
    if (!hasChanges) {
      html += '<div class="state-card"><p class="state-title">No changes between these sessions</p>' +
        "<p>Items, resolutions, and trust changes will appear here once something changes " +
        "between the two selected sessions.</p></div>";
      return html;
    }

    // Contract §8.1: the sighting states carry a named referent — the compared
    // checkpoint's date, or its session id when the date is missing. The
    // referent renders in the hist-suffix slot, never inside the chip: the
    // vocabulary tripwire reads the chip's whole inner text as one token.
    var refB = historyReferent(diffData.b);
    var refA = historyReferent(diffData.a);
    html += renderHistoryGroup("born", "First seen", born.length, true,
      born.map(function (it) { return renderHistoryRow(it, "t-born", "FIRST SEEN", null, refB); }).join(""));
    html += renderHistoryGroup("resolved", "Resolved", resolved.length, true,
      resolved.map(function (e) { return renderHistoryRow(e.item, "t-resolved", "RESOLVED", e.note); }).join(""));
    html += renderHistoryGroup("trust", "Trust class changed", trustChanged.length, true,
      trustChanged.map(function (e) {
        return renderHistoryRow(e.item, "t-trust", "TRUST CLASS CHANGED", null, e.from + " → " + e.to);
      }).join(""));
    html += renderHistoryGroup("carried", "Carried", carried.length, false,
      carried.map(function (e) {
        return renderHistoryRow(e.item, "t-carried", "CARRIED", null);
      }).join(""));
    html += renderHistoryGroup("gone", "Last seen", gone.length, false,
      gone.map(function (it) { return renderHistoryRow(it, "t-gone", "LAST SEEN", null, refA); }).join(""));
    return html;
  }
  export function renderError(e) {
    return '<div class="state-card state-error"><p class="state-title">⚠ Error — ' +
      escapeHtml(e.what) + "</p><p>" + escapeHtml(e.why) + "</p><p><strong>Fix:</strong> " +
      escapeHtml(e.fix) + "</p></div>";
  }
  export function renderSingleCheckpointEmpty() {
    return '<div class="state-card state-empty"><p class="state-title">History starts here — ' +
      'one checkpoint so far</p><p>Come back after your next session to see what changed.</p></div>';
  }

  // ---- search (#670): rows are daimon recall's, rendered — one matcher ----
  function ageOf(created) {
    if (typeof created === "number") return fmtRelative(new Date(created * 1000).toISOString());
    return fmtRelative(created);
  }
  export function renderSearchResults(q, rows) {
    var head = '<div class="search-head"><span class="search-title">Search</span>' +
      '<span class="search-note">results from daimon recall · ' + rows.length +
      ' match(es) for “' + escapeHtml(q) + '”</span></div>';
    if (!rows.length) {
      return head + '<div class="state-card state-empty"><p class="state-title">no matches</p>' +
        "<p>Same answer <code>daimon recall</code> gives; try fewer or different words.</p></div>";
    }
    var body = rows.map(function (r) {
      var trust = r.trust || "untagged";
      var sup = !r.superseded_by ? "" :
        (r.superseded_by === "resolved" ? " · resolved" : " · superseded by " + escapeHtml(r.superseded_by));
      var id = r.item_id ? '<code class="obj-id">' + escapeHtml(r.item_id) + "</code>" : "";
      var open = r.item_id ? ' data-item-id="' + escapeHtml(r.item_id) + '"' : "";
      return '<button type="button" class="search-row"' + open + ">" + trustGlyph(trust) +
        '<span class="search-text">' + escapeHtml(r.text || "") + "</span>" + id +
        '<span class="search-meta">' + escapeHtml(r.kind || "") + " · " +
        escapeHtml(String(r.session_id || "").slice(0, 8)) + " · " + ageOf(r.created) +
        sup + "</span></button>";
    }).join("");
    return head + '<div class="search-rows">' + body +
      '<div class="card-foot">click any row to open its entry</div></div>';
  }

  // ---- why (#670): the payload is daimon why's receipt, rendered as-is.
  // JSON carries no derived summary and the viewer must not invent one:
  // axes render as recorded values, nothing is folded into a verdict. ----
  export function renderWhyView(payload) {
    var item = payload.item || {};
    var axes = payload.axes || {};
    var cor = payload.corroboration || {};
    var trust = item.trust || "untagged";
    var origin = item.origin_session || item.session_id || "";

    var html = '<div class="why-view">' +
      '<div class="why-crumb"><button type="button" class="back-link" data-why-back>← back</button>' +
      '<span class="crumb-sep">/</span><code class="crumb-id">' + escapeHtml(item.item_id || "") +
      '</code><span class="crumb-trust">' + trustGlyph(trust) + escapeHtml(trust) + "</span></div>";

    html += '<div class="why-card">';
    html += '<p class="why-text">' + escapeHtml(item.text || "") + "</p>";
    html += '<div class="why-meta"><span>origin <span class="obj-ref">' +
      escapeHtml(String(origin).slice(0, 8)) + "</span></span><span>" +
      escapeHtml(String(item.occurrences || 0)) + " occurrence(s)</span><span>" +
      escapeHtml(item.kind || "") + "</span></div>";

    if (item.quote) {
      html += '<div class="why-quote"><div class="why-quote-head">' +
        '<span class="why-label">Stored quote</span></div>' +
        '<blockquote>' + escapeHtml(item.quote) + "</blockquote></div>";
    } else {
      html += '<div class="why-quote"><div class="why-quote-head">' +
        '<span class="why-label">Stored quote</span></div>' +
        '<p class="why-none">no quote stored</p></div>';
    }

    // source_excerpt is structured: {kind, text, truncated?} — render the text,
    // label its kind, and say when it was cut rather than pretending it wasn't.
    var src = payload.source_excerpt;
    if (src && src.text) {
      html += '<div class="why-source"><span class="why-label">Transcript context · fetched now, not stored' +
        (src.kind ? " · " + escapeHtml(String(src.kind)) : "") + "</span>" +
        "<pre>" + escapeHtml(String(src.text)) +
        (src.truncated ? "\n[truncated]" : "") + "</pre></div>";
    }

    // Sightings rule: agreement is not corroboration — references are listed as
    // session referents, never a bare count alone.
    if (cor.references && cor.references.length) {
      html += '<div class="why-seen">seen independently in ' +
        cor.references.map(function (s) {
          return '<span class="obj-ref">' + escapeHtml(String(s).slice(0, 8)) + "</span>";
        }).join(", ") + "</div>";
    } else {
      html += '<div class="why-seen">seen only at origin, no other sightings</div>';
    }

    html += '<div class="why-axes"><span class="why-label">Evidence axes</span><dl>' +
      Object.keys(axes).map(function (k) {
        return "<dt>" + escapeHtml(k) + "</dt><dd>" + escapeHtml(String(axes[k])) + "</dd>";
      }).join("") + "</dl></div>";

    html += '<div class="why-life"><span class="why-label">Life</span>' +
      '<div id="why-bio" class="why-bio"></div></div>';
    html += '<div class="card-foot">read-only · this page renders the record; it holds nothing of its own</div>';
    html += "</div></div>";
    return html;
  }
