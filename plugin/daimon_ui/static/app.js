import {
  ACT_ITEM_ID_RE, escapeHtml, fmtRelative, navCurrent, renderActivityView, renderBioPanel,
  renderEmptyProjects, renderError, renderHistoryView, renderProjCard, renderSearchResults,
  renderSections, renderSidebarScope, renderSingleCheckpointEmpty, renderWhyView, skeletonHtml
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
  function renderHistoryLink() {
    var slot = document.getElementById("history-link-slot");
    if ((state.view === "project" || state.view === "history" || state.view === "activity") && state.currentSlug) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "history-link";
      btn.textContent = "History";
      btn.setAttribute("aria-current", navCurrent(state.view, "history"));
      btn.addEventListener("click", enterHistory);
      slot.innerHTML = "";
      slot.appendChild(btn);
    } else {
      slot.innerHTML = "";
    }
  }
  function renderActivityLink() {
    var slot = document.getElementById("activity-link-slot");
    if ((state.view === "project" || state.view === "history" || state.view === "activity") && state.currentSlug) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "history-link";
      btn.textContent = "Activity";
      btn.setAttribute("aria-current", navCurrent(state.view, "activity"));
      btn.addEventListener("click", enterActivity);
      slot.innerHTML = "";
      slot.appendChild(btn);
    } else {
      slot.innerHTML = "";
    }
  }
  function enterActivity() {
    if (!state.currentSlug) return;
    var slug = state.currentSlug;
    state.view = "activity";
    state.requestId += 1;
    var reqId = state.requestId;
    renderHistoryLink();
    renderActivityLink();
    var stateEl = document.getElementById("state");
    var sectionsEl = document.getElementById("sections");
    var mainEl = document.getElementById("main");
    mainEl.setAttribute("aria-busy", "true");
    if (state.pendingTimer) clearTimeout(state.pendingTimer);
    state.pendingTimer = setTimeout(function () {
      if (reqId !== state.requestId) return;
      stateEl.innerHTML = skeletonHtml();
    }, 1000);
    api("/api/activity?project=" + encodeURIComponent(slug)).then(function (data) {
      if (reqId !== state.requestId) return;
      clearTimeout(state.pendingTimer);
      mainEl.removeAttribute("aria-busy");
      sectionsEl.innerHTML = "";
      if (!data.ok) { showError(stateEl, data.error); return; }
      stateEl.innerHTML = renderActivityView(data);
      announce("Activity loaded.");
      wireBioToggles(stateEl);
    }).catch(function () {
      if (reqId !== state.requestId) return;
      clearTimeout(state.pendingTimer);
      mainEl.removeAttribute("aria-busy");
      sectionsEl.innerHTML = "";
      showError(stateEl, {
        what: "Could not load activity.",
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
    renderHistoryLink();
    renderActivityLink();
    var stateEl = document.getElementById("state");
    var sectionsEl = document.getElementById("sections");
    if (state.projects.length === 0) {
      sectionsEl.innerHTML = "";
      stateEl.innerHTML = renderEmptyProjects();
      announce("No projects found.");
      return;
    }
    stateEl.innerHTML = "";
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
    renderHistoryLink();
    renderActivityLink();
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
      renderHistoryLink();
      renderActivityLink();
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
    state.view = "why";
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
        announce("Entry loaded.");
        var back = sectionsEl.querySelector("[data-why-back]");
        if (back) back.addEventListener("click", function () {
          if (state.lastSearchQ) { runSearch(state.lastSearchQ); }
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
      runSearch(box.value);
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
  function enterHistory() {
    if (!state.currentSlug) return;
    var slug = state.currentSlug;
    state.view = "history";
    state.requestId += 1;
    var reqId = state.requestId;
    renderHistoryLink();
    renderActivityLink();
    var stateEl = document.getElementById("state");
    var sectionsEl = document.getElementById("sections");
    var mainEl = document.getElementById("main");
    mainEl.setAttribute("aria-busy", "true");
    if (state.pendingTimer) clearTimeout(state.pendingTimer);
    state.pendingTimer = setTimeout(function () {
      if (reqId !== state.requestId) return; // superseded by a newer navigation
      stateEl.innerHTML = skeletonHtml();
    }, 1000);
    Promise.all([
      api("/api/history?project=" + encodeURIComponent(slug)),
      api("/api/diff?project=" + encodeURIComponent(slug))
    ]).then(function (results) {
      if (reqId !== state.requestId) return; // stale response — a newer request is in flight
      clearTimeout(state.pendingTimer);
      mainEl.removeAttribute("aria-busy");
      var hist = results[0], diff = results[1];
      state.historySessions = hist.sessions || [];
      state.historyUnreadable = hist.unreadable || 0;
      stateEl.innerHTML = "";
      if (!diff.ok) {
        sectionsEl.innerHTML = "";
        showError(stateEl, diff.error);
        return;
      }
      if (diff.empty === "single_checkpoint") {
        sectionsEl.innerHTML = "";
        stateEl.innerHTML = renderSingleCheckpointEmpty();
        announce("Only one checkpoint — nothing to compare.");
        return;
      }
      // Defaults must come from the filename-based session list (same source the server
      // uses when a/b are omitted) — diff.a/diff.b meta.session_id is read from inside the
      // checkpoint JSON and isn't guaranteed to match the filename the API expects back.
      state.historyPick = {
        a: state.historySessions.length > 1 ? state.historySessions[1].session_id : null,
        b: state.historySessions.length > 0 ? state.historySessions[0].session_id : null
      };
      sectionsEl.innerHTML = renderHistoryView(diff);
      announce("History loaded.");
      wireHistoryControls(sectionsEl, slug);
      wireSectionToggles(sectionsEl);
      wireBioToggles(sectionsEl);
    }).catch(function () {
      if (reqId !== state.requestId) return; // stale response — a newer request is in flight
      clearTimeout(state.pendingTimer);
      mainEl.removeAttribute("aria-busy");
      sectionsEl.innerHTML = "";
      showError(stateEl, {
        what: "Could not load history.",
        why: "The request to the daimon server failed.",
        fix: "Check the server is running and reload the page."
      });
    });
  }
  function loadDiff(slug, a, b) {
    state.requestId += 1;
    var reqId = state.requestId;
    var stateEl = document.getElementById("state");
    var sectionsEl = document.getElementById("sections");
    var mainEl = document.getElementById("main");
    mainEl.setAttribute("aria-busy", "true");
    if (state.pendingTimer) clearTimeout(state.pendingTimer);
    state.pendingTimer = setTimeout(function () {
      if (reqId !== state.requestId) return; // superseded by a newer navigation
      stateEl.innerHTML = skeletonHtml();
    }, 1000);
    api("/api/diff?project=" + encodeURIComponent(slug) + "&a=" + encodeURIComponent(a) +
      "&b=" + encodeURIComponent(b)).then(function (diff) {
      if (reqId !== state.requestId) return; // stale response — a newer request is in flight
      clearTimeout(state.pendingTimer);
      mainEl.removeAttribute("aria-busy");
      stateEl.innerHTML = "";
      if (!diff.ok) {
        sectionsEl.innerHTML = "";
        showError(stateEl, diff.error);
        return;
      }
      sectionsEl.innerHTML = renderHistoryView(diff);
      announce("Comparison updated.");
      wireHistoryControls(sectionsEl, slug);
      wireSectionToggles(sectionsEl);
      wireBioToggles(sectionsEl);
    }).catch(function () {
      if (reqId !== state.requestId) return; // stale response — a newer request is in flight
      clearTimeout(state.pendingTimer);
      mainEl.removeAttribute("aria-busy");
      sectionsEl.innerHTML = "";
      showError(stateEl, {
        what: "Could not load the diff.",
        why: "The request to the daimon server failed.",
        fix: "Check the server is running and reload the page."
      });
    });
  }
  function wireHistoryControls(container, slug) {
    Array.prototype.forEach.call(container.querySelectorAll(".sess-pick"), function (sel) {
      sel.addEventListener("change", function () {
        state.historyPick[sel.dataset.pick] = sel.value;
        loadDiff(slug, state.historyPick.a, state.historyPick.b);
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
