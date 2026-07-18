# Gemini CLI

El soporte de Gemini replica la forma de Claude Code, dividido en dos
scripts. El hook del briefing está publicado, pero la serialización **hoy no
puede correr de punta a punta**: la captura está detenida detrás del issue
upstream `gemini-cli#14715` (`transcript_path` es un stub) — medio ciclo, por
restricción upstream, no por un bug de daimon.

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
  desacoplado), pero Gemini CLI actualmente envía `transcript_path` como un
  **stub vacío** (`gemini-cli#14715`, limitación upstream al 2026-07-01), así
  que el comportamiento principal de este hook hoy es una omisión elegante y
  registrada. La ruta de lanzamiento queda lista para cuando upstream
  complete el campo.

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

Hasta que `gemini-cli#14715` se resuelva upstream, espera que `daimon status`
muestre la captura como omitida en lugar de escrita — la inyección del
briefing en `SessionStart` funciona con independencia de la captura.
