"""#365 slice 1: deterministic external-state spot-check for carried PR/issue-
state claims at briefing render.

A claim true at capture can go false OFF-session (a PR merged by someone
else). Carry's supersession detection only fires when a later session
contradicts the item, and the briefing's VERIFY BEFORE TRUSTING section is
advice, not machinery — so a carried "PR #N awaiting review" whose PR merged
yesterday renders as confidently as a fresh fact. This module is the
machinery: at brief time, carried items making a CHECKABLE claim (repo-local
"#N" ref + a state word) are spot-checked against reality with read-only `gh`
probes, and a contradicted item is flagged in the render + offered the
existing resolve/reverify confirm path.

Hard constraints (the briefing must never block or fail on the network):
- strict aggregate wall-clock budget (BUDGET_SECONDS) — probes run in
  parallel and anything unfinished at the deadline is killed and SKIPPED;
- probe count cap (MAX_PROBES) — first N distinct refs only;
- `gh` missing / no GitHub remote / non-zero exit / bad output -> skip
  SILENTLY: the render is exactly what it would be without this module.

The stamp is TRANSIENT (underscore key, same convention as withhold's
`_supersede_candidate`): it lives only on the in-memory checkpoint the brief
renders; nothing here writes to disk. Probes are read-only by construction —
`gh pr view` / `gh issue view` only.

Slice 1 was deliberately narrow: PR/issue state claims only. The measurable
question it answered — via the worldcheck:confirmed/contradicted/skipped usage
counters the CLI writes — is how often a carried repo-state claim is already
false at next read; that fires-true rate was the pre-registered evidence gate
for later claim classes.

#397 slice 2 opens that gate and adds three DETERMINISTIC classes that need
no network at all — file-exists (`Path.exists()`, no subprocess), branch-state
(git's on-disk refs, read directly), dependency-version (the lockfile or
manifest on disk). Everything the slice-1 contract promised holds across all
four classes: ONE aggregate budget, ONE probe cap, silent skip on any failure,
contradicted items flagged and never dropped. Two rules make the shared budget
fair rather than first-come-lucky:

- ALLOCATION follows checkpoint order, so no class is privileged when the cap
  binds;
- EXECUTION runs the on-disk classes BEFORE the `gh` fan-out, so a hanging
  network probe can never starve a probe that costs microseconds.

Counters land per class (worldcheck:<class>:<outcome>, counted per CLAIM) so
the fires-true rate is readable per class before any further expansion, while
the aggregate three count once per ITEM (#830) under a precedence rollup —
contradicted > confirmed > skipped (#833): an item is what its strongest
outcome says it is, so a probe-cap- or deadline-starved claim's "skipped"
never swallows a real answer on the item's other axis. Content-hash — the strongest
form — is deliberately NOT here: it would have to capture a hash at serialize
time, which changes the serializer contract, so it is its own issue.

#439 slice 3 adds RECEIPT-VALIDITY, the first class whose subject is daimon's
own artifact rather than the world around it. Slices 1-2 ask "is what this item
says still true"; this one asks "is the session that FIRST said it still
provably the session that said it". The briefing's existing check is only a
cheap byte match (receipts.verbatim_degraded); here the carried item's ORIGIN
checkpoint (#268's write-time binding) gets the FULL vitni verification —
signature, canonical structure, policy binding — sampled at one origin per day
and run under this module's own deadline, never `receipts._run_cli`'s blocking
10s timeout. A failed verification flags the item like any other failed check
AND feeds the #376 rejection ledger, which is the one thing worldcheck cannot
do itself: nothing here writes to disk, so `check()` RETURNS the failures and
the CLI appends them.
"""

import json
import re
import shutil
import subprocess
import time
from collections import namedtuple
from pathlib import Path

from . import config, receipts, serializer, store

# Aggregate wall-clock budget for ALL probes together (sub-second by
# contract). Probes run in parallel, so this bounds the render delay, not a
# per-probe allowance.
BUDGET_SECONDS = 0.8

# First N distinct targets get probed; further claims count as skipped. Caps
# the fan-out no matter how claim-heavy a checkpoint gets. The cap is shared
# across ALL classes: cheap disk probes spend from the same allowance as `gh`,
# which is why allocation follows checkpoint order rather than class.
MAX_PROBES = 5

# #833: per-item aggregate rollup precedence — an item is what its strongest
# outcome says it is, and "skipped" never decides when another outcome
# exists (a probe-cap- or deadline-starved claim must not swallow a real
# answer on the item's other axis).
_OUTCOME_RANK = {"skipped": 0, "confirmed": 1, "contradicted": 2}

# Claim classes, in the fixed priority order claim_for tries them. The order
# is part of the contract: it keeps an item's reading stable as classes are
# added, so a text carrying both a PR ref and a path keeps its slice-1 claim.
PR_STATE = "pr-state"
BRANCH_STATE = "branch-state"
FILE_EXISTS = "file-exists"
DEPENDENCY_VERSION = "dependency-version"

# #439. Deliberately NOT in claim_for's priority list: that list is
# text-derived and first-match-wins, so a receipt class inside it would either
# steal an item's PR-state reading or be starved by it. Receipt claims come
# from the item's `origin_session` stamp, not from its text, so they are
# collected in their own pass and an item may legitimately carry BOTH.
RECEIPT_VALIDITY = "receipt-validity"

# Classes answered from disk alone — no subprocess, no network.
_LOCAL_CLASSES = frozenset({BRANCH_STATE, FILE_EXISTS, DEPENDENCY_VERSION})

Claim = namedtuple("Claim", "num kind expected")
PathClaim = namedtuple("PathClaim", "path expected")
BranchClaim = namedtuple("BranchClaim", "name expected")
DepClaim = namedtuple("DepClaim", "name version")
# No `expected` field: a receipt claim is implicit — carrying an item asserts
# its origin's provenance still holds, and only "VALID" satisfies that.
ReceiptClaim = namedtuple("ReceiptClaim", "session")

# One receipt probe per brief, no matter how many origins a checkpoint carries:
# it is the only class that spawns a CRYPTO subprocess, and the point is a
# spot-check, not an audit (`daimon verify-receipt` is the audit).
MAX_RECEIPT_PROBES = 1

# The three answers a receipt probe can produce. Same bounded-vocabulary rule
# as _KNOWN_STATES: these are the only values that reach _flag.
_VALID = "VALID"            # vitni verified signature + structure + binding
_INVALID = "INVALID"        # vitni REJECTED it — a real provenance failure
_TAMPERED = "TAMPERED"      # bytes no longer match the signed outputs_hash

# `check()`'s return is a counter dict the CLI turns into usage counters. This
# reserved key (underscore, same convention as the transient `_worldcheck`
# stamp) carries the receipt FAILURES out to a caller that may write them to
# the #376 rejection ledger — worldcheck itself writes nothing to disk, which
# is the whole reason the rows travel rather than land here. Present only when
# something actually failed, so the happy-path dict stays all-ints.
LEDGER_KEY = "_ledger"
_LEDGER_CHECK = "receipt"
_LEDGER_REASONS = {_TAMPERED: "receipt-tampered", _INVALID: "receipt-invalid"}
# #839: the cure row. A SEPARATE check name, not the same check with a
# passing reason, because verification_counts sums every check into a "has
# verification ever caught anything here" total and a cure is not a catch.
# Emitted unconditionally here, exactly like a contradiction row; whether it
# is worth WRITING is decided at the write boundary (store.append_receipt_cure),
# because that is where the current ledger state is already in hand and where
# worldcheck's write-nothing-to-disk contract stays intact.
LEDGER_CONFIRM_CHECK = "receipt-ok"
_LEDGER_CONFIRM_REASON = "receipt-valid"

# A probe: how to spawn it, what to feed its stdin, and how to read its answer.
# The parser rides PER PROBE because the classes speak different dialects
# (`gh` answers {"state": ...}, vitni answers {"valid": ...}) while sharing ONE
# parallel fan-out — separate runners would let one class's slow probe
# serialize behind the other's entire budget.
Probe = namedtuple("Probe", "argv stdin parse")

# Repo-LOCAL ref: "#N" optionally preceded by an explicit "PR"/"pull
# request"/"issue" kind word. The lookbehind rejects any word char (or
# '/', '#', '-') butting up against the '#', so cross-repo refs like
# "gemini-cli#14715" or "owner/repo#12" NEVER match — `gh` here would answer
# for the wrong repository. Bounded quantifiers throughout: this fullmatches
# nothing, but it does scan checkpoint text, which is attacker-adjacent.
_REF_RE = re.compile(
    r"(?i)(?:\b(?P<kind>pr|pull request|issue)\s+)?(?<![\w/#-])#(?P<num>\d{1,6})\b")

# The state vocabulary that makes a ref an actual STATE CLAIM (issue #365's
# list). Bare "#48 slice 1" has no state word -> no claim -> nothing to check.
_STATE_RE = re.compile(r"(?i)\b(awaiting|open|merged|closed|review)\b")

# Claim direction: open-ish words assert the thing is still live; done-ish
# words assert it landed. A text containing BOTH is ambiguous — skip, because
# a wrong contradiction flag is worse than no check (don't-guess bias, same
# stance as carry's unique-match gate).
_OPENISH = frozenset({"awaiting", "open", "review"})
_DONEISH = frozenset({"merged", "closed"})

# The only actual states allowed to reach the rendered flag. The note text
# rides into briefing output (and the hook-injected LLM context), so the
# vocabulary is bounded here — `gh` output is trusted for truth, not for text.
_KNOWN_STATES = frozenset({"OPEN", "CLOSED", "MERGED"})


def claim_of(text):
    """The PR/issue-state claim in `text`, or None when there is nothing
    checkable. Conservative by design: requires a repo-local "#N" ref AND
    unambiguous state vocabulary; the FIRST ref wins when several appear
    (one claim per item keeps the probe budget honest)."""
    ref = _REF_RE.search(str(text or ""))
    if not ref:
        return None
    words = {w.lower() for w in _STATE_RE.findall(str(text))}
    open_words, done_words = words & _OPENISH, words & _DONEISH
    if not words or (open_words and done_words):
        return None  # no state claim, or an ambiguous one — nothing to check
    if open_words:
        expected = frozenset({"OPEN"})
    elif "merged" in done_words and "closed" not in done_words:
        expected = frozenset({"MERGED"})
    else:
        # "closed" (possibly alongside "merged"): for a PR either terminal
        # state satisfies the claim; an issue can only ever answer CLOSED.
        expected = frozenset({"CLOSED", "MERGED"})
    kind = (ref.group("kind") or "").lower()
    if kind in ("pr", "pull request"):
        kind = "pr"
    elif kind != "issue":
        # Bare ref: merge/review vocabulary is PR-shaped; plain open/closed
        # reads as an issue (a wrong guess fails closed — the probe errors
        # and the item is silently skipped, never mis-flagged).
        kind = "pr" if (words & {"merged", "review", "awaiting"}) else "issue"
    return Claim(num=ref.group("num"), kind=kind, expected=expected)


# A repo-relative source path. Segments are [\w-] ONLY, so the single literal
# dot before the extension is the only ambiguous split point in the whole
# pattern — no overlapping prefix class, no quadratic backtracking (scar 22).
# The extension is a WHITELIST rather than \w{1,8}: prose abbreviations
# ("e.g.", "i.e.") are word.word shaped and would otherwise read as files.
_PATH_EXT = ("tsx|jsx|toml|yaml|json|html|java|lock|yml|txt|cfg|ini|sql|css|"
             "md|py|js|ts|go|rs|sh|rb|c|h")

# The lookbehind rejects any '/', '~', '.' or word char butting up against the
# first segment, which is what keeps ABSOLUTE paths (/etc/x.toml), HOME paths
# (~/.config/x.json), URLs (https://host/docs/x.md) and parent traversal
# (../other/x.py) from ever matching: every candidate start inside them is
# preceded by one of those characters. Probing them would answer about a tree
# that is not this project — the same refusal as slice 1's cross-repo refs.
_PATH_RE = re.compile(
    r"(?<![\w/~.-])(?P<path>(?:[\w-]{1,40}/){0,6}[\w-]{1,40}"
    r"\.(?:" + _PATH_EXT + r"))\b")

_PATH_STATE_RE = re.compile(
    r"(?i)\b(lives in|implemented in|added|created|exists"
    r"|deleted|removed|missing|gone)\b")
_PATH_PRESENT = frozenset({"lives in", "implemented in", "added", "created", "exists"})
_PATH_ABSENT = frozenset({"deleted", "removed", "missing", "gone"})

# Branch names are only recognised after an explicit "branch" keyword. Without
# it any slash-bearing token would read as a branch, and a wrong contradiction
# flag is worse than no check. An optional backtick lets the common
# "branch `feat/x`" phrasing through.
_BRANCH_RE = re.compile(r"(?i)\bbranch\s+`?(?P<name>[\w][\w./-]{0,80})")
_BRANCH_STATE_RE = re.compile(
    r"(?i)\b(unmerged|active|current|checked out|deleted|gone)\b")
_BRANCH_PRESENT = frozenset({"unmerged", "active", "current", "checked out"})
_BRANCH_ABSENT = frozenset({"deleted", "gone"})

# Version literal, shared by the claim patterns and the manifest scan.
_VER = r"\d{1,4}(?:\.\d{1,4}){0,3}"

# Three phrasings, all PAST tense on purpose: "should bump X to 2.1" is a
# proposal, not a state claim, and must not be probed.
_DEP_PIN_RE = re.compile(
    r"(?i)(?<![\w.-])(?P<name>[A-Za-z][\w.-]{0,39})==(?P<ver>" + _VER + r")\b")
_DEP_RE = re.compile(
    r"(?i)(?<![\w.-])(?P<name>[A-Za-z][\w.-]{0,39})\s+(?:is\s+|was\s+|now\s+){0,2}"
    r"(?:pinned|bumped|upgraded|downgraded)\s+(?:to|at)\s+v?(?P<ver>" + _VER + r")\b")
_DEP_VERB_RE = re.compile(
    r"(?i)\b(?:pinned|bumped|upgraded|downgraded)\s+(?P<name>[A-Za-z][\w.-]{0,39})\s+"
    r"(?:to|at)\s+v?(?P<ver>" + _VER + r")\b")


def _direction(text, state_re, present_words, absent_words):
    """Shared claim-direction fold (slice 1's stance, reused verbatim by every
    slice-2 class): no vocabulary means no claim, and BOTH directions at once
    means an ambiguous one. Returns True for a present-claim, False for an
    absent-claim, None for neither."""
    words = {w.lower() for w in state_re.findall(text)}
    present, absent = words & present_words, words & absent_words
    if not words or (present and absent):
        return None
    return bool(present)


def path_claim_of(text):
    """The file-exists claim in `text`, or None. Requires assertion
    vocabulary AND a repo-relative source path — a bare path MENTION is not a
    claim, the same bar slice 1 sets for a bare "#N"."""
    text = str(text or "")
    present = _direction(text, _PATH_STATE_RE, _PATH_PRESENT, _PATH_ABSENT)
    if present is None:
        return None
    ref = _PATH_RE.search(text)
    if ref is None:
        return None
    return PathClaim(path=ref.group("path"),
                     expected=frozenset({"EXISTS"} if present else {"MISSING"}))


def branch_claim_of(text):
    """The branch-state claim in `text`, or None. Scoped to EXISTENCE: whether
    a branch is merged depends on a base this module cannot infer, but a
    branch that is simply GONE already falsifies every "still on branch X"
    claim a session carried."""
    text = str(text or "")
    present = _direction(text, _BRANCH_STATE_RE, _BRANCH_PRESENT, _BRANCH_ABSENT)
    if present is None:
        return None
    ref = _BRANCH_RE.search(text)
    if ref is None:
        return None
    name = ref.group("name").rstrip("./-")
    if ".." in name:
        return None  # would resolve outside refs/heads — never probe it
    return BranchClaim(name=name,
                       expected=frozenset({"EXISTS"} if present else {"MISSING"}))


def dep_claim_of(text):
    """The dependency-version claim in `text`, or None. Unlike the other
    classes there is no direction to resolve: every accepted phrasing asserts
    the SAME thing, that `name` currently sits at `version`."""
    text = str(text or "")
    for pattern in (_DEP_PIN_RE, _DEP_RE, _DEP_VERB_RE):
        ref = pattern.search(text)
        if ref is not None:
            return DepClaim(name=ref.group("name"), version=ref.group("ver"))
    return None


def claim_for(text):
    """The ONE checkable claim in `text` as (class, claim), or None. First
    class to match wins: one claim per item is what keeps the shared probe
    budget honest."""
    text = str(text or "")
    for cls, extract in ((PR_STATE, claim_of), (BRANCH_STATE, branch_claim_of),
                         (FILE_EXISTS, path_claim_of),
                         (DEPENDENCY_VERSION, dep_claim_of)):
        claim = extract(text)
        if claim is not None:
            return cls, claim
    return None


def _target(cls, claim):
    """The dedup key for a claim's probe — distinct targets are probed once
    and every claim naming them is scored against its OWN expectation."""
    if cls == PR_STATE:
        return f"{claim.kind}/{claim.num}"
    if cls == FILE_EXISTS:
        return claim.path
    if cls == RECEIPT_VALIDITY:
        return claim.session
    return claim.name


def _day_bucket() -> int:
    """Days since the epoch — the rotation counter, and a test seam."""
    return int(time.time() // 86400)


def _receipt_targets(carried, project, deadline) -> set:
    """The origin session(s) whose signed receipt THIS brief verifies (#439).

    Eligibility mirrors `capture._origin_on_disk`: an origin whose checkpoint
    is absent, unreadable, GC'd or belongs to another project is not a witness
    anybody can produce, so it makes no claim at all — the same bar a bare
    "#48" mention fails. That is a normal skip, never a contradiction: the
    store keeps a BOUNDED number of per-session files, so an old origin being
    gone says nothing about the claim it once carried.

    Sampling is a day-bucket ROTATION over the eligible list, not a
    hash-modulo gate. A `sha1(origin) % N` scheme is stable, which is exactly
    what makes it wrong here: the origins it excludes are excluded FOREVER —
    permanent blind spots, and a tampered checkpoint sitting in one would
    never be probed on any day. Rotation reaches every origin over
    consecutive days instead, at the same one-probe-per-brief cost.

    Reads one small checkpoint per DISTINCT origin, which is why it takes the
    shared deadline: collection work the budget does not cover is work the
    briefing can still be blocked by."""
    if not config.receipts_enabled():
        return set()
    slug = store.project_slug(project)
    if not slug:
        return set()
    eligible, seen = [], set()
    for item in carried:
        origin = item.get("origin_session")
        if not isinstance(origin, str) or not origin or origin in seen:
            continue
        seen.add(origin)
        if time.monotonic() >= deadline:
            break
        cp = store.read_checkpoint(origin)  # total — swallows torn/bad paths
        if isinstance(cp, dict) and cp.get("project_slug") == slug:
            eligible.append(origin)
    if not eligible:
        return set()
    start = _day_bucket() % len(eligible)
    return {eligible[(start + i) % len(eligible)]
            for i in range(min(MAX_RECEIPT_PROBES, len(eligible)))}


def _gh_path():
    """Seam for tests; None -> no gh on PATH -> skip everything silently."""
    return shutil.which("gh")


def _github_repo(project) -> bool:
    """True only when `project` is inside a git repo with a GitHub remote —
    the context `gh` resolves "#N" against. Anything else (no git, no
    remote, git itself missing/slow) -> False, and the caller skips: probing
    from the wrong repo answers the wrong question."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(project), "remote", "-v"],
            capture_output=True, text=True, timeout=2)
    except Exception:
        return False
    return proc.returncode == 0 and "github.com" in proc.stdout


def _probe_path(project, relpath):
    """EXISTS / MISSING for a repo-relative path, or None when the resolved
    target escapes the project root. A symlink pointing out of the tree
    answers about ANOTHER checkout — refuse rather than mis-flag, the same
    stance as slice 1's cross-repo refusal. Pure stdlib, no subprocess."""
    root = Path(project).resolve()
    target = (root / relpath).resolve()
    if target != root and root not in target.parents:
        return None
    return "EXISTS" if target.exists() else "MISSING"


def _git_common_dir(project):
    """The git dir whose refs/heads is authoritative for `project`, or None.
    In a linked WORKTREE `.git` is a file pointing at <common>/worktrees/<x>,
    whose refs live back in the common dir — reading the worktree dir instead
    would report every branch gone for anyone working out of a worktree."""
    dot = Path(project) / ".git"
    if dot.is_file():
        text = dot.read_text(encoding="utf-8", errors="replace").strip()
        if not text.startswith("gitdir:"):
            return None
        gitdir = Path(text.split(":", 1)[1].strip())
        if not gitdir.is_absolute():
            gitdir = Path(project) / gitdir
        common = gitdir / "commondir"
        if common.is_file():
            rel = common.read_text(encoding="utf-8", errors="replace").strip()
            if rel:
                gitdir = gitdir / rel
        dot = gitdir.resolve()
    # No refs/heads means this is not a readable repo (or not a repo at all):
    # answering MISSING there would fabricate a contradiction for every claim.
    return dot if (dot / "refs" / "heads").is_dir() else None


def _probe_branch(project, name):
    """EXISTS / MISSING for a local branch, read straight off git's on-disk
    refs. Both halves of git's ref storage are consulted: a branch with no
    LOOSE ref file is not gone if packed-refs still carries it (every fresh
    clone packs its refs, so missing this would contradict on sight)."""
    git = _git_common_dir(project)
    if git is None:
        return None
    if (git / "refs" / "heads" / name).is_file():
        return "EXISTS"
    packed = git / "packed-refs"
    if packed.is_file():
        needle = f" refs/heads/{name}"
        for line in packed.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.endswith(needle):
                return "EXISTS"
    return "MISSING"


# Consulted in THIS order, lockfiles first: a lock records the resolved
# version, a manifest usually records a RANGE, and reading both would leave
# every real project with two conflicting answers and nothing to say.
_MANIFESTS = ("uv.lock", "package-lock.json", "Cargo.lock", "pyproject.toml",
              "requirements.txt", "package.json", "Cargo.toml")

# A manifest larger than this is not read at all — the briefing's budget is
# sub-second and a pathological lockfile must not eat it.
_MAX_MANIFEST_BYTES = 2_000_000


def _probe_dep(project, name):
    """The version the on-disk manifests pin for `name`, or None when nothing
    answers or the answer is ambiguous. The first manifest yielding exactly
    ONE distinct version wins; two versions for one name in the same file
    (transitive duplicates) is ambiguous and stays unanswered — don't-guess
    bias, same as slice 1's conflicting-vocabulary refusal.

    A scan, not a parse: the repo floor is py3.10, which has no stdlib
    tomllib, and the kernel takes no dependencies (ADR). The two shapes below
    cover the TOML block form used by lockfiles and the inline `name<op>ver`
    form used by every manifest here."""
    esc = re.escape(name)
    block = re.compile(r'(?i)name\s*=\s*["\']' + esc
                       + r'["\']\s*\r?\n\s*version\s*=\s*["\'](' + _VER + r")")
    inline = re.compile(r'(?i)(?<![\w.-])["\']?' + esc
                        + r'["\']?\s*(?:==|>=|~=|[:=])\s*["\']?[\^~>=v]{0,3}('
                        + _VER + r")")
    root = Path(project)
    for fname in _MANIFESTS:
        path = root / fname
        try:
            if not path.is_file() or path.stat().st_size > _MAX_MANIFEST_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found = set(block.findall(text)) or set(inline.findall(text))
        if len(found) == 1:
            return found.pop()
        if found:
            return None  # ambiguous in the authoritative file — don't guess
    return None


def _local_probe(cls, project, target):
    """Dispatch for the on-disk classes. Looked up by name at CALL time so
    each probe stays an individually patchable seam, like `_gh_path`."""
    if cls == FILE_EXISTS:
        return _probe_path(project, target)
    if cls == BRANCH_STATE:
        return _probe_branch(project, target)
    return _probe_dep(project, target)


def _run_local(plan: dict, project, deadline) -> dict:
    """Answer every on-disk claim in the plan. Runs BEFORE the `gh` fan-out:
    these probes cost microseconds, so letting a hanging network probe starve
    them would trade the reliable classes for the unreliable one. Still bound
    by the shared deadline — the budget covers all classes, not just `gh`."""
    out: dict = {}
    for key in plan:
        if key[0] not in _LOCAL_CLASSES:
            continue
        if time.monotonic() >= deadline:
            break
        try:
            value = _local_probe(key[0], project, key[1])
        except Exception:
            value = None  # any probe failure is a SKIP, never a raise
        if value is not None:
            out[key] = value
    return out


def _parse_gh_state(out):
    """Slice 1's `gh` reading, unchanged: the state word, and ONLY when it is
    one this module is willing to put in front of a user."""
    try:
        state = str(json.loads(out).get("state") or "").strip().upper()
    except Exception:
        return None
    return state if state in _KNOWN_STATES else None


def _parse_vitni_valid(out):
    """vitni's verify answer: {"valid": true|false}. Anything else — garbage,
    a missing field, an {"error"} payload — is UNANSWERED, not a rejection: a
    broken verifier must never read as "this receipt is bad" (the same
    don't-guess bias as slice 1's ambiguous-vocabulary refusal)."""
    try:
        valid = json.loads(out).get("valid")
    except Exception:
        return None
    if valid is True:
        return _VALID
    return _INVALID if valid is False else None


def _gh_probes(plan: dict, project) -> dict:
    """Slice 1's read-only `gh` probes as probe specs. The `gh`/GitHub-remote
    gate is per CLASS: a missing `gh` skips the PR/issue claims ONLY, and a
    checkpoint with no PR/issue claim never shells out at all."""
    wanted = {key: claim for key, claim in plan.items() if key[0] == PR_STATE}
    if not wanted:
        return {}
    gh = _gh_path()
    if gh is None or not _github_repo(project):
        return {}
    probes = {}
    for key, claim in wanted.items():
        if claim.kind == "pr":
            argv = [gh, "pr", "view", claim.num, "--json", "state,mergedAt"]
        else:
            argv = [gh, "issue", "view", claim.num, "--json", "state"]
        probes[key] = Probe(argv=argv, stdin=None, parse=_parse_gh_state)
    return probes


def _verify_probe(session):
    """The vitni `verify` invocation for one origin session, or None when the
    verification cannot be attempted (no CLI, no sidecar, no local public key,
    corrupt sidecar) — all silent skips.

    Replicates `receipts._verify_receipt`'s CLI contract EXACTLY (same
    subcommand, same stdin object, same policy fields); only the RUNNER
    differs. It cannot call that function, or `receipts._run_cli` beneath it,
    because those block on `subprocess.run` with `_CLI_TIMEOUT` = 10s — twelve
    times this module's entire budget."""
    cli = receipts._resolve_cli()
    if cli is None:
        return None
    cp_file = receipts._checkpoint_file(session)
    try:
        sidecar = json.loads(
            receipts._sidecar_path(cp_file).read_text(encoding="utf-8"))
        jws = sidecar["jws"]
        receipt = sidecar["receipt"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None  # no sidecar at all = a pre-receipt origin: nothing to do
    performer = sidecar.get("performer_id") or receipt.get("performer_id")
    pubkey = receipts._load_pubkey(config.keys_dir())
    if not jws or not performer or pubkey is None:
        return None
    payload = json.dumps({
        "signed_receipt": jws,
        "keys": {performer: {sidecar.get("kid") or receipts.KID: pubkey}},
        "policy": {"expected_binding": receipts._BINDING,
                   "expected_method": receipts.METHOD},
    })
    return Probe(argv=[cli, "verify"], stdin=payload, parse=_parse_vitni_valid)


def _receipt_probes(plan: dict, out: dict) -> dict:
    """Phase (a) of the receipt check, plus the phase (b) probe specs (#439).

    Phase (a) is FREE — sidecar-exists + outputs_hash byte match, exactly the
    check the briefing already runs (`receipts.verbatim_degraded`), no
    subprocess. A byte mismatch is already terminal, so it answers TAMPERED
    straight into `out` and NO CLI is ever spawned for it. Only an origin that
    survives phase (a) earns phase (b), the full signature/structure/binding
    verification — which is the point of the class: the byte match alone
    cannot tell a re-signed forgery from the real thing.

    Mutates `out` (the results dict) for the phase-(a) answers and returns
    only what still needs spawning. Never raises: any failure is a skip."""
    probes = {}
    for key, claim in plan.items():
        if key[0] != RECEIPT_VALIDITY:
            continue
        try:
            cp = store.read_checkpoint(claim.session)
            if not isinstance(cp, dict):
                continue  # vanished between collection and probe -> skip
            if receipts.verbatim_degraded(cp):
                out[key] = _TAMPERED
                continue
            probe = _verify_probe(claim.session)
        except Exception:
            continue
        if probe is not None:
            probes[key] = probe
    return probes


def _run_probes(probes: dict, cwd, deadline) -> dict:
    """Run every probe in parallel under the SHARED aggregate deadline.
    `probes` is {key: Probe}; returns {key: value | None} — None for anything
    killed at the deadline, failed, or unparseable. Never raises: a probe
    error is a skip, not a briefing failure.

    ONE fan-out for every subprocess class: separate runners would make the
    aggregate budget a fiction, since the second runner could only start after
    the first had waited out its whole deadline."""
    if not probes:
        return {}
    procs: dict = {}
    results: dict = {key: None for key in probes}
    for key, probe in probes.items():
        try:
            proc = subprocess.Popen(
                probe.argv, cwd=str(cwd),
                stdin=subprocess.PIPE if probe.stdin else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except Exception:
            continue  # spawn failure (gh vanished mid-flight) -> skip
        if probe.stdin and proc.stdin is not None:
            # Written and closed IMMEDIATELY: a stdin-reading child otherwise
            # blocks forever and burns the whole budget. Safe without a writer
            # thread ONLY because the payload is one small JSON object (a JWS
            # plus one public key, far under the 64 KiB pipe buffer) — a
            # bigger payload would deadlock here and needs a different shape.
            try:
                proc.stdin.write(probe.stdin)
                proc.stdin.close()
            except Exception:
                pass  # child already gone — its rc/output decides the skip
            # MUST drop the handle: `communicate()` below flushes `self.stdin`
            # unconditionally and raises ValueError("I/O operation on closed
            # file") on a pipe WE closed. Caught by the reaper's except, it
            # turned every stdin-bearing probe into a silent skip — the answer
            # was on stdout the whole time.
            proc.stdin = None
        procs[key] = proc
    while (any(p.poll() is None for p in procs.values())
           and time.monotonic() < deadline):
        time.sleep(0.02)
    for key, p in procs.items():
        if p.poll() is None:
            # Budget exhausted: kill and reap — a briefing never waits.
            p.kill()
            try:
                p.communicate(timeout=1)
            except Exception:
                pass
            continue
        try:
            out, _ = p.communicate(timeout=1)
        except Exception:
            continue
        if p.returncode != 0:
            continue
        try:
            results[key] = probes[key].parse(out)
        except Exception:
            continue
    return results


def _satisfied(cls, claim, value) -> bool:
    """Does the probed reality match what the item claimed? Version claims
    compare by PREFIX — an item pinning "2.31" is not falsified by 2.31.4, a
    coarser claim is not a false one — every other class is set membership."""
    if cls == DEPENDENCY_VERSION:
        return value == claim.version or value.startswith(claim.version + ".")
    if cls == RECEIPT_VALIDITY:
        # The claim is implicit and absolute: carrying an item asserts its
        # origin's provenance still holds, and only a full VALID says so.
        return value == _VALID
    return value in claim.expected


def _flag(cls, claim, value):
    """The (note, status) a contradicted item is stamped with. Every word that
    can reach the render is chosen HERE, from a bounded vocabulary — probe
    output is trusted for truth, never for text. `status` rides into the
    suggested `daimon resolve --status` command."""
    if cls == PR_STATE:
        return f"#{claim.num} {value.lower()}", value.lower()
    if cls == FILE_EXISTS:
        return f"{claim.path} {value.lower()}", value.lower()
    if cls == BRANCH_STATE:
        word = "gone" if value == "MISSING" else "exists"
        return f"branch {claim.name} {word}", word
    if cls == RECEIPT_VALIDITY:
        # Two failure flavors worth telling apart — an edited artifact and a
        # receipt the verifier rejected are different incidents. Both notes
        # are FIXED literals: the only variable in scope is a session id, and
        # this text rides into briefing output and the hook-injected LLM
        # context, where a raw session id has no business being.
        note = ("signed receipt no longer matches checkpoint bytes"
                if value == _TAMPERED else "signed receipt failed verification")
        return note, "unverified"
    return f"{claim.name} {value} not {claim.version}", "changed"


def check(checkpoint, project_dir) -> dict:
    """Spot-check the checkpoint's CARRIED claim-bearing items against
    reality, stamping contradicted items with a transient `_worldcheck`
    annotation IN PLACE (the caller owns the in-memory dict; nothing is
    persisted).

    Returns {"confirmed": n, "contradicted": n, "skipped": n} counted per
    ITEM — once per claim-bearing item, under a precedence rollup:
    contradicted > confirmed > skipped (#830/#833). An item is what its
    strongest outcome says it is; "skipped" never decides when another
    outcome exists, so a probe-cap- or deadline-starved claim cannot
    swallow a real answer on the item's other axis, and the aggregate
    always agrees with the strongest stamp/ledger surface the item earned.
    Additionally a "<class>:<outcome>" key for every outcome that actually
    occurred (#397), counted per CLAIM, so the caller's usage counters
    measure the fires-true rate per claim class. Zero-claim checkpoints
    return all-zeros and cost one iteration, no probe of any kind.
    Confirmed items are untouched: only a contradiction earns any surface
    at all.

    #439 adds ONE non-counter key, `LEDGER_KEY`, present only when a receipt
    verification actually failed: [(item_ref, check, reason)] rows for the
    caller to append to the rejection ledger. A caller that ignores it loses
    the ledger row and nothing else — the counters and the flags are whole on
    their own. A caller that iterates the dict blindly must pop it first."""
    # dict, not dict[str, int]: LEDGER_KEY carries list rows (#439).
    stats: dict = {"confirmed": 0, "contradicted": 0, "skipped": 0}
    if not isinstance(checkpoint, dict) or not project_dir:
        return stats
    # Native items were just re-extracted — not in question, for any class.
    carried = [item for item in serializer.iter_items(checkpoint)
               if item.get("carried_from")]
    if not carried:
        return stats
    # ONE deadline for every class. Allocation of the shared cap follows
    # checkpoint order so no class is privileged when it binds; execution
    # then runs local-before-network so the cheap classes cannot be starved.
    # Armed BEFORE collection (#439): receipt-validity's eligibility scan
    # reads origin checkpoints off disk, and collection work the budget does
    # not cover is work the briefing can still be blocked by.
    deadline = time.monotonic() + BUDGET_SECONDS
    origins = _receipt_targets(carried, project_dir, deadline)
    claims = []
    for item in carried:
        found = claim_for(item.get("text"))
        if found is not None:
            claims.append((item, found[0], found[1]))
        # Second pass, interleaved per item so the cap still allocates in
        # CHECKPOINT order — appending all receipt claims at the end would
        # starve them on any claim-heavy checkpoint. The text claim goes
        # first, which is also what decides the stamp collision below.
        if item.get("origin_session") in origins:
            claims.append(
                (item, RECEIPT_VALIDITY, ReceiptClaim(session=item["origin_session"])))
    if not claims:
        return stats
    plan: dict = {}
    for _item, cls, claim in claims:
        key = (cls, _target(cls, claim))
        if key in plan or len(plan) >= MAX_PROBES:
            continue
        plan[key] = claim
    results = _run_local(plan, project_dir, deadline)
    probes = _receipt_probes(plan, results)  # phase (a) answers into results
    probes.update(_gh_probes(plan, project_dir))
    results.update(_run_probes(probes, cwd=project_dir, deadline=deadline))
    ledger = []
    # #830/#833: the aggregate three count once per ITEM (the docstring's
    # promise, and what cli emits usage events under), under the precedence
    # rollup — a starved claim's "skipped" must never swallow a real answer
    # on the item's other axis (the undercount fired exactly when the probe
    # cap bound, i.e. on the largest checkpoints). Identity-keyed, because
    # two items may be equal dicts yet distinct claims-bearers.
    rollup: dict[int, str] = {}
    for item, cls, claim in claims:
        value = results.get((cls, _target(cls, claim)))
        if value is None:
            outcome = "skipped"
        elif _satisfied(cls, claim, value):
            outcome = "confirmed"
            # #525: the quiet inverse of the contradiction stamp — transient,
            # in-memory, same lifecycle as `_worldcheck`. The render marks
            # solid ground with it; a contradiction on ANY axis outranks it
            # at render time, so stamping here stays unconditional.
            item.setdefault("_worldcheck_confirmed", True)
            ref = item.get("id")
            if cls == RECEIPT_VALIDITY and isinstance(ref, str) and ref:
                # #839: the same id-less rule the contradiction row keeps — a
                # cure nobody can trace back to an item cures nothing.
                ledger.append((ref, LEDGER_CONFIRM_CHECK,
                               _LEDGER_CONFIRM_REASON))
        else:
            outcome = "contradicted"
            note, status = _flag(cls, claim, value)
            # FIRST stamp wins, and text claims are emitted first per item
            # above — so when an item is contradicted on both axes the text
            # flag survives. It is the more actionable one: it names what
            # changed and drives a real `daimon resolve --status`, while the
            # receipt flag would replace it with a status of "unverified".
            # The receipt outcome still counts and still reaches the ledger.
            item.setdefault("_worldcheck", {"note": note, "status": status})
            ref = item.get("id")
            if cls == RECEIPT_VALIDITY and isinstance(ref, str) and ref:
                # An id-less item yields no row: a ledger entry nobody can
                # trace back is noise (serializer.verification_rejections).
                ledger.append((ref, _LEDGER_CHECK, _LEDGER_REASONS[value]))
        prev = rollup.get(id(item))
        if prev is None or _OUTCOME_RANK[outcome] > _OUTCOME_RANK[prev]:
            rollup[id(item)] = outcome
        stats[f"{cls}:{outcome}"] = stats.get(f"{cls}:{outcome}", 0) + 1
    for outcome in rollup.values():
        stats[outcome] += 1
    if ledger:
        stats[LEDGER_KEY] = ledger
    return stats
