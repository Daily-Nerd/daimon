---
sidebar_position: 8
description: "Inspeccioná por qué se confía en un item con daimon why: los ejes de evidencia, la salida JSON, la divulgación de la fuente, los ids retenidos y qué verbos actúan sobre un id."
---

# Inspeccionar un item

`daimon why` explica qué puede probar Daimon sobre un item recordado, sin
colapsar varios tipos de evidencia en un puntaje de confianza.

El flujo normal es:

```console
daimon recall retry policy
daimon why o-3f8a2c
daimon resolve o-3f8a2c --dry-run
daimon resolve o-3f8a2c --note "shipped in 0.27.0"
```

`recall` imprime el ID exacto del item entre corchetes. `why` tiene alcance de
proyecto por defecto, igual que `recall`; usá `--project PATH` o `--slug SLUG`
para nombrar otro alcance de forma explícita. Nunca adivina entre proyectos.

## Leer la evidencia

El inspector informa ejes independientes porque pueden discrepar legítimamente:

| Eje | Valores | Significado |
| --- | --- | --- |
| Capture | `verified`, `not-verified`, `unknown` | El resultado determinista de la cita al momento de la captura, registrado en un recibo durable. |
| Provenance | `bound`, `legacy-inferred`, `legacy-unbound` | Si la cita lleva un recibo de fuente autocontenido o solo metadatos de diagnóstico más viejos. |
| Locator | `resolved`, `absent-local`, `unsupported`, `ambiguous`, `unreadable`, `remote-author` | Si el resolvedor estricto del host puede identificar exactamente una fuente local legible. |
| Bytes | `unchanged`, `changed`, `unknown` | Si los bytes actuales de la fuente reproducen el digest del recibo. |
| Current support | `message-id-match`, `transcript-scan-match`, `not-reproduced`, `not-checked` | Si la cita almacenada se puede reproducir ahora. |
| Verifier | `same-version`, `different-version`, `unknown` | Si el verificador determinista actual coincide con el verificador registrado. |
| Lifecycle | `active`, `resolved`, `forgotten`, `superseded` | El último estado del ciclo de vida, de solo anexado. |

La corroboración se muestra aparte, como un conteo y referencias a sesiones
fuente. No modifica ningún eje de evidencia.

`preceding_tool_context` es una sección de auditoría aparte. Su estado
`observed` asigna a cada fuente de cita verificada del asistente hasta 20 IDs
de filas de resultados de herramientas anteriores, que terminan en un límite
reconocido de entrada de usuario del host. `unavailable` nombra por qué la
captura del host no pudo establecer esa ventana, y `not_recorded` se usa para
checkpoints más viejos. Una lista de resultados vacía bajo `observed` significa
que la ventana acotada no contenía resultados de herramientas. Estos IDs
describen solo el orden registrado y nunca prueban que el modelo haya leído o
usado un resultado; la salida y los argumentos de las herramientas nunca se
almacenan.

Por ejemplo, estos hechos pueden coexistir:

```text
Now: capture verified; source changed; quote supported by its bound message
Bytes: changed
Current support: message-id-match
```

Eso no significa que el item sea inventado o inválido. Significa que el archivo
fuente completo cambió mientras el mensaje atado sigue respaldando la cita. Los
ejes preservan esa distinción en vez de inventar un único resultado.

## JSON

Usá `--json` para automatizar:

```console
daimon why o-3f8a2c --json
```

El documento V1 tiene `schema_version`, valores estables bajo `axes`, metadatos
del item, referencias de corroboración, el recibo durable cuando existe, un
bloque `ranking`, y ningún campo `summary` derivado. Los scripts deben
inspeccionar los ejes que realmente les importan.

### Ranking

`ranking` publica la clave de orden que usa el propio Daimon, junto con las
entradas que la produjeron:

```json
{
  "effective_weight": 0.448,
  "computed_at": 1800000000.0,
  "item_type": "recent_decision",
  "rules": "recent_decision",
  "inputs": {
    "importance": 8,
    "importance_source": "item",
    "trust": "verbatim",
    "trust_ceiling": 3.0,
    "first_seen": "2026-08-01T00:00:00Z",
    "age_days": 10.0
  },
  "factors": {
    "base": 0.8,
    "recency": 0.7,
    "type_decay": 0.8,
    "overdue_boost": 1.0,
    "raw": 0.448
  }
}
```

El peso se recalcula en cada lectura y nunca se almacena, porque decae con la
edad: un valor escrito al momento de la captura estaría viejo para cuando
alguien lo lea. `computed_at` es la época contra la que se calculó, y el número
no se puede interpretar sin ella.

El sentido de publicar las entradas es que un consumidor pueda rehacer la
aritmética y llegar al mismo número. `raw` es el producto de los cuatro
factores, y `effective_weight` es ese producto bajo el techo de confianza. El
techo satura en vez de truncar, así que un `raw` por encima del techo es normal
y sigue ordenado.

Dos valores no son lo que parecen a propósito:

- `importance_source: "default"` significa que el item no traía importancia y
  se sustituyó por 5. Un item sin puntaje no es un item de importancia 5.
- `age_days: null` significa que no hay un `first_seen` utilizable, que no es lo
  mismo que un item recién creado. La recencia cae a neutral, no a máxima.

`rules` nombra las reglas de tipo que el cálculo realmente aplicó, y difiere de
`item_type` cuando la clase del item no corresponde a ningún tipo conocido.

Los códigos de salida describen la ejecución del comando, no el estado
epistémico:

- `0`: se renderizó el item con alcance único, incluidos los estados de
  evidencia degradados;
- `1`: el item no se encontró en ese proyecto;
- `2`: argumentos inválidos o forma de ID de item inválida.

## Divulgación de la fuente

La salida por defecto es solo metadatos y no imprime ningún extracto crudo del
transcript. Agregá `--source` deliberadamente:

```console
daimon why o-3f8a2c --source
```

Cuando hay vínculos de mensaje validados, Daimon lee la fuente cruda en memoria,
extrae como máximo tres mensajes vinculados y 600 caracteres colapsados, y
luego redacta una sola vez en el límite final de visualización. Nunca persiste
el extracto ni imprime una ruta absoluta al transcript.

Cuando los vínculos exactos no están disponibles, el inspector muestra la cita
almacenada ya redactada y dice que el tramo exacto del mensaje crudo no se
puede reconstruir con seguridad. No vuelve a redactar la evidencia almacenada.

`not-reproduced` es una observación actual, no una acusación. Las fuentes se
pueden editar, truncar, migrar o parsear distinto con un adaptador de host más
nuevo. Usá los demás ejes para entender qué condición cambió realmente.

### Qué conjuntos rechazan la ventana

La ventana se arma desde el transcript crudo, y la redacción solo atrapa formas
conocidas de secretos, nunca texto libre que alguien olvidó a propósito o que
una persona puso en cuarentena. Ninguna clave puede escanear un bloque de
transcript buscando un valor, así que el rechazo se basa en conjuntos y no en
items. `--source` se rechaza cuando se cumple cualquiera de estas condiciones, y
el rechazo nombra la razón y nunca un conteo:

| `reason` | Cuándo |
| --- | --- |
| `closed` | El ledger de confianza no se puede leer, así que no se puede probar que nada esté en cuarentena. |
| `forgotten-set` | Existe cualquier lápida de forget en esta máquina, en cualquier proyecto, o un compañero publicó una. |
| `quarantine-set` | Este proyecto tiene una cuarentena activa. |
| `withheld-item` | El item en sí está retenido. |

El costo se dice con claridad: una vez que algo se olvidó en cualquier lugar de
la máquina, `why --source` se rechaza en todos lados, para siempre, porque las
lápidas nunca vencen. El item en sí igual se imprime; solo se retiene el
extracto del transcript. Un conteo diría cuántos valores olvidaron otros
inquilinos, así que no se publica ninguno.

## Un id retenido

Un id exacto que el lector no puede ver igual responde, con el marcador en lugar
del valor:

```console
$ daimon why o-3f8a2c
Now: capture unknown; item withheld; lifecycle active
Item: [o-3f8a2c] [question] [withheld: quarantine tr-1a2b3c4d5e6f] (daimon trust show tr-1a2b3c4d5e6f)
Lifecycle: active
```

El marcador es uno de `[withheld: quarantine tr-…]` (una persona puso el valor
en cuarentena; `daimon trust show` tiene el registro), `[withheld: trust ledger
unreadable]` (no se puede probar que nada esté en cuarentena; corré
`daimon status`) y `[withheld: forgotten]`. En `--json`, el `text` y el `quote`
del item son `{"state": "withheld", "reason": …}` (más `quarantine_id` para una
cuarentena), `ranking`, `receipt`, `source` y `preceding_tool_context` son
`null`, y `current_support` es `withheld`. No hay ningún valor, clave, cita,
contexto de herramienta ni ventana de transcript en el documento. Los ejes de
evidencia que descansan en el recibo de la cita del item (capture, provenance,
locator, bytes, verifier) igual informan: la vista conserva una proyección sin
texto del recibo cuando retiene una copia, así que una cuarentena o el forget de
un compañero no borra cómo se capturó el item. El recibo, la fuente y la cita en
sí no se publican. Un id que es solo una lápida no tiene copia que proyectar e
informa `unknown`.

Forgotten se anuncia solo acá: `daimon why`, `daimon blame` y la página why del
visor dicen `[withheld: forgotten]` para el único id exacto que preguntaste.
Todo listado (`daimon diff`, recall, el briefing, `status`, las tablas del
visor) trata un valor olvidado como ausente: sin fila, sin conteo, sin arista.
Preguntar por un id que nunca se almacenó responde `no item`, así que lo único
que puede aprender una pregunta por id exacto es que ese id se almacenó y luego
se olvidó, que es el mismo hecho que un compañero ya recibe como lápida.

Una excepción, para las reglas. Una regla impone, y un conjunto de anclas
silenciosamente más corto dejaría que una compuerta construida sobre ella
falle abierta. Por eso toda fila de regla que un lector puede ver lleva
`as_ratified`, siempre presente, y vale `false` cuando uno de sus valores de
imposición (sus anclas, las anclas que coincidieron en un guard, o el match o
el cuerpo de su check) está retenido por cualquier razón. El bit está en
`ruling list --json` y `ruling show --json`, y su salida de texto imprime `not
as ratified: an enforcement value is withheld`. Las reglas vigentes del
briefing marcan la línea `[not as ratified]`, y el carril de reglas del visor
muestra la misma marca. La fila igual nunca nombra, cuenta ni ubica el valor.
Las filas de refutación no llevan esa clave. El manifiesto de checks armados se
construye con las mismas filas juzgadas, así que una regla que no está as
ratified no arma nada, y `check sync` y `hooks install` dicen cuántas se
saltearon. `refute guard` coincide con las anclas y sujetos que un lector puede
ver, así que uno retenido nunca lo dispara. `refute search` también coincide y
ordena solo valores que el lector puede ver.

Un id cuyas copias ya no están pero cuyo ciclo de vida el ledger todavía nombra
(un pendiente resuelto que salió de todos los checkpoints retenidos) imprime
`Item: … (content unavailable)` y su ciclo de vida; `daimon blame` responde el
mismo id de la misma manera.

## Dónde se busca un id

Una búsqueda está acotada al bucket de este proyecto, nunca a la máquina, y
nunca reconstruye el índice de recall:

1. la ventana de punteros (`latest.json` y los archivos `prev-N.json` que
   guarda `DAIMON_CHECKPOINT_HISTORY`, tres por defecto); gana la marca
   `created` más nueva cuando varias copias difieren;
2. el índice de recall, abierto solo lectura, únicamente para saber qué archivo
   de sesión tiene un id que la ventana ya no tiene; el valor sale de ese
   archivo por la misma regla de visibilidad que todo lo demás;
3. los registros de ids olvidados y de cuarentenas;
4. nada.

Un item respondido solo desde el índice (el checkpoint espejado de un compañero
sin archivo local, o un archivo legado sin sello de proyecto) se juzga en
lectura como cualquier otra copia y se marca `index_only` en el JSON. Una fila
cuyo archivo local está roto, o está sellado para otro proyecto, no se usa en
absoluto: la respuesta es `no item`.

Qué cambia esto, cada punto en la dirección segura (puede anunciar de menos,
nunca filtrar):

- Un item en cuarentena más viejo que la ventana de punteros, cuyo registro de
  cuarentena se abrió sin `--item-id`, dice `no item`: el registro nombra un
  valor, no un id, y el valor ya no está en ninguna copia que lea la búsqueda.
  La respuesta interna dice `outside_window`, que es donde buscó.
- El mismo id puede leerse `[withheld: quarantine …]` mientras el índice todavía
  tiene su fila previa a la cuarentena y `no item` después de la próxima
  reconstrucción del índice, así que la respuesta puede cambiar con el momento
  de `daimon recall` o de cualquier otra llamada que caliente el índice.
- `why` responde por el bucket propio de este proyecto. Ya no llega a los otros
  slugs listados en `DAIMON_EXTRA_READ_SLUGS`.
- Un item serializado desde la última construcción del índice se encuentra solo
  por la ventana de punteros.

`occurrences` cuenta las sesiones distintas que la ventana y el índice saben que
tienen el id, no cada copia en disco.

## Verbos que actúan sobre un id

`daimon resolve`, `reverify`, `amend propose`, `anchor --attach` y `forget`
eligen su objetivo con la misma regla de visibilidad que toda lectura. Ninguno imprime,
cuenta ni escribe un valor que el lector no puede ver.

**Una consulta de texto nunca adivina.** `resolve <text>` y `forget <text>` solo
se atan cuando coincide exactamente un item visible y ningún item retenido
también coincide. Un acierto visible junto a uno retenido es ambiguo, porque
atarlo sería una conjetura sobre un valor que no podés ver. El rechazo dice
`ambiguous` y lista los candidatos visibles, luego una línea de conteo y el
puntero propio del verbo:

```console
  o-3f8a2c  [open_questions] the release pipeline needs a rollback step
1 withheld item(s) also match; use the exact id (daimon status --suppressed lists them)
resolve by exact id: daimon resolve <id>; daimon loops lists the open items
```

Un fallo imprime `no item matches '<query>'` y la misma línea de puntero, nunca
una lista de todo. La línea de conteo no se imprime mientras el ledger de
confianza no se puede leer, porque entonces todo item está retenido y un número
solo diría qué consultas coincidieron. `anchor --attach` es más estricto: mira
solo items visibles y no imprime conteo, porque una subcadena más un conteo
permite encontrar un valor oculto de a un carácter.

**Por id exacto:**

| el id es | `resolve`, `reverify` | `amend propose` | `forget` |
| --- | --- | --- | --- |
| visible | se ata | se ata (solo pendientes abiertos) | se ata |
| en cuarentena | una persona lo ata, sin eco; un agente es rechazado (salida 2) | rechazado en todo canal (salida 2) | una persona lo ata; un agente es rechazado (salida 2) |
| retenido porque el ledger de confianza no se puede leer | rechazado en todo canal con `daimon trust repair` (salida 2) | rechazado en todo canal (salida 2) | se ata en todo canal |
| olvidado | no se encuentra | no se encuentra | no se encuentra |

Los verbos por id de item existen en la CLI, el visor y la api en proceso; las
herramientas MCP no reciben ningún id de item por diseño (fijado por un test).

Un id olvidado nunca es objetivo de escritura para `resolve`, `reverify` ni
`amend propose`: se atan por la misma regla de visibilidad que toda lectura, que lee un id
olvidado como sin coincidencia tanto para una persona como para un agente.
`forget` tampoco lo toma. El texto plano que un forget dejó atrás lo termina
`daimon forget --resume` (`daimon ledger repair <name>` para un solo ledger).
Un id exacto en `forget` sí pasa por encima de una caída del ledger de
confianza, porque el id no nombra ningún valor.

### Qué escribe un forget, y los dos verbos que lo terminan o lo levantan

Un forget agrega tres tipos de fila a `events.jsonl` en una sola escritura: la
fila del id que nombraste, una fila de clave en una ref `forgotten-key:` propia,
y una fila por cada id hermano que tenía el mismo valor en cualquier superficie
(una copia prev-N cuenta). La fila del id nombra la clave de contenido en su
estado, así que incluso una cola rota retiene el valor. La ref de clave es un
token aleatorio y no se deriva de nada, así que no le dice nada a un lector
sobre el valor.

Un reopen sobre el id reemplaza solo la fila del id, así que el valor sigue
retenido. Solo `daimon unforget` levanta un forget:

- `daimon unforget <id>` toma el id exacto que imprimió el recibo del forget y
  levanta toda ref vigente que lleve la clave, en un solo append de filas
  `reopened:unforget`. Necesita una persona en una terminal y no tiene canal de
  agente. La comprobación es si stdin es una terminal, como en `trust release`,
  así que un host que corre los comandos de un agente dentro de una
  pseudo-terminal la pasa.
- No restaura nada. El valor se quitó de toda superficie, así que el
  levantamiento solo deja volver a entrar a una nueva captura de la misma
  afirmación.
- Prueba el levantamiento o sale con 4. La salida 0 significa que ningún otro
  proyecto de esta máquina, ninguna fila publicada de un compañero y ningún
  ledger no probado sigue reteniendo el valor. Si no, dice qué clase lo hace:
  otro proyecto de esta máquina, una fila publicada de un compañero (que la
  levanta él), o un ledger que no pudo probar. Nunca nombra un proyecto ni un
  autor.
- Un canal de agente, un id desconocido y un id olvidado reciben el mismo
  rechazo, antes de cualquier búsqueda, así que el verbo no sirve para preguntar
  si un id fue olvidado.

`daimon forget --resume` no toma objetivo y necesita una persona en una
terminal. Termina todas las fases de todo forget vigente del proyecto: la fila
de clave, la publicación al equipo, las limpiezas por valor y por id, las
purgas totales y las cachés derivadas. Una superficie que no pudo alcanzar se
lista como `unreached:` con su cura y la corrida sale con 4; volvé a correrlo
tras arreglar la causa. Un forget hecho antes de que existiera la fila de clave
es una sola fila de id que un reopen todavía levanta. `--resume` escribe la
fila de clave faltante e imprime una línea con el conteo, y de ahí en más solo
`unforget` levanta también esos. `--only <phase>` corre una sola fase, y
`--republish` es la fase de publicación (`--resume --only publish`). `--dry-run`
previsualiza un resume (cuántos forgets, cuántos recibirían una fila de clave,
qué fases, qué reglas ACTIVAS) y no escribe nada. Una copia de checkpoint o de
espejo de equipo que no se puede parsear mantiene `forget` en salida 4 hasta que
una persona arregla el archivo a mano; `--resume` la informa en cada corrida.

Una fila de lápida publicada no tiene retiro porque las filas de lápida no
llevan versión ni gancho que falle cerrado; esa compuerta existe solo para las
filas de cuarentena. Entonces `unforget` levanta tu máquina y deja la fila que
publicaste, y un compañero que ya la plegó sigue reteniendo el valor hasta que
la quite.

**Nada escribe el valor.** El evento que agregan `resolve` y `reverify` tiene el
id, el estado y la nota, nunca el texto del item, y el candidato de supersesión
del serializador tiene los dos ids y nada más. `--dry-run` imprime `would
resolve <id>: <text>` para un objetivo visible y el marcador para uno retenido.
`forget` juzga un registro del ledger tanto por el sujeto que imprime como por
el valor con el que coincidió tu consulta, así que una refutación cuya evidencia
repite una afirmación en cuarentena se cuenta, no se lista.

**`anchor --attach` conserva todos los demás bytes.** Parchea un item por su
posición en el checkpoint almacenado y reescribe el resto como estaba, así que
un item en cuarentena que el briefing oculta sigue ahí después. El tema es un
objetivo válido.

**Relaciones y la auditoría de citas.** `daimon relations list` y `show`, y el
panel de relaciones del visor, leen el texto de cada extremo de una arista por
una sola búsqueda: un extremo visible imprime su texto, uno en cuarentena (o
cualquiera mientras el ledger de confianza no se puede leer) imprime el
marcador, y una arista que toca un item olvidado se deja afuera y no se cuenta
en ningún lado. `daimon audit quotes` audita las copias que un lector puede ver,
así que un item en cuarentena u olvidado no se comprueba ni se imprime.

## Prosa en los ledgers

Los ledgers de refutaciones, reglas, solicitudes, enmiendas y confianza guardan
lo que escribieron personas y agentes, y la frase de una persona puede ser
exactamente el valor que una cuarentena o un forget retiene. Todo verbo que
imprime un registro (`refute`, `ruling`, `request`, `amend list`, `trust`,
`decide`, la herramienta de bandeja de solicitudes y el carril de refutaciones
del visor), en texto y en `--json`, juzga cada columna de prosa como un valor
entero: un valor olvidado queda en blanco, uno en cuarentena es `[withheld:
quarantine tr-…]`, y una copia del texto de un item se enmascara mientras el
ledger de confianza no se puede leer. Lo que escribió una persona sigue
legible entonces, igual que las reglas vigentes en el briefing. Una solicitud es
una unión entre dos proyectos, así que la juzga cada proyecto que nombra.

Una ceremonia no pide una firma sobre un texto que la persona no puede leer:
`ruling ratify`, `ruling revise`, `ruling propose --ratify` y los verbos
`refute` que actúan sobre un registro se detienen con salida 2 y no escriben
nada cuando el registro (o las palabras recién tipeadas) tiene un campo
retenido. Esto vale en todo canal, una terminal incluida: una cuarentena es un
cerrojo de una persona, así que el registro queda congelado hasta que
`daimon trust show <id>` en una terminal y `daimon trust release <id>` lo
levanten, como dice el rechazo. Un campo olvidado dice que el registro no se
puede revisar. La firma ata el registro almacenado, nunca lo que se mostró.

`daimon trust show` y `daimon trust list` entregan la evidencia que cita una
cuarentena a una persona en una terminal. Quienes llaman sin terminal ven
`Evidence: [N withheld]` en el texto y ninguna clave `evidence` en el JSON, y el
motivo de la cuarentena se enmascara para todos. Una cuarentena cuyo valor fue
olvidado no muestra clave de valor.
