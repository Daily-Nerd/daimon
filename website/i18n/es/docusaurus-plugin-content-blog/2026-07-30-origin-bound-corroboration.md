---
slug: origin-bound-corroboration
title: "Que dos memorias coincidan no es evidencia: corroboración ligada al origen"
authors: [daimon]
tags: [concepts, trust-classes, corroboration, release]
---

Hay una trampa hacia la que camina todo sistema de memoria para agentes,
tarde o temprano: un ítem que aparece una y otra vez empieza a *sentirse*
verdadero. Cinco sesiones "recuerdan" el mismo hecho, así que el hecho debe
ser sólido. Promuévelo. Confía más en él.

Queríamos esa funcionalidad. La re-observación independiente *debería*
fortalecer una memoria — así funciona la evidencia en todos lados. Pero antes
de construirla salimos a buscar las formas en que se rompe, y lo que
encontramos cambió el diseño, el release y una suposición de seguridad con la
que veníamos conviviendo. Todo eso salió hoy en v0.22.0.

{/* truncate */}

## El eco en nuestra propia casa

Daimon inyecta memoria previa en las sesiones nuevas: un briefing al inicio y
una línea `daimon recall:` cuando un prompt coincide con trabajo pasado. Ese
texto inyectado pasa a formar parte de la transcripción de la sesión nueva. El
serializador después lee esa transcripción para extraer lo que la sesión
aprendió.

¿Ves el bucle? Un ítem de la sesión A se inyecta en la transcripción de la
sesión B, y la extracción de B puede "observarlo" ahí — no porque el hecho se
haya re-derivado de trabajo real, sino porque daimon se citó a sí mismo. En
nuestro propio corpus, trece transcripciones cargan ítems previos inyectados.
Un contador ingenuo de corroboración ("otra sesión lo vio de nuevo") contaría
los ecos de daimon como testigos independientes, y los ítems más recordados
acumularían la mayor confianza. La frecuencia de recall se volvería verdad.

Se puso peor antes de mejorar. Mapeando las superficies de inyección
encontramos que nuestro verificador de citas comparaba las citas verbatim
contra la transcripción *sin filtrar*. Una cita copiada de una línea inyectada
por el propio daimon pasaba la verificación y se guardaba como `verbatim`,
`quote_verified: true` — un ítem de una sesión anterior lavado como si fuera
testimonio fresco. Ese agujero no esperó a la funcionalidad de corroboración:
salió como fix de seguridad independiente el mismo día en que se encontró, y
la verificación ahora rechaza cualquier cita cuyo único soporte esté dentro de
la salida del propio daimon. Los ecos rechazados tienen su propia razón en el
ledger (`echo-only`), así que la tasa de eco ahora se puede medir en vez de
ser invisible.

## La prueba de que la versión ingenua no se puede parchar

Esto no es solo un bug nuestro. Un paper reciente — [*Securing LLM-Agent
Long-Term Memory Against Poisoning: Non-Malleable, Origin-Bound Authority
with Machine-Checked Guarantees*](https://arxiv.org/abs/2606.24322) —
demuestra, con teoremas TLA+ verificados por máquina, que las defensas
basadas en el contenido de un ítem o en su historial de derivación no son
sólidas. Los atacantes lavan orígenes no confiables por tres canales: el
propio resumen del agente, los ecos de herramientas confiables y la
**corroboración fabricada** — plantar fuentes que coinciden para que la
coincidencia se lea como verificación.

Ese tercer canal es exactamente el bucle de auto-referencia de arriba, con
nombre de primitiva de ataque y un resultado de imposibilidad detrás. La
reparación que prescribe el paper: ligar el origen al momento de escritura es
*necesario*, y la autoridad ligada al origen con elevación por corroboración
resistente a Sybil es *suficiente*. En corto: fija de dónde vino una
afirmación en el momento en que se escribe, y cuenta la coincidencia solo
cuando la independencia se pueda probar desde esos orígenes — nunca asumirla
desde la coincidencia misma.

## Qué trae v0.22.0

Nuestra implementación de esa prescripción, en una CLI local-first:

- **Origen ligado al escribir.** Cada ítem queda estampado con la sesión y el
  autor que lo escribió por primera vez, en la frontera de admisión, sobre el
  mismo riel que nunca se reescribe donde vive su identidad. Si un modelo
  emite sus propios campos de origen, se le quitan — una memoria no puede
  emitirse un testigo a sí misma.
- **Independencia probada desde los orígenes.** Una re-observación cuenta
  solo cuando el escritor original es una sesión *distinta*, la observación
  nueva es verbatim local con cita verificada (lo que, después del fix del
  eco, excluye estructuralmente la salida del propio daimon), la coincidencia
  es lo bastante fuerte para certificar y no solo deduplicar, y las dos
  observaciones no comparten anclajes de mensajes de transcripción.
- **Un conteo auditable, nunca un puntaje guardado.** Las corroboraciones son
  eventos en el log append-only, uno por testigo independiente. El conteo se
  deriva al leer. La contradicción manda: un ítem superseded o contradicho
  por el world-check pierde su insignia, y reabrirlo no restaura conteos
  ganados antes de la contradicción.
- **Un eje aparte, no una promoción de confianza.** Los ítems vistos
  independientemente dos veces se muestran como `[≈ corroborated ×2]` al lado
  de su clase de confianza. La clase en sí nunca sube por recurrencia — eso
  sigue exigiendo evidencia de re-verificación o una decisión humana
  explícita.
- **Conectado a nada, a propósito.** La insignia no afecta ningún ranking ni
  el scoring de recall, y un test impone que el código de scoring ni siquiera
  pueda importar el lector de corroboraciones. El bucle auto-reforzante —
  la promoción sube la saliencia, la saliencia sube la inyección, la
  inyección fabrica la próxima corroboración — se cierra exactamente donde un
  contador alimenta el ranking, así que ese cable queda cortado hasta que los
  datos de campo digan otra cosa. Publicamos la medición antes de que nada
  actúe sobre la medición.

## Qué no hace

Sección de honestidad, como siempre. La corroboración solo se acumula sobre
ítems escritos desde v0.22.0 en adelante — los orígenes nunca se adivinan
retroactivamente, porque una adivinanza errada haría parecer independientes a
observaciones dependientes. Una sesión reanudada que repite la misma
conversación bajo otro id se rechaza donde el host preserva los ids de
mensaje, pero un host que emite ids frescos puede hacer que una conversación
parezca dos. Un atacante que controla el contenido de dos sesiones separadas
todavía puede fabricar dos orígenes — el diseño sube el costo del acuerdo
falso de una línea de recall a dos sesiones comprometidas; no lo vuelve
imposible. Y los checkpoints de compañeros de equipo no corroboran en esta
versión: una copia sincronizada de una afirmación sigue siendo un solo
testigo, y las compuertas que harían el conteo entre autores resistente a
Sybil están diseñadas pero deliberadamente apagadas por ahora.

El release completo también trae el endurecimiento del gateway de escritura
sobre el que se apoyó este trabajo: borrado por valor de punta a punta, un
guard de auditoría de escritura sobre cada comando, y contenido entrante de
equipo pasando por las mismas compuertas de scope, redacción, forget y
confianza que las escrituras locales.

Si la memoria de tu agente te dice algo dos veces, pregúntale quién se lo
dijo primero. [daimon](https://github.com/Daily-Nerd/daimon) ahora puede
responder.
