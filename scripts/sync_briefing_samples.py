#!/usr/bin/env python3
"""Regenerate every published briefing sample from the real renderer.

A sample a person copied into the README can only drift from what the hook
injects. So the samples are not copied: each is the output of
`briefing.render`, the function the session-start hook calls, run over a
fixture checkpoint under scripts/briefing_samples/. Edit a fixture or the
renderer, then run

    uv run --project plugin python scripts/sync_briefing_samples.py

to rewrite the docs and the landing data. `--check` reports drift and writes
nothing (the drift test in plugin/tests/test_sync_briefing_samples.py runs it).

Two kinds of published copy, both named in the tables below:

* markdown: a fenced block whose first line opens the sample is replaced by
  the render. No marker is needed (MDX rejects HTML comments), so the block is
  found by what it starts with.
* landing data: website/src/generated/briefing-sample.json, imported by the
  landing components, so the TSX holds no briefing text of its own.

The skill text in plugin/daimon_briefing/skill_content.py quotes one sample
line inside prose it cannot regenerate; the drift test checks that line too.
"""

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path("scripts/briefing_samples")
GENERATED = Path("website/src/generated/briefing-sample.json")

_DOCS_ES = "website/i18n/es/docusaurus-plugin-content-docs/current"

# fixture name -> the files carrying that sample as a fenced block.
MARKDOWN_SAMPLES = {
    "full": (
        "README.md",
        "website/docs/index.md",
        f"{_DOCS_ES}/index.md",
        "website/docs/getting-started/quickstart.md",
        f"{_DOCS_ES}/getting-started/quickstart.md",
    ),
    "trust_lines": (
        "website/docs/concepts/trust-classes.md",
        f"{_DOCS_ES}/concepts/trust-classes.md",
        "website/blog/2026-07-28-verbatim-vs-inferred.md",
        "website/i18n/es/docusaurus-plugin-content-blog/2026-07-28-verbatim-vs-inferred.md",
    ),
}

# The line a sample's fenced block starts with, as a test of the first line.
# "full" opens with the greeting (either wording: a stale block is still found
# and rewritten); "trust_lines" opens with the verbatim PR #60 item.
_PR60_LINE = re.compile(r"- \[✓ verbatim\] PR #60 ")

_FENCE = re.compile(r"^(```[^\n]*)\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)
_ITEM = re.compile(r"- \[(?P<mark>[^\]]+)\] (?P<rest>.*)")


def _opens(fixture: str, first_line: str) -> bool:
    from daimon_briefing import marks
    if fixture == "full":
        return first_line in (marks.GREETING, *marks.LEGACY_GREETINGS)
    return bool(_PR60_LINE.match(first_line))


@contextlib.contextmanager
def _isolated_env():
    """Render with no DAIMON_* setting and no store in reach: the sample is
    what a default install prints, never what this machine is configured to."""
    saved = dict(os.environ)
    try:
        for key in [k for k in os.environ if k.startswith("DAIMON_")]:
            del os.environ[key]
        with tempfile.TemporaryDirectory() as scratch:
            os.environ["DAIMON_CHECKPOINT_DIR"] = scratch
            yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def render(fixture: str, root: Path = REPO_ROOT) -> str:
    """The briefing the hook would inject for the fixture checkpoint, through
    the same `briefing.render` the hook calls."""
    from daimon_briefing import briefing
    checkpoint = json.loads((root / FIXTURES / f"{fixture}.json").read_text("utf-8"))
    with _isolated_env():
        text = briefing.render(checkpoint)
    if not text:
        raise SystemExit(f"fixture {fixture!r} renders to nothing")
    return text


def sample(fixture: str, root: Path = REPO_ROOT) -> str:
    """What a published copy shows: the whole briefing for "full", only the
    item lines for the snippets that illustrate a line, not a briefing."""
    text = render(fixture, root)
    if fixture == "full":
        return text
    return "\n".join(line for line in text.splitlines() if _ITEM.fullmatch(line))


def landing_payload(root: Path = REPO_ROOT) -> dict:
    """What the landing components import: the rendered landing sample, line
    by line, with each line's class read from the mark the renderer wrote."""
    text = render("landing", root)
    fixture = json.loads((root / FIXTURES / "landing.json").read_text("utf-8"))
    by_text = {i["text"]: i for i in
               fixture["working_context"]["recent_decisions"]}
    items = []
    for line in text.splitlines():
        m = _ITEM.fullmatch(line)
        if not m:
            continue
        mark = m["mark"]
        kind = ("verbatim" if "verbatim" in mark
                else "inferred" if "inferred" in mark else "stale")
        source = next(i for t, i in by_text.items() if m["rest"].startswith(t))
        items.append({"kind": kind, "line": m["rest"],
                      "text": source["text"], "quote": source.get("quote", "")})
    return {"text": text, "items": items}


def generated_text(root: Path = REPO_ROOT) -> str:
    return json.dumps({"landing": landing_payload(root)},
                      indent=2, ensure_ascii=False) + "\n"


def sync_markdown(fixture: str, source: str, root: Path = REPO_ROOT) -> str:
    """`source` with every sample block of `fixture` replaced by its render.
    A file that holds none is an error: a registered copy that cannot be found
    is a copy nothing keeps honest."""
    shown = sample(fixture, root)
    found = 0

    out = []
    pos = 0
    for m in _FENCE.finditer(source):
        first = m.group(2).split("\n", 1)[0]
        if not _opens(fixture, first):
            continue
        found += 1
        out.append(source[pos:m.start()])
        out.append(f"{m.group(1)}\n{shown}\n```")
        pos = m.end()
    out.append(source[pos:])
    if not found:
        raise SystemExit(f"no {fixture!r} sample block found")
    return "".join(out)


def targets(root: Path = REPO_ROOT):
    """(repo-relative path, the text it should hold) for every derived file."""
    for fixture, paths in MARKDOWN_SAMPLES.items():
        for rel in paths:
            current = (root / rel).read_text("utf-8")
            yield rel, sync_markdown(fixture, current, root)
    yield str(GENERATED), generated_text(root)


def check(root: Path = REPO_ROOT) -> int:
    drift = []
    for rel, want in targets(root):
        path = root / rel
        if not path.exists() or path.read_text("utf-8") != want:
            drift.append(rel)
    for rel in drift:
        print(f"out of date: {rel}", file=sys.stderr)
    if drift:
        print("run: uv run --project plugin python scripts/sync_briefing_samples.py",
              file=sys.stderr)
        return 1
    return 0


def sync(root: Path = REPO_ROOT) -> int:
    for rel, want in targets(root):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text("utf-8") != want:
            path.write_text(want, "utf-8")
            print(f"wrote {rel}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="report drift and exit non-zero without writing")
    args = ap.parse_args(argv)
    return check(REPO_ROOT) if args.check else sync(REPO_ROOT)


if __name__ == "__main__":
    sys.exit(main())
