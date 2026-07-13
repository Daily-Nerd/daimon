"""LongMemEval-S dataset access for the harness (#267).

The dataset (MIT, github.com/xiaowu0162/LongMemEval; distributed on HuggingFace)
is NEVER vendored into this repo — it is fetched on demand and checksum-pinned.
`longmemeval_s_cleaned.json` is ~277 MB (~500 questions, ~40-50 haystack sessions
each), so the download is cached to disk and reused.

Each question is a dict with (fields used here):
- question_id           unique id; a `_abs` suffix marks an abstention question
- question              the query text
- haystack_session_ids  ids of the history sessions (the haystack)
- haystack_sessions     parallel list; each entry is a list of {role, content, ...}
- answer_session_ids    the EVIDENCE sessions — the retrieval gold set

Abstention questions carry no evidence session; the harness excludes them from
retrieval scoring (see metrics).
"""

from __future__ import annotations

import hashlib
import json
import random
import urllib.request
from pathlib import Path

# Official distribution. The deprecated `longmemeval` repo is replaced by
# `-cleaned`, which removes history sessions that corrupted answer correctness.
DATASET_URL = (
    "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/"
    "resolve/main/longmemeval_s_cleaned.json?download=true"
)
DATASET_FILENAME = "longmemeval_s_cleaned.json"


class ChecksumError(RuntimeError):
    """A downloaded dataset file did not match the pinned checksum."""


def verify_sha256(path: Path, expected: str | None) -> bool:
    """True when `path` matches `expected`. Raise ChecksumError on mismatch.

    `expected=None` means no checksum is pinned yet (trust-on-first-use): the
    caller records the computed digest, so later runs verify against it. Returns
    False in that case — verification did not happen — without raising.
    """
    actual = sha256_of(path)
    if expected is None:
        return False
    if actual.lower() != expected.lower():
        raise ChecksumError(
            f"{path.name}: sha256 {actual} != pinned {expected} — "
            "the download is corrupt or the upstream file changed"
        )
    return True


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(dest: Path, url: str = DATASET_URL) -> Path:
    """Fetch the dataset to `dest` (atomic). No-op if `dest` already exists."""
    dest = Path(dest)
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url) as resp, open(tmp, "wb") as out:  # noqa: S310
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    tmp.replace(dest)
    return dest


def load(path: Path) -> list[dict]:
    """Parse the dataset JSON array into a list of question dicts."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON array of questions")
    return data


def sample(questions: list[dict], n: int, seed: int) -> list[dict]:
    """A deterministic, seed-stable subset of `n` questions (all if n<=0 or n>=len).

    Order-preserving: the sampled questions are returned in their dataset order so
    a run reads the same regardless of the RNG's selection order.
    """
    if n <= 0 or n >= len(questions):
        return list(questions)
    idx = sorted(random.Random(seed).sample(range(len(questions)), n))
    return [questions[i] for i in idx]


def is_abstention(question: dict) -> bool:
    """True for abstention questions: `_abs` id suffix, or no evidence session."""
    if str(question.get("question_id", "")).endswith("_abs"):
        return True
    return not question.get("answer_session_ids")


def gold_sessions(question: dict) -> set[str]:
    """The evidence (gold) session ids for a question."""
    return set(question.get("answer_session_ids") or [])


def sessions_of(question: dict) -> list[tuple[str, list[dict]]]:
    """(session_id, messages) pairs for the haystack, in order.

    Each message is trimmed to the {role, content} shape the serializer reads;
    the `has_answer` evidence marker and any other per-turn keys are dropped so a
    session serializes exactly as a captured transcript would. Ids and sessions
    are zipped — a length mismatch keeps only the aligned prefix (never crashes).
    """
    ids = question.get("haystack_session_ids") or []
    sessions = question.get("haystack_sessions") or []
    out: list[tuple[str, list[dict]]] = []
    for sid, turns in zip(ids, sessions):
        messages = [
            {"role": str(t.get("role") or ""), "content": str(t.get("content") or "")}
            for t in turns
            if isinstance(t, dict)
        ]
        out.append((str(sid), messages))
    return out
