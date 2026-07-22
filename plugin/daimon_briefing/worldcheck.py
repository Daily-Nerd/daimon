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

Slice 1 is deliberately narrow: PR/issue state claims only. The measurable
question it answers — via the worldcheck:confirmed/contradicted/skipped usage
counters the CLI writes — is how often a carried repo-state claim is already
false at next read; that fires-true rate is the evidence for (or against)
later claim classes (file-exists, dependency-version, branch-state).
"""

import json
import re
import shutil
import subprocess
import time
from collections import namedtuple

from . import serializer

# Aggregate wall-clock budget for ALL probes together (sub-second by
# contract). Probes run in parallel, so this bounds the render delay, not a
# per-probe allowance.
BUDGET_SECONDS = 0.8

# First N distinct refs get probed; further claims count as skipped. Caps the
# subprocess fan-out no matter how claim-heavy a checkpoint gets.
MAX_PROBES = 5

Claim = namedtuple("Claim", "num kind expected")

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


def _run_probes(probes: dict, cwd) -> dict:
    """Run every probe in parallel under ONE aggregate deadline.
    `probes` is {(kind, num): argv}; returns {(kind, num): STATE | None} —
    None for anything killed at the deadline, failed, or unparseable. Never
    raises: a probe error is a skip, not a briefing failure."""
    deadline = time.monotonic() + BUDGET_SECONDS
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


def check(checkpoint, project_dir) -> dict:
    """Spot-check the checkpoint's CARRIED claim-bearing items against `gh`,
    stamping contradicted items with a transient `_worldcheck` annotation
    IN PLACE (the caller owns the in-memory dict; nothing is persisted).

    Returns {"confirmed": n, "contradicted": n, "skipped": n} — per ITEM, so
    the caller's usage counters measure the fires-true rate this slice
    exists to answer. Zero-claim checkpoints return all-zeros and cost one
    iteration, no subprocess. Confirmed items are untouched: only a
    contradiction earns any surface at all."""
    stats = {"confirmed": 0, "contradicted": 0, "skipped": 0}
    if not isinstance(checkpoint, dict) or not project_dir:
        return stats
    claims = []
    for item in serializer.iter_items(checkpoint):
        if not item.get("carried_from"):
            continue  # native items were just re-extracted — not in question
        claim = claim_of(item.get("text"))
        if claim is not None:
            claims.append((item, claim))
    if not claims:
        return stats
    gh = _gh_path()
    if gh is None or not _github_repo(project_dir):
        stats["skipped"] = len(claims)
        return stats
    probes: dict = {}
    for _item, claim in claims:
        key = (claim.kind, claim.num)
        if key in probes or len(probes) >= MAX_PROBES:
            continue
        if claim.kind == "pr":
            probes[key] = [gh, "pr", "view", claim.num, "--json", "state,mergedAt"]
        else:
            probes[key] = [gh, "issue", "view", claim.num, "--json", "state"]
    results = _run_probes(probes, cwd=project_dir)
    for item, claim in claims:
        state = results.get((claim.kind, claim.num))
        if state is None:
            stats["skipped"] += 1
        elif state in claim.expected:
            stats["confirmed"] += 1
        else:
            stats["contradicted"] += 1
            item["_worldcheck"] = {"note": f"#{claim.num} {state.lower()}",
                                   "status": state.lower()}
    return stats
