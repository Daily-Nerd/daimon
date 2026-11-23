---
sidebar_position: 4
description: "Cerrar un pendiente, reafirmar una afirmación, resolver una supersesión, eliminar contenido, levantar un olvido o dejar un batón: qué comando corresponde y cómo correrlo."
---

# Corregir la memoria

Daimon nunca adivina una transición por vos, así que cada cambio a lo que lleva un briefing tiene su propio comando. El contrato detrás está en [el ciclo de vida de un item](../concepts/lifecycle.md).

| querés | corré |
| --- | --- |
| cerrar un item que ya está hecho | `daimon resolve` |
| decir que un item arrastrado sigue siendo cierto | `daimon reverify` |
| registrar que un item abierto avanzó | `daimon amend propose`, mirá [decidir y ratificar](./decide-and-ratify.md) |
| eliminar contenido para siempre | `daimon forget` |
| levantar un olvido en esta máquina | `daimon unforget` |
| decirle a la próxima sesión qué hacer primero | `daimon handoff` |

`resolve` y `forget` aceptan un id exacto o una consulta de texto libre, y la consulta debe coincidir con exactamente un item. Agregá `--dry-run` primero para ver qué coincidiría.

## `daimon resolve`: cerrar un pendiente

```sh
daimon resolve "retry policy for the payments webhook" --dry-run
daimon resolve o-3f8a2c --note "shipped exponential backoff in #212"
```

Resolver registra un evento de solo-anexado; desde entonces, los briefings
**retienen** el ítem en lugar de arrastrarlo desactualizado. El ítem no se
borra, su historia sigue siendo buscable, y el rastro de eventos muestra
cuándo y por qué se cerró. `--status` acepta un estado de ciclo de vida
libre; cualquier estado que empiece con `reopen` revive el ítem. Un estado
que empiece con `forgotten:` se rechaza: es una lápida, y solo `daimon forget`
la escribe (además quita el valor de los archivos). Solo `daimon unforget`
levanta una.

## `daimon reverify`: afirmar que sigue siendo cierto

```sh
daimon reverify o-3f8a2c --evidence "checked the release page"
```

Reverify es la respuesta a la
[advertencia de desactualización](../concepts/carry.md#la-advertencia-de-desactualización):
un ítem arrastrado envejeció más allá del umbral, verificaste el mundo, y
sigue en pie. El evento reinicia el sello de última verificación del ítem,
así que el reloj de la advertencia arranca de nuevo. Reverify acepta
**solo ids exactos**, re-afirmar una afirmación es deliberado, así que no
hay búsqueda difusa que pueda dispararse mal.

Reverify es también la mitad de **rechazo** de un candidato a supersesión
(abajo).

## Candidatos a supersesión

Cuando una sesión nueva contradice un ítem arrastrado, el briefing presenta
un **candidato a supersesión**: ambos lados, con los comandos de
confirmar/rechazar en línea. Verificas cuál lado es cierto en el mundo, y
respondes con exactamente esos comandos:

- **Confirmar**, `daimon resolve <id>`: el ítem viejo está genuinamente
  superado; los briefings futuros lo retienen.
- **Rechazar**, `daimon reverify <id>`: la contradicción era aparente, no
  real; el ítem se mantiene, recién verificado.

El principio de diseño en todo el ciclo de vida: daimon señala, tú decides.
La contradicción, la desactualización y la eliminación se presentan con
evidencia y se resuelven con una acción explícita de un humano (o de un
agente explícitamente instruido), nunca con un merge silencioso.

## `daimon forget`: eliminar contenido

Resolve cierra un item pero deja su contenido en el historial. Forget elimina el contenido en sí y deja una lápida con solo el hash. Qué elimina, y la garantía de que se queda eliminado, están en [el ciclo de vida de un item](../concepts/lifecycle.md#daimon-forget--eliminar-de-forma-demostrable).

```sh
daimon forget o-3f8a2c --reason "contains client name"
daimon forget "wrong belief about retry nonce" --dry-run
```

### `daimon unforget`: levantar un olvido

```sh
daimon unforget o-3f8a2c --reason "olvidé el equivocado"
daimon unforget o-3f8a2c --dry-run
```

Unforget toma el id que imprimió el recibo del olvido, desde una persona en una
terminal (un agente no tiene forma de ejecutarlo), y levanta el olvido en cada
referencia que lleva su clave. No restaura nada: el valor se quitó de todas las
superficies, así que el levantamiento solo deja entrar de nuevo una captura
nueva de la misma afirmación. Sale con 0 solo cuando nada más retiene el valor;
si otro proyecto de esta máquina, una fila publicada de un compañero o un ledger
que no pudo probar lo retiene, dice cuál y sale con 4. Una fila de tombstone
publicada no tiene retractación, así que la fila que publicaste se queda y tus
compañeros siguen reteniendo el valor hasta que la levanten.

## `daimon handoff`: el batón

Los checkpoints son reconstructivos: extraídos de la transcripción,
rankeados, recortados por presupuesto, compitiendo por lugares. El momento
del traspaso deliberado, "próxima sesión: hacé ESTO primero, ojo con
AQUELLO", tiene otra semántica: intencional, chico, imperativo, y no debe
perder rango jamás frente al ruido ambiente.

```sh
daimon handoff "Cortá el release primero. Ojo: la clave de caché rotó."
daimon handoff --clear
```

El batón encabeza el próximo briefing, arriba de todas las secciones:

```text
HANDOFF (left deliberately by previous session, 2026-08-03T03:43:58Z):
→ Cortá el release primero. Ojo: la clave de caché rotó.
```

El encabezado dice quién lo escribió. Un batón escrito desde una terminal se ve
como arriba. Uno escrito sin terminal, o con `daimon handoff --by agent`, dice
`HANDOFF (written by an agent, unverified, <hora>):` y hay que tomarlo como una
nota de un agente, no como una instrucción de una persona. Los batones
guardados antes de que esto se registrara llevan la fuente `cli` heredada y
conservan la primera redacción.

Se guarda como evento, nunca como ítem cognitivo, no puede entrar al
ranking, al dedup ni al scoring de carry, y no puede resolver nada. Un batón
por proyecto; uno nuevo reemplaza al anterior (el rastro de eventos guarda la
historia). Sigue activo hasta que la sesión que lo leyó termina y serializa , 
una sesión que crashea nunca lo consume. Acotado chico a propósito: un batón
es "hacé X, ojo con Y", no un segundo checkpoint.
