# Windsurf (Cascade)

El adaptador de Windsurf está publicado y validado en uso real: el ciclo de
captura (serialización desde transcript nativo, endurecido con probes) fue
probado de punta a punta en uso real de Windsurf.

## Instalación

```sh
daimon hooks install windsurf   # copia daimon-windsurf-hooks.py, _daimon_hook_lib.py
                                 # y redact.py a ~/.daimon/hooks/, y luego imprime el
                                 # snippet de registro para la config de hooks de Cascade
daimon hooks list                # hosts con scripts de hook empaquetados
```

`daimon hooks install <host>` incluye `redact.py` junto a los scripts de hook
para que el adaptador limpie secretos en cada uno de sus propios puntos de
escritura (acumulación de transcript, checkpoint, log de eventos) — la
redacción no depende de que el paquete completo de `daimon` sea importable
desde el hook. Re-ejecuta `daimon hooks install windsurf` después de cada
actualización de `daimon` para que los scripts instalados queden
sincronizados con el CLI instalado. Para revisar si derivaron, ejecuta
`daimon hooks status` — reporta cada copia instalada como
CURRENT/STALE/MISSING contra la versión empaquetada y sale con código
distinto de cero ante deriva.

Apunta la configuración de hooks de Cascade en Windsurf (JSON a nivel de
usuario — mira
[la documentación de hooks de Cascade](https://docs.windsurf.com/windsurf/cascade/hooks))
al script instalado para los **tres** eventos: `pre_user_prompt`,
`post_cascade_response` y `post_cascade_response_with_transcript`.

## Cómo un solo script cubre tres eventos

Un script, `daimon-windsurf-hooks.py`, se registra para tres eventos de hook
de Cascade:

- **Transcript nativo preferido:** cuando `post_cascade_response_with_transcript`
  está registrado y existe el transcript nativo `.jsonl` de Cascade
  (`~/.windsurf/transcripts/<trajectory_id>.jsonl`) para la trayectoria, se
  serializa directamente — sin acumulación.
- **Fallback de acumulación:** `pre_user_prompt` / `post_cascade_response` no
  traen ruta de transcript, así que el adaptador anexa cada turno a su propio
  `~/.daimon/windsurf/transcripts/<trajectory_id>.md` con la misma forma
  marcada `**role**:` que `daimon serialize` ya parsea.
- **Serialización con throttle:** ambos eventos capaces de serializar se
  disparan cada turno; `DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL` (por defecto
  300s, `0` = cada turno) regula el lanzamiento por trayectoria, compartiendo
  un marcador para que registrar ambos eventos nunca lance doble.
- **Finalizador con debounce:** cada evento capaz de serializar también arma
  un temporizador desacoplado de un solo disparo; tras
  `DAIMON_WINDSURF_FINALIZER_QUIET_SECONDS` (por defecto 600s, `0` lo
  desactiva) sin más actividad en la trayectoria, el temporizador del último
  turno serializa el estado final del transcript — así una sesión cuyos
  últimos turnos cayeron dentro de la ventana del throttle igual se captura.
- **Auto-sondeo:** cualquier forma de payload que el adaptador no pueda
  manejar se vuelca a `~/.daimon/windsurf/unparsed-<event>-<stamp>.json`
  (como máximo un volcado por nombre de evento), para que la siguiente
  iteración del adaptador tenga evidencia real en lugar de otra ronda de
  probes manuales.
- **Sin inyección de briefing:** el conjunto de hooks de Cascade no tiene un
  evento equivalente a inicio de sesión, así que a diferencia de Claude
  Code/Codex/Gemini el briefing no se inyecta como contexto. Es una
  restricción permanente del host, no un bug — la skill cierra el ciclo desde
  el lado del agente: instruye a Cascade a ejecutar `daimon brief --team` al
  inicio de sesión (mira
  [Enséñale el protocolo al agente](#enséñale-el-protocolo-al-agente)).
- Fail-open en todas partes; interruptor de apagado `DAIMON_DISABLE=1`.

Windsurf no tiene evento de fin de sesión, así que la serialización corre con
el throttle de arriba, con un finalizador con debounce cubriendo la cola de
la sesión: una sesión cuyos últimos turnos caen dentro de la ventana del
throttle se serializa una vez que pasa
`DAIMON_WINDSURF_FINALIZER_QUIET_SECONDS` (por defecto 600) sin actividad
nueva, en lugar de perder esos turnos. Pon la perilla en `0` para desactivar
el finalizador. `DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL=0` sigue siendo el
recurso de demora-cero — serializa cada turno (una llamada LLM por turno),
así nada espera al periodo de silencio. Una perilla que vale la pena en tu
primera semana:

```sh
echo 'DAIMON_MIN_MESSAGES=4' >> ~/.daimon/env   # no omitas sesiones cortas iniciales
```

## Enséñale el protocolo al agente

```sh
daimon skill install windsurf             # ~/.codeium/windsurf/skills/daimon/SKILL.md
daimon skill install windsurf --project   # .windsurf/rules/daimon.md
```

En Windsurf la skill no es solo etiqueta de protocolo — es la vía de entrega
del briefing. Como Cascade no tiene evento de inicio de sesión donde
inyectar, la skill instruye al agente a ejecutar `daimon brief --team` en la
terminal antes de otro trabajo (con los briefings de compañeros incluidos
cuando el proyecto comparte un equipo daimon; idéntico a `daimon brief` sin
uno), y a continuar en silencio cuando daimon no está configurado.

Re-ejecuta install después de actualizar `daimon` para refrescar el
contenido.

## Verificar

```sh
daimon status
```

Los briefings se leen con `daimon brief` en una terminal — no se inyectan —
así que `daimon status` (y no un prompt de inicio de sesión) es la manera de
confirmar que se escribió un checkpoint tras tu última sesión. Con la skill
instalada, el agente ejecuta la lectura por sí mismo (`daimon brief --team`)
al inicio de sesión.
