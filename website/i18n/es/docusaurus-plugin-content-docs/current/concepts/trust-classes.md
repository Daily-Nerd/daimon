---
sidebar_position: 1
---

# Clases de confianza

Cada ítem de un briefing lleva una clase de confianza — un marcador visible de
*cómo llegó a existir ese ítem*. Esta es la idea central de daimon: memoria
que te dice qué partes son citas y qué partes son conjeturas.

## Las tres clases

### `[✓ verbatim]`

Una cita exacta del transcript de una sesión pasada, fijada al
capturarse. Los ítems verbatim nunca se reformulan — ni al
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

Lo que el chequeo garantiza, con precisión: la cita se **encuentra en el
transcript después de plegar ambos lados de la misma manera** — se normalizan
mayúsculas, corridas de espacios, énfasis y marcadores de lista de markdown, y
variantes de comillas curvas y guiones; una cita elidida con `…` se parte en
fragmentos que deben aparecer en orden, y los fragmentos muy cortos se
descartan por demasiado genéricos para fijar. Es un chequeo mecánico de
presencia, no una garantía byte por byte ni una afirmación de verdad sobre el
mundo. La inmutabilidad byte a byte aplica al *almacenamiento*: una vez
fijada, la cita guardada nunca se reformula por arrastre, renderizado ni
truncamiento.

La garantía se extiende más allá del momento de escritura: con
[receipts](./receipts.md) habilitados, los bytes exactos del checkpoint se
firman al escribirse — si alguien edita el archivo después, la verificación
al momento del briefing lo nota, y las etiquetas `✓ verbatim` afectadas se
**degradan visiblemente** en lugar de confiarse en silencio.

## La insignia de corroboración — un segundo eje

Algunas líneas del briefing llevan una anotación extra junto a la etiqueta de
confianza:

```
- [~ inferred] The staging config drift needs an owner [carried] [≈ corroborated ×2]
```

Esa insignia cuenta **avistamientos independientes** de la afirmación: cuántas
sesiones distintas la han observado, incluyendo la que la escribió primero. Es
una pregunta distinta de la clase de confianza, que responde *qué tipo de
evidencia respalda esto*. Los dos ejes nunca se mezclan — la línea de arriba
es un ítem corroborado que sigue siendo `~ inferred`, y seguirá siendo
inferido por más sesiones que coincidan.

**La insignia no es un ascenso.** Una clase de confianza cambia por exactamente
dos vías: un `daimon reverify` con evidencia, o una acción humana explícita.
La coincidencia no es evidencia sobre el *tipo* de una afirmación, así que
ninguna cantidad de ella mueve la etiqueta.

Qué gana la insignia:

- Una sesión posterior **reafirma de forma independiente** la afirmación, con
  sus propias palabras o con las mismas, y esa reafirmación es a su vez una
  cita verbatim verificada contra el transcript de *esa* sesión.
- El primer autor de la afirmación es **demostrablemente otro** — cada ítem
  registra la sesión que lo escribió originalmente, y una sesión no puede
  corroborarse a sí misma.
- El total llega a **dos** — el origen registrado más al menos un testigo
  independiente.

Qué nunca la gana:

- **Los ecos del propio daimon.** Una inyección de recall o un bloque de
  briefing impresos en el transcript son salida de daimon, no un testigo. La
  verificación de citas descarta los fragmentos que daimon inyectó antes de
  comprobar nada, así que una reafirmación copiada de un briefing se degrada a
  `~ inferred` y no puede corroborar. Queda excluida por construcción, no por
  un chequeo que se pueda saltar.
- **Sobrevivir al [arrastre](./carry.md).** Un ítem que avanza de sesión en
  sesión es una afirmación copiada N veces, no N avistamientos.
- **Los compañeros de equipo.** El checkpoint sincronizado de un compañero es
  inverificable en tu máquina — sus afirmaciones verbatim llegan degradadas a
  `inferred`, y su sesión de origen no existe en tu directorio de checkpoints.
  La corroboración de equipo no está en esta versión.

La degradación le gana a la insignia. Cualquier cosa que contradiga un ítem —
una [resolución](./lifecycle.md), un marcador `superseded-by`, una supersesión
marcada como probable, una nota de estado cambiado desde la captura, una
tumba — pone el conteo en cero desde ese momento, y la contradicción se
muestra sola. "Tres sesiones coincidieron" impreso junto a "esto probablemente
está mal" se lee como respaldo de la afirmación, que es justo la inversión de
la señal. Reabrir un ítem **no** restaura lo que perdió; la corroboración se
vuelve a ganar con un testigo, no con un cambio de estado.

El conteo nunca se guarda en el ítem. Se deriva al momento de leer, desde el
registro de eventos append-only, donde cada corroboración es una fila que
nombra la sesión que coincidió y el ítem sobre el que coincidió — así los
testigos son auditables, y ninguna edición de un archivo de checkpoint puede
inventar un número que el registro no respalde.

La corroboración tampoco entra en el ranking. No sube el puntaje de un ítem,
ni en el briefing ni en recall. Una insignia que levantara un ítem lo haría
aparecer más seguido, lo que lo inyectaría en más transcripts, lo que
produciría más reafirmaciones — un ciclo que mide cuántas veces daimon se
mostró un ítem a sí mismo, y nada sobre el mundo.

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

## Suelo firme, no solo arena movediza

El marcador inverso también existe. Al momento del brief, daimon verifica por
muestreo los ítems arrastrados con afirmaciones comprobables contra la
realidad — estado de PRs, existencia de ramas y archivos, validez de recibos.
Una contradicción reemplaza el render del ítem con lo que cambió. Una
*confirmación* antes no renderizaba nada, lo que hacía indistinguible una
afirmación recién verificada de una nunca revisada; ahora gana un sufijo
discreto:

```text
- [~ inferred] PR #60 awaiting review [carried] [✓ world-checked]
```

`[✓ world-checked]` significa que el mundo mismo coincidió con esta
afirmación durante este brief — un eje separado de la clase de confianza
(cómo se capturó) y de la corroboración (cuántas sesiones la atestiguaron).
Sobre estas podés apoyarte sin re-verificar. Una asimetría es deliberada: una
contradicción en cualquier eje suprime la insignia — la arena movediza
siempre le gana al suelo firme.
