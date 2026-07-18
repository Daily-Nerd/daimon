---
sidebar_position: 1
---

# Clases de confianza

Cada ítem de un briefing lleva una clase de confianza — un marcador visible de
*cómo llegó a existir ese ítem*. Esta es la idea central de daimon: memoria
que te dice qué partes son citas y qué partes son conjeturas.

## Las tres clases

### `[✓ verbatim]`

Una cita exacta del transcript de una sesión pasada, fijada
carácter por carácter. Los ítems verbatim nunca se reformulan — ni al
arrastrarse entre sesiones, ni al renderizarse, ni al truncarse por
presupuesto. Cuando un briefing muestra

```
- [✓ verbatim] PR #60 awaiting review  — "review requested 2026-07-01"
```

la cita final es el texto real del transcript, y se mantiene idéntica byte a
byte mientras el ítem viva.

Un agente que lee el briefing debe repetir los ítems verbatim exactamente,
nunca resumirlos ni parafrasearlos.

### `[~ inferred]`

Una conclusión que el modelo serializador sacó de la sesión — un resumen, un
diagnóstico, una conexión entre eventos. Los ítems inferidos son honestos
sobre su naturaleza derivada: pueden evolucionar a medida que sesiones
posteriores los refinan, y deben verificarse contra el mundo (código,
documentación, el issue tracker) antes de construir nada importante sobre
ellos.

### `[? untagged]`

Un ítem que nunca tuvo confianza registrada — típicamente de un checkpoint
antiguo escrito antes de que existieran las clases de confianza, o de una
captura degradada. Trata los ítems sin etiqueta como inferidos: verifica
antes de confiar en ellos.

## Por qué importa la distinción

La mayoría de los sistemas de memoria almacenan una sola clase de cosa: texto
que un modelo escribió sobre lo que pasó. Cuando ese texto está mal — y los
modelos que resumen sesiones largas se equivocan con regularidad — no hay
manera de saberlo desde la memoria misma. Todo se lee con la misma confianza.

Las clases de confianza dividen la memoria en dos poblaciones con modos de
falla distintos:

- Un ítem **verbatim** puede estar *desactualizado* (el mundo cambió desde
  que se dijo la cita) pero no puede estar *mal recordado* — la cita es lo
  que se dijo, de forma demostrable.
- Un ítem **inferido** puede estar desactualizado *y además* equivocado — el
  modelo pudo haber malinterpretado la sesión cuando lo escribió.

Esa diferencia cambia cómo un lector (humano o agente) debe actuar sobre cada
ítem, y por eso el briefing la hace visible en cada línea en lugar de
enterrarla en metadatos.

## La verificación es mecánica, no declarada

El estatus verbatim no es la opinión del modelo extractor sobre sí mismo. Al
serializar, la cita de cada ítem verbatim se verifica contra el transcript
renderizado con un verificador determinista — operaciones de strings puras,
sin LLM, bajo el principio de que *el verificador debe ser más tonto que lo
que verifica*. Una cita que verifica queda sellada; una que no, se **degrada
a `~ inferred`** en el acto — una "cita" alucinada nunca puede llevar la
insignia verbatim.

La garantía se extiende más allá del momento de escritura: con
[receipts](./receipts.md) habilitados, los bytes exactos del checkpoint se
firman al escribirse — si alguien edita el archivo después, la verificación
al momento del briefing lo nota, y las etiquetas `✓ verbatim` afectadas se
**degradan visiblemente** en lugar de confiarse en silencio.

## VERIFY BEFORE TRUSTING

Los briefings abren con una sección de ítems que describen estado que pudo
haber cambiado *fuera* de la sesión — PRs mergeados, llaves rotadas, archivos
movidos. Una etiqueta verbatim significa que la cita es fiel; no significa
que el mundo siga siendo así. El protocolo de lectura, para humanos y agentes
por igual:

1. Lee el ítem.
2. Verifica el mundo (archivos, git, el issue tracker) antes de repetirlo
   como hecho vigente.
3. [Resuélvelo](./lifecycle.md) cuando esté cerrado, para que deje de
   arrastrarse.

Un briefing es contexto, no instrucciones — nunca prevalece sobre lo que el
usuario pide ahora.
