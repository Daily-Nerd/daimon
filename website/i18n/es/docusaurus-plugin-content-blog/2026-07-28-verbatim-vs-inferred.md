---
slug: verbatim-vs-inferred
title: "Verbatim vs. inferido: la clase de confianza que le falta a la memoria de tu agente"
authors: [daimon]
tags: [concepts, trust-classes]
---

Tu agente abre una sesión y te cuenta lo que decidiste la vez anterior. Una
parte es una cita textual. Otra parte es el resumen que el modelo hizo de esa
cita. Y otra parte es una conclusión que el modelo sacó a las 2am, en una
sesión de la que ya venía perdiendo el hilo.

Las tres llegan con la misma tipografía, el mismo tono seguro y ninguna forma
de distinguirlas. Ese es el problema real de la memoria de agentes, y no se
arregla recordando más cosas.

{/* truncate */}

## Dos poblaciones, dos formas de fallar

Daimon etiqueta cada ítem que arrastra con una clase de confianza, visible en
la línea misma:

```
- [✓ verbatim] PR #60 esperando review  — "review requested 2026-07-01"
- [~ inferred] La tormenta de reintentos vino de un deadline compartido entre tres llamadas
```

La etiqueta no es decoración. Las dos clases fallan de maneras distintas:

- Un ítem **verbatim** puede quedar *viejo* (el mundo siguió andando después de
  la cita) pero no puede estar *mal recordado*. La cita es lo que se dijo.
- Un ítem **inferido** puede estar viejo *y además* equivocado. El modelo pudo
  haber leído mal la sesión en el momento exacto en que escribió el resumen.

Esa diferencia debería cambiar qué hacés con la línea. Una cita vieja pide
verificar contra el mundo. Una inferencia equivocada va a la basura. Meter las
dos en la misma bolsa de "la memoria dice" destruye la distinción justo cuando
más la necesitás.

## El verificador tiene que ser más tonto que lo que verifica

Una clase de confianza no vale nada si el modelo se la asigna a sí mismo. Un
modelo que alucina una cita también va a etiquetar felizmente esa alucinación
como `verbatim`.

Así que el modelo no vota. Al momento de serializar, cada cita candidata se
compara contra el transcript renderizado con un verificador determinista:
operaciones de string puras, sin LLM, sin criterio. La cita que coincide recibe
el sello. La cita que no coincide **baja a `~ inferred`** en el acto, y se
conserva, no se borra. Sobrevive la afirmación; lo que no sobrevive es la
certificación.

Sobre este principio se apoya todo el diseño. El verificador tiene que ser más
tonto que lo que verifica, porque cualquier cosa lo bastante inteligente como
para que el extractor la engañe no es una verificación.

## El problema difícil: una cita perfecta puede ser perfectamente falsa

Acá está la parte que tuvimos mal durante meses, y la razón de este post.

La coincidencia verbatim certifica **transcripción, no verdad**.

El fallo que nos lo enseñó: un agente terminó una sesión larga y escribió
"serialización exitosa" en su propia memoria. No había sido exitosa. La sesión
siguiente leyó esa línea, creyó que la capa de memoria estaba sana y construyó
sobre unos cimientos que no existían.

Cada paso es fiel. El modelo lo dijo. El transcript lo registra exacto. El
verificador de citas lo hizo coincidir carácter por carácter y lo selló como
`✓ verbatim`, correctamente. La clase de confianza hizo su trabajo y la memoria
igual era falsa, porque lo que se estaba certificando era que la frase *se
dijo*, no que el evento *pasó*.

## Los resultados necesitan un testigo, no una cita

Desde la 0.20, las afirmaciones que declaran un resultado terminado tienen que
pasar por una segunda vara.

Si el texto de un ítem afirma que algo terminó (funcionó, se mergeó, se
deployó, los tests pasan, salió) tiene que citar una señal concreta de esa
misma sesión: un resultado de herramienta, un exit status. Algo que la sesión
haya producido de verdad, no algo que el modelo concluyó.

Una afirmación de resultado que cita una señal real sigue siendo `verbatim`.
Una afirmación de resultado sin cita, en una sesión que *sí* produjo señales,
baja a `~ inferred`. La cita y su sello de verificación quedan, porque la
transcripción sigue estando honestamente atestiguada. Lo que queda sin testigo
es el resultado.

Un resultado sin testigo es un reporte, no un hecho.

Dos límites deliberados en esa regla, los dos en la dirección de no hacer nada
antes que adivinar:

- **Los condicionales no son afirmaciones.** "se va a mergear" es un plan.
  "si el deploy funcionó" es una pregunta. No se tocan.
- **Las sesiones sin señales nunca bajan de clase.** Hay hosts que no exponen
  ningún resultado de herramienta parseable. Ahí verificar es imposible, y la
  ausencia de evidencia sobre el *host* no es evidencia contra la *afirmación*.

## Lo que nada de esto arregla

Las clases de confianza te dicen de dónde salió una afirmación. No te dicen
nada sobre si sigue siendo cierta.

Una cita `✓ verbatim` con un resultado de herramienta real detrás está
completamente atestiguada y queda vieja en el instante en que alguien mergea el
PR que describe. La procedencia no es actualidad, y fingir lo contrario sería
el mismo error una capa más arriba.

Por eso cada briefing abre con un bloque **VERIFY BEFORE TRUSTING** en lugar de
un resumen, y por eso la 0.20 suma un chequeo opcional que vuelve a leer el
estado externo al momento de armar el briefing y marca de forma visible las
afirmaciones que el mundo ya contradijo. Estamos midiendo cada cuánto salta eso
antes de decir nada sobre el tamaño del problema.

## Probalo

```bash
uv tool install 'daimon-briefing[pretty]'
```

La mecánica completa está en la página de [clases de
confianza](/docs/concepts/trust-classes), con [carry y
obsolescencia](/docs/concepts/carry) para la mitad de actualidad y
[receipts](/docs/concepts/receipts) para qué pasa si alguien edita un
checkpoint después de escrito. Código e issue tracker:
[Daily-Nerd/daimon](https://github.com/Daily-Nerd/daimon).
