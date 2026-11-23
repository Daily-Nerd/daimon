---
sidebar_position: 8
description: "Cómo sabe daimon qué proyecto es una carpeta: un nombre de bucket permanente, la carpeta que el bucket registra, comandos explícitos para cambiarla y qué hacen los worktrees y el aviso de carpeta sin asociar."
---

# Identidad de proyecto

daimon guarda la memoria de un proyecto en un **bucket**, un directorio bajo `checkpoints/`. A qué bucket pertenece una carpeta tiene una sola respuesta, y nunca depende de adivinar.

## El nombre del bucket es permanente

Un bucket se nombra una sola vez, cuando nace, a partir de la ruta de la carpeta (`/Users/me/code/app` pasa a ser `-Users-me-code-app`). Después el nombre es solo un id. Mover el proyecto, renombrar la carpeta, cambiar de usuario o copiar el store a otra máquina no lo renombra, y ningún checkpoint firmado se reescribe. `daimon slug <ruta>` sigue imprimiendo el nombre que una ruta recibiría al nacer, y por eso funciona con una ruta que daimon nunca vio.

## La raíz registrada es la asociación

Cada bucket tiene un archivo chico llamado `root` con la carpeta a la que pertenece. Ese archivo es el único vínculo entre un bucket y un lugar en disco. Para encontrar el bucket de una carpeta, daimon busca el bucket cuya raíz registrada es esa carpeta, comparada sin distinguir mayúsculas ni `/` contra `\` donde el sistema no las distingue. Un bucket que registra una carpeta nunca se entrega a otra distinta.

Un bucket anterior al registro de raíces no tiene `root` y sigue respondiendo como siempre hasta su primera escritura, que registra una. `daimon bucket stamp` registra raíces antes de una mudanza.

## Cambiar la asociación es un acto explícito

daimon nunca reasocia un bucket por su cuenta. Una carpeta que no coincide con nada recibe un bucket nuevo, así que una suposición equivocada no puede unir dos proyectos ni entregar la memoria de uno a otro. Los comandos que cambian una asociación los corrés vos. Los que escriben imprimen primero un plan y escriben solo con `--apply`, y todos se niegan en un home con alcance de tenant:

| Comando | Usalo cuando |
| --- | --- |
| `daimon bucket remap --from DIR --to DIR` | Cambió una carpeta o un usuario, y todos los proyectos debajo se movieron igual. |
| `daimon bucket adopt --bucket NOMBRE --path DIR` | Un proyecto se movió a un lugar que `remap` no cubre. |
| `daimon bucket merge --from NOMBRE --into NOMBRE` | Un proyecto ya tiene dos buckets, por ejemplo uno nacido en la carpeta nueva antes de que reasociaras el viejo. |
| `daimon bucket stamp` | Antes de una mudanza, para que cada bucket registre dónde vive su proyecto. |
| `daimon bucket doctor` | Querés ver qué buckets están sin asociar, compartidos, vacíos o abandonados. No escribe nada. |

La raíz vieja queda en el registro de identidad del bucket como historial. `daimon import` usa ese historial: un transcript escrito en la máquina vieja registra la carpeta vieja, y se acepta para el proyecto una vez que `remap` o `adopt` la registró como raíz anterior. Un transcript de cualquier otra carpeta se sigue rechazando. La versión paso a paso de una mudanza de máquina es [Mover daimon a una máquina o carpeta nueva](../guides/move-to-a-new-machine.md).

## Los worktrees usan su repositorio principal

Una sesión en un worktree de git vinculado usa el bucket de su repositorio principal, así que el historial, las reglas y los pedidos de un proyecto quedan en un solo lugar. La lista de worktrees del propio repositorio principal es la autoridad, y un archivo `.git` que apunta a otro lado no asocia nada. Un worktree que ya tiene un bucket propio lo conserva hasta que lo unís con `daimon bucket merge`.

## Dos avisos, una línea cada uno

Cuando una carpeta no tiene bucket, el briefing lleva una línea fija que dice que no hay memoria asociada a ella y pide que la persona corra `daimon bucket doctor --path .`. Cuando un worktree todavía tiene un bucket propio junto al de su repositorio principal, lleva una segunda línea fija que pide `daimon bucket doctor`. Ninguna contiene un nombre de bucket ni texto de proyecto, porque un agente lee el briefing y el relevamiento que nombra otros proyectos pertenece a la terminal de una persona. Ninguno de los dos avisos rechaza ni cambia nada.
