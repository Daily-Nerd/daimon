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

Counters now also land per class (worldcheck:<class>:<outcome>) while the
aggregate three keep their slice-1 meaning, so the fires-true rate is
readable per class before any further expansion. Content-hash — the strongest
form — is deliberately NOT here: it would have to capture a hash at serialize
time, which changes the serializer contract, so it is its own issue.
"""

import json
import re
import shutil
import subprocess
import time
from collections import namedtuple
from pathlib import Path

from . import serializer

# Aggregate wall-clock budget for ALL probes together (sub-second by
# contract). Probes run in parallel, so this bounds the render delay, not a
# per-probe allowance.
BUDGET_SECONDS = 0.8

# First N distinct targets get probed; further claims count as skipped. Caps
# the fan-out no matter how claim-heavy a checkpoint gets. The cap is shared
# across ALL classes: cheap disk probes spend from the same allowance as `gh`,
# which is why allocation follows checkpoint order rather than class.
MAX_PROBES = 5

# Claim classes, in the fixed priority order claim_for tries them. The order
# is part of the contract: it keeps an item's reading stable as classes are
# added, so a text carrying both a PR ref and a path keeps its slice-1 claim.
PR_STATE = "pr-state"
BRANCH_STATE = "branch-state"
FILE_EXISTS = "file-exists"
DEPENDENCY_VERSION = "dependency-version"

# Classes answered from disk alone — no subprocess, no network.
_LOCAL_CLASSES = frozenset({BRANCH_STATE, FILE_EXISTS, DEPENDENCY_VERSION})

Claim = namedtuple("Claim", "num kind expected")
PathClaim = namedtuple("PathClaim", "path expected")
BranchClaim = namedtuple("BranchClaim", "name expected")
DepClaim = namedtuple("DepClaim", "name version")

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
    return claim.name


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


def _run_gh(plan: dict, project, deadline) -> dict:
    """Slice 1's read-only `gh` probes, now sharing the aggregate deadline and
    cap with the on-disk classes. The `gh`/GitHub-remote gate is per CLASS:
    a missing `gh` skips the PR/issue claims ONLY, and a checkpoint with no
    PR/issue claim never shells out at all."""
    wanted = {key: claim for key, claim in plan.items() if key[0] == PR_STATE}
    if not wanted:
        return {}
    gh = _gh_path()
    if gh is None or not _github_repo(project):
        return {}
    probes = {}
    for key, claim in wanted.items():
        if claim.kind == "pr":
            probes[key] = [gh, "pr", "view", claim.num, "--json", "state,mergedAt"]
        else:
            probes[key] = [gh, "issue", "view", claim.num, "--json", "state"]
    return _run_probes(probes, cwd=project, deadline=deadline)


def _run_probes(probes: dict, cwd, deadline) -> dict:
    """Run every probe in parallel under the SHARED aggregate deadline.
    `probes` is {key: argv}; returns {key: STATE | None} — None for anything
    killed at the deadline, failed, or unparseable. Never raises: a probe
    error is a skip, not a briefing failure."""
    procs: dict = {}
    results: dict = {key: None for key in probes}
    for key, argv in probes.items():
        try:
            procs[key] = subprocess.Popen(
                argv, cwd=str(cwd), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except Exception:
            continue  # spawn failure (gh vanished mid-flight) -> skip
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
            state = str(json.loads(out).get("state") or "").strip().upper()
        except Exception:
            continue
        if state in _KNOWN_STATES:
            results[key] = state
    return results


def _satisfied(cls, claim, value) -> bool:
    """Does the probed reality match what the item claimed? Version claims
    compare by PREFIX — an item pinning "2.31" is not falsified by 2.31.4, a
    coarser claim is not a false one — every other class is set membership."""
    if cls == DEPENDENCY_VERSION:
        return value == claim.version or value.startswith(claim.version + ".")
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
    return f"{claim.name} {value} not {claim.version}", "changed"


def check(checkpoint, project_dir) -> dict:
    """Spot-check the checkpoint's CARRIED claim-bearing items against
    reality, stamping contradicted items with a transient `_worldcheck`
    annotation IN PLACE (the caller owns the in-memory dict; nothing is
    persisted).

    Returns {"confirmed": n, "contradicted": n, "skipped": n} counted per
    ITEM, plus a "<class>:<outcome>" key for every outcome that actually
    occurred (#397) — so the caller's usage counters measure the fires-true
    rate per claim class while the aggregate three keep their slice-1
    meaning. Zero-claim checkpoints return all-zeros and cost one iteration,
    no probe of any kind. Confirmed items are untouched: only a contradiction
    earns any surface at all."""
    stats = {"confirmed": 0, "contradicted": 0, "skipped": 0}
    if not isinstance(checkpoint, dict) or not project_dir:
        return stats
    claims = []
    for item in serializer.iter_items(checkpoint):
        if not item.get("carried_from"):
            continue  # native items were just re-extracted — not in question
        found = claim_for(item.get("text"))
        if found is not None:
            claims.append((item, found[0], found[1]))
    if not claims:
        return stats
    # ONE deadline for every class. Allocation of the shared cap follows
    # checkpoint order so no class is privileged when it binds; execution
    # then runs local-before-network so the cheap classes cannot be starved.
    deadline = time.monotonic() + BUDGET_SECONDS
    plan: dict = {}
    for _item, cls, claim in claims:
        key = (cls, _target(cls, claim))
        if key in plan or len(plan) >= MAX_PROBES:
            continue
        plan[key] = claim
    results = _run_local(plan, project_dir, deadline)
    results.update(_run_gh(plan, project_dir, deadline))
    for item, cls, claim in claims:
        value = results.get((cls, _target(cls, claim)))
        if value is None:
            outcome = "skipped"
        elif _satisfied(cls, claim, value):
            outcome = "confirmed"
        else:
            outcome = "contradicted"
            note, status = _flag(cls, claim, value)
            item["_worldcheck"] = {"note": note, "status": status}
        stats[outcome] += 1
        stats[f"{cls}:{outcome}"] = stats.get(f"{cls}:{outcome}", 0) + 1
    return stats
