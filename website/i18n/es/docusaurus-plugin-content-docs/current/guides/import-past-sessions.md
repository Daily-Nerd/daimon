---
sidebar_position: 1
description: "Llená el briefing de un proyecto con los transcripts que ya están en esta máquina: el plan, el costo, cómo retomar una corrida detenida y qué dice el primer briefing."
---

# Importar sesiones pasadas

Hasta que termines una sesión no hay de dónde armar un briefing. `daimon import` llena ese hueco con los transcripts que ya están en esta máquina, por el mismo pipeline que usan los hooks, y dice cuánto va a costar la corrida antes de gastar una llamada al modelo. Corrélo desde el directorio del proyecto, con un backend configurado (`daimon configure --test` lo comprueba).

```sh
daimon import --project . --plan
```

`--plan` imprime el plan y se detiene ahí. No se gasta nada:

```
import plan for app (claude-code): 4 sessions, about 8 model calls
  1  2026-10-07  88 msgs  3 calls  8d2e4b71-c6a9-4f30-b5d2-91e7a3c40f6e  note: compact-summary-only-context
  2  2026-10-09  30 msgs  2 calls  b71f0c3d-2e85-47a6-8c19-d4f6a0b2e715
  3  2026-10-12  57 msgs  2 calls  5a9e3d27-18f4-4c6b-a2d0-7e3b9c1f48a2
  4  2026-10-14  23 msgs  1 call  c4d81f6a-9b3e-4a27-8f05-1e6d2b7c93f4
  skipped 0e7b6a52-3c1d-4e8f-9a04-b5c7d2e1f690: modified in the last 10 minutes, may be live
  claude-code: 5 of 14 transcripts under ~/.claude/projects recorded this project (7 recorded another project, 2 recorded no directory)
  codex: no transcripts under ~/.codex/sessions, ~/.codex/archived_sessions
  not discovered: windsurf, kimi (records no project, pass --transcript); gemini (no registered root); hermes (no transcript file)
```

El descubrimiento cubre Claude Code y Codex. daimon lista las transcripciones
bajo las raíces de ambos hosts y se queda con las que registraron este
proyecto como directorio de trabajo. Las 5 más nuevas por fin de sesión forman
la ventana; `--sessions N` cambia la cantidad. Cada salto que el plan nombra
sale de esa ventana y ninguna más vieja ocupa su lugar, así que el mismo
comando reconstruye el mismo plan. Arriba, el archivo más nuevo se escribió
hace minutos, así que se salta por posiblemente en vivo y se planifican cuatro
sesiones. Una sesión que ya tiene checkpoint, una más corta que
`DAIMON_MIN_MESSAGES`, un archivo ausente, uno ilegible: cada uno es un salto
con nombre, antes de gastar. Cuando toda la ventana ya está capturada y existen
transcripciones más viejas, el plan dice `all N newest sessions already
captured; raise --sessions to reach older history`. Una corrida sin `--plan`
cuya ventana solo tiene saltos imprime `nothing to import` y sale con 0.
Windsurf y Kimi no registran proyecto en sus transcripciones, así que nunca se
descubren: pasa `--transcript RUTA` con `--project <dir>` escrito en la línea
de comandos. Gemini CLI y Hermes no tienen una raíz de transcripciones que
daimon registre, así que la importación no llega a ninguno, ni por
descubrimiento ni por ruta.

Un proyecto que se mudó conserva sus transcripciones. Claude Code y Codex
escriben la carpeta donde corrió una sesión, y después de una mudanza esa
carpeta es la vieja, que no existe en esta máquina. Una vez que
`daimon bucket remap` o `adopt` asoció el bucket del proyecto a su carpeta
nueva, la carpeta vieja queda en el historial del bucket, y un transcript que
la registró (o una carpeta debajo) se acepta para el proyecto, por
descubrimiento y por `--transcript`. Un transcript de cualquier otra carpeta se
sigue rechazando. La mudanza completa está en [Mover daimon a una máquina o
carpeta nueva](move-to-a-new-machine.md).

La cantidad de llamadas es un plan: una llamada por fragmento de la
transcripción renderizada más las fusiones. La corrida se detiene antes de la
sesión cuyas llamadas planificadas llevarían el gasto por encima de
`--max-calls`. El valor por defecto es el total planificado, así que una
corrida sin banderas no gasta más que el número que imprimió, salvo por los
reintentos y el rescate de respaldo de una sola sesión, que el plan no puede
ver.

Ahora ejecútalo:

```sh
daimon import --project .
```

Se imprime el mismo plan, después las sesiones van de la más vieja a la más
nueva, dos líneas por cada una, y un resumen cierra la corrida:

```
wrote checkpoint: /Users/you/.daimon/checkpoints/8d2e4b71-c6a9-4f30-b5d2-91e7a3c40f6e.json (took 231s) [import]
[1/4] 8d2e4b71-c6a9-4f30-b5d2-91e7a3c40f6e: 96,004 tokens
wrote checkpoint: /Users/you/.daimon/checkpoints/b71f0c3d-2e85-47a6-8c19-d4f6a0b2e715.json (took 80s) [import]
[2/4] b71f0c3d-2e85-47a6-8c19-d4f6a0b2e715: 30,118 tokens, 2 quotes downgraded
wrote checkpoint: /Users/you/.daimon/checkpoints/5a9e3d27-18f4-4c6b-a2d0-7e3b9c1f48a2.json (took 101s) [import]
[3/4] 5a9e3d27-18f4-4c6b-a2d0-7e3b9c1f48a2: tokens unreported
wrote checkpoint: /Users/you/.daimon/checkpoints/c4d81f6a-9b3e-4a27-8f05-1e6d2b7c93f4.json (took 44s) [import]
[4/4] c4d81f6a-9b3e-4a27-8f05-1e6d2b7c93f4: 18,902 tokens
imported 4 of 4, 8 calls (8 planned), 145,024 tokens reported, 1 session unreported
```

Los tokens son lo que el backend reportó para esa sesión. `tokens unreported`
significa que no reportó nada, y el resumen cuenta esas sesiones en lugar de
adivinar. `quotes downgraded` cuenta las citas que la verificación no pudo
encontrar en la transcripción. El `note:` del plan es otro dato: esa sesión
abre con un resumen de compactación, así que el modelo nunca vio lo que vino
antes. Sin el extra `[pretty]`, cuando stdout no es una terminal, o con
`NO_COLOR` o `DAIMON_PLAIN` definidos, se imprime una línea
`importing <session id>...` antes de cada par.

El primer serialize fallido de cualquier tipo detiene la corrida. Primero se
imprime la línea `error: ...` del propio serializador, y después:

```
[2/4] b71f0c3d-2e85-47a6-8c19-d4f6a0b2e715: error after 3s (the error line is above)
stopped after the first failed session. 1 of 4 imported, 3 left.
run the same command again to resume; done sessions are skipped for free.
imported 1 of 4, 3 calls (3 planned), 96,004 tokens reported
```

Reanudar es el mismo comando. El plan se reconstruye sobre la misma ventana y
la sesión que ya quedó escrita ahora es un salto, sin costo. El reintento
automático al inicio de sesión (`daimon heal`) nunca toca una importación:

```
import plan for app (claude-code): 3 sessions, about 5 model calls
  1  2026-10-09  30 msgs  2 calls  b71f0c3d-2e85-47a6-8c19-d4f6a0b2e715
  2  2026-10-12  57 msgs  2 calls  5a9e3d27-18f4-4c6b-a2d0-7e3b9c1f48a2
  3  2026-10-14  23 msgs  1 call  c4d81f6a-9b3e-4a27-8f05-1e6d2b7c93f4
  skipped 0e7b6a52-3c1d-4e8f-9a04-b5c7d2e1f690: modified in the last 10 minutes, may be live
  skipped 8d2e4b71-c6a9-4f30-b5d2-91e7a3c40f6e: already has a checkpoint
  claude-code: 5 of 14 transcripts under ~/.claude/projects recorded this project (7 recorded another project, 2 recorded no directory)
  codex: no transcripts under ~/.codex/sessions, ~/.codex/archived_sessions
  not discovered: windsurf, kimi (records no project, pass --transcript); gemini (no registered root); hermes (no transcript file)
```

El primer briefing después de una importación abre con una nota debajo del
saludo, para que nadie confunda trabajo viejo con el punto donde acaba de
parar:

```
⚠ Imported history: 4 past sessions, the newest ended 12d ago (2026-10-14), before daimon captured anything live here. Check it before acting on it.
```

La línea del tema activo dice `Active topic (imported, 12d ago): ...` por la
misma razón. La nota cuenta los archivos importados que siguen en disco y
da la fecha del más nuevo. Cuando ya existe una captura en vivo del proyecto, dice en
cambio que la importación es `newer than daimon's last live capture here and
serialized after the fact`; un briefing mostrado por el fallback global dice
`in that project` en lugar de `here`. Tanto la nota como el calificador
terminan solos la primera vez que una captura en vivo pasa a ser el último
checkpoint. No hay nada que limpiar.

Cada rechazo y código de salida está en la
[notas del comando `daimon import`](../reference/cli/notes.md), y
cada [página de host](../hosts/) dice hasta dónde llega la importación en ese
host.
