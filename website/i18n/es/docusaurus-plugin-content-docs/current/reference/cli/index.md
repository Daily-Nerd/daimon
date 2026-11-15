---
sidebar_position: 1
description: "Todos los comandos de daimon agrupados por tarea, con flags y valores por defecto generados desde la CLI, más las reglas que valen para todos."
---

# Referencia CLI

Cada comando de daimon, agrupado por lo que querés hacer. Cada página de grupo se genera a partir de los parsers de argumentos de la CLI, así que un flag o un valor por defecto se describe en un solo lugar: el código. `daimon <comando> --help` imprime los mismos datos.

| grupo | comandos |
| --- | --- |
| [Preparar](setup.md) | `configure`, `hooks`, `skill`, `check`, `heal`, `import`, `mcp` |
| [Brief](brief.md) | `brief`, `recall`, `handoff` |
| [Comprobar](check.md) | `why`, `diff`, `blame`, `verify-receipt`, `audit`, `audit-quotes`, `serve`, `relations` |
| [Corregir](correct.md) | `resolve`, `reverify`, `trust`, `anchor`, `refute`, `ruling`, `amend` |
| [Olvidar](forget.md) | `forget`, `unforget` |
| [Coordinar](coordinate.md) | `request`, `decide` |
| [Estado](status.md) | `status`, `stats`, `log`, `loops`, `projects`, `slug`, `bucket`, `ledger`, `team` |
| [Internos](internals.md) | `serialize`, `write-checkpoint`, `recall-inject`, `action-recall`, `request-inject` |

En un home con alcance de inquilino (`DAIMON_TENANT_SCOPED`) ningún verbo deja
que quien llama salga de su propio proyecto: `--slug`, `--all-projects`,
`audit privacy --all`, `brief --global-fallback` y un `--project` que nombre
otro bucket se rechazan con un error claro, y `decide` no muestra cuentas de
otros proyectos. Sin alcance de inquilino los verbos se comportan como se
describe en las [notas de comandos](notes.md). Una shell todavía puede cambiar su propio directorio o entorno,
así que la CLI no es un límite contra un agente que tenga una; las
herramientas MCP y la biblioteca sí lo son.

## Estabilidad

daimon es pre-1.0. Una versión que rompe un comando, un flag o una salida contra la que scripteás llega como un salto de versión menor, así que el número de versión solo no te avisa. Cada cambio así aparece bajo `### ⚠ BREAKING CHANGES` en `CHANGELOG.md`, con prosa que dice qué se movió y qué hacer. Fijá una versión exacta y leé esa sección antes de cambiar. Para las llamadas de biblioteca con las que puede contar un proceso anfitrión, mirá la [API de Python](../python-api.md).

## Códigos de salida de los auditores

Los auditores comparten un mismo contrato de salida, para que un script pueda
actuar sobre la respuesta:

| salida | significado |
| --- | --- |
| `0` | limpio comprobado, se escaneó cada superficie y no se encontró nada |
| `1` | hay residuo; el reporte nombra la superficie y el hash (nunca el texto) |
| `3` | no se puede probar, alguna superficie no se pudo leer, o no había nada en alcance para escanear. Nunca lo trates como limpio |

`--project <dir>` acota a un proyecto, `--all` audita cada proyecto local
(cada uno contra sus propias lápidas); los dos son mutuamente excluyentes.

## Notas de comportamiento

Las páginas generadas dicen qué es cada flag. Lo que hace un comando más allá de eso (rechazos, códigos de salida, qué escribe, cómo se comportan los paneles) está en las [notas de comandos](notes.md) y en las guías y páginas de concepto enlazadas desde cada grupo.
