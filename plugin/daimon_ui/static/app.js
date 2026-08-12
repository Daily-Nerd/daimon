import {
  ACT_ITEM_ID_RE, escapeHtml, fmtRelative, navCurrent, renderBioPanel,
  renderEmptyProjects, renderError, renderLedgerSessions, renderLedgerView, renderProjCard,
  renderRefutationsView, renderSearchResults, renderSections, renderSessionView,
  renderSidebarScope, renderWhyView, skeletonHtml
} from "./render.js";
import { state } from "./state.js";

  function api(path) {
    return fetch(path).then(function (r) { return r.json(); });
  }
  // #status is the page's only live region. Keep what goes in short: a screen
  // reader interrupts to read it. Views themselves render into #state, which is
  // deliberately NOT live — announcing it would re-read the whole view each nav.
  function announce(msg) {
    document.getElementById("status").textContent = msg;
  }
  // Every error path renders and announces the same way. `what` is the error's own
  // one-line summary; when the payload carries none we say so rather than inventing
  // a friendlier message the server never sent.
  function showError(el, err) {
    el.innerHTML = renderError(err);
    announce((err && err.what) || "Request failed.");
  }
  function setShellView(view) {
    document.querySelector(".shell").classList.toggle("grid-view", view === "grid");
  }
  // Every view swap starts reading at the top. Without this, a search or a nav
  // click from deep in a long page renders the new view 2000px above the
  // viewport — the page looks blank and the app reads as broken or slow.
  function toTop() {
    window.scrollTo(0, 0);
  }
  function loadList() {
    state.requestId += 1;
    var reqId = state.requestId;
    api("/api/projects").then(function (data) {
      if (reqId !== state.requestId) return; // superseded by a newer navigation
      state.projects = data.projects || [];
      state.defaultSlug = data.current;
      var hasCurrent = state.projects.some(function (p) { return p.slug === state.defaultSlug; });
      if (state.projects.length > 1 || !hasCurrent) {
        renderGrid();
      } else {
        enterProject(state.defaultSlug, false); // solo-user fast path: skip the grid
      }
    }).catch(function () {
      showError(document.getElementById("state"), {
        what: "Could not load projects.",
        why: "The request to the daimon server failed.",
        fix: "Check the server is running and reload the page."
      });
    });
  }
  function renderBackLink() {
    var slot = document.getElementById("back-link-slot");
    if (state.view === "project" && state.cameFromGrid) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "back-link";
      btn.textContent = "← All projects";
      btn.addEventListener("click", returnToGrid);
      slot.innerHTML = "";
      slot.appendChild(btn);
    } else {
      slot.innerHTML = "";
    }
  }
  function returnToGrid() {
    state.requestId += 1; // invalidate any in-flight project fetch
    state.view = "grid";
    renderGrid();
  }
  // The mockup nav: two pills, briefing and ledger, shown whenever a project
  // is open. The scaffold's History/Activity buttons are gone — the ledger and
  // session pages are the design's reading of the same recorded events.
  function renderNavPills() {
    var slot = document.getElementById("nav-pills-slot");
    slot.innerHTML = "";
    var inProject = state.currentSlug &&
      ["project", "ledger", "session", "search", "why", "refutations"].indexOf(state.view) !== -1;
    if (!inProject) return;
    // Pill order is the frozen chrome: briefing · ledger · refutations.
    [["briefing", "project", goBriefing], ["ledger", "ledger", enterLedger],
     ["refutations", "refutations", enterRefutations]].forEach(function (pill) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "nav-pill";
      btn.textContent = pill[0];
      btn.setAttribute("aria-current", navCurrent(state.view, pill[1]));
      btn.addEventListener("click", pill[2]);
      slot.appendChild(btn);
    });
  }
  function goBriefing() {
    if (!state.currentSlug) return;
    enterProject(state.currentSlug, state.cameFromGrid);
  }
  // ---- ledger (#670 slice 2): one row per object, grouped by session ----
  function enterLedger() {
    if (!state.currentSlug) return;
    var slug = state.currentSlug;
    state.view = "ledger";
    state.requestId += 1;
    var reqId = state.requestId;
    renderNavPills();
    var stateEl = document.getElementById("state");
    var sectionsEl = document.getElementById("sections");
    var mainEl = document.getElementById("main");
    mainEl.setAttribute("aria-busy", "true");
    if (state.pendingTimer) clearTimeout(state.pendingTimer);
    state.pendingTimer = setTimeout(function () {
      if (reqId !== state.requestId) return;
      stateEl.innerHTML = skeletonHtml();
    }, 1000);
    Promise.all([
      api("/api/ledger?project=" + encodeURIComponent(slug)),
      api("/api/history?project=" + encodeURIComponent(slug))
    ]).then(function (results) {
      if (reqId !== state.requestId) return; // superseded by a newer navigation
      clearTimeout(state.pendingTimer);
      mainEl.removeAttribute("aria-busy");
      var ledger = results[0], hist = results[1];
      state.ledgerSessions = hist.sessions || [];
      stateEl.innerHTML = "";
      if (!ledger.ok) {
        sectionsEl.innerHTML = "";
        showError(stateEl, ledger.error);
        return;
      }
      renderSessionSidebar(null);
      sectionsEl.innerHTML = renderLedgerView(ledger);
      toTop();
      announce("Ledger loaded.");
      wireWhyOpeners(sectionsEl);
      wireSessionOpeners(sectionsEl);
      wireLedgerToggles(sectionsEl);
    }).catch(function () {
      if (reqId !== state.requestId) return;
      clearTimeout(state.pendingTimer);
      mainEl.removeAttribute("aria-busy");
      sectionsEl.innerHTML = "";
      showError(stateEl, {
        what: "Could not load the ledger.",
        why: "The request to the daimon server failed.",
        fix: "Check the server is running and try again."
      });
    });
  }
  function renderSessionSidebar(activeSid) {
    var nav = document.getElementById("sidebar");
    nav.innerHTML = renderLedgerSessions(state.ledgerSessions, activeSid);
    Array.prototype.forEach.call(nav.querySelectorAll(".sess-item"), function (btn) {
      btn.addEventListener("click", function () { enterSession(btn.dataset.sid); });
    });
  }
  function wireLedgerToggles(container) {
    Array.prototype.forEach.call(container.querySelectorAll(".ledger-ck-toggle"), function (btn) {
      btn.addEventListener("click", function () {
        var body = document.getElementById(btn.getAttribute("aria-controls"));
        var marker = btn.querySelector(".disclosure");
        var open = !body.hasAttribute("hidden");
        if (open) {
          body.setAttribute("hidden", "");
          btn.setAttribute("aria-expanded", "false");
          if (marker) marker.textContent = "▸";
        } else {
          body.removeAttribute("hidden");
          btn.setAttribute("aria-expanded", "true");
          if (marker) marker.textContent = "▾";
        }
      });
    });
  }
  function wireSessionOpeners(container) {
    Array.prototype.forEach.call(container.querySelectorAll("[data-open-session][data-sid]"), function (btn) {
      btn.addEventListener("click", function () { enterSession(btn.dataset.sid); });
    });
  }
  // ---- refutations lane (#670 slice 3): renders `daimon refute list` ----
  function enterRefutations() {
    if (!state.currentSlug) return;
    var slug = state.currentSlug;
    state.view = "refutations";
    state.requestId += 1;
    var reqId = state.requestId;
    renderNavPills();
    var stateEl = document.getElementById("state");
    var sectionsEl = document.getElementById("sections");
    var mainEl = document.getElementById("main");
    mainEl.setAttribute("aria-busy", "true");
    if (state.pendingTimer) clearTimeout(state.pendingTimer);
    state.pendingTimer = setTimeout(function () {
      if (reqId !== state.requestId) return;
      stateEl.innerHTML = skeletonHtml();
    }, 1000);
    api("/api/refutations?project=" + encodeURIComponent(slug))
      .then(function (data) {
        if (reqId !== state.requestId) return; // superseded by a newer navigation
        clearTimeout(state.pendingTimer);
        mainEl.removeAttribute("aria-busy");
        stateEl.innerHTML = "";
        if (!data.ok) {
          sectionsEl.innerHTML = "";
          showError(stateEl, data.error);
          return;
        }
        sectionsEl.innerHTML = renderRefutationsView(data);
        toTop();
        announce("Refutations loaded.");
        wireWhyOpeners(sectionsEl);
      }).catch(function () {
        if (reqId !== state.requestId) return;
        clearTimeout(state.pendingTimer);
        mainEl.removeAttribute("aria-busy");
        sectionsEl.innerHTML = "";
        showError(stateEl, {
          what: "Could not load refutations.",
          why: "The request to the daimon server failed.",
          fix: "Check the server is running and try again."
        });
      });
  }
  // ---- session page (#670 slice 2): what one session wrote ----
  function enterSession(sid) {
    if (!state.currentSlug || !sid) return;
    var slug = state.currentSlug;
    state.view = "session";
    state.sessionSid = sid;
    state.requestId += 1;
    var reqId = state.requestId;
    renderNavPills();
    var stateEl = document.getElementById("state");
    var sectionsEl = document.getElementById("sections");
    var mainEl = document.getElementById("main");
    mainEl.setAttribute("aria-busy", "true");
    if (state.pendingTimer) clearTimeout(state.pendingTimer);
    state.pendingTimer = setTimeout(function () {
      if (reqId !== state.requestId) return;
      stateEl.innerHTML = skeletonHtml();
    }, 1000);
    api("/api/session?project=" + encodeURIComponent(slug) + "&sid=" + encodeURIComponent(sid))
      .then(function (data) {
        if (reqId !== state.requestId) return; // superseded by a newer navigation
        clearTimeout(state.pendingTimer);
        mainEl.removeAttribute("aria-busy");
        stateEl.innerHTML = "";
        if (!data.ok) {
          sectionsEl.innerHTML = "";
          showError(stateEl, data.error);
          return;
        }
        renderSessionSidebar(sid);
        sectionsEl.innerHTML = renderSessionView(data);
        toTop();
        announce("Session loaded.");
        wireWhyOpeners(sectionsEl);
        var back = sectionsEl.querySelector("[data-session-back]");
        if (back) back.addEventListener("click", enterLedger);
      }).catch(function () {
        if (reqId !== state.requestId) return;
        clearTimeout(state.pendingTimer);
        mainEl.removeAttribute("aria-busy");
        sectionsEl.innerHTML = "";
        showError(stateEl, {
          what: "Could not load this session.",
          why: "The request to the daimon server failed.",
          fix: "Check the server is running and try again."
        });
      });
  }
  function renderGrid() {
    state.view = "grid";
    state.currentSlug = null;
    setShellView("grid");
    document.getElementById("sidebar").innerHTML = "";
    document.getElementById("project").textContent = "";
    renderBackLink();
    renderNavPills();
    var stateEl = document.getElementById("state");
    var sectionsEl = document.getElementById("sections");
    if (state.projects.length === 0) {
      sectionsEl.innerHTML = "";
      stateEl.innerHTML = renderEmptyProjects();
      announce("No projects found.");
      return;
    }
    stateEl.innerHTML = "";
    toTop();
    announce("All projects.");
    // Current project first — it is where the user physically is — then the
    // rest stay in the server's newest-first order.
    var ordered = state.projects.slice().sort(function (a, b) {
      return (b.slug === state.defaultSlug) - (a.slug === state.defaultSlug);
    });
    sectionsEl.innerHTML =
      '<div class="grid-head"><h1 class="page-heading">Projects</h1>' +
      '<span class="brief-sub">' + ordered.length +
      ' project(s) · each card is where that project left off</span></div>' +
      '<div class="proj-grid">' + ordered.map(renderProjCard).join("") + "</div>";
    Array.prototype.forEach.call(sectionsEl.querySelectorAll(".proj-card"), function (btn) {
      btn.addEventListener("click", function () { enterProject(btn.dataset.slug, true); });
    });
  }
  function enterProject(slug, cameFromGrid) {
    state.currentSlug = slug;
    state.view = "project";
    state.cameFromGrid = !!cameFromGrid;
    setShellView("project");
    renderBackLink();
    renderNavPills();
    loadCheckpointsList(slug);
  }
  function loadCheckpointsList(slug) {
    state.requestId += 1;
    var reqId = state.requestId;
    api("/api/checkpoints?project=" + encodeURIComponent(slug)).then(function (data) {
      if (reqId !== state.requestId) return; // superseded by a newer navigation
      document.getElementById("project").textContent = data.project || "";
      state.checkpoints = data.checkpoints || [];
      state.sessionsTotal = data.sessions_total || 0;
      renderSidebar();
      if (state.checkpoints.length === 0) {
        renderEmptyCheckpoints();
        document.getElementById("sections").innerHTML = "";
        return;
      }
      selectCheckpoint(state.checkpoints[0].ref);
    }).catch(function () {
      if (reqId !== state.requestId) return; // superseded by a newer navigation
      showError(document.getElementById("state"), {
        what: "Could not load checkpoints.",
        why: "The request to the daimon server failed.",
        fix: "Check the server is running and reload the page."
      });
    });
  }
  function renderSidebar() {
    var nav = document.getElementById("sidebar");
    nav.innerHTML = state.checkpoints.map(function (c) {
      var active = c.ref === state.activeRef;
      var topic = c.active_topic ? escapeHtml(c.active_topic) : "(untitled)";
      var rel = fmtRelative(c.created);
      return '<button type="button" class="cp-item' + (active ? " active" : "") +
        '" data-ref="' + escapeHtml(c.ref) + '" aria-current="' + (active ? "true" : "false") + '">' +
        '<span class="cp-topic">' + topic + '</span>' +
        '<span class="cp-meta">' + escapeHtml(c.ref) + (rel ? " · " + escapeHtml(rel) : "") + '</span>' +
        '</button>';
    }).join("") + renderSidebarScope(state.checkpoints.length, state.sessionsTotal);
    Array.prototype.forEach.call(nav.querySelectorAll(".cp-item"), function (btn) {
      btn.addEventListener("click", function () { selectCheckpoint(btn.dataset.ref); });
    });
  }
  function selectCheckpoint(ref) {
    state.activeRef = ref;
    state.requestId += 1;
    if (state.view !== "project") {
      state.view = "project";
      renderNavPills();
    }
    renderSidebar();
    loadCheckpoint(ref, state.requestId);
  }
  function loadCheckpoint(ref, requestId) {
    var stateEl = document.getElementById("state");
    var sectionsEl = document.getElementById("sections");
    var mainEl = document.getElementById("main");
    mainEl.setAttribute("aria-busy", "true");
    if (state.pendingTimer) clearTimeout(state.pendingTimer);
    state.pendingTimer = setTimeout(function () {
      if (requestId !== state.requestId) return; // superseded by a newer selection
      stateEl.innerHTML = skeletonHtml();
    }, 1000);
    api("/api/checkpoint/" + encodeURIComponent(ref) + "?project=" + encodeURIComponent(state.currentSlug)).then(function (data) {
      if (requestId !== state.requestId) return; // stale response — a newer request is in flight
      clearTimeout(state.pendingTimer);
      mainEl.removeAttribute("aria-busy");
      stateEl.innerHTML = "";
      if (data.ok) {
        sectionsEl.innerHTML = renderSections(data);
        toTop();
        announce("Checkpoint " + ref + " loaded.");
        wireWhyOpeners(sectionsEl);
        wireSectionToggles(sectionsEl);
        wireBioToggles(sectionsEl);
      } else {
        sectionsEl.innerHTML = "";
        showError(stateEl, data.error);
      }
    }).catch(function () {
      if (requestId !== state.requestId) return; // stale response — a newer request is in flight
      clearTimeout(state.pendingTimer);
      mainEl.removeAttribute("aria-busy");
      sectionsEl.innerHTML = "";
      showError(stateEl, {
        what: "Could not load this checkpoint.",
        why: "The request to the daimon server failed.",
        fix: "Check the server is running and reload the page."
      });
    });
  }
  function wireItemToggles(container) {
    var buttons = container.querySelectorAll(".it-wrap > button[aria-controls]:not([data-bio-toggle])");
    Array.prototype.forEach.call(buttons, function (btn) {
      btn.addEventListener("click", function () {
        var ev = btn.parentElement.querySelector(".ev");
        var open = !ev.hasAttribute("hidden");
        if (open) {
          ev.setAttribute("hidden", "");
          btn.setAttribute("aria-expanded", "false");
        } else {
          ev.removeAttribute("hidden");
          btn.setAttribute("aria-expanded", "true");
        }
      });
    });
  }
  function wireBioToggles(container) {
    Array.prototype.forEach.call(container.querySelectorAll("[data-bio-toggle]"), function (btn) {
      btn.addEventListener("click", function () {
        var panel = document.getElementById(btn.getAttribute("aria-controls"));
        var open = !panel.hasAttribute("hidden");
        if (open) {
          panel.setAttribute("hidden", "");
          btn.setAttribute("aria-expanded", "false");
          return;
        }
        panel.removeAttribute("hidden");
        btn.setAttribute("aria-expanded", "true");
        loadBiography(btn.dataset.itemId, panel);
      });
    });
  }
  function loadBiography(itemId, panel) {
    var reqId = state.requestId;
    var cacheKey = reqId + ":" + itemId;
    if (state.bioCache[cacheKey]) {
      panel.innerHTML = renderBioPanel(state.bioCache[cacheKey]);
      return;
    }
    var timer = setTimeout(function () {
      panel.innerHTML = skeletonHtml();
    }, 1000);
    api("/api/biography?project=" + encodeURIComponent(state.currentSlug) + "&id=" + encodeURIComponent(itemId))
      .then(function (data) {
        clearTimeout(timer);
        if (reqId !== state.requestId) return; // navigated away meanwhile
        if (data.ok) {
          state.bioCache[cacheKey] = data;
          panel.innerHTML = renderBioPanel(data);
        } else {
          panel.innerHTML = renderError(data.error);
        }
      }).catch(function () {
        clearTimeout(timer);
        if (reqId !== state.requestId) return;
        panel.innerHTML = renderError({
          what: "Could not load this item's history.",
          why: "The request to the daimon server failed.",
          fix: "Check the server is running and try again."
        });
      });
  }
  // ---- search (#670): the box renders daimon recall — one matcher, two renderings ----
  function searchSlug() {
    return state.currentSlug || state.defaultSlug;
  }
  // Briefing lines and search rows both open the entry page the same way.
  function wireWhyOpeners(container) {
    Array.prototype.forEach.call(container.querySelectorAll("[data-open-why][data-item-id]"), function (btn) {
      btn.addEventListener("click", function () { openWhy(btn.dataset.itemId); });
    });
  }
  function runSearch(q) {
    var slug = searchSlug();
    if (!slug || !q || !q.trim()) return;
    state.view = "search";
    renderNavPills();
    state.requestId += 1;
    var reqId = state.requestId;
    var stateEl = document.getElementById("state");
    var sectionsEl = document.getElementById("sections");
    api("/api/recall?project=" + encodeURIComponent(slug) + "&q=" + encodeURIComponent(q))
      .then(function (data) {
        if (reqId !== state.requestId) return; // superseded by a newer navigation
        stateEl.innerHTML = "";
        if (!data.ok) {
          sectionsEl.innerHTML = "";
          showError(stateEl, data.error);
          return;
        }
        state.lastSearchQ = q;
        sectionsEl.innerHTML = renderSearchResults(q, data.rows);
        toTop();
        announce(data.rows.length + " search result(s).");
        Array.prototype.forEach.call(sectionsEl.querySelectorAll(".search-row[data-item-id]"), function (btn) {
          btn.addEventListener("click", function () { openWhy(btn.dataset.itemId); });
        });
      }).catch(function () {
        if (reqId !== state.requestId) return;
        sectionsEl.innerHTML = "";
        showError(stateEl, {
          what: "Search failed.",
          why: "The request to the daimon server failed.",
          fix: "Check the server is running and try again."
        });
      });
  }
  // ---- why (#670): the entry page renders daimon why's receipt as recorded ----
  function openWhy(itemId) {
    var slug = searchSlug();
    if (!slug || !ACT_ITEM_ID_RE.test(itemId || "")) return;
    // Remember which surface the entry was opened from: back must return the
    // reader to the ledger or session page they were on, not to the briefing.
    if (state.view === "ledger") state.whyReturn = { view: "ledger" };
    else if (state.view === "session") state.whyReturn = { view: "session", sid: state.sessionSid };
    else if (state.view === "refutations") state.whyReturn = { view: "refutations" };
    else state.whyReturn = null;
    state.view = "why";
    renderNavPills();
    state.requestId += 1;
    var reqId = state.requestId;
    var stateEl = document.getElementById("state");
    var sectionsEl = document.getElementById("sections");
    api("/api/why?project=" + encodeURIComponent(slug) + "&id=" + encodeURIComponent(itemId) + "&source=1")
      .then(function (data) {
        if (reqId !== state.requestId) return; // superseded by a newer navigation
        stateEl.innerHTML = "";
        if (!data.ok) {
          sectionsEl.innerHTML = "";
          showError(stateEl, data.error);
          return;
        }
        sectionsEl.innerHTML = renderWhyView(data);
        toTop();
        announce("Entry loaded.");
        var back = sectionsEl.querySelector("[data-why-back]");
        if (back) back.addEventListener("click", function () {
          if (state.whyReturn && state.whyReturn.view === "session") { enterSession(state.whyReturn.sid); }
          else if (state.whyReturn && state.whyReturn.view === "ledger") { enterLedger(); }
          else if (state.whyReturn && state.whyReturn.view === "refutations") { enterRefutations(); }
          else if (state.lastSearchQ) { runSearch(state.lastSearchQ); }
          else if (state.activeRef) { selectCheckpoint(state.activeRef); }
          else { loadList(); }
        });
        var bio = document.getElementById("why-bio");
        if (bio) loadBiography(itemId, bio);
      }).catch(function () {
        if (reqId !== state.requestId) return;
        sectionsEl.innerHTML = "";
        showError(stateEl, {
          what: "Could not load this entry.",
          why: "The request to the daimon server failed.",
          fix: "Check the server is running and try again."
        });
      });
  }
  function wireSearchForm() {
    var form = document.getElementById("search-form");
    var box = document.getElementById("search-box");
    if (!form || !box) return;
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      if (state.searchTimer) clearTimeout(state.searchTimer);
      runSearch(box.value);
    });
    // Search-as-you-type, debounced: still one matcher — every keystroke's
    // results are recall's, the debounce only spaces the calls out. Enter
    // still fires immediately via the submit handler above.
    box.addEventListener("input", function () {
      if (state.searchTimer) clearTimeout(state.searchTimer);
      var q = box.value;
      if (!q || q.trim().length < 2) return;
      state.searchTimer = setTimeout(function () { runSearch(q); }, 250);
    });
  }
  function wireSectionToggles(container) {
    Array.prototype.forEach.call(container.querySelectorAll(".sec-toggle"), function (btn) {
      if (btn.disabled) return;
      btn.addEventListener("click", function () {
        var body = document.getElementById(btn.getAttribute("aria-controls"));
        var marker = btn.querySelector(".disclosure");
        var open = !body.hasAttribute("hidden");
        if (open) {
          body.setAttribute("hidden", "");
          btn.setAttribute("aria-expanded", "false");
          if (marker) marker.textContent = "▸";
        } else {
          body.removeAttribute("hidden");
          btn.setAttribute("aria-expanded", "true");
          if (marker) marker.textContent = "▾";
        }
      });
    });
  }
  function renderEmptyCheckpoints() {
    document.getElementById("state").innerHTML =
      '<div class="state-card state-empty"><p class="state-title">No checkpoints yet</p>' +
      "<p>Once you run <code>daimon brief</code>, checkpoints will appear here with decisions, " +
      "open loops, and verified quotes ready to browse.</p></div>";
    announce("No checkpoints yet.");
  }

  document.addEventListener("DOMContentLoaded", function () {
    wireSearchForm();
    loadList();
  });
