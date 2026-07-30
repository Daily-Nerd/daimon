---
sidebar_position: 4
---

# El ciclo de vida de los ítems

Un ítem de briefing no es una fila estática — atraviesa un ciclo de vida:
nace en una sesión, se arrastra mientras está abierto, y eventualmente se
cierra, se revive o se elimina. Tres comandos manejan las transiciones, y los
tres comparten un contrato: **nunca se adivina nada por ti.**

## El contrato de nunca-adivinar

`resolve` y `forget` aceptan un id exacto de ítem (`o-3f8a2c`) o una consulta
de texto libre — pero la consulta debe coincidir con **exactamente un** ítem.
Una coincidencia ambigua se rechaza listando los candidatos; eliges uno por
id. Ambos comandos aceptan `--dry-run`, que ejecuta la misma búsqueda e
imprime lo que *pasaría* sin escribir nada — mira antes de escribir.

Una salvedad que el contrato no puede cubrir: una coincidencia segura con el
ítem *equivocado* no es ambigua, así que el rechazo nunca se dispara. Para
eso existe `--dry-run`.

## `daimon resolve` — cerrar un pendiente

```sh
daimon resolve "retry policy for the payments webhook" --dry-run
daimon resolve o-3f8a2c --note "shipped exponential backoff in #212"
```

Resolver registra un evento de solo-anexado; desde entonces, los briefings
**retienen** el ítem en lugar de arrastrarlo desactualizado. El ítem no se
borra — su historia sigue siendo buscable, y el rastro de eventos muestra
cuándo y por qué se cerró. `--status` acepta un estado de ciclo de vida
libre; cualquier estado que empiece con `reopen` revive el ítem.

## `daimon reverify` — afirmar que sigue siendo cierto

```sh
daimon reverify o-3f8a2c --evidence "checked the release page"
```

Reverify es la respuesta a la
[advertencia de desactualización](./carry.md#la-advertencia-de-desactualización):
un ítem arrastrado envejeció más allá del umbral, verificaste el mundo, y
sigue en pie. El evento reinicia el sello de última verificación del ítem,
así que el reloj de la advertencia arranca de nuevo. Reverify acepta
**solo ids exactos** — re-afirmar una afirmación es deliberado, así que no
hay búsqueda difusa que pueda dispararse mal.

Reverify es también la mitad de **rechazo** de un candidato a supersesión
(abajo).

## `daimon forget` — eliminar, de forma demostrable

```sh
daimon forget o-3f8a2c --reason "contains client name"
daimon forget "wrong belief about retry nonce" --dry-run
```

Resolve cierra un ítem pero conserva su contenido en la historia. Forget es
para los casos donde el contenido mismo debe irse — un nombre que nunca debió
capturarse, un detalle de proyecto, una creencia equivocada que se sigue
arrastrando. La redacción en el momento de captura es la primera línea de
defensa; forget es la segunda, para los juicios que ningún patrón de
redacción puede conocer.

Qué ocurre al olvidar:

- El ítem se elimina del checkpoint vivo, que se reescribe por el camino
  normal del store — la redacción se re-ejecuta y, con receipts activos, el
  **receipt se re-mintea sobre los bytes posteriores a la eliminación**
  (mira [receipts](./receipts.md)).
- Un **evento tombstone** de solo-anexado registra
  `forgotten:<hash de 12 caracteres>` — el hash, nunca el texto. Eliminar
  significa que el contenido también sale del rastro de auditoría; el rastro
  aún puede demostrar *que* algo se eliminó, cuándo y por qué (`--reason`,
  redactado como cualquier nota).
- El índice de recall borra las filas del ítem en **todas** las copias
  históricas de checkpoints de tu índice local — incluidas tus copias
  locales de los mirrors de equipo — así recall no puede resucitarlo.
  (Propagar tombstones a los mirrors propios de tus compañeros es un
  seguimiento deliberado, no está en v1.)
- La **caché de chunks** del serializer se purga **por completo**. Esa caché
  guarda salida de extracción pre-redacción durante unos días para que una
  captura interrumpida nunca re-pague sus llamadas al LLM — y como sus
  entradas se indexan por el texto del chunk, no por el valor contenido, la
  eliminación selectiva es imposible. Forget la vacía entera (el costo: los
  chunks más jóvenes que la ventana de rotación, `chunk_cache_days`, 3 días
  por defecto, se re-extraen la próxima vez). Las entradas también se
  cosechan por edad en esa ventana, independientemente de forget. La purga
  nunca es fatal — la eliminación del estado de creencias siempre se
  completa — y el comando reporta honestamente si la purga tuvo éxito.
- La retención en el briefing, la supresión del arrastre y `daimon stats`
  heredan el tombstone a través del mismo flujo de eventos.

### Cumplimiento de durabilidad de la eliminación

La afirmación "una memoria olvidada permanece olvidada" no se declara — se
comprueba como un protocolo ejecutable. `plugin/tests/test_deletion_durability_protocol.py`
pasa un valor olvidado por cada camino que podría resucitarlo silenciosamente y
demuestra que sigue ausente en cada uno, mientras un gemelo nunca-olvidado
permanece recuperable para que ninguna verificación pase de forma vacua:

| # | Paso | Resultado |
|---|------|-----------|
| 1 | Escribir un dato distintivo a través del serializer | recuperable |
| 2 | `forget` — briefing, arrastre, recall | eliminado |
| 3 | Re-alimentar el **transcript de origen** y re-serializar | no resucita |
| 4 | Reconstrucción del índice de recall | ausente |
| 5 | Un arrastre posterior | ausente |
| 6 | Mirror de escritura dual de equipo | ausente en la copia remota |
| 7 | Cadena del briefing renderizado | ausente |
| 8 | Filas SQLite de recall | ausente |
| 9 | Receipt firmado | vincula los bytes posteriores a la eliminación |
| 10 | Rastro de auditoría | registra la eliminación, sin nada de su texto |
| 11 | Caché de chunks del serializer (pre-redacción) | purgada por completo al olvidar |

**Resultado: 11 / 11 pasos en cumplimiento.** Las pruebas son deterministas y
usan cero cuota de modelo — un extractor precargado y un firmante simulado
reemplazan al LLM y a la CLI de vitni — así que es una verificación de
cumplimiento que corre en cada commit, no un benchmark. El paso 3 es el que más
importa: re-ingerir el material crudo del que salió un ítem eliminado es cómo
los sistemas lo traen de vuelta sin querer, y el tombstone por-valor lo
descarta en el límite de escritura sin importar qué re-produzca el extractor.

Un límite que vale la pena declarar con exactitud: la caché de chunks es
pre-redacción *por necesidad* (la verificación de citas necesita el texto
crudo), así que antes de que este paso entrara al protocolo, los bytes de un
valor olvidado podían quedarse en la caché hasta que actuara la cosecha por
edad. Ahora `forget` purga la caché local de chunks en el mismo comando, y la
cosecha de `chunk_cache_days` (3 días por defecto) sigue siendo el límite
superior independiente para todo lo escrito después de un forget. La
afirmación se limita a esta máquina: la caché nunca se sincroniza a ningún
lado.

## Candidatos a supersesión

Cuando una sesión nueva contradice un ítem arrastrado, el briefing presenta
un **candidato a supersesión**: ambos lados, con los comandos de
confirmar/rechazar en línea. Verificas cuál lado es cierto en el mundo, y
respondes con exactamente esos comandos:

- **Confirmar** — `daimon resolve <id>`: el ítem viejo está genuinamente
  superado; los briefings futuros lo retienen.
- **Rechazar** — `daimon reverify <id>`: la contradicción era aparente, no
  real; el ítem se mantiene, recién verificado.

El principio de diseño en todo el ciclo de vida: daimon señala, tú decides.
La contradicción, la desactualización y la eliminación se presentan con
evidencia y se resuelven con una acción explícita de un humano (o de un
agente explícitamente instruido) — nunca con un merge silencioso.
