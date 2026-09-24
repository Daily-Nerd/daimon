---
sidebar_position: 7
description: "Solicitudes entre proyectos, la cola decide, los pendientes abiertos, las enmiendas, y los eventos de log libres: cómo un proyecto le pide algo a otro, y qué sigue esperando una decisión humana."
---

# Solicitudes y decisiones

La memoria de un proyecto no se detiene en su propio límite. Un proyecto le
puede pedir algo a otro, y daimon sigue las dos puntas de ese pedido, más
una cola separada para todo lo demás que está esperando una decisión humana
adentro de un solo proyecto.

## Solicitudes entre proyectos

Una solicitud vive en el bucket propio del proyecto **emisor**: "este
proyecto le pide algo a aquel, y esta es la razón". El destinatario nunca
escribe en el ledger del emisor. Descubre el pedido leyendo el bucket del
emisor al momento del briefing, y lo responde con su propia decisión,
registrada en su propio bucket. Toda solicitud abarca entonces dos buckets,
y la vista combinada que ve cualquiera de las dos partes se arma al momento
de leer; nadie escribe nunca en el ledger de otro proyecto.

```sh
daimon request open --to <directorio> --ask "…" --why "…"
```

`--to` toma el directorio del proyecto destinatario, validado contra los
proyectos que daimon ya conoce, con una sugerencia si hay un typo parecido.
Hay dos tipos de pedido:

- **`work`**, el default: le pide al destinatario que cambie algo o gaste
  esfuerzo, y espera la decisión de una persona ahí antes de contar como
  aceptado.
- **`info`**: se puede responder con los propios artefactos del
  destinatario, y no debe ningún accept. Abrir un pedido `info` normalmente
  requiere un canal humano también del lado del emisor. Un agente solo
  puede abrir uno bajo una [regla](./rulings.md) activa de tipo `verb=open`
  que su propio proyecto ratificó para ese destinatario, y el registro lo
  dice claramente, así que un pedido abierto por una regla nunca parece uno
  que abrió una persona a mano.

Una vez abierta, un puñado de verbos la hacen avanzar:

- **`revise`** responde un needs-info, o afina el pedido, hasta tres veces
  por registro; al cuarto intento se abre una solicitud nueva con
  `--supersedes` en vez de reescribir en silencio, así que el linaje queda
  visible.
- **`accept` / `reject` / `needs-info`** son la decisión del destinatario, y
  son solo para humanos: hace falta una terminal interactiva. La única
  excepción tallada es un pedido `info` dirigido que sigue abierto, o en
  needs-info, que un agente puede aceptar directamente; un pedido `work`
  solo lo puede aceptar un agente cuando una
  [regla de permiso de solicitud](./rulings.md#los-permisos-de-solicitud-también-son-una-clase-de-regla)
  activa nombra a ese emisor puntual, y el registro entonces también nombra
  la regla. `reject` es definitivo para ese registro: el emisor abre una
  solicitud nueva en vez de volver a pedir.
- **`suppress`** saca un pedido del panel de briefing propio del
  destinatario, solo atención, solo humano; el registro sigue totalmente
  visible en `list` e `inbox`, y cualquier decisión posterior revierte la
  supresión.
- **`done`** reporta el pedido como satisfecho, desde cualquiera de los dos
  lados. El reclamo de un agente se renderiza sin verificar hasta que el
  próximo fin de sesión del destinatario verifica byte a byte la cita de
  evidencia contra su propia transcripción; un `done` humano se renderiza
  como está. En una solicitud `work` que nadie aceptó todavía, el `done` de
  un agente registra el reclamo pero deja abierta la cola de decisión, a la
  espera de `accept` o `reject`.

```sh
daimon request list    # las solicitudes que este proyecto envió
daimon request inbox   # las solicitudes dirigidas A este proyecto
```

Las dos aceptan `--json`. El propio briefing lleva dos paneles chicos con
los mismos datos: "solicitudes esperando por vos" del lado del destinatario,
y "decisiones sobre solicitudes que enviaste" del lado del emisor, cada uno
con un tope y una línea de exceso bien visible en vez de un descarte
silencioso. El [servidor MCP](../reference/mcp.md) expone la vista del
destinatario en modo solo lectura, como la herramienta `requests_inbox`;
ningún verbo que escriba una solicitud es alcanzable por MCP, y
`daimon_brief` en sí nunca lleva contenido de solicitudes.

## `decide`: qué está esperando por vos

`daimon loops` lista los pendientes abiertos y direccionables propios de
este proyecto, la contraparte de lectura de cerrar uno con `resolve`.
`daimon decide` es su espejo del lado humano: todo lo que de verdad está
esperando una persona, cada cosa con el comando exacto que la cierra.

```sh
daimon decide
daimon decide --all-projects
```

Lo que entra en esta cola es estructural, no una cuestión de gusto: un
registro pertenece acá solo cuando su propio camino de escritura rechaza un
canal que no sea humano, la misma regla que hace que `request accept`,
`amend ratify`, `ruling ratify` y `refute ratify` sean solo para humanos
desde el vamos. `decide` lee registros que ya existen y no escribe nada, así
que abrirlo nunca cambia lo que ve un agente. Por defecto está acotado a
este proyecto; los demás proyectos llegan solo como conteos, y
`--all-projects` los expande a texto completo, cada comando ya enrutado con
`--slug` para que igual corra desde donde estás. El briefing lleva el mismo
conteo en una sola línea, que apunta de vuelta acá.

## Enmiendas: el estado avanzó, el pendiente sigue abierto

Una enmienda dice que el estado de un ítem del briefing avanzó mientras el
ítem sigue abierto. No es una resolución (el pendiente no se cerró) ni un
reverify (nada se puso viejo): es el verbo para el paso intermedio, con
evidencia, como "se aprobó el issue al que hacía referencia" o "se destrabó
el bloqueo".

```sh
daimon amend propose <item-id> --change progressed \
  --evidence "se mergeó el PR" --by agent
daimon amend ratify a-0f1e2d3c4b5a
daimon amend reject a-0f1e2d3c4b5a --note "el PR se mergeó pero el ítem es de otro repo"
daimon amend list
```

`--change` es un conjunto cerrado: `progressed`, `blocked` o `changed`. La
`--evidence` es una cita textual de la transcripción, no un resumen,
verificada byte a byte contra la transcripción de la sesión al final de
ella. La propuesta de un agente queda invisible en el briefing hasta que esa
verificación pasa; una vez que pasa, se renderiza como una línea marcada, sin
confirmar, atribuida al agente, con sus propios comandos de confirmar y
rechazar, nunca como un hecho asentado, porque una verificación aprobada
certifica que la cita existe, no que significa lo que el agente dice que
significa. Solo un `ratify` humano explícito gana el marco neutro de
"amended". Una enmienda rechazada se puede volver a proponer: el rechazo es
un resultado registrado, no un candado sobre la afirmación.

## `log`: un rastro libre

```sh
daimon log --text "…" --kind note
```

`log` agrega un evento libre a la línea de tiempo propia del proyecto. Cero
LLM, nada extraído, nada rankeado: es para la nota que pertenece al
registro pero no es en sí un pendiente, una decisión, ni una afirmación
sobre el estado actual de nada.
