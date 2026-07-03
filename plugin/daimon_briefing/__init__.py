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

    skills_dir = Path(__file__).parent.parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                ctx.register_skill(child.name, skill_md)
