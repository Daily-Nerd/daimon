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
- La retención en el briefing, la supresión del arrastre y `daimon stats`
  heredan el tombstone a través del mismo flujo de eventos.

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
