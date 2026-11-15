"""Generate the reference pages that the code owns.

`reference/cli/<group>.md` is written from the argparse parsers, through the
same walker the CLI help golden test uses (`cli/_surface.py`), and the tool
table in `reference/mcp.md` from the MCP server's tool schemas. Both are
deterministic: they read structured parser and schema data, never formatted
help text, so the output is the same on every Python version and in every
environment. `tests/test_docs_generated.py` fails when a committed page
differs from what this module writes.

    uv run python -m daimon_briefing._docgen        # rewrite the pages

The Spanish pages carry a Spanish header and the English flag text, because
that text is what the CLI prints.
"""
import re
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from . import cli, config
from .cli._surface import arguments, children, walk

REPO = Path(__file__).resolve().parents[2]
EN_DOCS = "website/docs"
ES_DOCS = "website/i18n/es/docusaurus-plugin-content-docs/current"
LANGS = (("en", EN_DOCS), ("es", ES_DOCS))


@dataclass(frozen=True)
class Group:
    key: str
    title: dict
    intro: dict
    commands: tuple
    see: tuple = ()     # (docs-relative path, {lang: label})


CLI_GROUPS = (
    Group("setup",
          {"en": "Set up", "es": "Preparar"},
          {"en": "Install daimon's pieces on a machine, fill the first "
                 "briefing and serve the tools.",
           "es": "Instalar las piezas de daimon en una máquina, llenar el "
                 "primer briefing y servir las herramientas."},
          ("configure", "hooks", "skill", "check", "heal", "import", "mcp"),
          (("reference/configuration.md",
            {"en": "Configuration", "es": "Configuración"}),
           ("reference/mcp.md", {"en": "MCP server", "es": "Servidor MCP"}))),
    Group("brief",
          {"en": "Brief", "es": "Brief"},
          {"en": "Read where you left off.",
           "es": "Leer dónde lo dejaste."},
          ("brief", "recall", "handoff"),
          (("concepts/carry.md", {"en": "Carry", "es": "Arrastre"}),)),
    Group("check",
          {"en": "Check", "es": "Comprobar"},
          {"en": "Ask why an item is trusted and prove what the store "
                 "promises.",
           "es": "Preguntar por qué se confía en un item y comprobar lo que "
                 "el store promete."},
          ("why", "diff", "blame", "verify-receipt", "audit", "audit-quotes",
           "serve", "relations"),
          (("concepts/trust-classes.md",
            {"en": "Trust classes", "es": "Clases de confianza"}),
           ("concepts/receipts.md", {"en": "Receipts", "es": "Recibos"}),
           ("reference/viewer.md", {"en": "Viewer", "es": "Visor"}),
           ("reference/relations.md",
            {"en": "Relations", "es": "Relaciones"}))),
    Group("correct",
          {"en": "Correct", "es": "Corregir"},
          {"en": "Change what daimon carries: close, re-assert, amend, "
                 "refute and rule.",
           "es": "Cambiar lo que daimon arrastra: cerrar, reafirmar, "
                 "enmendar, refutar y dictaminar."},
          ("resolve", "reverify", "trust", "anchor", "refute", "ruling",
           "amend"),
          (("concepts/lifecycle.md",
            {"en": "Lifecycle", "es": "Ciclo de vida"}),
           ("concepts/rulings.md", {"en": "Rulings", "es": "Dictámenes"}),
           ("concepts/negative-knowledge.md",
            {"en": "Negative knowledge", "es": "Conocimiento negativo"}))),
    Group("forget",
          {"en": "Forget", "es": "Olvidar"},
          {"en": "Remove an item's content and lift a forget.",
           "es": "Quitar el contenido de un item y levantar un olvido."},
          ("forget", "unforget"),
          (("concepts/lifecycle.md",
            {"en": "Lifecycle", "es": "Ciclo de vida"}),)),
    Group("coordinate",
          {"en": "Coordinate", "es": "Coordinar"},
          {"en": "Ask another project for something and answer what is "
                 "asked of you.",
           "es": "Pedirle algo a otro proyecto y responder lo que te piden."},
          ("request", "decide"),
          (("concepts/requests-and-decisions.md",
            {"en": "Requests and decisions",
             "es": "Pedidos y decisiones"}),)),
    Group("status",
          {"en": "Status", "es": "Estado"},
          {"en": "Look at the store, its ledgers and the other projects.",
           "es": "Mirar el store, sus ledgers y los otros proyectos."},
          ("status", "stats", "log", "loops", "projects", "slug", "bucket",
           "ledger", "team"),
          (("team/team.md", {"en": "Team memory", "es": "Memoria de equipo"}),)),
    Group("internals",
          {"en": "Internals", "es": "Internos"},
          {"en": "Invoked by hooks, documented for completeness.",
           "es": "Los invocan los hooks; documentados por completitud."},
          ("serialize", "write-checkpoint", "recall-inject", "action-recall",
           "request-inject"),
          ()),
)

_UI = {
    "en": {
        "note": ("This page is generated from the CLI's argument parsers. "
                 "Do not edit it: change the flag or its help text in the "
                 "code, then run `UPDATE_DOCS_REFERENCE=1 uv run pytest "
                 "tests/test_docs_generated.py` from `plugin/`."),
        "see": "See also",
        "cmd": "command", "what": "what it does", "arg": "argument",
        "value": "value", "default": "default", "desc": "description",
        "required": "required", "examples": "Examples",
        "desc_page": "Every `daimon` command in the {group} group: usage, "
                     "arguments and defaults, generated from the CLI.",
    },
    "es": {
        "note": ("Esta página se genera a partir de los parsers de "
                 "argumentos de la CLI. No la edites: cambiá el flag o su "
                 "texto de ayuda en el código y corré `UPDATE_DOCS_REFERENCE=1 "
                 "uv run pytest tests/test_docs_generated.py` desde "
                 "`plugin/`. El texto de cada flag está en inglés porque es "
                 "el que imprime la CLI."),
        "see": "Ver también",
        "cmd": "comando", "what": "qué hace", "arg": "argumento",
        "value": "valor", "default": "por defecto", "desc": "descripción",
        "required": "obligatorio", "examples": "Ejemplos",
        "desc_page": "Cada comando `daimon` del grupo {group}: uso, "
                     "argumentos y valores por defecto, generado desde la "
                     "CLI.",
    },
}

_BACKTICK = re.compile(r"(`[^`]*`)")


def cell(text: str) -> str:
    """One line of text that is safe in a markdown table and in MDX: pipes,
    angle brackets, braces and asterisks are escaped outside code spans, and a
    pipe inside one (a table cell splits there too)."""
    out = []
    for part in _BACKTICK.split(" ".join(text.split())):
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            out.append(part.replace("|", "\\|"))
        else:
            out.append(part.replace("|", "\\|").replace("<", "&lt;")
                       .replace(">", "&gt;").replace("{", "&#123;")
                       .replace("}", "&#125;").replace("*", "\\*"))
    return "".join(out)


def _synopsis(path, parser) -> str:
    parts = ["daimon", *path]
    kids = children(parser)
    for a in arguments(parser):
        if a.flags:
            flag = max(a.flags, key=len)
            token = f"{flag} {a.value}".strip()
            parts.append(token if a.required else f"[{token}]")
        else:
            token = a.value
            parts.append(token if a.required else f"[{token}]")
    if kids:
        parts.append("<command>")
    return " ".join(parts)


def _heading(depth: int) -> str:
    return "#" * min(depth + 1, 6)


def _command_section(lang, path, parser, parent_help) -> list[str]:
    ui = _UI[lang]
    lines = [f"{_heading(len(path))} `daimon {' '.join(path)}`", ""]
    summary = parent_help or ""
    description = " ".join((parser.description or "").split())
    if summary:
        lines += [cell(summary), ""]
    if description and description != " ".join(summary.split()):
        lines += [cell(description), ""]
    lines += ["```text", _synopsis(path, parser), "```", ""]
    kids = children(parser)
    args = arguments(parser)
    if kids:
        lines += [f"| {ui['cmd']} | {ui['what']} |", "| --- | --- |"]
        for name, help_, _sub in kids:
            lines.append(
                f"| [`{name}`](#daimon-{'-'.join(path + (name,))}) | "
                f"{cell(help_)} |")
        lines.append("")
    if args:
        lines += [f"| {ui['arg']} | {ui['value']} | {ui['default']} | "
                  f"{ui['desc']} |", "| --- | --- | --- | --- |"]
        for a in args:
            name = ", ".join(a.flags) if a.flags else a.value.split()[0]
            value = a.value if a.flags else ""
            req = f" ({ui['required']})" if a.flags and a.required else ""
            default = f"`{a.default}`" if a.default else ""
            lines.append(
                f"| `{name}` | {f'`{cell(value)}`' if value else ''} | "
                f"{default} | {cell(a.help)}{req} |")
        lines.append("")
    if parser.epilog:
        body = parser.epilog.strip("\n").split("\n")
        body = body[1:] if body and body[0].rstrip(":").lower().startswith(
            "example") else body
        lines += [f"**{ui['examples']}**", "", "```text",
                  *[ln.rstrip() for ln in
                    _dedent(body)], "```", ""]
    return lines


def _dedent(lines):
    pad = min((len(x) - len(x.lstrip()) for x in lines if x.strip()),
              default=0)
    return [x[pad:] for x in lines]


def _group_page(lang, group, position, helps) -> str:
    ui = _UI[lang]
    title = group.title[lang]
    top = {name: (h, sub) for name, h, sub in
           children(cli.build_parser())}
    body = []
    for name in group.commands:
        help_, parser = top[name]
        for path, sub in walk(parser, (name,)):
            body += _command_section(
                lang, path, sub, help_ if path == (name,) else helps[path])
    see = ""
    if group.see:
        links = ", ".join(f"[{label[lang]}](../../{rel})"
                          for rel, label in group.see)
        see = f"{ui['see']}: {links}.\n\n"
    head = [
        "---",
        f"sidebar_position: {position}",
        f'description: "{ui["desc_page"].format(group=title)}"',
        "---", "",
        f"# {title}", "",
        group.intro[lang], "",
        ":::note", ui["note"], ":::", "",
        see.rstrip("\n"), "" if see else None,
    ]
    return "\n".join(x for x in head if x is not None).rstrip("\n") + "\n\n" \
        + "\n".join(body).rstrip("\n") + "\n"


def render_cli() -> dict[str, str]:
    """`{repo-relative path: page text}` for every generated CLI page."""
    helps = {}
    root = cli.build_parser()
    for path, parser in walk(root):
        for name, help_, _s in children(parser):
            helps[path + (name,)] = help_
    out = {}
    for lang, base in LANGS:
        for i, group in enumerate(CLI_GROUPS, start=2):
            out[f"{base}/reference/cli/{group.key}.md"] = _group_page(
                lang, group, i, helps)
    return out


# ---------------------------------------------------------------------------
# MCP tool table

MCP_BEGIN = "{/* generated:mcp-tools (daimon_briefing._docgen) */}"
MCP_END = "{/* end generated:mcp-tools */}"

_MCP_UI = {
    "en": ("Tool", "What it does", "Arguments", "required", "default",
           "only with `DAIMON_MODEL_CROSS_PROJECT`"),
    "es": ("Herramienta", "Qué hace", "Argumentos", "obligatorio",
           "por defecto", "solo con `DAIMON_MODEL_CROSS_PROJECT`"),
}


def _descriptors(cross: bool):
    from . import mcp_server
    with mock.patch.object(config, "model_cross_project",
                           lambda: cross):
        return {t["name"]: t for t in mcp_server._tool_descriptors()}


def mcp_table(lang: str) -> str:
    """The tool table, from the schemas the server sends to a client. A tool's
    description and arguments are those of a host that granted no
    cross-project read; arguments only a grant offers are marked."""
    tool_h, what_h, args_h, req_t, def_t, grant_t = _MCP_UI[lang]
    plain, granted = _descriptors(False), _descriptors(True)
    lines = [f"| {tool_h} | {what_h} | {args_h} |", "| --- | --- | --- |"]
    for name, tool in plain.items():
        props = dict(tool["inputSchema"].get("properties", {}))
        both = granted[name]["inputSchema"].get("properties", {})
        required = set(tool["inputSchema"].get("required", []))
        pieces = []
        for arg, spec in {**props, **{k: v for k, v in both.items()
                                      if k not in props}}.items():
            kind = spec.get("type", "")
            if "enum" in spec:
                kind = "|".join(spec["enum"])
            bits = [f"`{arg}`", f"`{cell(kind)}`" if kind else ""]
            if arg in required:
                bits.append(req_t)
            if "default" in spec:
                bits.append(f"{def_t} `{spec['default']}`")
            if arg not in props:
                bits.append(grant_t)
            text = " ".join(b for b in bits if b)
            if spec.get("description"):
                text += f": {cell(spec['description'])}"
            pieces.append(text)
        lines.append(f"| `{name}` | {cell(tool['description'])} | "
                     f"{'<br />'.join(pieces) or '-'} |")
    return "\n".join(lines)


def _with_mcp_table(lang: str, rel: str) -> str:
    text = (REPO / rel).read_text(encoding="utf-8")
    pattern = re.compile(re.escape(MCP_BEGIN) + r".*?" + re.escape(MCP_END),
                         re.S)
    block = f"{MCP_BEGIN}\n\n{mcp_table(lang)}\n\n{MCP_END}"
    if not pattern.search(text):
        raise SystemExit(f"{rel}: generated mcp-tools markers are missing")
    return pattern.sub(lambda _m: block, text)


def generated_files() -> dict[str, str]:
    files = render_cli()
    for lang, base in LANGS:
        rel = f"{base}/reference/mcp.md"
        files[rel] = _with_mcp_table(lang, rel)
    return files


def write(files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = REPO / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


if __name__ == "__main__":      # pragma: no cover
    write(generated_files())
