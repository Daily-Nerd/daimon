# Claude Code

Claude Code es el host con soporte más profundo: el ciclo completo
(serialize -> carry -> brief -> recall) corre en él en uso real a diario, y
los incidentes de campo retroalimentan el código — el rastro de evidencia
vive en el
[logbook de investigación](https://github.com/Daily-Nerd/daimon/blob/main/research/README.md).

## Instalación (plugin — recomendado)

```
/plugin marketplace add Daily-Nerd/daimon
/plugin install daimon@daimon
```

El plugin registra por sí mismo los hooks `SessionStart` / `UserPromptSubmit`
/ `SessionEnd` vía `.claude-plugin/plugin.json` + `hooks/hooks.json`. El orden
no importa respecto a instalar el CLI `daimon`: si los hooks llegan antes que
el CLI, las sesiones arrancan con normalidad y el hook imprime una línea con
la sugerencia de instalación en lugar de un briefing.

> **No mezcles rutas de instalación.** Quien usa el plugin **no** debe correr
> además el instalador manual de abajo — ambas rutas coexistiendo registran
> los hooks dos veces (briefings dobles, llamadas LLM de serialización
> dobles). Para pasar de manual a plugin: ejecuta primero
> `python3 hook/daimon-hooks.py uninstall`.

## Instalación (manual, desde un clon)

Trabajando desde un checkout del código sin el sistema de plugins,
`hook/daimon-hooks.py` es el gestor de ciclo de vida:

```sh
python3 hook/daimon-hooks.py install   [--dry-run]
python3 hook/daimon-hooks.py uninstall [--dry-run]
python3 hook/daimon-hooks.py status
```

Install copia los scripts de hook a `~/.claude/hooks/` y los registra bajo
`SessionStart` / `SessionEnd` en `~/.claude/settings.json` (idempotente; los
settings se respaldan antes de cada mutación). Requiere el CLI `daimon` en el
PATH — `uv tool install 'daimon-briefing[pretty]'`, mira el
[inicio rápido](../getting-started/quickstart) — y los hooks también aceptan
el alias obsoleto `daimon-briefing` como respaldo. Después de actualizar el
CLI, re-ejecuta install para que los scripts de hook queden sincronizados.

## Qué hace cada script

Tres scripts cierran el ciclo de captura -> inyección, sea cual sea la ruta de
instalación que los registre:

- **`daimon-session-brief.py`** — hook `SessionStart`. Lee el payload de stdin
  y delega en el CLI `daimon brief` instalado (única fuente de verdad para el
  renderizado); imprime el briefing a stdout, que Claude Code inyecta como
  contexto de sesión. **Enrutamiento por proyecto:** el `cwd` del payload se
  convierte en slug (estilo Claude Code: `/Users/x/proj` -> `-Users-x-proj`) y
  se prefiere el `<checkpoint-dir>/<slug>/latest.json` de este proyecto; si el
  proyecto no tiene checkpoint propio, se usa el `latest.json` global y el
  encabezado del briefing se etiqueta `(global fallback — checkpoint may be
  from another project)`. El cwd se reenvía al CLI vía `DAIMON_PROJECT_DIR`
  para que ambos enruten igual. Sin `cwd` en el payload -> comportamiento
  global, sin etiqueta. Fail-open: siempre sale con 0, imprime una línea de
  diagnóstico ante una falla en lugar de morir en silencio. Respeta
  `DAIMON_DISABLE` y `DAIMON_CHECKPOINT_DIR`.
- **`daimon-session-end.py`** — hook `SessionEnd`. Lee el payload de stdin y
  lanza `daimon serialize <transcript_path>` como proceso desacoplado en
  segundo plano — la serialización es una llamada LLM (30s+ en sesiones
  largas) y nunca debe bloquear `/exit`. El `cwd` del payload se pasa al hijo
  como `DAIMON_PROJECT_DIR`, así el serializador escribe el
  `<slug>/latest.json` de este proyecto además del `latest.json` global
  (conservado por compatibilidad y para la ruta de fallback). Sin `cwd` ->
  entorno del hijo intacto, solo-global como antes. Los diagnósticos y la
  salida del serializador aterrizan en `~/.daimon/logs/serialize.log` — tanto
  la línea de éxito `wrote checkpoint: <path> (took Ns)` como las líneas de
  error con nombre (`... after Ns`) llevan los segundos transcurridos.
  Fail-open, respeta `DAIMON_DISABLE`. No se dispara en kills duros (terminal
  cerrada, SIGKILL) — los briefings aún pueden quedar desactualizados, y por
  eso el encabezado de `SessionStart` muestra la edad del checkpoint.

  Las credenciales del LLM vienen de `~/.daimon/env` (mira
  [Conecta un LLM](../getting-started/quickstart#2-conecta-un-llm)) — los
  hooks heredan el entorno del proceso host, no tu perfil de shell, así que
  `DAIMON_LLM_API_KEY` / `DAIMON_LLM_MODEL` / `DAIMON_LLM_BASE_URL` van en
  ese archivo (chmod 600). Sin él, la serialización falla rápido con un error
  con nombre en `~/.daimon/logs/serialize.log`.
- **`daimon-prompt-recall.py`** — hook `UserPromptSubmit` (recall proactivo:
  "trabajaste en esto antes"). Se dispara en cada prompt, envía el prompt a
  `daimon recall-inject` por stdin, e inyecta un puntero de una línea cuando
  el prompt se solapa con un pendiente previo. Como se dispara por prompt,
  las fallas son silenciosas (exit 0, sin salida) — lo único que imprime es
  una sugerencia real — y los comandos slash (directivas del host, no
  enunciados de trabajo) nunca coinciden.

Estos dos scripts de captura/inyección se cablean de una de dos maneras
mutuamente excluyentes — elige UNA (ambas a la vez disparan todo dos veces
por sesión: dos inyecciones de briefing, dos llamadas LLM de serialización).
Mira las secciones de instalación de arriba.

## Enséñale el protocolo al agente

```sh
daimon skill install claude      # ~/.claude/skills/daimon/SKILL.md
```

`daimon skill show` imprime el contenido de la skill; `daimon skill list`
muestra qué alcances soporta cada host. Re-ejecuta install después de
actualizar `daimon` para refrescar el contenido.

## Verificar

```sh
daimon status
```

`daimon status` reporta la salud de captura con honestidad, incluidas fallas,
omisiones y crashes; una captura fallida se auto-repara en el siguiente
inicio. Termina una sesión -> se escribe un checkpoint; inicia la siguiente ->
aparece el briefing.
