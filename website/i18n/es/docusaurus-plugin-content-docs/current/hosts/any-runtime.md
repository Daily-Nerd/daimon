---
description: "Usa daimon con un runtime sin adaptador: el contrato de transcripción neutral, el comando serialize que corre el host, el entorno que el host define, qué muestra el ledger y qué runtimes tienen adaptador nativo."
---

# Cualquier otro runtime

Un runtime sin adaptador de daimon igual puede alimentarlo. Convierte la sesión
a la transcripción neutral de abajo y corre `daimon serialize` por tu cuenta cuando
el runtime termina. El checkpoint es el mismo que escribiría un hook.

Es el camino para un host que corre el agente dentro de su propio sandbox,
donde un hook de SessionEnd no llega al home de daimon. El host es dueño de la
corrida, así que `--host` hace que el CLI haga lo que un hook habría hecho
alrededor del spawn.

## La transcripción neutral

Un archivo `.jsonl`, un objeto JSON por línea:

| Campo | Obligatorio | Significado |
|---|---|---|
| `role` | sí | `user` o `assistant` |
| `content` | sí | el texto del mensaje, un string |
| `uuid` | no | un id estable del mensaje, usado para volver a señalarlo |
| `said_by` | no | quién dijo un mensaje de usuario, para una sesión con más de una persona |

```jsonl
{"role": "user", "content": "The nightly export fails on the staging box. Can you find out why?", "uuid": "m-001"}
{"role": "assistant", "content": "I read the export log. It dies on a permission error writing to /srv/exports.", "uuid": "m-002"}
{"role": "user", "content": "Is that the cron user or the deploy user?", "uuid": "m-003", "said_by": "Dana"}
{"role": "assistant", "content": "The cron user. The deploy user owns the directory and the cron user has no write bit.", "uuid": "m-004"}
{"role": "user", "content": "Then give the cron user the group, not the whole directory.", "uuid": "m-005", "said_by": "Dana"}
{"role": "assistant", "content": "Decision: add the cron user to the exports group and leave the directory mode alone.", "uuid": "m-006"}
```

Las otras formas de fila que daimon ya lee (transcripciones de Claude Code,
Codex, Windsurf y Kimi, y markdown plano con marcadores `user:` y
`assistant:`) se detectan por contenido. No se pasa un formato y no existe un
flag `--format`. Si escribes un conversor, emite la forma de arriba.

## Cómo correrlo

```sh
daimon serialize --host my-runner \
  --project /work/my-repo \
  --session 7f3c9a10 \
  /path/to/session.jsonl
```

- `--host` es el nombre que tu runtime se da a sí mismo: letras minúsculas,
  dígitos y `-`, de 1 a 40 caracteres, empezando con letra o dígito. Cualquier
  otra cosa sale con código 2.
- `--project` es el directorio del proyecto al que pertenece el checkpoint. Sin
  él, daimon usa `DAIMON_PROJECT_DIR` y después el directorio de trabajo.
- `--session` es el id de sesión. Sin él, el nombre del archivo (sin `.jsonl`)
  es el id. Pásalo cuando tus archivos de transcripción no se llaman como la
  sesión.

Lo que `--host` agrega antes de que empiece la corrida:

- Escribe la línea de spawn en el ledger (más abajo), así una corrida fallida
  igual conoce su proyecto y se puede curar.
- Se niega a arrancar cuando ya hay un serialize vivo para la misma sesión (un
  heartbeat más joven que `DAIMON_HUNG_AFTER`, 1800 segundos por defecto). El
  código de salida es 75, con una línea en stderr, y no se escribe nada en el
  store.
- Define `DAIMON_CAPTURE_HOST` con tu nombre como procedencia, salvo que el
  entorno ya traiga uno.
- Si una excepción escapa del comando, agrega una entrada a
  `serialize-crash.log` en el directorio de logs antes de que el proceso
  muera. El archivo se recorta a un tope fijo, el mismo que usan los hooks.

Sin `--host` el comando se comporta como siempre.

## Entorno que define el host

Defínelas en el entorno del proceso `daimon serialize`. Cada una se lee primero
del entorno del proceso y después de `~/.daimon/env`.

| Variable | Uso |
|---|---|
| `DAIMON_PROJECT_DIR` | el directorio del proyecto, el mismo valor que `--project`. Define ambos en un home con scope de tenant, donde se rechaza un `--project` distinto del proyecto propio del proceso |
| `DAIMON_TENANT_SCOPED` | defínela para acotar el home a un tenant: se rechaza una lectura o escritura que salga del proyecto propio del proceso |
| `DAIMON_SESSION_SPEAKER` | una etiqueta de quién habló en la sesión. Aplica a todo el proceso, así que sirve solo cuando una sesión es una persona; usa `said_by` por mensaje en otro caso |
| `DAIMON_AUTHOR` | el nombre de autor que se estampa en lo que captura la corrida. Sin ella daimon usa git y después el usuario del sistema |

El serialize también necesita un backend de LLM, como en todos lados. Mira el
[quickstart](../getting-started/quickstart) para la clave y el modelo.

## Qué muestra el ledger

Una corrida del host escribe sus líneas en `serialize.log`, en el directorio de
logs, el archivo que leen `daimon status` y `daimon heal`. Primero la línea de
spawn y después la línea de resultado que imprime el comando:

```text
2026-11-08T14:02:11Z my-runner: spawned serialize for 7f3c9a10 (reason: host-run, project: /work/my-repo) (transcript: /path/to/session.jsonl)
wrote checkpoint: /home/you/.daimon/checkpoints/7f3c9a10.json (took 41s)
```

Un duplicado rechazado deja una línea de skip y ninguna de spawn:

```text
2026-11-08T14:02:15Z my-runner: skipped serialize for 7f3c9a10 (already in flight)
```

`daimon status` muestra como en curso una corrida con línea de spawn y sin
resultado, y como colgada cuando pasa `DAIMON_HUNG_AFTER`. Una corrida fallida
la reintenta una vez `daimon heal`, dirigida al proyecto de la línea de spawn,
igual que una sesión de Claude Code.

## Adaptadores hoy

| Runtime | Cómo llega a daimon |
|---|---|
| Claude Code | hooks, ver [Claude Code](./claude-code.md) |
| Codex | hooks, ver [Codex](./codex.md) |
| Windsurf | hooks, ver [Windsurf](./windsurf.md) |
| Kimi Code | hooks, ver [Kimi Code](./kimi.md) |
| Hermes | en proceso, sin archivo y sin comando |
| Gemini CLI | ninguno por diseño: su hook no manda una transcripción usable, así que daimon no tiene parser para él. Ver [Gemini CLI](./gemini.md) |
| OpenCode | ninguno. Convierte a la transcripción neutral y usa el comando de arriba |

Cualquier otro runtime toma el camino de esta página.
