# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Import real Claude Code sessions as Track A/C transcripts.

Claude Code stores each session as JSONL under
~/.claude/projects/<project-slug>/<session-id>.jsonl. This tool converts one
into a clean, redacted plain-text transcript (user + assistant text, tool calls
compressed to markers, thinking dropped).

PRIVACY MODEL (two layers):
1. Tool RESULTS are excluded entirely. Command output, file reads, and env
   dumps — the highest-risk secret carriers — never enter the transcript. This
   also keeps the transcript focused on the discussion (decisions, open
   questions, beliefs), which is exactly what Track A scores.
2. Text-borne secrets (a key pasted into a prompt) are regex-redacted on top.
   Best-effort, NOT bulletproof. When routing to a CLOUD model (kimi until the
   local GPU lands), REVIEW the output before running the harness.

List sessions in a project (newest first, with titles):
    uv run lib/claude_sessions.py --list --project MyOrg-my-repo
    uv run lib/claude_sessions.py --list-projects

Convert one to a Track A session file:
    uv run lib/claude_sessions.py --project MyOrg-my-repo \\
        --session <id> --out track-a/sessions/S1.txt
"""

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

PROJECTS = Path(os.path.expanduser("~/.claude/projects"))

SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN[^-]+PRIVATE KEY-----.*?-----END[^-]+PRIVATE KEY-----", re.S), "[REDACTED-PRIVATE-KEY]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "[REDACTED-KEY]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED-AWS-KEY]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED-GH-TOKEN]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED-GH-TOKEN]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "[REDACTED-SLACK-TOKEN]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*[\"']?([A-Za-z0-9/+_\-]{12,})[\"']?",), None),
]


def redact(s: str) -> str:
    for pat, repl in SECRET_PATTERNS:
        if repl is None:  # keyed assignment: keep the key name, mask the value
            s = pat.sub(lambda m: f"{m.group(1)}=[REDACTED]", s)
        else:
            s = pat.sub(repl, s)
    return s


NOISE_PAIRED = re.compile(
    r"<(command-name|command-message|command-args|local-command-stdout|"
    r"local-command-stderr|bash-input|bash-stdout|bash-stderr|system-reminder)>"
    r".*?</\1>",
    re.S,
)
NOISE_PREFIXES = (
    "<local-command-caveat>",
    "Caveat:",
    "<command-name>",
    "[Request interrupted",
)


def clean_user_text(text: str) -> str:
    """Strip injected command wrappers / system reminders; '' if nothing real remains."""
    text = NOISE_PAIRED.sub("", text)
    stripped = text.strip()
    if not stripped or stripped.startswith(NOISE_PREFIXES):
        return ""
    return stripped


def text_from_content(content) -> str:
    """Extract human-readable text from a message.content (str or block list)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            parts.append(b.get("text", ""))
        # 'thinking', 'tool_use', and 'tool_result' are dropped — Track A wants
        # the discussion, not the execution trace. A turn that is only tool
        # calls carries no decision/question to reconstruct, so it vanishes.
    return "\n".join(p for p in parts if p)


def iter_sessions(slug: str):
    d = PROJECTS / slug
    for f in sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        yield f


def session_title(path: Path) -> str:
    title = ""
    with open(path) as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") == "ai-title" and d.get("title"):
                title = d["title"]
                break
    return title or "(untitled)"


def convert(path: Path, do_redact: bool) -> tuple[str, dict]:
    turns, user_n, asst_n = [], 0, 0
    with open(path) as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = d.get("type")
            if t not in ("user", "assistant"):
                continue
            if d.get("isMeta"):
                continue
            msg = d.get("message", {})
            text = text_from_content(msg.get("content")).strip()
            if t == "user":
                text = clean_user_text(text)
            if not text:
                continue
            role = "User" if t == "user" else "Assistant"
            if t == "user":
                user_n += 1
            else:
                asst_n += 1
            turns.append(f"{role}: {text}")
    body = "\n\n".join(turns)
    if do_redact:
        body = redact(body)
    approx_tokens = len(body) // 4
    return body, {"user_turns": user_n, "assistant_turns": asst_n, "approx_tokens": approx_tokens, "chars": len(body)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Import Claude Code sessions as transcripts")
    ap.add_argument("--list-projects", action="store_true", help="list project slugs with session counts")
    ap.add_argument("--list", action="store_true", help="list sessions in --project")
    ap.add_argument("--project", help="project slug (under ~/.claude/projects, the leading dashes optional)")
    ap.add_argument("--session", help="session id (filename stem) to convert")
    ap.add_argument("--out", help="output transcript path")
    ap.add_argument("--no-redact", action="store_true", help="DANGER: skip secret redaction")
    args = ap.parse_args()

    if args.list_projects:
        for d in sorted(PROJECTS.glob("*")):
            if d.is_dir():
                n = len(list(d.glob("*.jsonl")))
                if n:
                    print(f"  {n:3}  {d.name}")
        return 0

    def resolve_slug(s: str) -> str:
        if (PROJECTS / s).is_dir():
            return s
        # allow shorthand without the leading "-Users-...-" prefix
        matches = [d.name for d in PROJECTS.glob("*") if d.is_dir() and d.name.endswith(s)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            sys.exit(f"No project matching '{s}'. Try --list-projects.")
        sys.exit(f"Ambiguous '{s}': {matches}")

    if args.list:
        if not args.project:
            sys.exit("--list needs --project")
        slug = resolve_slug(args.project)
        print(f"Sessions in {slug} (newest first):\n")
        for f in iter_sessions(slug):
            kb = f.stat().st_size // 1024
            print(f"  {f.stem}  {kb:>5} KB   {session_title(f)}")
        return 0

    if args.session and args.out:
        slug = resolve_slug(args.project) if args.project else None
        path = None
        if slug:
            cand = PROJECTS / slug / f"{args.session}.jsonl"
            if cand.exists():
                path = cand
        if path is None:  # search all projects
            hits = glob.glob(str(PROJECTS / "*" / f"{args.session}.jsonl"))
            if hits:
                path = Path(hits[0])
        if path is None:
            sys.exit(f"Session {args.session} not found.")
        body, stats = convert(path, do_redact=not args.no_redact)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body)
        red = "OFF (raw secrets!)" if args.no_redact else "on"
        print(f"Wrote {out}")
        print(f"  turns: {stats['user_turns']} user / {stats['assistant_turns']} assistant")
        print(f"  ~{stats['approx_tokens']} tokens ({stats['chars']} chars), redaction {red}")
        print("  tool outputs excluded (privacy + focus); thinking dropped")
        if stats["approx_tokens"] > 60000:
            print("  WARNING: large session — may exceed the serialize model's context. Consider a shorter session.")
        print("  REVIEW the output before running the harness against a cloud model.")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
