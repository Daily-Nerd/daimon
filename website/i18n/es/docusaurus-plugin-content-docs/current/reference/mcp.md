---
description: "daimon mcp serve expone la memoria a hosts MCP sobre stdio. Opt-in, solo lectura, cinco herramientas sin superficie de escritura y solo libreria estandar."
---

# Servidor MCP (solo lectura)

`daimon mcp serve` expone la memoria de daimon como una superficie de
herramientas MCP sobre stdio — para hosts que hablan MCP pero no tienen un
sistema de hooks al que daimon pueda engancharse. Es opt-in (nada lo registra
por ti), de solo lectura (cinco herramientas, cero escrituras) y biblioteca
estándar pura (sin dependencias extra, igual que el resto de daimon).

```bash
daimon mcp serve   # bloquea y sirve JSON-RPC por stdio hasta EOF
```

## Herramientas

| Herramienta | Qué devuelve |
|-------------|--------------|
| `daimon_recall` | Resultados de búsqueda con procedencia completa: clase de confianza (`verbatim` = cita exacta, `inferred` = conclusión del modelo), autor, estado de supersesión, slug del proyecto de origen |
| `daimon_brief` | El último briefing del proyecto actual — render determinista, etiquetado por confianza, con resoluciones retenidas |
| `daimon_projects` | Cada proyecto del que daimon tiene memoria: slug, sesión, rama, último tema |
| `daimon_status` | Salud de captura: frescura del checkpoint, resultado del último serialize, fallas pendientes, alarmas — el mismo payload que `daimon status --json` |
| `requests_inbox` | Solicitudes que otros proyectos dirigieron a este — el lado de lectura del [ledger de solicitudes entre proyectos](cli.md#coordinar). `daimon_brief` nunca lleva este contenido; abrir, responder o decidir una solicitud es exclusivo de la CLI. |

Las cinco llevan `readOnlyHint`. Las fallas a nivel de herramienta
(argumentos inválidos, FTS5 ausente) vuelven como resultados `isError` que el
agente puede leer; nunca matan el servidor.

`daimon_brief` renderiza las mismas etiquetas de confianza que la CLI, así que
una línea puede traer además la insignia `[≈ corroborated ×N]` — un conteo de
sesiones independientes que atestiguaron la afirmación, en su propio eje y
nunca una clase de confianza superior. Ver
[clases de confianza](../concepts/trust-classes.md).

## Reglas de alcance

El servidor hereda la disciplina entre proyectos de daimon:

- **Las lecturas tienen alcance de proyecto.** El proyecto se resuelve desde
  el directorio de trabajo del proceso, o desde `DAIMON_PROJECT_DIR` si está
  definido — pon uno de los dos en la configuración MCP de tu host.
- **Sin fallback implícito.** Un proyecto sin checkpoint recibe
  `no checkpoint for this project` más un puntero a `daimon_projects` —
  nunca el contenido de otro proyecto. Cruzar de proyecto es siempre
  explícito: pasa un `slug` a `daimon_brief` o `daimon_recall`.
- **Kill switch respetado.** Con `DAIMON_DISABLE=1` el servidor sale limpio
  sin servir, así un daimon deshabilitado nunca rompe el arranque del host.
- **El uso queda local.** Cada llamada escribe una línea `mcp:<tool>` en el
  log de uso local de daimon (los mismos contadores de `daimon stats` que la
  CLI). Nada se transmite.

## Registrarlo en un host

Cada host que daimon adapta ahora registra este servidor como parte de su
propia instalación — nada que ejecutar a mano en ninguno. El puntero de
recall (la línea "trabajaste en esto antes" por prompt) nombra la
herramienta en lugar del comando de shell `daimon recall "..."` donde existe
un hook de recall por prompt; donde no existe, igual vale la pena registrar
el servidor, porque el propio agente del host puede llamar la herramienta
directamente.

| Host | Registro MCP | Forma-herramienta del puntero de recall |
|------|---------------|-------------------------------------------|
| Claude Code | soportado | soportado |
| Kimi Code | soportado | soportado |
| Codex | soportado | no soportado (sin hook de recall por prompt) |
| Windsurf | soportado | no soportado (sin hook de recall por prompt) |
| Gemini CLI | soportado | no soportado (sin hook de recall por prompt) |

- **Claude Code (plugin):** la [instalación como plugin](../hosts/claude-code)
  declara este servidor en `mcpServers` dentro de
  `.claude-plugin/plugin.json`, así que `daimon_recall` y sus hermanas quedan
  listadas en el momento en que se instala el plugin.
- **Claude Code (CLI, sin el plugin):** `claude mcp add daimon -- daimon mcp serve`.
- **Codex:** `daimon hooks install codex` escribe `[mcp_servers.daimon]` en
  `~/.codex/config.toml` junto a su registro en `hooks.json`.
  `daimon hooks remove codex` retira solo esa tabla.
- **Kimi Code:** `daimon hooks install kimi` fusiona `mcpServers.daimon` en
  `~/.kimi-code/mcp.json` (o `$KIMI_CODE_HOME/mcp.json`) junto a su registro
  de hooks en `config.toml`, y su comando `UserPromptSubmit` recoge
  automáticamente la forma-herramienta del puntero en cuanto esa entrada
  existe.
- **Windsurf:** `daimon hooks install windsurf` imprime un snippet
  `mcpServers` para `~/.codeium/windsurf/mcp_config.json`, igual que ya
  imprime el snippet de registro de hooks — daimon no escribe los propios
  archivos de configuración de Cascade.
- **Gemini CLI:** el gestor de ciclo de vida independiente
  `hook/gemini-hooks.py install` registra `mcpServers.daimon` en
  `~/.gemini/settings.json` junto a sus dos hooks, y lo retira en
  `uninstall`.

Configuración stdio MCP genérica (cualquier otro host que hable MCP):

```json
{
  "mcpServers": {
    "daimon": {
      "command": "daimon",
      "args": ["mcp", "serve"],
      "env": { "DAIMON_PROJECT_DIR": "/ruta/a/tu/proyecto" }
    }
  }
}
```

Si tu host lanza los servidores MCP desde el directorio del proyecto puedes
omitir `DAIMON_PROJECT_DIR` — el directorio de trabajo resuelve igual.

Nota: en hosts donde los hooks de daimon ya corren, el briefing por hook es
la integración más rica — el servidor MCP es para lecturas a demanda y para
hosts sin hooks. Correr ambos está bien; las herramientas son de solo
lectura.
