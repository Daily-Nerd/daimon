---
slug: negative-knowledge
title: "Los fracasos de tu agente son su memoria más valiosa, y casi nadie los guarda"
authors: [daimon]
tags: [concepts, scars, negative-knowledge]
---

Todos los que construyen memoria para agentes están construyendo lo mismo:
recordar qué funcionó, recordar qué se decidió, recordar quién prefiere tabs.
Útil. Pero la amnesia cara no es olvidar los éxitos. Es olvidar los fracasos.

Un agente que olvida un éxito lo vuelve a derivar en unos cientos de tokens.
Un agente que olvida un fracaso lo vuelve a intentar: el refactor que rompió
prod, la "limpieza obvia" que sostenía todo, el upgrade de dependencia que se
abandonó dos veces por la misma razón. Pagás el mismo callejón sin salida
cada vez que una sesión nueva entra caminando con toda confianza.

{/* truncate */}

## El campo recién se dio cuenta

Tres papers aterrizaron sobre esto en los últimos dos meses.

Un paper del workshop AI4Research de ICML 2026
([arXiv:2606.21024](https://arxiv.org/abs/2606.21024)) le pone nombre al
objeto: *conocimiento negativo*. Su diagnóstico de los sistemas de
investigación automatizada aplica palabra por palabra a los agentes de
código: los fracasos "aparecen como señales locales de debugging pero rara
vez se convierten en objetos de investigación durables". Su propuesta es una
capa de memoria "diseñada explícitamente para el fracaso", con registros
tipados y un curador separado del agente que falló, "para reducir el sesgo de
autoevaluación". El paper conceptual que el espacio necesitaba. También,
siendo honestos, deja abiertas las preguntas operativas: sin política de
obsolescencia, sin modelo de expiración, y sus propias tablas muestran un
registro de fracaso de un sistema confundiendo a otro (transferencia
negativa). El concepto quedó establecido. El ciclo de vida, no.

Un reporte de campo de un equipo en producción
([arXiv:2607.13091](https://arxiv.org/html/2607.13091v1)) convirtió
comentarios de review aceptados en reglas de comportamiento persistentes, con
una heurística de calificación que vale la pena robar textual: *"¿Este error
podría plausiblemente repetirse en otro contexto? Si sí, se convierte en
regla."* Su franqueza también vale la pena robarla: sin grupo de control,
muestra chica, y esta advertencia que todo sistema de memoria debería
enmarcar y colgar en la pared: "una cultura de review ruidosa puede envenenar
el conjunto de reglas más rápido de lo que el paso de validación puede
atrapar".

Y [PROJECTMEM](https://arxiv.org/abs/2606.12329) publicó lo más parecido a
nuestro diseño: un log de eventos append-only, git-nativo, en texto plano,
con una compuerta determinística pre-acción, "memoria que no se limita a
responderle al agente sino que actúa sobre su próxima acción". A la categoría
la llaman *Memory-as-Governance*. Es el nombre correcto. (Relacionado, del
lado de la escritura: [GovMem](https://arxiv.org/abs/2607.02579) gobierna la
promoción de memorias con una decisión promote / reject / needs-review, que
es estructuralmente la misma compuerta que describimos más abajo del lado
humano.)

Así que el terreno no está vacío, y no estamos reclamando un "primero". Lo
que podemos ofrecer es un diseño que lleva un mes corriendo en un repo real,
con las cicatrices para demostrarlo.

## Cómo lo hacemos: autoría, promoción, disparo

Nuestra versión son dos herramientas con un humano en el medio.

**Los agentes escriben durante el trabajo.** Cuando una sesión abandona un
enfoque, conserva una rareza a propósito, o pisa un acople no obvio, el
agente lo registra como scar candidato ahí mismo, bajo un contrato de
autoría: un callejón sin salida necesita evidencia del intento y del
abandono, una mina necesita los dos sitios acoplados nombrados, y la prosa de
transcript en primera persona se descarta porque un sentimiento no es una
afirmación sobre código. Cada scar activo en el repo de daimon se escribió
así, durante la sesión que se lo ganó.

**Sobre la cosecha automática, con honestidad.** También construimos un
cosechador sin LLM que mina los checkpoints de sesión buscando candidatos,
porque un repo recién arrancado no tiene sesiones desde las cuales escribir.
Su historial de campo hasta ahora es mayormente ruido: el primer conteo fue 4
candidatos, 0 promovibles, y el log de falsos disparos es más largo que la
lista de aceptados. En respuesta se agregó un filtro de calificación que
exige las mismas obligaciones estructurales al momento de escritura
automática, y su veredicto sigue pendiente. Te lo contamos porque los
recibos son el punto: medimos nuestra propia herramienta, no llegó a la vara,
la enrejamos, y la reja está a prueba. Un sistema de memoria que no puede
rechazar sus propias escrituras es el vector de envenenamiento del que
advierte el paper de reglas de comportamiento.

**Un humano promueve.** Los candidatos caen en `.scars/candidates/`, nunca en
el conjunto activo. La promoción es un acto humano deliberado. Es la misma
conclusión a la que llegó el paper de reglas de comportamiento desde la otra
dirección: la calidad de sus reglas "depende de la calidad del review
humano", y las reglas malas amplifican errores. Una memoria de fracasos
auto-promovida es un vector de envenenamiento con diagrama de flujo.

**Scar dispara.** [Scar](https://github.com/Daily-Nerd/Scar) es la mitad de
enforcement: los scars promovidos llevan anclas, y cuando un agente está por
editar código anclado, el scar se inyecta antes de que corra la herramienta
de edición. No en el commit, cuando el error ya está hecho y stageado. Antes
de la edición.

Esa última distinción no es solo nuestra; es el propio roadmap de PROJECTMEM.
Su sección de trabajo futuro describe "moverla a la frontera de tool-call del
agente (un hook pre-acción)" para que la compuerta avise "en el instante en
que un cambio empieza a parecerse a uno que ya falló—interviniendo antes de
la edición, no en el commit". Esa es la compuerta que Scar entrega hoy. Y
para ser igual de claros con la otra columna: PROJECTMEM tiene un paper
publicado y unos doscientos stars; Scar tiene tres. Esto es una nota de
diseño, no un reclamo de madurez.

## Las dos piezas que no vimos en ningún otro lado

**Una condición de falsación en cada scar.** Cada scar registra
`expires.condition`: el cambio específico que lo volvería obsoleto, más una
fecha de revisión que el linter hace cumplir. La condición en sí hoy es
disciplina de autoría, todavía no verificada por máquina; el punto es que
existe al momento de escribir. El conocimiento negativo se pudre distinto que
el positivo; un hecho que queda viejo está mal, pero una advertencia que
queda vieja es fricción que entrena a todos a ignorar advertencias. Ninguno
de los papers de arriba tiene modelo de expiración. Uno hace crecer su
conjunto de reglas monotónicamente y nunca borra nada. El paper del workshop
ni trata la obsolescencia. Dejar escrita la condición bajo la cual una
advertencia debe morir es, hasta donde sabemos, terreno todavía sin reclamar.

**El fence.** Los callejones sin salida y las minas son memoria de fracasos.
El tercer tipo de scar no lo es: un *fence* protege código que se ve mal a
propósito. El timeout de menos de un segundo que parece demasiado apretado
pero acota un presupuesto real. El bloque duplicado que dos sistemas no deben
compartir. La memoria de fracasos le dice al agente "no repitas este
intento". Un fence le dice "no limpies esto". Ningún esquema de registro de
fracasos que hayamos encontrado representa eso, y en la práctica dispara todo
el tiempo, porque limpiar rarezas intencionales es exactamente lo que un
agente capaz quiere hacer.

## Lo que no estamos reclamando

No hay tasa de prevención medida. Ni nuestra ni de nadie: PROJECTMEM nombra
"fracasos-prevenidos-por-commits" como el benchmark faltante de toda la
categoría, y el paper de reglas de comportamiento titula una sección "The
Missing Benchmark". Estamos de acuerdo, y no vamos a llenar ese hueco con una
sensación. Lo que tenemos son recibos de capturas individuales: un scar que
marcó un riesgo de denegación de servicio por regex que había pasado tests
unitarios y review, y un fence que disparó en medio de un build y cambió el
piso de timeout de un wizard esa misma tarde. Anécdotas, etiquetadas como
anécdotas.

Si querés que la mitad de fracasos de la memoria de tu agente exista:
[Scar](https://github.com/Daily-Nerd/Scar) entrega el contrato de autoría
como skill cargable y dispara los scars promovidos,
[daimon](https://github.com/Daily-Nerd/daimon) borradorea candidatos de
arranque en frío desde los checkpoints de sesión, y el formato es
[una página de YAML y prosa](https://github.com/Daily-Nerd/Scar/blob/main/SCAR-FORMAT.md)
que podrías implementar vos mismo en una tarde. Los callejones sin salida que
ya pagaste son el conocimiento más barato que vas a entregar jamás.
