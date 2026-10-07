import json
import re
from dataclasses import dataclass
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, unquote, urlsplit

from . import reader

# Engine imports (#670): search and item inspection are served by daimon's own
# engines — recall.search is the one matcher (the viewer renders recall, it
# never grows a second search engine) and inspector.inspect_item is the same
# read-side receipt `daimon why` prints. reader.py stays daimon-import-free
# (files are its seam); the engine boundary lives here in dispatch only.
from daimon_briefing import (config, inspector, recall, refutations,
                             relations, view)

_PAGE = Path(__file__).parent / "page.html"
_SLUG_RE = re.compile(r"^[\w-]+$")

_STATIC_DIR = Path(__file__).parent / "static"

# Fixed literal allowlist. Request text is NEVER joined to a directory, never
# normalized, never resolved. An unlisted name 404s without touching the filesystem.
_STATIC = {
    "app.css": (_STATIC_DIR / "app.css", "text/css; charset=utf-8"),
    "state.js": (_STATIC_DIR / "state.js", "text/javascript; charset=utf-8"),
    "render.js": (_STATIC_DIR / "render.js", "text/javascript; charset=utf-8"),
    "app.js": (_STATIC_DIR / "app.js", "text/javascript; charset=utf-8"),
}

def _refutations_payload(slug):
    """#693: refutation polarity only — the ledger holds rulings too, and
    this lane's vocabulary (✗, "Refutes") would invert them. A rulings lane
    is its own surface. Kept as a seam so a test can assert on the ROWS the
    endpoint serves rather than grep the source."""
    return {"ok": True,
            "rows": refutations.listing(
                polarity="refutation", project_dir=slug),
            # #693 PR 2: the rulings lane rides the same payload, its own
            # polarity — the two lanes never mix, and neither call is ever
            # unscoped.
            "rulings": refutations.listing(
                polarity="ruling", project_dir=slug)}


class _Handler(BaseHTTPRequestHandler):
    def __init__(self, data_dir, default_slug, project_label, *a, **kw):
        self.data_dir = data_dir
        self.default_slug = default_slug
        self.project_label = project_label
        super().__init__(*a, **kw)

    def log_message(self, *a):  # keep the terminal quiet
        pass

    def _send(self, status, ctype, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        self._send(200, "application/json", json.dumps(obj).encode("utf-8"))

    def _label_for(self, slug):
        return self.project_label if slug == self.default_slug else slug

    def _bad_slug_error(self, raw_slug):
        return {"ok": False, "error": {
            "what": f"Project {raw_slug!r} isn't one this inspector knows.",
            "why": "The slug doesn't match a project directory discovered under the data dir.",
            "fix": "Pick a project from the grid, or check the URL.",
        }}

    def _resolve_slug(self, params):
        """Slug is accepted only if it matches ^[\\w-]+$ AND names a bucket this
        caller may open: `view.bucket_exists`, the one existence check and the
        one tenant rule (#899), a stat and not a scan. Never joined to a path
        before that double-check passes."""
        raw = params.get("project", [self.default_slug])[0]
        if not _SLUG_RE.fullmatch(raw or ""):
            return None
        if not view.bucket_exists(raw, self.default_slug):
            return None
        return raw

    def _slug_or_refuse(self, params):
        """The request's project slug, or None after answering the bad-slug
        error: every project-scoped route starts here."""
        slug = self._resolve_slug(params)
        if slug is None:
            self._json(self._bad_slug_error(
                params.get("project", [self.default_slug])[0]))
        return slug

    def _fail(self, exc):
        """A generic 500: the exception TYPE and nothing else. A message, a
        path or an item's text never reaches the client."""
        body = json.dumps({"ok": False, "error": {
            "what": "The inspector could not answer this request.",
            "why": f"An internal error occurred ({type(exc).__name__}).",
            "fix": "Retry; if it persists, run the same read from the CLI.",
        }}).encode("utf-8")
        try:
            self._send(500, "application/json", body)
        except OSError:
            pass  # the client is gone; there is nobody to tell

    def do_GET(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
        if host not in ("127.0.0.1", "localhost"):
            self._send(403, "text/plain", b"forbidden: bad host header")
            return
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        params = parse_qs(parsed.query)
        route = _match(path)
        if route is None:
            self._send(404, "text/plain", b"not found")
            return
        try:
            # The store scope is per request and set HERE, inside the handler
            # thread: a ContextVar set in the main thread is invisible to a
            # ThreadingHTTPServer request thread. Every read, the reader's
            # files and the engines alike, then answers for `--data-dir`.
            with config.checkpoint_dir_override(self.data_dir):
                route.handler(self, path, params)
        except Exception as exc:  # noqa: BLE001 - the one catch-all, by design
            self._fail(exc)


# ---- routes ----------------------------------------------------------------
# One function per route: (handler, path, params). The ROUTES table below is
# the only dispatch; each row names the read-pipeline stage that converts it.

def _page(h, path, params):
    h._send(200, "text/html; charset=utf-8", _PAGE.read_bytes())


def _projects(h, path, params):
    allowed = set(view.buckets(h.default_slug))
    h._json({"projects": [b for b in reader.list_buckets(h.data_dir)
                          if b["slug"] in allowed],
             "current": h.default_slug})


def _checkpoints(h, path, params):
    slug = h._slug_or_refuse(params)
    if slug is None:
        return
    bucket = h.data_dir / slug
    # list_recent serves the pointer window; project_history serves every
    # session file for the slug. The sidebar needs both numbers or it
    # silently presents a window as if it were the whole history.
    h._json({"project": h._label_for(slug),
             "checkpoints": reader.list_recent(bucket),
             "sessions_total": len(reader.project_history(h.data_dir, slug)["sessions"])})


def _checkpoint(h, path, params):
    slug = h._slug_or_refuse(params)
    if slug is None:
        return
    ref = path.removeprefix("/api/checkpoint/")
    h._json(reader.load_checkpoint(h.data_dir, slug, ref))


def _history(h, path, params):
    slug = h._slug_or_refuse(params)
    if slug is None:
        return
    result = reader.project_history(h.data_dir, slug)
    result["project"] = h._label_for(slug)
    h._json(result)


def _diff(h, path, params):
    slug = h._slug_or_refuse(params)
    if slug is None:
        return
    a = params.get("a", [None])[0]
    b = params.get("b", [None])[0]
    if a is None or b is None:
        hist = reader.project_history(h.data_dir, slug)
        sessions = hist["sessions"]
        if len(sessions) < 2:
            h._json({"ok": True, "empty": "single_checkpoint", "sessions": len(sessions)})
            return
        a = sessions[1]["session_id"]
        b = sessions[0]["session_id"]
    h._json(reader.diff_checkpoints(h.data_dir, slug, a, b))


def _biography(h, path, params):
    slug = h._slug_or_refuse(params)
    if slug is None:
        return
    item_id = params.get("id", [None])[0]
    h._json(reader.item_biography(h.data_dir, slug, item_id))


def _recall(h, path, params):
    slug = h._slug_or_refuse(params)
    if slug is None:
        return
    q = params.get("q", [None])[0]
    if not q or not q.strip():
        h._json({"ok": False, "error": {
            "what": "No search query was given.",
            "why": "/api/recall needs a q parameter to match against.",
            "fix": "Type something in the search box.",
        }})
        return
    raw_limit = params.get("limit", ["20"])[0]
    try:
        limit = int(raw_limit)
        if limit < 1:
            raise ValueError
    except ValueError:
        h._json({"ok": False, "error": {
            "what": f"limit {raw_limit!r} isn't a positive whole number.",
            "why": "The result window must be at least 1.",
            "fix": "Drop the limit parameter or pass a positive number.",
        }})
        return
    try:
        rows = recall.search(q, slug=slug, limit=limit)
    except recall.RecallError as exc:
        h._json({"ok": False, "error": {
            "what": "Search is unavailable.",
            "why": str(exc),
            "fix": "Check that this Python's sqlite3 has FTS5.",
        }})
        return
    h._json({"ok": True, "rows": rows})


def _why(h, path, params):
    slug = h._slug_or_refuse(params)
    if slug is None:
        return
    item_id = params.get("id", [None])[0]
    if not inspector.valid_item_id(item_id or ""):
        h._json({"ok": False, "error": {
            "what": f"{item_id!r} isn't a valid item id.",
            "why": "Item ids look like a-<hex>, e.g. o-1a2b3c4d5e6f.",
            "fix": "Open an entry from search or the checkpoint view.",
        }})
        return
    include_source = params.get("source", ["0"])[0] not in ("0", "", "false")
    result = inspector.inspect_item(slug, item_id, include_source=include_source)
    if result is None:
        h._json({"ok": False, "error": {
            "what": f"No item with id {item_id!r} in this project.",
            "why": "The id doesn't match any item across the project's checkpoints.",
            "fix": "Search for the item to find its current id.",
        }})
        return
    h._json(dict({"ok": True}, **result))


def _grid(h, path, params):
    slug = h._slug_or_refuse(params)
    if slug is None:
        return
    h._json(reader.project_grid(h.data_dir, slug))


def _refutations(h, path, params):
    slug = h._slug_or_refuse(params)
    if slug is None:
        return
    # The lane renders `daimon refute list` — refutations.listing is the
    # same fold and the same order the CLI prints. A slug is already its
    # own project_slug, so passing it as project_dir resolves to the same
    # bucket (the inspector endpoint leans on the same property).
    h._json(_refutations_payload(slug))


def _relations(h, path, params):
    slug = h._slug_or_refuse(params)
    if slug is None:
        return
    # History lane (#678 Phase 3): CONFIRMED edges only — the fold,
    # the confirmed boundary, and the erased-edge withholding all live
    # in relations.for_item, shared with the CLI's presentation seam,
    # so the two surfaces cannot drift. Texts are a read-time join
    # (the ledger holds no text by construction) scoped to the ids the
    # response actually names.
    item_id = params.get("id", [""])[0]
    rows, withheld = relations.for_item(item_id, project_dir=slug)
    named = {item_id}
    for row in rows:
        for endpoint in (row.get("from") or {}, row.get("to") or {}):
            named.add(str(endpoint.get("item_id") or ""))
    texts = {k: v for k, v in relations.endpoint_texts(slug).items()
             if k in named}
    h._json({"ok": True, "rows": rows, "texts": texts,
             "withheld": withheld})


def _ledger(h, path, params):
    slug = h._slug_or_refuse(params)
    if slug is None:
        return
    h._json(reader.project_ledger(h.data_dir, slug))


def _session(h, path, params):
    slug = h._slug_or_refuse(params)
    if slug is None:
        return
    sid = params.get("sid", [None])[0]
    if not sid:
        h._json({"ok": False, "error": {
            "what": "No session id was given.",
            "why": "/api/session needs a sid parameter to look up.",
            "fix": "Open a session from the ledger's session list.",
        }})
        return
    h._json(reader.session_events(h.data_dir, slug, sid))


def _activity(h, path, params):
    slug = h._slug_or_refuse(params)
    if slug is None:
        return
    h._json(reader.project_activity(h.data_dir, slug))


def _static(h, path, params):
    entry = _STATIC.get(path[len("/static/"):])
    if entry is None:
        h._send(404, "text/plain", b"not found")
        return
    file_path, ctype = entry
    h._send(200, ctype, file_path.read_bytes())


@dataclass(frozen=True)
class Route:
    """One viewer route. `owner` is the read-pipeline stage that converts it
    onto the view (None: the route shows no item content and never converts);
    `prefix` makes `path` match every request path that starts with it."""

    path: str
    handler: Callable
    owner: str | None
    prefix: bool = False


ROUTES: dict[str, Route] = {r.path: r for r in (
    Route("/", _page, None),
    Route("/api/projects", _projects, "8b-1"),
    Route("/api/checkpoints", _checkpoints, "8b-1"),
    Route("/api/checkpoint/", _checkpoint, "8b-1", prefix=True),
    Route("/api/history", _history, "8b-1"),
    Route("/api/diff", _diff, "8b-1"),
    Route("/api/biography", _biography, "8b-2"),
    Route("/api/recall", _recall, "9"),
    Route("/api/why", _why, "11"),
    Route("/api/grid", _grid, "8b-2"),
    Route("/api/refutations", _refutations, "11"),
    Route("/api/relations", _relations, "11"),
    Route("/api/ledger", _ledger, "8b-2"),
    Route("/api/session", _session, "8b-2"),
    Route("/api/activity", _activity, "8b-2"),
    Route("/static/", _static, None, prefix=True),
)}


def _match(path):
    """The route for a request path: an exact row first, then a prefix row."""
    route = ROUTES.get(path)
    if route is not None and not route.prefix:
        return route
    for route in ROUTES.values():
        if route.prefix and path.startswith(route.path):
            return route
    return None


def make_server(data_dir: Path, default_slug: str, project_label: str, port: int = 0):
    handler = partial(_Handler, data_dir, default_slug, project_label)
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
