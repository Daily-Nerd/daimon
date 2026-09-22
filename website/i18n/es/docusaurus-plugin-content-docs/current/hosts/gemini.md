---
description: "Configura daimon en Gemini CLI. La inyeccion del briefing ya funciona; el stub upstream transcript_path (gemini-cli#14715) se corrigio en gemini-cli v0.21.0, asi que la serializacion puede correr, pero la captura de punta a punta no esta verificada por este proyecto."
---

# Gemini CLI

El soporte de Gemini replica la forma de Claude Code, dividido en dos
scripts. El hook del briefing está publicado. La serialización también puede
correr: el stub upstream de `transcript_path` (`gemini-cli#14715`) se corrigió
en gemini-cli **v0.21.0** (2025-12-16), así que a partir de esa versión el
hook `SessionEnd` recibe una ruta real. Qué hace `daimon serialize` con una
transcripción de Gemini una vez que tiene una ruta real no está verificado
por este proyecto, ver la sección Verificar más abajo.

## Qué hace cada script

- **`daimon-gemini-session-start.py`** — hook `SessionStart`. Delega en
  `daimon brief` e inyecta el resultado vía el sobre
  `{"hookSpecificOutput": {"additionalContext": ...}}` de Gemini. Gemini
  exige **stdout JSON-puro** ("Silence is Mandatory") — a diferencia del hook
  de Claude Code, nada se imprime crudo; los diagnósticos para el operador
  viajan en `{"systemMessage": ...}`. `SessionStart` es solo-consultivo:
  siempre exit 0, el arranque nunca se bloquea.
- **`daimon-gemini-session-end.py`** — hook `SessionEnd`. Replica el hook
  `SessionEnd` de Claude Code (lanza `daimon serialize <transcript_path>`
  desacoplado cuando `transcript_path` no está vacío, y registra una omisión
  elegante cuando el campo llega vacío). En gemini-cli **v0.21.0 en adelante**
  (la corrección de `gemini-cli#14715`), `transcript_path` trae una ruta real,
  así que el hook lanza la serialización. En versiones anteriores el campo
  sigue llegando vacío y el hook sigue omitiendo.

## Instalación (manual, desde un clon)

`gemini-hooks.py` es el gestor de ciclo de vida (misma forma que
`codex-hooks.py`):

```sh
python3 hook/gemini-hooks.py install   [--dry-run]
python3 hook/gemini-hooks.py uninstall [--dry-run]
python3 hook/gemini-hooks.py status
```

Install copia ambos scripts (más `_daimon_hook_lib.py`) a `~/.gemini/hooks/`
y los registra en `~/.gemini/settings.json` (capa de usuario). Requiere el
CLI `daimon` en el `PATH` (`uv tool install 'daimon-briefing[pretty]'`).

## MCP

`hook/gemini-hooks.py install` también registra el [servidor MCP](../reference/mcp)
de solo lectura en `mcpServers.daimon` dentro de `~/.gemini/settings.json`,
junto a sus dos hooks, y lo retira en `uninstall`. Gemini no tiene hook de
recall por prompt (solo SessionStart, ver arriba), así que el puntero de
recall no tiene dónde renderizar todavía aquí.

## Enséñale el protocolo al agente

```sh
daimon skill install gemini      # bloque gestionado en ~/.gemini/GEMINI.md
```

En el archivo compartido `GEMINI.md`, daimon solo toca su propio bloque
marcado — `daimon skill uninstall gemini` elimina exactamente ese bloque.
Re-ejecuta install después de actualizar `daimon` para refrescar el
contenido.

## Verificar

```sh
daimon status
```

En gemini-cli **v0.21.0 o posterior**, `daimon status` debería mostrar un
checkpoint nuevo del proyecto después de terminar una sesión. Si una
transcripción de Gemini efectivamente se parsea en un checkpoint no está
verificado por este proyecto: `daimon serialize` tiene parseo hecho a medida
para Codex, Windsurf y Kimi, más un mecanismo genérico de respaldo, y nadie
en el proyecto corrió una sesión real de Gemini desde que se publicó la
corrección upstream. Si `daimon status` no muestra un checkpoint nuevo, o
muestra un error de serialización, un hueco de parseo es la causa más
probable, y un reporte que indique tu versión de gemini-cli es bienvenido.

En gemini-cli **anterior a v0.21.0**, espera que la captura se muestre como
omitida en lugar de escrita. La inyección del briefing en `SessionStart`
funciona con independencia de la captura de todas formas.
