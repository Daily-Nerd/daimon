"""Daimon dream-briefing — hermes plugin entrypoint (Slice 1, local-file, no Honcho)."""

from pathlib import Path

from . import hooks

__version__ = "0.2.0"


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
