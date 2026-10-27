---
sidebar_position: 1
description: "Instala daimon y obtene tu primer briefing en seis pasos, desde uv tool install hasta un checkpoint que la proxima sesion realmente lee."
---

# Inicio rápido

De la instalación a tu primer briefing. Seis pasos, dos opcionales.

## 1. Instala el CLI

```sh
uv tool install 'daimon-briefing[pretty]'
```

`pipx install 'daimon-briefing[pretty]'` funciona igual. El extra `[pretty]`
agrega tablas y paneles enriquecidos a `status` y `brief`; sin él, la salida
es texto plano.

## 2. Conecta un LLM

La serialización — convertir una sesión terminada en un checkpoint — necesita
un endpoint de LLM. Ejecuta:

```sh
daimon configure
```

Si el CLI `claude` está en tu PATH, esto imprime `✓ ready` y ya está — cero
configuración. Si no, apunta daimon a cualquier endpoint compatible con
OpenAI:

```sh
daimon configure --backend litellm \
  --base-url https://generativelanguage.googleapis.com/v1beta/openai \
  --api-key <TU-KEY> --model gemini-2.5-flash
```

Después verifica el backend de punta a punta antes de confiar en él:

```sh
daimon configure --test
```

Esto envía un prompt mínimo por el backend resuelto y reporta si pasó o
falló. La configuración se escribe en `~/.daimon/env` — mira
[Configuración](./configuration) para todas las variables, y la
[matriz de backends](../reference/backends-tested) para combinaciones de
modelos medidas en uso real.

## 3. Conecta tu host

Los hooks son lo que captura tus sesiones. Para Claude Code, instala el
plugin — registra los hooks de sesión por sí solo:

```
/plugin marketplace add Daily-Nerd/daimon
/plugin install daimon@daimon
```

Para Windsurf, Codex o Gemini CLI, sigue la página de tu host en
[Hosts](../hosts/) — cada host expone una superficie de hooks
distinta, y las guías por host cubren los pasos exactos de registro y sus
particularidades.

## 4. Llena el briefing con sesiones pasadas

Opcional, y vale la pena en una instalación fresca. Hasta que termines una
sesión no hay de qué informar, y las transcripciones que ya están en esta
máquina pueden llenar ese hueco. `daimon import` serializa una cantidad acotada de ellas por el mismo pipeline
que usan los hooks, y dice cuánto va a costar la corrida antes de gastar una
llamada al modelo. Ejecútalo desde el directorio del proyecto, con el backend
del paso 2 ya configurado:

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
  not discovered: windsurf, kimi (records no project, pass --transcript); gemini, hermes (no transcript file)
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
  not discovered: windsurf, kimi (records no project, pass --transcript); gemini, hermes (no transcript file)
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
[fila de `daimon import`](../reference/cli#preparar) de la referencia CLI, y
cada [página de host](../hosts/) dice hasta dónde llega la importación en ese
host.

## 5. Enséñale el protocolo a tu agente (opcional, recomendado)

Los hooks capturan sesiones; la skill le enseña al agente del otro lado cómo
*usar* el briefing — leerlo al inicio de sesión, tratar los ítems `verbatim`
como citas inmutables, verificar afirmaciones que parezcan desactualizadas
antes de repetirlas:

```sh
daimon skill install claude
```

`daimon skill list` muestra los destinos de instalación para otros hosts. Corré `daimon skill install` sin host para que daimon detecte qué corre esta máquina, o `daimon skill install --all` para cablear todos los hosts que encuentre sin preguntarte.


## 6. Termina una sesión, inicia la siguiente

Ese es todo el ciclo:

1. Trabaja una sesión normal en tu agente.
2. Termínala. El hook de fin de sesión serializa un checkpoint en segundo
   plano.
3. Inicia la siguiente sesión. El briefing se inyecta al arrancar:

```
While you were away — here's where we left off.

Decisions made:
- [✓ verbatim] Postgres advisory locks over Redis locks for the scheduler  — "let's not add a Redis dependency for this"

VERIFY BEFORE TRUSTING (state may have changed outside this session):
- [✓ verbatim] PR #212 state — you said you'd merge it yourself from the UI  — "I'll merge it after the demo"

Open loops:
- [✓ verbatim] Retry policy for the payments webhook — exponential or fixed?  — "don't ship the retry loop until we pick a policy"

Active topic: Migrating the scheduler off cron to the new worker pool
```

`[✓ verbatim]` significa que un verificador determinista encontró esa cita
en la transcripción de la sesión; `[~ inferred]` marca las conclusiones
propias del modelo. El sistema de etiquetas es el punto del producto — mira
[Clases de confianza](../concepts/trust-classes.md).

También puedes leerlo en una terminal en cualquier momento con
`daimon brief`.

:::note[Las sesiones cortas se omiten a propósito]
Una sesión con menos mensajes que `DAIMON_MIN_MESSAGES` (por defecto: 10) no
se serializa — no hay nada que valga la pena recordar en un intercambio de
dos mensajes. Si estás evaluando daimon y quieres capturar tus sesiones
cortas de prueba, baja el umbral por ahora:

```sh
echo 'DAIMON_MIN_MESSAGES=4' >> ~/.daimon/env
```
:::

## Confirma que funciona

```sh
daimon status
```

Status reporta la salud de captura con honestidad — fallas, omisiones y
crashes incluidos. Las líneas que importan en una instalación fresca:

```
project checkpoint: <session id>, written <n>m ago
last serialize result: success — wrote checkpoint: ...
```

Si la última serialización falló, el siguiente inicio de sesión la reintenta
una vez en segundo plano: lanza `daimon heal`, que re-serializa la sesión
fallida más reciente cuya transcripción sigue en disco. Un solo reintento
automático por sesión. Ejecuta `daimon heal` para reintentar ahora, `daimon
heal --all` para cada sesión reparable de esta máquina (`--project <dir>`
para un solo proyecto), o `daimon heal --force` para gastar otro intento en
una sesión que ya usó su reintento (`--session <id>` para elegir una).

## Antes de terminar una sesión

Si dejás trabajo sin terminar, pasá el batón — una línea imperativa que la
próxima sesión ve arriba de todo en su briefing:

```sh
daimon handoff "Terminá la migración primero. Ojo: la DB de staging está vieja."
```

Un checkpoint reconstruye lo que pasó; el batón lleva lo que pensabas hacer
después. Historia completa en [el ciclo de vida](../concepts/lifecycle.md).

## Adónde seguir

- [Referencia CLI](../reference/cli.md) — todos los verbos, una página.
- [Configuración](./configuration) — todas las variables de entorno,
  incluido el interruptor de apagado `DAIMON_DISABLE`.
- [Hosts](../hosts/) — detalle de configuración por host y
  limitaciones conocidas.
- [Memoria de equipo](../team/) — comparte checkpoints con tu equipo
  a través de un remoto git privado (opt-in).
