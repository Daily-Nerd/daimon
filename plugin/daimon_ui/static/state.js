// Shared mutable app state. Read by pure render functions (defaultSlug) and
// read/written by the DOM layer in app.js. diffSessions/diffPick back the diff
// view's picker; whyHighlightSid lights the source rung when an entry is
// opened from a diff row.
export const state = {
  checkpoints: [], sessionsTotal: 0, activeRef: null, pendingTimer: null, requestId: 0,
  projects: [], defaultSlug: null, currentSlug: null, view: "loading", cameFromGrid: false,
  diffSessions: [], diffUnreadable: 0, diffPick: { a: null, b: null },
  ledgerSessions: [], sessionSid: null, whyReturn: null, whyHighlightSid: null,
  whyBio: null, printItemId: null,
  bioCache: {}, lastSearchQ: null, searchTimer: null
};
