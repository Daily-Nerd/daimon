---
sidebar_position: 1
---

# Referencia CLI

Cada verbo de daimon, agrupado por lo que querés hacer. El `--help` de cada
comando trae la superficie completa de flags; esta página es el mapa.

## El ciclo diario

| comando | qué hace |
| --- | --- |
| `daimon brief` | Renderiza el briefing del último checkpoint — dónde quedaste, con etiquetas de confianza. `--team` suma lo último del equipo; `--slug <s>` lee el bucket de otro proyecto explícitamente. |
| `daimon recall "consulta"` | Búsqueda full-text sobre el historial local + de equipo. `--json` para filas, `--all-projects` para ampliar. |
| `daimon handoff "Hacé X primero. Ojo con Y."` | Deja un batón autoral para la próxima sesión — se renderiza arriba de todas las secciones del briefing y nunca compite con ítems rankeados. `--clear` lo retira; uno nuevo reemplaza al anterior. |
| `daimon loops` | Lista los loops abiertos direccionables con sus ids — la contraparte de lectura del camino de escritura de `resolve`. |
| `daimon status` | Presencia y edad del checkpoint, resultado del último serialize, avisos de salud. `--suppressed` lista los ítems resueltos retenidos. |

## Cerrar y corregir

| comando | qué hace |
| --- | --- |
| `daimon resolve <id o texto>` | Marca un ítem como resuelto — evento append-only; el ítem deja de arrastrarse. `--dry-run` previsualiza el match; `--by agent --evidence "<cita>"` reclama un cierre que se verifica byte a byte al final de la sesión. |
| `daimon reverify <id>` | Afirma que un ítem arrastrado sigue siendo cierto — exige evidencia y reinicia su reloj de vencimiento. También es la mitad de rechazo de un candidato a supersesión. |
| `daimon forget <id o texto>` | Elimina el contenido de un ítem del disco y del índice, dejando una lápida de solo-hash. La eliminación sobrevive a re-serializar la transcripción original. |
| `daimon log --text "…"` | Agrega un evento libre a la línea de tiempo del proyecto — cero LLM, solo rastro de auditoría. |

## Confianza y auditoría

| comando | qué hace |
| --- | --- |
| `daimon verify-receipt` | Verifica el recibo firmado de procedencia de un checkpoint (chequeo criptográfico completo vía el CLI de vitni). |
| `daimon audit quotes` | Re-verifica cada cita verbatim almacenada contra su transcripción de origen y reporta discrepancias. Solo lectura — nunca reescribe etiquetas. |
| `daimon audit privacy` | Prueba el contrato de borrado: hashea cada campo con texto plano en cada superficie (checkpoints, punteros rotados, el registro de eventos, el espejo de equipo, el índice de recall y sus snapshots huérfanos) y reporta todo valor olvidado que haya sobrevivido. Solo lectura. |
| `daimon anchor <archivo> <símbolo>` | Ancla un ítem cognitivo a un símbolo de código; los briefings avisan cuando el código anclado cambió. |

Los auditores comparten un mismo contrato de salida, para que un script pueda
actuar sobre la respuesta:

| salida | significado |
| --- | --- |
| `0` | limpio comprobado — se escaneó cada superficie y no se encontró nada |
| `1` | hay residuo; el reporte nombra la superficie y el hash (nunca el texto) |
| `3` | no se puede probar — alguna superficie no se pudo leer, o no había nada en alcance para escanear. Nunca lo trates como limpio |

`--project <dir>` acota a un proyecto, `--all` audita cada proyecto local
(cada uno contra sus propias lápidas); los dos son mutuamente excluyentes.

## Setup y operación

| comando | qué hace |
| --- | --- |
| `daimon configure` | Detecta el backend LLM resuelto y completa los huecos en `~/.daimon/env`. `--test` corre un round-trip real. |
| `daimon hooks install <host>` | Instala los hook scripts del host (Windsurf, Codex) desde el paquete. `list` / `status` inspeccionan. |
| `daimon skill install <host>` | Instala la skill de agente de daimon en el directorio de skills del host. Volvé a correrlo después de cada upgrade. |
| `daimon team init\|sync\|status` | Memoria de equipo compartida vía repo sidecar — ruteo cerrado por defecto, redacción por forma antes de sincronizar nada. |
| `daimon stats` | Agregados locales de uso y captura — nada se transmite; compartir la salida es un pegado deliberado. `--json` para máquinas. |
| `daimon heal` | Re-serializa la última sesión fallida cuando es seguro hacerlo. |
| `daimon projects` | Lista cada proyecto con checkpoint, con un adelanto del tema. |

## Internos (los invocan los hooks; documentados por completitud)

| comando | qué hace |
| --- | --- |
| `daimon serialize <transcripción>` | Convierte un archivo de transcripción en checkpoint — lo llaman los hooks de fin de sesión; a mano, rellena uno. |
| `daimon write-checkpoint` | Almacena un checkpoint recibido como JSON por stdin — el camino de introspección en sesión. La confianza la fija el código: nada en este camino puede reclamar `verbatim`, porque no hay transcripción contra la cual verificar. |
| `daimon recall-inject` | El backend de sugerencias por prompt detrás del hook de recall: prompt por stdin, de cero a dos líneas de trabajo previo, exit 0 siempre. |
| `daimon mcp serve` | Sirve las herramientas de daimon por MCP (stdio). |

## Anotaciones del briefing, decodificadas

El briefing marca cada línea; la historia completa de confianza vive en
[clases de confianza](../concepts/trust-classes.md). Clave rápida:

- `[✓ verbatim]` / `[~ inferred]` / `[? untagged]` — cómo se capturó el ítem.
- `[carried]` — heredado de una sesión anterior, no contexto fresco.
- `[≈ corroborated ×N]` — N sesiones independientes atestiguaron la afirmación.
- `[✓ world-checked]` — una sonda en vivo coincidió con esta afirmación durante este brief.
- `HANDOFF (…)` — un batón autoral de la sesión anterior; va arriba de todo.
- `— because …` — el razonamiento declarado de la decisión, capturado solo cuando la transcripción lo declara.
