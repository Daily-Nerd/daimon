---
sidebar_position: 5
description: "Las reglas vigentes son restricciones ratificadas por un humano que se renderizan en cada briefing futuro y nunca decaen. El ciclo de vida, los canales de autoridad, el tope, los checks con dientes y las capas."
---

# Reglas vigentes

Todo lo demás en este sitio es memoria: una afirmación sobre lo que pasó,
que se arrastra hacia adelante hasta que decae o alguien la resuelve. Una
regla vigente no es una memoria. Es una restricción que un humano puso en
vigencia, y no decae, no compite por el arrastre, y no se vuelve a extraer
con un modelo en la próxima sesión. Una vez ratificada, se renderiza en cada
briefing hasta que un humano la retira.

Las reglas viven en el mismo ledger append-only que las
[refutaciones](./negative-knowledge.md): mismo espacio de ids, misma cita de
evidencia, misma maquinaria de borrado. Son las dos polaridades opuestas de
un mismo registro. Una refutación dice qué no es cierto. Una regla dice qué
tiene que valer de ahora en más.

## El ciclo de vida

Cuatro verbos mueven una regla a través de su vida:

```sh
daimon ruling propose --subject "posts públicos" --scope publicaciones \
  --evidence issue:693 --by agent
daimon ruling ratify r-1a2b3c4d5e6f
daimon ruling revise r-1a2b3c4d5e6f --evidence issue:700
daimon ruling retire r-1a2b3c4d5e6f
```

(`propose` y `revise` también toman la bandera que lleva el texto mismo de
la regla; mirá la [referencia de CLI](../reference/cli.md) para la sintaxis
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

## Canales de autoridad

Ratificar es la única transición que nunca puede autodeclararse. La CLI
solo puede observar dos canales: una terminal interactiva (`cli-tty`,
autoridad `human`) y un `--by agent` explícito (`cli-agent`, autoridad
`agent`). No existe una bandera para afirmar que sos humano: el canal
humano es la *ausencia* de `--by agent`, verificada contra una terminal
real, no contra una cadena que un agente podría pasar. Hay otros dos
canales, `ui` y `signed`, que existen solo para un proceso anfitrión que
tiene su propia autoridad de operador verificada y escribe a través de la
biblioteca, en proceso; ninguna bandera de la CLI llega a ninguno de los
dos, porque una bandera que un agente pudiera pasar desde una shell sería
apenas `--by human` con otro nombre.

Cada regla renderizada nombra su canal con honestidad: `ratified
(interactive)`, `ratified (ui)`, `ratified (signed)`, o, para una escritura
todavía pendiente, `agent-proposed`. Nada de esto hace que la falsificación
sea imposible. Alguien con acceso a la máquina todavía puede manejar una UI
o abrir una terminal. Lo que compra el canal es procedencia, no prueba: la
falsificación cuesta una impostura deliberada en vez de una sola bandera, y
el registro queda auditable después.

Una etiqueta sobrevive a la ratificación sin importar el canal: si un
agente escribió el texto de la regla, el briefing lo marca `[agent-written]`
aunque un humano ya la haya ratificado. Quién escribió las palabras y quién
las aprobó son dos hechos distintos, y el renderizado mantiene ambos
visibles.

## El tope, y por qué se aplica donde se aplica

Un proyecto puede tener como máximo `DAIMON_RULING_CAP` reglas activas,
siete por defecto. El número es un default de presupuesto de renderizado,
no una afirmación sobre cuántas reglas necesita alguien en la práctica. Lo
que importa es dónde muerde el tope: en la **activación**, en `ratify` y en
`propose --ratify`, nunca en el momento de renderizar.

El razonamiento es directo. La sección de reglas de un briefing siempre
renderiza lo que le entra. Si el tope se aplicara en el renderizado en vez
de en la activación, una octava regla activa simplemente no se imprimiría,
en silencio, sin que nada le avise a nadie que una restricción ratificada
por un humano desapareció del único lugar donde se supone que siempre
aparece. Esa es la única falla que esta sección no puede tener. Por eso el
rechazo pasa antes, en la escritura que crearía la octava regla activa:
ratify se rechaza mostrando los ids ya activos, y vos retirás una o subís
el tope a propósito. Si un ledger termina igual por encima del tope (un
archivo editado a mano, o el tope bajado después de los hechos), el
briefing renderiza lo que entra en el tope y agrega una línea bien visible
que nombra cuántas quedaron afuera, apuntando a `daimon ruling list
--inherited` para ver el panorama completo.

## Checks con dientes

Una regla puede llevar un `check`: un script de shell que se compara contra
una línea de comando, y que corre antes de que ese comando se ejecute en un
anfitrión que lo soporte.

```sh
daimon ruling propose --subject "mensajes de commit" --scope git \
  --evidence issue:900 \
  --check-body-file check.sh --check-match '^git commit' \
  --check-intent enforce
```

Quien la propone pide una *intención* (`enforce`, `warn` o `record-only`);
el anfitrión entrega un *modo*, que siempre es el más débil entre lo que se
pidió y lo que ese anfitrión puede hacer de verdad. Claude Code entrega
`enforce` y `warn` tal como se pidieron. Codex no tiene ningún canal para
un aviso, así que ahí `warn` degrada a `record-only`. Windsurf no tiene
ningún canal de cumplimiento medido, así que ahí toda intención llega como
`unsupported`. `enforce` bloquea el comando con la denegación estructurada
propia del anfitrión; `warn` lo deja pasar y muestra el motivo;
`record-only` no dice nada y solo registra que el check corrió.

El cuerpo del check viaja con la regla: sus bytes quedan guardados en el
registro, nunca una ruta a un archivo, porque una ruta es invisible en
cualquier otra máquina a la que la regla pueda llegar. `ratify` muestra el
cuerpo exacto del check y ata la activación a su hash, la misma disciplina
de atar-al-contenido que recibe el texto de la regla. `daimon ruling check
try <id> --command "<cmd>"` corre un check contra un comando que vos
nombrás sin armar nada ni registrar nada, que es la forma correcta de
ensayarlo antes de ratificarlo. `daimon check sync` y `daimon hooks
install` mantienen sincronizado con el ledger el manifiesto en disco que
lee un anfitrión; `daimon ruling checks` muestra qué está armado, en qué
anfitrión, y si alguna vez corrió. La mecánica completa, incluyendo
exactamente qué recibe un check como su sujeto y cada modo de falla, está
en la [referencia de CLI](../reference/cli.md#checks-en-ejecución).

## Los permisos de solicitud también son una clase de regla

Una regla puede otorgar un permiso de manejo de solicitudes en vez de un
check, o junto con uno. Dos formas, según el `verb`:

- `sender=<slug> kind=work|info verb=accept by=agent` deja que el propio
  agente de ese proyecto emisor registre `accept` sobre una solicitud
  `work` que este proyecto le debe, sin que una persona intervenga para ese
  emisor puntual.
- `to=<slug> kind=info verb=open by=agent` deja que el propio agente de
  este proyecto abra una solicitud de tipo `info` hacia ese destinatario,
  también sin que ninguna persona la toque.

Las dos se renderizan como una sola línea compacta en el briefing en vez de
la forma en prosa habitual, marcadas para que quien lea distinga un permiso
aplicado por código de una regla en prosa. Mirá
[solicitudes y decisiones](./requests-and-decisions.md) para lo que hacen
de verdad `kind` y los verbos accept/open.

## Reglas de capa

Una regla no tiene por qué pertenecer a un solo repositorio. Ratificala
contra un directorio simple por encima de tus proyectos, uno que no sea en
sí mismo un árbol de trabajo de git, y cada proyecto debajo la hereda:

```sh
daimon ruling propose --project ~/work --subject "secretos compartidos" \
  --scope "cada repo bajo ~/work" --evidence issue:1092 --ratify
```

El directorio califica como capa una vez que está en el home o por debajo
de él, no está tapado por un `.git` en ningún punto arriba, y tiene su
propio bucket. Cada proyecto debajo entonces renderiza esa regla *antes*
que las propias, marcada `[from ~/work]` para que nadie confunda una regla
heredada con una local.

La herencia muerde en las dos direcciones. Una regla heredada cuenta contra
el tope propio del proyecto hijo, exactamente como si se hubiera ratificado
ahí mismo, porque a la falla que el tope existe para evitar (una sección
truncada en silencio) no le importa si el exceso vino de este proyecto o de
una capa de arriba. Y un hijo no puede pisar ni recuperar en silencio un id
heredado: `retire`, `revise` y `ratify` sobre un id que solo existe como
regla de una capa se rechazan en el hijo, y nombran el directorio de la
capa desde donde hay que correr el comando. Un hijo tampoco puede fundar ni
activar una regla propia bajo un id que una capa de arriba ya tiene activo.
Cuando hay más reglas vigentes de las que un briefing tiene lugar para
mostrar, heredadas incluidas, la nota de exceso apunta a `daimon ruling
list --inherited` para la vista combinada.

Mirá la [referencia de CLI](../reference/cli.md) para la superficie
completa de banderas de cada verbo de arriba.
