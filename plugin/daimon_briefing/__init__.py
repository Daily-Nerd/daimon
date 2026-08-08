"""Daimon dream-briefing — hermes plugin entrypoint (Slice 1, local-file, no Honcho)."""

from importlib import metadata
from pathlib import Path

from . import hooks

try:
    # Single source of truth: the installed distribution (pyproject version).
    # A hardcoded string here shipped a 0.3.0 wheel that reported 0.2.0 (#10).
    __version__ = metadata.version("daimon-briefing")
except metadata.PackageNotFoundError:  # imported from a raw source tree
    __version__ = "0.0.0+unknown"


def register(ctx):
    """Called once at hermes startup. Wires the two hooks and bundles the skill.

    # VERIFIED website/docs/guides/build-a-hermes-plugin.md:
    #   ctx.register_hook("<event>", callback)
    #   ctx.register_skill(skill_name: str, skill_md_path: Path)
    """
    ctx.register_hook("on_session_end", hooks.on_session_end)
    ctx.register_hook("pre_llm_call", hooks.pre_llm_call)

    # #643: the skills live at the PLUGIN ROOT, which is the repository root —
    # `.claude-plugin/marketplace.json` sets `"source": "./"`, and Claude Code
    # discovers plugin skills at `<plugin-root>/skills/`. They used to sit at
    # `plugin/skills/`, one level too deep, so no host could see them.
    #
    # An installed wheel has no repository root: `packages = ["daimon_briefing"]`
    # ships the package alone, so registering skills here is a SOURCE-CHECKOUT
    # capability. That is the honest state rather than a new one — the old path
    # resolved to `site-packages/skills` and never existed either. Whether the
    # wheel should carry them is #264's call, when hermes is actually built.
    #
    # Gated on the plugin manifest, not a bare is_dir(): from site-packages the
    # third parent is an arbitrary directory, and a stranger's `skills/` must
    # never be registered as daimon's.
    root = Path(__file__).resolve().parents[2]
    skills_dir = root / "skills"
    if (root / ".claude-plugin" / "plugin.json").is_file() and skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                ctx.register_skill(child.name, skill_md)
