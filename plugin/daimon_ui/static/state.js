// Shared mutable app state. Read by pure render functions (defaultSlug, historyPick,
// historySessions, historyUnreadable) and read/written by the DOM layer in app.js.
export const state = {
  checkpoints: [], sessionsTotal: 0, activeRef: null, pendingTimer: null, requestId: 0,
  projects: [], defaultSlug: null, currentSlug: null, view: "loading", cameFromGrid: false,
  historySessions: [], historyUnreadable: 0, historyPick: { a: null, b: null },
  bioCache: {}
};
