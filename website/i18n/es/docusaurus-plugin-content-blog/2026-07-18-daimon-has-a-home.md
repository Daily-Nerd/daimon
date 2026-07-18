---
slug: daimon-has-a-home
title: "daimon tiene casa: docs bilingües, y qué salió esta semana"
authors: [daimon]
tags: [announcement, release]
---

daimon ahora tiene un sitio de documentación — el que estás leyendo — en
inglés y español, con un inicio rápido que te lleva de la instalación a tu
primer briefing, y páginas de conceptos para las ideas que hacen distinto a
daimon: clases de confianza, arrastre, receipts y el ciclo de vida de los
ítems. Este blog es el nuevo hogar canónico de releases, explicaciones de
features e incidentes de campo; cada anuncio que veas de nosotros en otro
lado va a enlazar de vuelta aquí.

{/* truncate */}

## Qué salió esta semana

**daimon 0.18.0 está en PyPI** (`uv tool install 'daimon-briefing[pretty]'`).
El experimento principal: **scene traces** por ítem, opt-in, indexadas para
recall. Sale detrás de un flag mientras la sometemos a un A/B contra nuestro
propio benchmark — si los números no la justifican, no se activa por
defecto. Ese es el trato que hacemos con cada feature.

**`daimon forget` está mergeado** y sale en el próximo release: eliminación
de ítems con un evento tombstone. El ítem sale del checkpoint vivo, del
índice de recall y del *contenido* del rastro de auditoría — pero el flujo
de eventos conserva un tombstone con el hash del contenido, y con receipts
activos el checkpoint posterior a la eliminación se re-firma. Borrado que
puedes demostrar que ocurrió, sin conservar lo borrado. La página del
[ciclo de vida de los ítems](/docs/concepts/lifecycle) cubre la mecánica.

**La documentación se volvió bilingüe.** Cada página — inicio rápido,
conceptos, hosts, configuración, memoria de equipo — está disponible en
español. No es un volcado de máquina: está escrita para desarrolladores que
leen en español, porque la comunidad hispanohablante de agentes merece
documentación de primera, no una ocurrencia tardía.

**Windsurf está validado en uso real.** El ciclo de captura (serialización
desde transcript nativo) ya fue probado de punta a punta en uso real de
Windsurf, sumándose a Claude Code. Codex sigue; Gemini espera un arreglo
upstream.

## Por qué existe este proyecto, en un párrafo

Tu agente olvida todo entre sesiones, y la mayoría de los sistemas de
memoria lo "arreglan" almacenando texto que un modelo escribió sobre lo que
pasó — sin manera de saber qué partes son citas y qué partes son conjeturas.
daimon marca cada ítem recordado como **verbatim** (una cita exacta,
verificada mecánicamente contra el transcript por un verificador
determinista — ningún LLM calificando su propia tarea) o **inferido** (puede
evolucionar, se señala para verificación). Una encuesta reciente de la
investigación en memoria de agentes llama a la procedencia a nivel de
afirmación un problema abierto; nosotros creemos que la respuesta es hacer
la memoria *demostrable*, y ese es el eje sobre el que está construido todo
esto.

Pronto más — releases, historias de guerra del campo, y análisis a fondo de
cómo funciona la maquinaria de verificación. Suscríbete por
[RSS](https://daily-nerd.github.io/daimon/blog/rss.xml) o sigue el repo en
[GitHub](https://github.com/Daily-Nerd/daimon).
