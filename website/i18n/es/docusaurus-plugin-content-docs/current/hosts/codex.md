# Codex

Codex está verificado a nivel de código, con tests unitarios
(`test_codex_hooks.py`), y validado en vivo del lado de captura: sesiones
reales de Codex se serializan a checkpoints desde el 2026-08-06 — el log de
serialización del maintainer registra capturas tanto de `codex-session-end`
como de `codex-stop` con throttling desde transcripts de rollout, y hay
checkpoints en registro cuyo id de sesión es el archivo de rollout. El parser
de transcripts sigue el formato de rollout de Codex a medida que deriva (los
eventos `item_completed` de 0.147.0 se manejan desde daimon 0.27.0). Alcance
declarado con honestidad: la validación tiene la profundidad de una sola
máquina del maintainer, no de una flota.

## Instalación

Agrega el ciclo de captura -> inyección de Daimon a Codex desde el paquete
publicado (sin necesidad de clonar el repo):

```sh
daimon hooks install codex
```

Esto copia los cuatro scripts de hook y los módulos que comparten a
`~/.codex/hooks/` y registra `SessionStart`, `SessionEnd`, `Stop` y
`PreToolUse` en `~/.codex/hooks.json`, preservando
cualquier entrada no relacionada que ya exista. Es idempotente — re-ejecútalo
después de cada `uv tool upgrade daimon-briefing` para refrescar los scripts
y que coincidan con el CLI instalado. Tras instalar, abre `/hooks` en Codex
para revisar y confiar en las definiciones de hooks — Codex omite las
definiciones no confiadas hasta que lo hagas.

Una copia instalada obsoleta sigue *funcionando* con el comportamiento viejo,
así que la deriva es invisible. Ejecuta `daimon hooks status` para auditar
las copias instaladas contra las versiones empaquetadas
(CURRENT/STALE/MISSING, más el estado de registro en `hooks.json`); sale con
código distinto de cero cuando algo derivó, y `daimon hooks install codex` lo
refresca en el lugar.

Requiere el CLI `daimon` en el `PATH` (el alias obsoleto `daimon-briefing`
también funciona como respaldo):

```sh
uv tool install 'daimon-briefing[pretty]'
```

### Instalación manual (desde un clon)

Trabajando desde un checkout del código, el gestor de ciclo de vida
independiente ofrece la misma integración más `uninstall` y `status`:

```sh
python3 hook/codex-hooks.py install   [--dry-run]
python3 hook/codex-hooks.py uninstall [--dry-run]
python3 hook/codex-hooks.py status
```

## Qué hace cada script

- **`daimon-codex-session-end.py`** — hook `SessionEnd`. Serializa la sesión
  terminada en segundo plano cuando Codex la cierra de forma ordenada.
- **`daimon-codex-session-start.py`** — hook `SessionStart`. Lee el último
  checkpoint del proyecto y devuelve JSON `additionalContext` de Codex, así
  el briefing se inyecta como contexto de desarrollo.
- **`daimon-codex-stop.py`** — hook `Stop`. Codex expone `Stop` a nivel de
  turno, no como un evento limpio de fin de sesión, así que este hook
  serializa de forma oportunista y está regulado por
  `DAIMON_CODEX_MIN_SERIALIZE_INTERVAL` (por defecto `300` segundos por
  sesión). Ponlo en `0` para serializar cada turno, o pon
  `DAIMON_CODEX_SERIALIZE_ON_STOP=0` para desactivar la captura de Codex
  dejando instalada la inyección del briefing.

- **`daimon-codex-pre-action.py`** — hook `PreToolUse`, matcher `Bash|shell`.
  **Este es el primer hook de daimon que puede hacer fallar una acción del
  anfitrión.** Antes de que corra un comando de shell, corre contra él los
  checks armados de este proyecto y devuelve el rechazo estructurado de Codex
  cuando un check ratificado con intención `enforce` reporta una violación o
  daimon no pudo leer lo que el comando envía. Codex no documenta ningún canal
  para una advertencia, así que la intención `warn` degrada acá a
  `record-only`: la corrida queda en el registro y no se muestra nada. Sale
  con 0 en todos los caminos. Mira
  [Checks en ejecución](../reference/cli#checks-at-runtime).

  Cada corrida agrega una fila a `~/.daimon/logs/checks.jsonl`. Una fila
  prueba que el check CORRIÓ. Solo `decision_emitted: deny` bajo `enforce`
  cierra la distancia entre un check que corrió y un check que fue respetado.

  `DAIMON_DISABLE=1` en el entorno del anfitrión apaga todos los hooks de
  daimon, este incluido, y es la salida de un check `enforce` cuyo patrón
  coincide con más de lo que querías: el comando que retira la regla es a su
  vez una acción de shell que el check rechazaría.

  El presupuesto es compartido por toda la acción, no se le da a cada check,
  así que en un manifiesto cargado un check tardío puede encontrar el tiempo
  ya gastado y reportar `unresolved`, que rechaza bajo `enforce` y advierte
  bajo `warn`. Mantené pocos checks armados por proyecto y sus cuerpos
  rápidos.

La documentación de Codex señala que `transcript_path` se provee por
conveniencia pero su formato no es una interfaz estable. El parser JSONL de
Daimon es deliberadamente best-effort e ignora filas desconocidas en lugar de
tratar JSON crudo como texto del transcript.

## Enséñale el protocolo al agente

```sh
daimon skill install codex       # bloque gestionado en ~/.codex/AGENTS.md
```

En el archivo compartido `AGENTS.md`, daimon solo toca su propio bloque
marcado — `daimon skill uninstall codex` elimina exactamente ese bloque.
Re-ejecuta install después de actualizar `daimon` para refrescar el
contenido.

## Verificar

```sh
daimon status
```

`daimon status` reporta la salud de captura con honestidad, incluidas fallas,
omisiones y crashes.
