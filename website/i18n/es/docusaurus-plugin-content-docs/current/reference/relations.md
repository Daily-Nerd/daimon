# Relaciones (tipadas, confirmadas por humanos)

El ledger de relaciones registra afirmaciones tipadas entre items de memoria —
"esta decisión revisa aquella", "esto responde aquella pregunta" — como un
registro append-only en modo sombra: las relaciones existen junto a tu memoria
y nunca cambian por sí solas lo que hace cualquier otra superficie.

La frontera de autoridad es todo el diseño: **las máquinas pueden proponer, y
ninguna puede confirmar.** Una decisión requiere una terminal interactiva, y no
existe flag que lo delegue a un agente. Los candidatos nunca se renderizan en
la superficie de una entrada — la lista de decisión es el único lugar
donde son visibles. Una cadena que un lector ve en el panel History del visor
es una que una persona avaló.

## Verbos

| comando | qué hace |
| --- | --- |
| `daimon relations list` | Cada relación renderizable, candidatos primero; los textos de los endpoints se resuelven al leer. `--state` filtra; `--json` para filas. |
| `daimon relations show <rel-id>` | Una relación con su historial completo de propuestas. |
| `daimon relations confirm <rel-id>` | Registra una confirmación humana de una edge candidata. Solo humanos: necesita una terminal interactiva. |
| `daimon relations reject <rel-id>` | Registra un rechazo humano — pegajoso contra re-propuestas. Solo humanos. |
| `daimon relations retract <rel-id>` | Deshace una confirmación; una propuesta nueva puede revivir la edge. Solo humanos. |

## Contrato de borrado

Una edge es en sí misma una afirmación sobre contenido, así que las relaciones
honran `daimon forget`: una edge que toca un item olvidado se retiene fuera de
las superficies renderizadas, y solo se muestra un conteo de edges retenidas —
la redacción en la CLI y en el visor es la misma, y no nombra ningún id. Un
endpoint que simplemente envejeció fuera de la ventana de retención de
checkpoints es distinto: se renderiza como `[unresolved]` y sigue siendo un
registro válido.

## Consejos para decidir

Leé la cadena completa antes de confirmar sus edges. Dos items pueden parecer
relacionados de a pares porque comparten vocabulario del proyecto siendo
pensamientos distintos — las decisiones honestas son "es el mismo pensamiento
evolucionando" (confirm), "esta edge está mal" (reject), o dejar el candidato
en paz cuando no estás seguro. Un candidato sin confirmar es inerte; una
confirmación equivocada no.
