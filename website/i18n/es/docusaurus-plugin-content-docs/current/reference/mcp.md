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

{/* generated:mcp-tools (daimon_briefing._docgen) */}

| Herramienta | Qué hace | Argumentos |
| --- | --- | --- |
| `daimon_recall` | Search daimon's cross-session memory. Rows carry provenance: trust class (verbatim = exact quote, inferred = model conclusion), author, origin project slug, and a `status` field in plain words when a row is resolved, superseded or contradicted (null for a live row). Searches this project only. | `query` `string` obligatorio: search terms<br />`limit` `integer` por defecto `20`<br />`session` `string`: the live session id this pull should be attributed to, if known<br />`all_projects` `boolean` solo con `DAIMON_MODEL_CROSS_PROJECT`: search every project<br />`slug` `string` solo con `DAIMON_MODEL_CROSS_PROJECT`: search one other project by slug |
| `daimon_brief` | The latest briefing for this project: what the last session was doing, decisions, open loops, trust-tagged. Strictly project-scoped: no checkpoint here answers 'none', never another project's content. | `slug` `string` solo con `DAIMON_MODEL_CROSS_PROJECT`: read another project by slug<br />`project` `string` solo con `DAIMON_MODEL_CROSS_PROJECT`: project directory path |
| `daimon_projects` | This project's own entry: slug, age, last topic. | - |
| `daimon_status` | Capture health for this project: checkpoint freshness, last serialize result, outstanding failures, alarms. | `project` `string` solo con `DAIMON_MODEL_CROSS_PROJECT`: project directory path |
| `requests_inbox` | Requests other daimon projects have addressed to this one (the cross-project ask ledger, #694), the full listing. daimon_brief carries the capped panel of the same requests; this tool reads all of them, with the severity filter. Read-only: opening, answering, or deciding a request is CLI-only (`daimon request ...`). | `min_severity` `critical\|high\|normal\|low`: only requests at this severity or a more severe one<br />`project` `string` solo con `DAIMON_MODEL_CROSS_PROJECT`: project directory path |

{/* end generated:mcp-tools */}

Las filas de `requests_inbox` llevan `severity` y `severity_reason` (una fila `normal` tiene la razón vacía), y el texto en cuarentena u olvidado vuelve como marcador o en blanco. La tabla de arriba lista los argumentos de las herramientas tal como el servidor se los envía a un cliente.

Las cinco llevan `readOnlyHint`. Los verbos por id de item existen en la CLI,
el visor y la api en proceso; las herramientas MCP no reciben ningún id de item
por diseño (fijado por un test). Las fallas a nivel de herramienta
(argumentos inválidos, FTS5 ausente) vuelven como resultados `isError` que el
agente puede leer; nunca matan el servidor.

`daimon_brief` renderiza las mismas etiquetas de confianza que la CLI, así que
una línea puede traer además la insignia `[≈ corroborated ×N]` — un conteo de
sesiones independientes que atestiguaron la afirmación, en su propio eje y
nunca una clase de confianza superior. Ver
[clases de confianza](../concepts/trust-classes.md).

Con `DAIMON_WORLDCHECK` activo, `daimon_brief` también ejecuta evidencia del
mundo sobre las afirmaciones arrastradas del proyecto actual y registra el
resultado, igual que `daimon brief`. Una lectura por `slug` nunca lo hace. Ver
[Worldcheck](../reference/configuration.md#worldcheck) para el costo.

## Reglas de alcance

El servidor hereda la disciplina entre proyectos de daimon:

- **Las lecturas tienen alcance de proyecto.** El proyecto se resuelve desde
  el directorio de trabajo del proceso, o desde `DAIMON_PROJECT_DIR` si está
  definido — pon uno de los dos en la configuración MCP de tu host.
- **El host elige el alcance, nunca el modelo.** Por defecto cada
  herramienta responde solo por este proyecto. Un pedido que nombra otro
  proyecto (un `slug`, `all_projects`, o una ruta `project` fuera de este
  proyecto) se rechaza con un mensaje que nombra `DAIMON_MODEL_CROSS_PROJECT`,
  y `daimon_projects` lista solo este proyecto. Define
  `DAIMON_MODEL_CROSS_PROJECT=1` en el entorno del proceso o en el archivo env
  para permitir esas lecturas; las descripciones de las herramientas entonces
  ofrecen los argumentos. Bajo `DAIMON_TENANT_SCOPED` la variable no tiene
  efecto. Esto vale para las herramientas MCP; un agente con shell puede
  igual correr la CLI desde otro directorio.
- **Bloquear nombres de herramientas no es el límite.** Un host puede ver el
  mismo servidor con más de un prefijo: `mcp__daimon__*` cuando está
  registrado en la configuración MCP del host, y `mcp__plugin_daimon_daimon__*`
  cuando llega por el plugin de Claude Code. Una lista de bloqueo por nombre
  deja pasar todo prefijo que no nombra, así que es incompleta por
  construcción. El límite de los datos es `DAIMON_MODEL_CROSS_PROJECT`, y vale
  bajo cualquier nombre con el que responda el servidor. Para quedarte en un
  solo proyecto, déjala sin definir (o con cualquier valor que no sea `1`,
  `true`, `yes` u `on`) en el entorno del proceso del servidor y en el
  archivo env de daimon.
- **Sin fallback implícito.** Un proyecto sin checkpoint recibe
  `no checkpoint for this project`, nunca el contenido de otro proyecto. Con
  la variable activa, el mensaje además apunta a `daimon_projects`, y cruzar
  de proyecto sigue siendo explícito: pasa un `slug` a `daimon_brief` o
  `daimon_recall`.
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
| Codex | soportado | soportado |
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
