import json
import re
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import reader

# Engine imports (#670): search and item inspection are served by daimon's own
# engines — recall.search is the one matcher (the viewer renders recall, it
# never grows a second search engine) and inspector.inspect_item is the same
# read-side receipt `daimon why` prints. reader.py stays daimon-import-free
# (files are its seam); the engine boundary lives here in dispatch only.
from daimon_briefing import inspector, recall, refutations

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
        """Slug is accepted only if it matches ^[\\w-]+$ AND exact-matches a name from a
        fresh list_buckets() scan. Never joined to a path before that double-check passes."""
        raw = params.get("project", [self.default_slug])[0]
        if not _SLUG_RE.fullmatch(raw or ""):
            return None
        buckets = reader.list_buckets(self.data_dir)
        if not any(b["slug"] == raw for b in buckets):
            return None
        return raw

    def do_GET(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
        if host not in ("127.0.0.1", "localhost"):
            self._send(403, "text/plain", b"forbidden: bad host header")
            return
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        params = parse_qs(parsed.query)
        if path == "/":
            self._send(200, "text/html; charset=utf-8", _PAGE.read_bytes())
        elif path == "/api/projects":
            self._json({"projects": reader.list_buckets(self.data_dir), "current": self.default_slug})
        elif path == "/api/checkpoints":
            slug = self._resolve_slug(params)
            if slug is None:
                self._json(self._bad_slug_error(params.get("project", [self.default_slug])[0]))
                return
            bucket = self.data_dir / slug
            # list_recent serves the pointer window; project_history serves every
            # session file for the slug. The sidebar needs both numbers or it
            # silently presents a window as if it were the whole history.
            self._json({"project": self._label_for(slug),
                        "checkpoints": reader.list_recent(bucket),
                        "sessions_total": len(reader.project_history(self.data_dir, slug)["sessions"])})
        elif path.startswith("/api/checkpoint/"):
            slug = self._resolve_slug(params)
            if slug is None:
                self._json(self._bad_slug_error(params.get("project", [self.default_slug])[0]))
                return
            ref = path.removeprefix("/api/checkpoint/")
            self._json(reader.load_checkpoint(self.data_dir, slug, ref))
        elif path == "/api/history":
            slug = self._resolve_slug(params)
            if slug is None:
                self._json(self._bad_slug_error(params.get("project", [self.default_slug])[0]))
                return
            result = reader.project_history(self.data_dir, slug)
            result["project"] = self._label_for(slug)
            self._json(result)
        elif path == "/api/diff":
            slug = self._resolve_slug(params)
            if slug is None:
                self._json(self._bad_slug_error(params.get("project", [self.default_slug])[0]))
                return
            a = params.get("a", [None])[0]
            b = params.get("b", [None])[0]
            if a is None or b is None:
                hist = reader.project_history(self.data_dir, slug)
                sessions = hist["sessions"]
                if len(sessions) < 2:
                    self._json({"ok": True, "empty": "single_checkpoint", "sessions": len(sessions)})
                    return
                a = sessions[1]["session_id"]
                b = sessions[0]["session_id"]
            self._json(reader.diff_checkpoints(self.data_dir, slug, a, b))
        elif path == "/api/biography":
            slug = self._resolve_slug(params)
            if slug is None:
                self._json(self._bad_slug_error(params.get("project", [self.default_slug])[0]))
                return
            item_id = params.get("id", [None])[0]
            self._json(reader.item_biography(self.data_dir, slug, item_id))
        elif path == "/api/recall":
            slug = self._resolve_slug(params)
            if slug is None:
                self._json(self._bad_slug_error(params.get("project", [self.default_slug])[0]))
                return
            q = params.get("q", [None])[0]
            if not q or not q.strip():
                self._json({"ok": False, "error": {
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
                self._json({"ok": False, "error": {
                    "what": f"limit {raw_limit!r} isn't a positive whole number.",
                    "why": "The result window must be at least 1.",
                    "fix": "Drop the limit parameter or pass a positive number.",
                }})
                return
            try:
                rows = recall.search(q, slug=slug, limit=limit)
            except recall.RecallError as exc:
                self._json({"ok": False, "error": {
                    "what": "Search is unavailable.",
                    "why": str(exc),
                    "fix": "Check that this Python's sqlite3 has FTS5.",
                }})
                return
            self._json({"ok": True, "rows": rows})
        elif path == "/api/why":
            slug = self._resolve_slug(params)
            if slug is None:
                self._json(self._bad_slug_error(params.get("project", [self.default_slug])[0]))
                return
            item_id = params.get("id", [None])[0]
            if not inspector.valid_item_id(item_id or ""):
                self._json({"ok": False, "error": {
                    "what": f"{item_id!r} isn't a valid item id.",
                    "why": "Item ids look like a-<hex>, e.g. o-1a2b3c4d5e6f.",
                    "fix": "Open an entry from search or the checkpoint view.",
                }})
                return
            include_source = params.get("source", ["0"])[0] not in ("0", "", "false")
            result = inspector.inspect_item(slug, item_id, include_source=include_source)
            if result is None:
                self._json({"ok": False, "error": {
                    "what": f"No item with id {item_id!r} in this project.",
                    "why": "The id doesn't match any item across the project's checkpoints.",
                    "fix": "Search for the item to find its current id.",
                }})
                return
            self._json(dict({"ok": True}, **result))
        elif path == "/api/refutations":
            slug = self._resolve_slug(params)
            if slug is None:
                self._json(self._bad_slug_error(params.get("project", [self.default_slug])[0]))
                return
            # The lane renders `daimon refute list` — refutations.listing is the
            # same fold and the same order the CLI prints. A slug is already its
            # own project_slug, so passing it as project_dir resolves to the same
            # bucket (the inspector endpoint leans on the same property).
            self._json({"ok": True, "rows": refutations.listing(project_dir=slug)})
        elif path == "/api/ledger":
            slug = self._resolve_slug(params)
            if slug is None:
                self._json(self._bad_slug_error(params.get("project", [self.default_slug])[0]))
                return
            self._json(reader.project_ledger(self.data_dir, slug))
        elif path == "/api/session":
            slug = self._resolve_slug(params)
            if slug is None:
                self._json(self._bad_slug_error(params.get("project", [self.default_slug])[0]))
                return
            sid = params.get("sid", [None])[0]
            if not sid:
                self._json({"ok": False, "error": {
                    "what": "No session id was given.",
                    "why": "/api/session needs a sid parameter to look up.",
                    "fix": "Open a session from the ledger's session list.",
                }})
                return
            self._json(reader.session_events(self.data_dir, slug, sid))
        elif path == "/api/activity":
            slug = self._resolve_slug(params)
            if slug is None:
                self._json(self._bad_slug_error(params.get("project", [self.default_slug])[0]))
                return
            self._json(reader.project_activity(self.data_dir, slug))
        elif path.startswith("/static/"):
            entry = _STATIC.get(path[len("/static/"):])
            if entry is None:
                self._send(404, "text/plain", b"not found")
                return
            file_path, ctype = entry
            self._send(200, ctype, file_path.read_bytes())
        else:
            self._send(404, "text/plain", b"not found")

def make_server(data_dir: Path, default_slug: str, project_label: str, port: int = 0):
    handler = partial(_Handler, data_dir, default_slug, project_label)
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
