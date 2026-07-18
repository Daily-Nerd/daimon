# Guías de configuración por host

Daimon cierra un ciclo de captura -> inyección alrededor del host de agentes
que uses: un hook de fin de sesión escribe un checkpoint, y un hook de inicio
de sesión (o por prompt) convierte el último checkpoint en un briefing. La
profundidad del soporte varía según los eventos de hook que cada host expone.

| Host | Instalación | Captura | Inyección del briefing | Estado |
|---|---|---|---|---|
| [Claude Code](./claude-code.md) | Plugin (`/plugin install daimon@daimon`) o manual `hook/daimon-hooks.py install` | El hook `SessionEnd` lanza `daimon serialize` en segundo plano | El hook `SessionStart` inyecta el briefing; el hook `UserPromptSubmit` agrega recall proactivo | validado en uso real a diario |
| [Codex](./codex.md) | Manual `hook/codex-hooks.py install` (desde un clon) | Hook `Stop` con throttle (Codex no tiene evento de fin de sesión) | El hook `SessionStart` inyecta vía `additionalContext` | publicado, a la espera de su primera ejecución real |
| [Gemini CLI](./gemini.md) | Manual `hook/gemini-hooks.py install` (desde un clon) | Bloqueado upstream (`gemini-cli#14715` — `transcript_path` es un stub vacío) | El hook `SessionStart` inyecta vía `additionalContext` | bloqueado upstream (`gemini-cli#14715`) |
| [Windsurf (Cascade)](./windsurf.md) | `daimon hooks install windsurf` | Serialización con throttle en `pre_user_prompt` / `post_cascade_response(_with_transcript)` | Ninguna — Cascade no tiene evento equivalente a inicio de sesión; la skill instruye al agente a ejecutar `daimon brief --team` en la terminal al empezar | validado en uso real |

## Tres piezas móviles

Cada configuración de host combina hasta tres piezas, instaladas de forma
independiente:

- **Los hooks** capturan tus sesiones y (donde el host lo permite) inyectan el
  briefing como contexto. Claude Code tiene un plugin empaquetado; los demás
  hosts instalan scripts de hook independientes vía
  `daimon hooks install <host>` (actualmente Windsurf) o los scripts manuales
  de ciclo de vida bajo `hook/` (Codex, Gemini, y Claude Code sin el plugin).
- **La skill** (`daimon skill install <host>`) le enseña al agente del otro
  lado del hook cómo usar lo que los hooks capturan — leer el briefing al
  inicio de sesión (obteniéndolo con `daimon brief --team` cuando el host no
  inyecta nada, p. ej. Windsurf), tratar los ítems `verbatim` como citas
  inmutables, verificar afirmaciones que parezcan desactualizadas antes de
  repetirlas.
- **El CLI `daimon`** es la única fuente de verdad a la que todos los hooks
  delegan la serialización, el renderizado y el almacenamiento — instálalo una
  vez (`uv tool install 'daimon-briefing[pretty]'`) y los hooks de todos los
  hosts comparten el mismo almacén de checkpoints.

Elige tu host arriba para la guía completa.
