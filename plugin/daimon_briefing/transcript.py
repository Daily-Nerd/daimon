"""Transcript access. Two sources:

1. from_session(session_id) — reads hermes session history via SessionDB. The hermes
   import is guarded (hermes only available in-hermes); unavailable -> [] not raise.
2. from_file(path) — CLI/dogfood fallback. `.jsonl` parses as an agent session
   transcript; anything else as plain text/markdown. Includes a dedicated
   branch for Windsurf Cascade's native transcript (#70).

All messages normalize to OpenAI-format dicts: {"role": str, "content": str},
plus an optional "id" (#358) when the host row carries a stable per-message
identifier (Claude Code JSONL `uuid`). Hosts without one — Windsurf Cascade's
native rows ({type, status, payload}, field-confirmed #70), the Codex event
stream (payload is just {type, message}), hermes SessionDB, markdown/plain
text — keep the exact two-key shape, and downstream quote verification falls
back to whole-transcript scanning.

#359: Claude Code rows whose only payload is tool_result blocks — previously
dropped as noise — surface as {"role": "tool", "content": <capped output>,
"id": uuid, "tool_result": True, ["tool_error": True]} so outcome claims can
ground in the concrete signal (exit status, test summary) they carry. Claude
Code only: it is the one host with BOTH stable ids and parseable tool
results; the other hosts' output stays byte-identical.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def _load_session_db():
    """Return a SessionDB instance, or None if hermes is not importable.

    # VERIFIED website/docs/developer-guide/session-storage.md:
    #   from hermes_state import SessionDB
    #   db = SessionDB()  # defaults to ~/.hermes/state.db
    #   db.get_messages_as_conversation(session_id) -> [{"role","content"}, ...]
    """
    try:
        from hermes_state import SessionDB  # type: ignore
    except Exception:
        return None
    try:
        return SessionDB()
    except Exception:
        return None


def from_session(session_id: str) -> list[dict]:
    """Read a session transcript by id. Returns [] when hermes is unavailable/empty."""
    db = _load_session_db()
    if db is None:
        return []
    try:
        msgs = db.get_messages_as_conversation(session_id)
    except Exception:
        return []
    return msgs or []


def _text_of(content) -> str:
    """Flatten Claude Code message content to plain text.

    String content passes through; block arrays keep only `text` blocks —
    thinking, tool_use, and tool_result blocks are noise for the serializer
    (tool_result rows get their own dedicated extraction, #359).
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p).strip()
    return ""


# #359: rendered tool output is a SIGNAL (exit status, test summary, error
# line), not conversation — the useful part lives at the head, and full
# payloads (a Read of a 2000-line file) would bloat the serialize prompt for
# nothing. Cap applies at parse time so every downstream consumer (chunking,
# rendering, quote verification) sees the same bounded text.
_TOOL_RESULT_MAX_CHARS = 500


# #512: `daimon` as the INVOKED binary — start of command or after a shell
# separator / substitution, tolerating env-var prefixes, sudo, uv run (with
# flags), python -m, and path prefixes. Deliberately NOT a bare substring:
# `rg daimon cli.py` greps ABOUT daimon and its output is a genuine witness
# (measured: a blanket tool-row strip costs 9.5% of the corpus's verifiable
# quotes; this invocation-scoped rule costs 0.06%).
#
# #781: the backtick is NOT in the delimiter class, though it opens a command
# substitution in shell. In a repository whose own directory is named `daimon`
# and whose issue bodies, PR bodies, docstrings and commit messages discuss its
# commands constantly, a backtick opens a markdown inline code span far more
# often. Measured over the local corpus: 127 rows were flagged ONLY by that
# delimiter and NOT ONE was a shell substitution — every one was prose a person
# wrote, blanked from the extractor as though daimon had produced it.
# The substitution it protected is still covered: `$(...)` reaches this pattern
# through the `(` delimiter, which is the spelling anyone writes today. The
# residual is legacy backtick substitution, which fails toward admitting an
# echo rather than destroying a witness — a real trade, measured at zero here
# and pinned by test_modern_command_substitution_still_matches.
_DAIMON_CMD_RE = re.compile(
    r"(?:^|[|;&(]|\&\&|\|\|)\s*"
    r"(?:\S+=\S+\s+)*(?:sudo\s+)?"
    # #778: `timeout` is the only wrapper with measured field usage, and the
    # case that earns it is `timeout N daimon handoff`, whose output is the
    # densest memory content daimon renders. Flags and the duration are both
    # consumed (`timeout -k 5 30s daimon ...`). Env assignments repeat after
    # it because both `timeout 60 FOO=1 daimon` and `FOO=1 timeout 60 daimon`
    # are real orderings. The wider wrapper family (`nice`, `xargs`, `env`,
    # `time`) is deliberately NOT enumerated: measured usage is zero and each
    # one adds prose-matching surface next to a repo whose own directory is
    # named `daimon`.
    r"(?:timeout\s+(?:-\S+\s+|\d+[a-z]*\s+)*)?"
    r"(?:\S+=\S+\s+)*"
    # #585: a shell wrapper puts a QUOTE immediately before the command, and
    # quotes are deliberately absent from the delimiter class above — adding
    # them there would match `rg "daimon" cli.py`, which greps ABOUT daimon and
    # whose output is a genuine witness. Recognise the wrapper explicitly
    # instead, the same way `uv run` is handled below.
    r"(?:(?:\S*/)?(?:ba|z|da|k)?sh\s+-[a-z]*c\s+['\"]?)?"
    r"(?:uv\s+run\s+(?:--?[\w-]+(?:[= ]\S+)?\s+)*)?"
    # #778: the module spelling gets its own branch. The old inline
    # `(?:python\s+-m\s+)?` could never fire: it required a following bare
    # `daimon`, and the module is `daimon_briefing`. This is how the CLI runs
    # from a source checkout, so it is what anyone testing a branch produces,
    # and it appears in no manifest section at all.
    r"(?:(?:\S*/)?python[\d.]*\s+-m\s+daimon_briefing(?:\.[\w.]+)?(?:\s|$)"
    r"|(?:\S*/)?daimon(?:\s|$))", re.MULTILINE)
# #783: re.MULTILINE means `^` matches at every line start, not just the start of
# the command. That is mostly right — a multi-line script whose second line invokes
# daimon is a genuine invocation — but it also matches a line of PROSE that happens
# to begin with `daimon`, inside a heredoc body or a quoted argument. Those rows are
# blanked from the extractor as though daimon had produced them.
# Measured before deciding, at three corpus definitions so the answer could not rest
# on one: of the matches anchored at a non-first line start, ~13% are prose (heredoc
# body plus quoted string) and ~87% are genuine commands. The number that decides the
# trade is COMMANDS FLAGGED ONLY BY PROSE, since anything with a real invocation
# elsewhere is flagged correctly regardless: that is ~2.3%, stable across all three.
# Kept as-is, deliberately. Heredoc opener/terminator tracking is short and was
# written to take this measurement, but it would not touch the quoted-string group,
# so it narrows the family without closing it. Dropping re.MULTILINE is worse still:
# it loses ordinary multi-line shell, which is common and genuine.
# Two things a future editor should know. The affected commands are mostly commit
# bodies and prose file writes, so the count is low while the content per command is
# dense — do not read 2.3% as uniformly cheap. And `daimon audit quotes` cannot judge
# a change here: it returns identical output with this detection disabled entirely,
# so it is never the gate. The cost lands on what the extractor reads, and that half
# has no instrument today; when it gets one, revisit. This is the third narrowing of
# this pattern, and #591 is the standing argument that provenance belongs on the
# message rather than inferred from a command string.


def _is_daimon_tool_use(block: dict) -> bool:
    """True when a tool_use block invokes daimon: an MCP tool whose name
    carries `daimon`, or a shell command with daimon in command position."""
    if "daimon" in str(block.get("name") or "").lower():
        return True
    inp = block.get("input")
    cmd = inp.get("command") if isinstance(inp, dict) else None
    return isinstance(cmd, str) and bool(_DAIMON_CMD_RE.search(cmd))


def _daimon_tool_use_ids(objects: list[dict]) -> set[str]:
    """ids of every tool_use block that invokes daimon, across the file —
    the prepass that lets a tool_result row know its own provenance (#512).
    A tool result born from a daimon invocation is daimon's own output
    echoed back through the transcript; quote verification must never treat
    it as a witness, and keying on the INVOCATION (not the output shape)
    means every daimon subcommand — current or future — is covered without
    enumerating render formats."""
    ids: set[str] = set()
    for obj in objects:
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if (isinstance(block, dict) and block.get("type") == "tool_use"
                    and _is_daimon_tool_use(block)):
                bid = str(block.get("id") or "").strip()
                if bid:
                    ids.add(bid)
    return ids


def _tool_result_of(obj: dict) -> tuple[str, bool, set[str]] | None:
    """(flattened text, is_error, answered tool_use ids) for a row whose
    payload is tool_result blocks, or None when the row carries none.
    `content` inside a block can be a plain string or a nested block list —
    both flatten. Empty output is still a signal ("ran, said nothing"), kept
    via a placeholder. The ids ride along from the same block walk (#512) so
    the caller can resolve the row's provenance without a second pass."""
    msg = obj.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    use_ids: set[str] = set()
    is_error = False
    found = False
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        found = True
        if block.get("is_error"):
            is_error = True
        use_id = str(block.get("tool_use_id") or "").strip()
        if use_id:
            use_ids.add(use_id)
        inner = block.get("content")
        if isinstance(inner, str):
            parts.append(inner.strip())
        elif isinstance(inner, list):
            parts.append(_text_of(inner))
    if not found:
        return None
    text = "\n".join(p for p in parts if p).strip()[:_TOOL_RESULT_MAX_CHARS]
    return (text or "(no output)", is_error, use_ids)


def _from_jsonl(text: str) -> list[dict]:
    """Parse a JSONL agent transcript into conversation messages.

    Claude Code exposes stable-enough user/assistant rows today. Codex also
    exposes a `transcript_path` to hooks, but its docs explicitly say the format
    is not stable, so this parser accepts a small set of role/content shapes and
    ignores everything else. A noise-only file returns [] — never the raw-blob
    fallback.
    """
    objects: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        objects.append(obj)

    # Current Codex rollouts emit each visible turn twice: once as an
    # `event_msg` and once as a nested `response_item`. Prefer the event stream
    # when present so platform-injected developer context and duplicate turns
    # never reach the serializer.
    codex_messages: list[dict] = []
    for obj in objects:
        if obj.get("type") != "event_msg":
            continue
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        payload_type = payload.get("type")
        role = {"user_message": "user", "agent_message": "assistant"}.get(
            str(payload_type or "")
        )
        content = payload.get("message")
        if role and isinstance(content, str) and content.strip():
            codex_messages.append({"role": role, "content": content.strip()})
            continue
        # Codex CLI 0.147.0 (#622) dropped user_message/agent_message events:
        # visible turns now arrive as item_completed with a PascalCase
        # item.type and text as a content-block list. Block-type case differs
        # by role in the field (UserMessage blocks say "text", AgentMessage
        # blocks say "Text") — match it case-insensitively. AgentMessage
        # phases (commentary, final_answer) are both assistant content.
        if payload_type != "item_completed":
            continue
        item = payload.get("item")
        if not isinstance(item, dict):
            continue
        role = {"UserMessage": "user", "AgentMessage": "assistant"}.get(
            str(item.get("type") or "")
        )
        if role is None:
            continue
        blocks = item.get("content")
        if not isinstance(blocks, list):
            continue
        parts = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "").lower() != "text":
                continue
            block_text = block.get("text")
            if isinstance(block_text, str) and block_text.strip():
                parts.append(block_text.strip())
        joined = "\n".join(parts)
        if joined:
            codex_messages.append({"role": role, "content": joined})
    if codex_messages:
        return codex_messages

    # Windsurf Cascade's native transcript (#70): rows carry
    # {type, status, <payload-key>} and the payload key does NOT always equal
    # the type (grep_search_v2 keeps payload key grep_search) — so text
    # carriers are matched by type, never derived from it. The `status` key
    # (deliberately NOT a key count: a schema-widened row must still parse,
    # or the whole branch silently disables and the role-less fallback drops
    # every planner_response) is what distinguishes a genuine Cascade row
    # from other hosts' JSONL that happens to reuse a `user_input` key.
    # canceled lines are dropped; done AND error both serialize (a
    # failed-but-emitted response is still context).
    windsurf_messages: list[dict] = []
    for obj in objects:
        obj_type = obj.get("type")
        if obj_type not in ("user_input", "planner_response"):
            continue
        if "status" not in obj:
            continue
        if obj.get("status") == "canceled":
            continue
        role = "user" if obj_type == "user_input" else "assistant"
        payload = obj.get(obj_type)
        if not isinstance(payload, dict):
            continue
        text_key = "user_response" if obj_type == "user_input" else "response"
        payload_text = payload.get(text_key)
        if isinstance(payload_text, str) and payload_text.strip():
            windsurf_messages.append(
                {"role": role, "content": payload_text.strip()})
    if windsurf_messages:
        return windsurf_messages

    messages: list[dict] = []
    daimon_uses = _daimon_tool_use_ids(objects)  # #512 provenance prepass
    for obj in objects:
        if obj.get("isSidechain") or obj.get("isMeta"):
            continue

        role = _role_of(obj)
        if role is None:
            continue
        # #358: Claude Code rows carry a stable per-message `uuid` —
        # keep it as the message id so verbatim items can bind to the
        # exact transcript entry their quote came from. A discriminating
        # FIELD, not a row shape (deadend #20): rows without a usable
        # string uuid keep the exact two-key shape.
        raw_uuid = obj.get("uuid")
        mid = (raw_uuid.strip()
               if isinstance(raw_uuid, str) and raw_uuid.strip() else None)
        content = _content_of(obj)
        if content:
            msg = {"role": role, "content": content}
            if mid is not None:
                msg["id"] = mid
            messages.append(msg)
            continue
        # #359: a row whose ONLY payload is tool_result blocks used to be
        # dropped as noise — surface it as a signal-bearing "tool" message so
        # outcome claims can ground in the concrete evidence it holds (exit
        # status, test summary, error line). Only rows with a usable uuid
        # qualify: grounding is pointer-based ([mN] marker -> host id), and an
        # id-less tool row cannot be cited — id-less hosts keep pre-#359
        # output byte-identical. Discriminating FIELDS again (deadend #20):
        # tool_result block type + uuid, never a row shape.
        if mid is not None:
            tool = _tool_result_of(obj)
            if tool is not None:
                tool_text, is_error, use_ids = tool
                tool_msg: dict = {"role": "tool", "content": tool_text,
                                  "id": mid, "tool_result": True}
                if is_error:
                    tool_msg["tool_error"] = True
                # #512: provenance flag, resolved via the tool_use pairing.
                # Discriminating FIELD again (deadend #20) — downstream strips
                # key on this, never on the rendered shape.
                if use_ids & daimon_uses:
                    tool_msg["daimon_output"] = True
                messages.append(tool_msg)
    return messages


def _role_of(obj: dict) -> str | None:
    """Best-effort role extraction for known JSONL transcript shapes."""
    for key in ("role", "type"):
        val = str(obj.get(key) or "").lower()
        if val in ("user", "assistant"):
            return val

    typ = str(obj.get("type") or obj.get("event") or "").lower()
    if typ in ("user_input", "user_message", "prompt"):
        return "user"
    if typ in ("assistant_message", "assistant_response", "agent_message", "response"):
        return "assistant"

    msg = obj.get("message")
    if isinstance(msg, dict):
        val = str(msg.get("role") or "").lower()
        if val in ("user", "assistant"):
            return val
    return None


def _content_of(obj: dict) -> str:
    """Best-effort content extraction for known JSONL transcript shapes."""
    msg = obj.get("message")
    if isinstance(msg, dict):
        text = _text_of(msg.get("content"))
        if text:
            return text

    text = _text_of(obj.get("content"))
    if text:
        return text

    for key in ("text", "prompt", "response"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    user_input = obj.get("user_input")
    if isinstance(user_input, dict):
        for key in ("user_response", "prompt", "text", "content"):
            val = user_input.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

    return ""


def _stamp_epoch(stamp) -> float | None:
    """Epoch seconds for a transcript row's `timestamp`, or None when absent or
    malformed. Accepts fractional seconds and a trailing Z (Claude Code rows emit
    e.g. `2026-07-01T10:05:30.500Z`), unlike store._created_epoch's strict
    checkpoint format — the two clocks carry different precisions on purpose."""
    if not isinstance(stamp, str):
        return None
    try:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def last_timestamp(path) -> str | None:
    """Session-end stamp for a `.jsonl` transcript: the max top-level `timestamp`
    across rows, normalized to the checkpoint `created` format (#123). The max —
    not the last row — so out-of-order rows can't report an early end. None for
    non-jsonl files, unreadable files, or stamp-free transcripts; callers fall
    back (cli uses the file mtime)."""
    p = Path(path)
    if p.suffix != ".jsonl":
        return None
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    best = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        epoch = _stamp_epoch(obj.get("timestamp"))
        if epoch is not None and (best is None or epoch > best):
            best = epoch
    if best is None:
        return None
    return datetime.fromtimestamp(best, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Matches "**user**:", "user:", "**Assistant**:" etc. at line start.
_ROLE_RE = re.compile(r"^\s*\**\s*(user|assistant|system|tool)\s*\**\s*:\s*", re.IGNORECASE)


def file_sha256(path) -> str | None:
    """SHA-256 hex over a transcript file's RAW bytes (#125), or None when the
    file is unreadable. Binds a checkpoint to its source content, not just the
    filename stem, so a transcript later truncated, rotated, or edited is
    detectable. Hashes bytes, not decoded text — no encoding normalization can
    silently change the digest."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def from_file(path) -> list[dict]:
    """Parse a transcript file into OpenAI-format messages.

    `.jsonl` -> agent session transcript (user/assistant turns only).
    Markdown with role markers (**user**: / assistant: / ...) -> one message per turn.
    Plain text with no markers -> a single user message carrying the whole blob.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    text = p.read_text(encoding="utf-8")

    if p.suffix == ".jsonl":
        return _from_jsonl(text)

    messages: list[dict] = []
    current_role = None
    buf: list[str] = []

    def flush():
        if current_role is not None:
            content = "\n".join(buf).strip()
            if content:
                messages.append({"role": current_role, "content": content})

    for line in text.splitlines():
        m = _ROLE_RE.match(line)
        if m:
            flush()
            current_role = m.group(1).lower()
            buf = [line[m.end():]]
        else:
            buf.append(line)
    flush()

    if not messages:
        blob = text.strip()
        if blob:
            messages.append({"role": "user", "content": blob})
    return messages
