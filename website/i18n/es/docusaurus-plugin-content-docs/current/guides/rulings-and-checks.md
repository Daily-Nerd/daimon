---
sidebar_position: 5
description: "Proponé, ratificá, revisá y retirá una regla vigente, adjuntale un check, ensayalo antes de armarlo y ratificá en una capa por encima de tus repositorios."
---

# Reglas y checks

Una regla vigente es una regla que una persona puso en vigor: se renderiza en cada briefing y nunca decae. Esta guía cubre los verbos, un check que corre antes de un comando de shell y una regla que obliga a todos los proyectos bajo un directorio. Qué es una regla, y por qué solo una persona puede ratificarla, está en [reglas vigentes](../concepts/rulings.md).

## Los cuatro verbos


Cuatro verbos mueven una regla a través de su vida:

```sh
daimon ruling propose --subject "posts públicos" --scope publicaciones \
  --evidence issue:693 --by agent
daimon ruling ratify r-5b19c3
daimon ruling revise r-5b19c3 --evidence issue:700
daimon ruling retire r-5b19c3
```

(`propose` y `revise` también toman la bandera que lleva el texto mismo de
la regla; mirá la [referencia de CLI](../reference/cli/index.md) para la sintaxis
exacta.)

- **`propose`** funda una candidata. Una propuesta de agente queda como
  candidata: no se renderiza nada, no se hace cumplir nada, hasta que un
  humano actúe sobre ella.
- **`ratify`** la activa. Es el único verbo que convierte una candidata en
  una regla vigente, y es deliberadamente teatral: imprime el texto
  completo, avisa qué va a significar para cada sesión futura, y pide una
  confirmación explícita con `y`. El registro queda atado al texto exacto
  que te mostró, por hash, así que una carrera entre lo que se mostró y lo
  que confirmaste nunca puede activar palabras distintas de las que leíste.
- **`revise`** cambia el texto, el alcance o la evidencia. Sobre una regla
  activa, el revise de un agente escribe una *propuesta* y la regla
  conserva su texto actual hasta que un humano la acepta con `ratify` (que
  muestra la propuesta pendiente, no el texto viejo, antes de volver a
  preguntar). El revise de un humano sobre una regla activa se aplica de
  inmediato, sin necesitar un ratify aparte para esa edición.
- **`retire`** la termina. Un humano la retira directamente; el retire de un
  agente queda registrado como propuesta y la regla sigue en pie. La
  evidencia es opcional acá, porque una regla que simplemente dejó de
  aplicar muchas veces no tiene nada que citar.
  El ledger registra por qué terminó: una candidata que nunca fue
  ratificada se lee como **rejected**, una regla activa que dejó de aplicar
  se lee como **retired**. Las dos terminan en el mismo estado,
  `overturned`; la palabra es la etiqueta que imprimen `ruling show` y
  `ruling list`, así meses después podés distinguir una propuesta que
  rechazaste de una regla que usaste y después dejaste. `daimon decide`
  lista solo candidatas y ofrece rechazar una como `reject`; el comando que
  da es `ruling retire`. Las reglas terminadas antes de que existiera esta
  etiqueta se siguen leyendo como `retired`.

## Adjuntar un check

Una regla puede llevar un script que corre contra un comando antes de que se ejecute, en un host que lo soporte.

```sh
daimon ruling propose --subject "mensajes de commit" --scope git \
  --evidence issue:900 \
  --check-body-file check.sh --check-match '^git commit' \
  --check-intent enforce
```

1. Ensayalo: `daimon ruling check try <id> --command "<cmd>"` corre el check contra un comando que nombrás, no arma nada y no registra nada.
2. Armalo con la intención más baja primero. Empezá con `--check-intent warn`, así un patrón que coincide con más de lo que querías cuesta una advertencia y no una acción bloqueada.
3. Ratificá. `ratify` muestra el cuerpo exacto del check y ata la activación a su hash.
4. Mirá qué está armado con `daimon ruling checks`. `daimon check sync` reconstruye el manifiesto que lee un host, y cada ratify, revise y retire ya lo hace.

Qué entrega cada host, qué recibe un check como sujeto y cada modo de falla están en [reglas heredadas y checks en ejecución](../reference/check-runtime.md).

## Ratificar en una capa

Una regla ratificada en un directorio común por encima de tus repositorios obliga a todos los proyectos que están debajo.

```sh
daimon ruling propose --project ~/work --subject "secretos compartidos" \
  --scope "cada repo bajo ~/work" --evidence issue:1092 --ratify
```

El briefing de un proyecto hijo renderiza primero las reglas heredadas, etiquetadas con la capa. `retire`, `revise` y `ratify` sobre un id heredado se rechazan dentro del hijo y nombran el directorio de la capa desde donde correrlos. `daimon ruling list --inherited` muestra la vista combinada.
