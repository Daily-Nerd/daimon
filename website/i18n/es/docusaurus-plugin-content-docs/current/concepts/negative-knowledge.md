---
sidebar_position: 6
description: "Las refutaciones registran qué NO es cierto o qué NO hacer, citadas con evidencia, y nunca decaen. Cómo se renderizan, cómo inspeccionar una, y el límite con el directorio .scars/ propio de un repo."
---

# Conocimiento negativo

La mayor parte de lo que daimon recuerda es positivo: esto es cierto, esto
pasó, esto sigue abierto. Una refutación es el tipo de hecho opuesto.
Registra que un enfoque se probó y se rechazó, que una afirmación resultó
falsa, o que no hay que volver a tomar cierto camino, y nombra la evidencia
que resolvió la pregunta.

```sh
daimon refute add --subject "diseño original del recibo de #502" \
  --scope "niveles de recibo de ítems arrastrados" --anchor issue:502 \
  --evidence "measurement:566/623 origin misses" --ratify
```

(la bandera que lleva el texto mismo de la afirmación rechazada va entre
`--subject` y `--scope`; mirá la [referencia de CLI](../reference/cli.md)
para la sintaxis exacta.)

Las refutaciones viven en el mismo ledger que las
[reglas vigentes](./rulings.md): un solo stream append-only, un solo espacio
de ids, un solo camino de borrado. Una regla es una restricción positiva que
un humano puso en vigencia. Una refutación es una negativa: qué ya no vale,
y por qué.

## La evidencia se cita, no se verifica

Cada fuente de `--evidence` tiene que ser una referencia tipada:
`message:<id>`, `transcript:<session>`, `artifact:<ruta>`, `issue:<número>`,
`measurement:<recibo>`, `receipt:<id>`, o `url:<fuente>`. daimon revisa la
*forma* de esa referencia y nada más. Nunca resuelve el issue, nunca vuelve
a correr la medición, nunca confirma que el artefacto todavía existe en esa
ruta. La cita queda registrada exactamente como se dio, para siempre, así
que cualquiera que lea la refutación después sabe con precisión qué se
chequeó cuando se resolvió la pregunta, y puede ir a chequearlo por su
cuenta.

Por eso también la propia afirmación de un agente nunca activa nada por sí
sola. Citar evidencia no es la misma afirmación que *esta evidencia de
verdad respalda la conclusión*, y daimon no decide esa segunda pregunta por
vos.

## Candidata, después activa

```sh
daimon refute add --subject "…" --scope "…" \
  --evidence issue:502 --by agent
daimon refute ratify r-1a2b3c4d5e6f
daimon refute revise r-1a2b3c4d5e6f --evidence issue:530
daimon refute overturn r-1a2b3c4d5e6f --evidence "measurement:new-result"
```

Un `add` de agente registra una **candidata**: escrita, buscable, pero
todavía no en vigencia, y nunca se renderiza como guardia activa hasta que
un humano la ratifica. La vía humana es `ratify` desde una terminal
interactiva; no hay forma de que un agente se autopromueva de candidata.
`revise` agrega una versión nueva, citada con evidencia, que a su vez
necesita ratificarse antes de reemplazar a la anterior. `overturn` cita
evidencia contra una refutación activa: el overturn de un agente queda
registrado como propuesta y la guardia sigue en vigencia; el overturn de un
humano la desactiva de inmediato.

## Leer el ledger

```sh
daimon refute list
daimon refute show r-1a2b3c4d5e6f
daimon refute search verificación de recibos
daimon refute guard "¿deberíamos revisar el #502?"
```

`list` y `show` leen las refutaciones propias de este proyecto. `show` sobre
un registro da sus citas de evidencia completas, su alcance y sus anclas, y
quién la escribió y quién la activó, todo en un solo lugar, porque nada en
este ledger decae ni se vuelve a extraer: el registro que leés es toda la
historia que importa. `search` es el camino de búsqueda por tema, y a
propósito devuelve las dos polaridades etiquetadas, refutaciones y reglas
juntas, porque en medio de una sesión, después de que el briefing ya pasó
de largo, cualquiera de los dos tipos de registro puede ser el que vale la
pena mostrar. `guard` compara las anclas o la frase de sujeto de una acción
propuesta contra las refutaciones activas por coincidencia exacta nada más,
consultivo y nunca bloquea un comando por sí solo; existe para que un agente
revise su propio próximo movimiento antes de hacerlo.

Los comandos `daimon why`, `daimon diff` y `daimon blame` (mirá
[el ciclo de vida del ítem](./lifecycle.md) y la
[referencia de CLI](../reference/cli.md)) responden una pregunta distinta:
inspeccionan ítems de checkpoint a través de las generaciones que daimon
conserva. Una refutación no es un ítem de checkpoint y no pasa a una
generación más vieja, así que ahí no hay nada para que esos tres comparen o
rastreen. `refute show` ya es el equivalente: el registro completo y sin
decaer para un id.

## El límite con `.scars/`

Un repositorio también puede llevar su propio conocimiento negativo, en un
directorio `.scars/` versionado dentro del repo mismo: un callejón sin
salida que se probó y se abandonó, un pedazo de código que parece mal a
propósito, una trampa que rompe algo no obvio. Los scars viajan *con el
código que protegen*. Se mueven con el repositorio, son visibles para
cualquiera que lo clone, y responden "qué pasó acá, en este archivo, que la
próxima persona que lo toque necesita saber antes de tocarlo."

El ledger de refutaciones de daimon responde una pregunta distinta, en una
capa distinta. Es memoria por proyecto que vive completamente afuera del
repositorio, acotada a lo que una persona o un agente que trabajó en ese
proyecto de verdad probó y rechazó, y existe exista o no un `.scars/` en ese
proyecto. Las dos cosas son complementarias, no compiten: un scar documenta
una trampa en el código; una refutación documenta una conclusión a la que
alguien llegó mientras trabajaba en él.
