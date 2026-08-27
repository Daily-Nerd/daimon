---
sidebar_position: 1
---

# Referencia CLI

Cada verbo de daimon, agrupado por lo que querés hacer. El `--help` de cada
comando trae la superficie completa de flags; esta página es el mapa.

## Preparar

| comando | qué hace |
| --- | --- |
| `daimon configure` | Detecta el backend LLM resuelto y completa los huecos en `~/.daimon/env`. `--test` corre un round-trip real. |
| `daimon hooks install <host>` | Instala los hook scripts del host (Windsurf, Codex) desde el paquete. `list` / `status` inspeccionan. |
| `daimon skill install <host>` | Instala la skill de agente de daimon en el directorio de skills del host. Volvé a correrlo después de cada upgrade. |
| `daimon heal` | Re-serializa la última sesión fallida cuando es seguro hacerlo. |
| `daimon mcp serve` | Sirve las herramientas de daimon por MCP (stdio). |

## Brief

| comando | qué hace |
| --- | --- |
| `daimon brief` | Renderiza el briefing del último checkpoint — dónde quedaste, con etiquetas de confianza. `--team` suma lo último del equipo; `--slug <s>` lee el bucket de otro proyecto explícitamente. |
| `daimon recall "consulta"` | Búsqueda full-text sobre el historial local + de equipo. `--json` para filas, `--all-projects` para ampliar. |
| `daimon handoff "Hacé X primero. Ojo con Y."` | Deja un batón autoral para la próxima sesión — se renderiza arriba de todas las secciones del briefing y nunca compite con ítems rankeados. `--clear` lo retira; uno nuevo reemplaza al anterior. |

## Comprobar

| comando | qué hace |
| --- | --- |
| `daimon why <item-id>` | El inspector de confianza: muestra cada eje de evidencia detrás de un ítem — captura independiente, procedencia, fuente, integridad de bytes, soporte actual, resultado del chequeo de citas, ciclo de vida, corroboración. `--source` agrega una ventana de fuente acotada y redactada; `--json` para máquinas. Los ids de ítem salen de `daimon recall` o `daimon loops`. |
| `daimon verify-receipt` | Verifica el recibo firmado de procedencia de un checkpoint (chequeo criptográfico completo vía el CLI de vitni). |
| `daimon reverify <id>` | Afirma que un ítem arrastrado sigue siendo cierto — exige evidencia y reinicia su reloj de vencimiento. También es la mitad de rechazo de un candidato a supersesión. |
| `daimon audit quotes` | Re-verifica cada cita verbatim almacenada contra su transcripción de origen y reporta discrepancias. Solo lectura — nunca reescribe etiquetas. |
| `daimon audit privacy` | Prueba el contrato de borrado: hashea cada campo con texto plano en cada superficie (checkpoints, punteros rotados, el registro de eventos, el espejo de equipo, el índice de recall y sus snapshots huérfanos) y reporta todo valor olvidado que haya sobrevivido. Solo lectura. |
| `daimon refute list\|show\|search\|guard` | Lee el ledger de conocimiento negativo sin decaimiento. `guard` emite solo matches activos por ancla exacta o frase de sujeto; es consultivo y nunca bloquea un comando. `search` devuelve ambas polaridades, etiquetadas; `list` y `guard` quedan solo para refutaciones. Sumá `--json` para integraciones de deliberación. |
| `daimon ruling list\|show` | Lee las reglas vigentes: restricciones positivas ratificadas por humanos en el mismo ledger, que nunca decaen ni se re-extraen. `show` incluye propuestas de agentes pendientes. |
| `daimon serve` | Abre el [visor local de solo lectura](viewer.md) en localhost — búsqueda como recall, páginas "why" por entrada, refutaciones, diff, check strip, vista de impresión. Nada escribe. |
| `daimon relations list\|show\|confirm\|reject\|retract` | El [ledger de relaciones tipadas](relations.md): las máquinas proponen, solo una persona confirma, y decidir necesita una terminal interactiva. Los candidatos nunca se renderizan en la superficie de una entrada. |

Los auditores comparten un mismo contrato de salida, para que un script pueda
actuar sobre la respuesta:

| salida | significado |
| --- | --- |
| `0` | limpio comprobado — se escaneó cada superficie y no se encontró nada |
| `1` | hay residuo; el reporte nombra la superficie y el hash (nunca el texto) |
| `3` | no se puede probar — alguna superficie no se pudo leer, o no había nada en alcance para escanear. Nunca lo trates como limpio |

`--project <dir>` acota a un proyecto, `--all` audita cada proyecto local
(cada uno contra sus propias lápidas); los dos son mutuamente excluyentes.

## Corregir

| comando | qué hace |
| --- | --- |
| `daimon resolve <id o texto>` | Marca un ítem como resuelto — evento append-only; el ítem deja de arrastrarse. `--dry-run` previsualiza el match; `--by agent --evidence "<cita>"` reclama un cierre que se verifica byte a byte al final de la sesión. |
| `daimon anchor <archivo> <símbolo>` | Ancla un ítem cognitivo a un símbolo de código; los briefings avisan cuando el código anclado cambió. |
| `daimon refute add\|ratify\|revise\|overturn` | Gestiona conocimiento negativo con alcance en su propio ledger append-only. Las escrituras de agentes quedan como candidatas; solo una ratificación humana explícita activa un guard, y `ratify` exige la vía humana: una terminal interactiva y `--by` omitido. Las revisiones exigen una cita de evidencia tipada nueva, cuya forma se valida pero nunca se resuelve ni se verifica, y devuelven una refutación activa a candidata hasta que se vuelva a ratificar. Los overturns de agentes siguen siendo propuestas. |
| `daimon ruling propose\|ratify\|revise\|retire` | Gestiona reglas vigentes en el mismo ledger, con un ciclo más estricto: `ratify` muestra el texto completo, avisa que va a renderizarse en cada sesión futura y ata la activación al texto mostrado; un humano que revisa una regla activa confirma el cambio y la regla sigue activa; los revise y retire de agentes registran propuestas mientras el texto queda en pie; la activación se rechaza pasado el tope (`DAIMON_RULING_CAP`, por defecto 7). Retirar no exige cita de evidencia. |

## Olvidar

| comando | qué hace |
| --- | --- |
| `daimon forget <id o texto>` | Elimina el contenido de un ítem del disco y del índice, dejando una lápida de solo-hash. La eliminación sobrevive a re-serializar la transcripción original. |

## Coordinar

Una solicitud vive en el bucket del proyecto que la envía; el destinatario
responde con filas de decisión en su propio bucket. El registro combinado
es un join en tiempo de lectura — nadie escribe jamás en el ledger de otro
proyecto.

| comando | qué hace |
| --- | --- |
| `daimon request open --to <dir> --ask "…" --why "…"` | Pide algo a otro proyecto. `--to` toma el **directorio** del proyecto destinatario, no su slug (un slug real empieza con `-`, que argparse lee como una opción — `--to=<slug>` también funciona). Se valida contra `daimon projects`, con sugerencias por parecido ante un typo; `--anyway` registra el pedido igual contra un proyecto que nunca serializó en esta máquina. `--blocking` y `--to-human` son flags del registro. Cualquier canal. |
| `daimon request revise <id> [--ask] [--why] [--evidence]` | Responde un needs-info, o afina una solicitud abierta. Cualquier canal; tope de 3 revisiones por registro — superado el tope, se abre una nueva solicitud con `--supersedes <id>` para mantener visible el linaje. |
| `daimon request accept\|reject\|needs-info <id> [--note]` | Registra una decisión. Solo humano — requiere una terminal interactiva. `reject` es definitivo para ese registro; el remitente reemplaza con una nueva solicitud en vez de volver a pedir. |
| `daimon request suppress <id> [--note]` | Saca una solicitud del panel de briefing propio del destinatario. Solo humano; el registro sigue en `list`/`inbox`, y cualquier decisión posterior lo revierte. |
| `daimon request done <id> --evidence "<cita>"` | Reporta la solicitud como satisfecha. Cualquier canal; el reclamo de un agente se renderiza como `done (claimed, unverified)` hasta que el próximo fin de sesión del destinatario verifica byte a byte la cita de evidencia contra su transcripción. Un `done` humano se renderiza sin más. |
| `daimon request list` | Las solicitudes enviadas por este proyecto, primero las que siguen sin decidir. `--json` para máquinas. |
| `daimon request inbox` | Solicitudes que otros proyectos dirigieron a este, de cualquier remitente, primero las que siguen sin decidir — incluidas las que el panel de briefing dejó fuera de la atención. `--json` para máquinas. |

Dos paneles viajan solo con el `brief` de CLI del mismo proyecto — nunca con
`--slug`, el fallback al puntero global, ni por MCP. El destinatario ve
"Requests waiting on you"; el remitente ve "Decisions on requests you sent".
Cada uno tiene un tope de 3 tarjetas con una línea de desborde bien visible
(`+N more …`) que nombra el comando para ver el resto — nunca un descarte
silencioso. La supresión es solo atención del lado del destinatario: el
panel del remitente sigue mostrando una solicitud suprimida como publicada
y sin decidir. Una solicitud sin responder pasa a `stale` después de 3
sesiones del destinatario sin decisión; una ya decidida sale del panel del
remitente después de 2 sesiones del remitente. La atención decae — los
registros nunca se eliminan, y ambos siguen totalmente visibles en
`list`/`inbox`.

`daimon status` agrega un resumen de una línea, `requests: N open sent, M
awaiting you`, silencioso cuando los dos son cero.

El [servidor MCP](mcp.md) expone la vista del lado del destinatario como la
herramienta de solo lectura `requests_inbox`. `daimon_brief` nunca lleva
contenido de solicitudes, y ningún verbo de escritura de solicitudes es
alcanzable por MCP.

## Estado

| comando | qué hace |
| --- | --- |
| `daimon status` | Presencia y edad del checkpoint, resultado del último serialize, avisos de salud. `--suppressed` lista los ítems resueltos retenidos. |
| `daimon stats` | Agregados locales de uso y captura — nada se transmite; compartir la salida es un pegado deliberado. `--json` para máquinas. |
| `daimon log --text "…"` | Agrega un evento libre a la línea de tiempo del proyecto — cero LLM, solo rastro de auditoría. |
| `daimon loops` | Lista los loops abiertos direccionables con sus ids — la contraparte de lectura del camino de escritura de `resolve`. |
| `daimon projects` | Lista cada proyecto con checkpoint, con un adelanto del tema. |
| `daimon team init\|sync\|status` | Memoria de equipo compartida vía repo sidecar — ruteo cerrado por defecto, redacción por forma antes de sincronizar nada. |

## Internos (los invocan los hooks; documentados por completitud)

| comando | qué hace |
| --- | --- |
| `daimon serialize <transcripción>` | Convierte un archivo de transcripción en checkpoint — lo llaman los hooks de fin de sesión; a mano, rellena uno. |
| `daimon write-checkpoint` | Almacena un checkpoint recibido como JSON por stdin — el camino de introspección en sesión. La confianza la fija el código: nada en este camino puede reclamar `verbatim`, porque no hay transcripción contra la cual verificar. |
| `daimon recall-inject` | El backend de sugerencias por prompt detrás del hook de recall: prompt por stdin, de cero a dos líneas de trabajo previo, exit 0 siempre. |

## Anotaciones del briefing, decodificadas

El briefing marca cada línea; la historia completa de confianza vive en
[clases de confianza](../concepts/trust-classes.md). Clave rápida:

- `[✓ verbatim]` / `[~ inferred]` / `[? untagged]` — cómo se capturó el ítem.
- `[carried]` — heredado de una sesión anterior, no contexto fresco.
- `[≈ corroborated ×N]` — N sesiones independientes atestiguaron la afirmación.
- `[✓ world-checked]` — una sonda en vivo coincidió con esta afirmación durante este brief.
- `HANDOFF (…)` — un batón autoral de la sesión anterior; va arriba de todo.
- `— because …` — el razonamiento declarado de la decisión, capturado solo cuando la transcripción lo declara.
