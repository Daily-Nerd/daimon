# Servidor MCP (solo lectura)

`daimon mcp serve` expone la memoria de daimon como una superficie de
herramientas MCP sobre stdio — para hosts que hablan MCP pero no tienen un
sistema de hooks al que daimon pueda engancharse. Es opt-in (nada lo registra
por ti), de solo lectura (cuatro herramientas, cero escrituras) y biblioteca
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

Las cuatro llevan `readOnlyHint`. Las fallas a nivel de herramienta
(argumentos inválidos, FTS5 ausente) vuelven como resultados `isError` que el
agente puede leer; nunca matan el servidor.

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

Claude Code (CLI):

```bash
claude mcp add daimon -- daimon mcp serve
```

Configuración stdio MCP genérica (Windsurf, Cursor y la mayoría aceptan esta
forma):

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

Nota: en hosts donde los hooks de daimon ya corren (Claude Code, Windsurf,
Codex), el briefing por hook es la integración más rica — el servidor MCP es
para lecturas a demanda y para hosts sin hooks. Correr ambos está bien; las
herramientas son de solo lectura.
