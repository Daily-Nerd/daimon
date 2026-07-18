---
sidebar_position: 2
---

# Configuración

Daimon se configura por completo con variables de entorno. Cada variable se
resuelve en el mismo orden: **gana el entorno del proceso**, y todo lo que no
esté ahí cae al archivo de entorno en `~/.daimon/env`. La ubicación del
archivo se puede cambiar con `DAIMON_ENV_FILE`.

El archivo de entorno existe porque los hooks corren con el entorno que el
proceso host haya heredado — un agente lanzado desde la GUI no tiene perfil
de shell, así que los exports del shell no son un canal confiable. Su formato
es líneas `KEY=VALUE`; se toleran un `export ` inicial, comillas alrededor,
líneas en blanco y comentarios `#`. Mantenlo en `chmod 600` — puede contener
API keys.

`daimon configure` gestiona las perillas del backend LLM (mira
[Backend LLM](#backend-llm)) y las escribe en `~/.daimon/env`. Todo lo demás
se configura editando ese archivo o exportando la variable.

Las **variables booleanas** aceptan `1`, `true`, `yes` u `on` como verdaderos
(sin distinción de mayúsculas donde se indica). Unas pocas usan convenciones
distintas — interruptores de apagado que están activos salvo que valgan `0`,
o flags por presencia — y se señalan en la columna "Qué hace".

Las perillas internas de ajuste de la serialización (umbrales de chunking,
solapamiento, concurrencia, tamaño de grupo de merge) deliberadamente no se
documentan aquí — son defaults de carga calibrados contra comportamiento
medido, no configuración de usuario.

## Núcleo

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_DISABLE` | off | Interruptor de apagado. Cuando es verdadero, cada hook se vuelve un no-op — sin captura, sin briefing. |
| `DAIMON_ENV_FILE` | `~/.daimon/env` | Ruta del archivo de entorno que respalda a todas las demás variables. Se lee solo del entorno del proceso (nombra al archivo, así que no puede vivir dentro de él). |
| `DAIMON_PROJECT_DIR` | sin definir | Directorio de trabajo de la sesión que se briefea o serializa, usado para enrutar checkpoints por proyecto. Los hooks pasan el cwd del host a través de ella; sin definir significa proyecto desconocido y daimon cae al puntero global. |
| `DAIMON_MIN_MESSAGES` | `10` | Conteo mínimo de mensajes para que una sesión valga la pena serializar. Las sesiones más cortas se omiten. |
| `DAIMON_TIMEOUT` | `420` | Presupuesto total de serialización en segundos, compartido entre reintentos (los timeouts de socket por intento se limitan al presupuesto restante). Las llamadas reales de serialize/merge en backends gateway y CLI corren 74s–25min; mantén ≥420 o las llamadas lentas y los reintentos no caben. |
| `DAIMON_HUNG_AFTER` | `1800` | Segundos tras los cuales un proceso de serialización sin línea de resultado se trata como colgado/matado en lugar de aún corriendo. El default de 30 min queda con margen sobre una corrida lenta (las serializaciones en producción toman 4–25 min). |

## Almacén de checkpoints y GC

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_CHECKPOINT_DIR` | `~/.daimon/checkpoints` | Raíz del almacén de checkpoints por sesión. |
| `DAIMON_CHECKPOINT_KEEP` | `100` | Cuántos archivos de checkpoint por sesión retener (los N más nuevos). Los más viejos se recolectan tras una escritura exitosa. `0` desactiva el GC por completo (conservar para siempre). |
| `DAIMON_CHECKPOINT_HISTORY` | `3` | Cuántos punteros de checkpoint retener por directorio (`latest.json` más `prev-1` … `prev-(N-1)`), para que una serialización fallida pueda caer a un puntero previo. Mínimo 1 (solo latest). |
| `DAIMON_GC_PIN_IMPORTANCE` | `9` | Umbral de importancia de ítem que fija un archivo de checkpoint contra el GC: un archivo cuya importancia máxima de ítem alcanza este valor sobrevive fuera de la ventana de los N más nuevos. `0` desactiva el fijado (ventana de recencia pura); valores sobre 10 se recortan a 10. |

## Arrastre (carry)

Arrastre determinista de ítems sin resolver entre sesiones.

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_CARRY` | on | Interruptor maestro del arrastre. Activo salvo que valga exactamente `0` (cualquier otro valor lo mantiene activo). |
| `DAIMON_CARRY_FLOOR` | `0.05` | Peso efectivo mínimo para que un ítem arrastrado siga arrastrándose. Con el default, las decisiones expiran en ~5–6 semanas (graduado por importancia) y las preguntas abiertas escaladas viven ~3–4 meses. |
| `DAIMON_CARRY_MAX` | `8` | Tope de ítems arrastrados por tipo (los ítems nativos nunca cuentan contra él ni se descartan). Mínimo 1. |

## Briefing

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_BRIEF_MAX_TOKENS` | `3000` | Presupuesto de tokens para el briefing inyectado, estimado como `len(text)//4` (sin dependencia de tokenizador). `0` = sin límite. |
| `DAIMON_MAX_BRIEFING_DECISIONS` | `10` | Tope de decisiones mostradas en el briefing (solo vista de renderizado — el checkpoint las conserva todas). `0` = sin límite. |
| `DAIMON_BRIEF_GLOBAL_FALLBACK` | solo encabezado | Controla el fallback al puntero global entre proyectos cuando un proyecto no tiene checkpoint propio. El default muestra solo un encabezado; ponlo en `full` (o `1`) para inyectar el cuerpo foráneo completo. |
| `DAIMON_STALE_DAYS` | `7.0` | Umbral de edad (días) tras el cual el sello efectivo de última verificación de un ítem arrastrado (su `last_verified`, si no el último evento de resolutions.jsonl, si no `first_seen`) está lo bastante desactualizado para que `brief` lo advierta. `0` advierte en cada ítem arrastrado. |
| `DAIMON_PLAIN` | off | Cuando es verdadero (sin distinción de mayúsculas), fuerza salida de texto plano — desactiva las tablas/paneles enriquecidos en `status`, `brief` y `--help`. |
| `NO_COLOR` | sin definir | Por presencia, según la [convención NO_COLOR](https://no-color.org/): si la variable está definida con *cualquier* valor (incluso vacío), la salida enriquecida se desactiva. |

## Recall

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_RECALL_DB` | `~/.daimon/recall.db` | Ubicación del índice derivado de recall (SQLite FTS). Nunca es fuente de verdad — es seguro borrarlo en cualquier momento; recall lo reconstruye escaneando los directorios de checkpoints y de equipo. |
| `DAIMON_RECALL_SEEN_DIR` | `~/.daimon/recall_seen` | Estado de enfriamiento de sugerencias por sesión para que un tema repetido nunca se re-inyecte. Desechable — borrarlo solo reinicia los enfriamientos. |

## Memoria de equipo

Mirror de memoria compartida opt-in. Mira [memoria de equipo](../team/team.md)
para el flujo completo.

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_TEAM` | off | Cuando es verdadero, refleja cada checkpoint en el directorio de equipo compartido para que `brief --team` pueda mostrar a los compañeros. Regula **escrituras** solamente — las lecturas del directorio de equipo siempre están permitidas. Un remoto sincronizado exige además que el proyecto esté en su allowlist de alcance (mira [team.md](../team/team.md)); los proyectos fuera de alcance se reflejan solo al directorio local. |
| `DAIMON_AUTHOR` | `user.name` de git, luego el usuario del SO | Identidad de autor de equipo usada para separar tus checkpoints. Cae a `git config user.name`, luego al usuario del SO, luego a `unknown`. |
| `DAIMON_TEAM_DIR` | `~/.daimon/team` | Raíz del mirror de memoria de equipo compartida. |
| `DAIMON_TEAM_PROJECT` | sin definir | Ruta lógica de proyecto explícita para las sesiones de esta máquina (relativa, p. ej. `core/api-gateway`). Prevalece sobre el mapeo de `daimon-team.toml` del sidecar y sobre el fallback derivado del origin al enrutar checkpoints bajo `projects/`. |
| `DAIMON_TEAM_RETENTION_DAYS` | `365` | Ventana de edad al leer: los checkpoints de compañeros más viejos que esta cantidad de días se omiten al leer. `0` = conservar todos. Nunca borra físicamente de la rama compartida de solo-anexado. |

## Receipts

Receipts de procedencia firmados, opt-in (#204). Al habilitarlos, cada
checkpoint se empareja con un receipt de vinculación `local` de
[vitni](https://github.com/Daily-Nerd/vitni): una declaración firmada con
Ed25519 que vincula los bytes exactos en disco del checkpoint
(`outputs_hash`) con su transcript de origen (`inputs_hash`), escrita en un
archivo lateral `<session>.receipt`. Esto hace detectable una edición
posterior al archivo del checkpoint. Los receipts son totalmente válidos
offline — nada sale de la máquina.

Cada paso es **fail-open**: un CLI ausente, un openssl ausente, un timeout o
una salida mala registran una línea en `serialize.log` y se continúa sin
receipt — una falla de receipts nunca bloquea ni hace fallar una
serialización o un briefing. Verifica un checkpoint bajo demanda con
`daimon verify-receipt [session]`; al momento del briefing, un checkpoint de
la era de receipts cuyo receipt falta o ya no coincide con sus bytes tiene
sus etiquetas `✓ verbatim` degradadas con una nota visible.

La derivación de la llave pública prefiere el comando `keygen` del CLI de
vitni (vitni 0.5.0+) y cae a openssl en CLIs más viejos o ante un probe
fallido — así en macOS, donde el LibreSSL de Apple no tiene Ed25519 en
`openssl pkey`, los receipts funcionan una vez instalado vitni ≥ 0.5.0, sin
necesidad de un openssl con Ed25519.

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_RECEIPTS` | off | Cuando es verdadero, mintea un receipt firmado junto a cada checkpoint. Off por defecto — un subproceso nuevo por serialización es opt-in. |
| `DAIMON_VITNI_CLI` | `vitni-verify` (en el PATH) | El CLI verificador de vitni usado para firmar/verificar. Una ruta o un nombre resuelto en el PATH. Contrato: `<cli> <command>` con un objeto JSON por stdin y una línea JSON por stdout. |
| `DAIMON_KEYS_DIR` | `~/.daimon/keys` | Dónde viven la semilla de firma Ed25519 (`signing.seed`, modo 0600, auto-creada en el primer minteo) y la llave pública cacheada (`signing.pub.json`). |

## Hooks de host

Perillas de throttle de serialización para hosts sin un evento limpio de fin
de sesión. Mira [Hosts](../hosts/) para la configuración por host.

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_CODEX_SERIALIZE_ON_STOP` | on | Si el hook `Stop` de Codex serializa en absoluto. Activo salvo que valga `0`, `false`, `no` u `off` (sin distinción de mayúsculas). |
| `DAIMON_CODEX_MIN_SERIALIZE_INTERVAL` | `300` | Segundos mínimos entre lanzamientos de serialización de Codex. `0` serializa en cada `Stop`. |
| `DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL` | `300` | Segundos mínimos entre lanzamientos de serialización de Windsurf (Windsurf no tiene evento de fin de sesión, así que la captura corre con este throttle). `0` serializa cada turno. |
| `DAIMON_WINDSURF_FINALIZER_QUIET_SECONDS` | `600` | Periodo de silencio tras la última actividad de Windsurf antes de que un finalizador con debounce serialice el estado final del transcript de la trayectoria — cubre sesiones cuyos últimos turnos caen dentro de la ventana del throttle. Acepta valores fraccionarios; `0` desactiva el finalizador. |

## Operación y diagnóstico

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_LOG_DIR` | `~/.daimon/logs` | Dónde escribe `serialize.log` el hook de fin de sesión. El hook en sí tiene `~/.daimon/logs` fijo; este override existe para que el CLI (y los tests) puedan apuntar `status` a otra parte. |
| `DAIMON_CLAUDE_PROJECTS_DIR` | `~/.claude/projects` | Dónde viven los transcripts del host (`<slug>/<session>.jsonl`). Solo-lectura — la auditoría de re-verificación de citas los lee para re-revisar citas almacenadas contra su fuente. |
| `DAIMON_SCAR_HARVEST` | off | Cuando es verdadero, borra candidatos de scar (conocimiento negativo) desde el transcript al fin de sesión. |

## Backend LLM

La serialización necesita un endpoint de LLM. `daimon configure` es la vía
prevista para definir estas variables. La URL, la key y el modelo caen cada
uno a una variable `LITELLM_*` si la forma `DAIMON_*` no está definida.

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_LLM_BACKEND` | `auto` | Transporte: `auto` (litellm si hay credenciales, si no un CLI de comando si alguno resuelve), `litellm`, `command` o `claude-cli`. |
| `DAIMON_LLM_BASE_URL` | `http://localhost:4000` | URL del endpoint compatible con OpenAI (se recorta la barra final). Cae a `LITELLM_BASE_URL`. |
| `DAIMON_LLM_API_KEY` | sin definir | API key del endpoint. Cae a `LITELLM_API_KEY`. |
| `DAIMON_LLM_MODEL` | sin definir | Nombre de modelo a enviar. Cae a `LITELLM_MODEL`. |
| `DAIMON_LLM_TEMPERATURE` | `0.0` | Temperatura de muestreo de cada llamada de chat. `0.0` para extracción determinista; algunos upstreams rechazan cualquier valor que no sea uno fijo. |
| `DAIMON_LLM_FALLBACK` | on | Cuando el backend litellm falla, cae automáticamente a un backend de comando (resiliencia ante fallas del gateway). Ponlo en `0` para desactivarlo. |
| `DAIMON_LLM_NO_CACHE` | off | Cuando es verdadero, evita el cache de respuestas del gateway por request — necesario cuando una respuesta mala cacheada fija una falla o cuando las corridas deben ser estadísticamente independientes. |
| `DAIMON_LLM_BRIEFING` | off | Cuando es verdadero, renderiza el briefing vía LLM en lugar de la plantilla determinista. |
| `DAIMON_LLM_COMMAND` | sin definir | Invocación completa del CLI para el backend `command` (binario + modelo + flags). |
| `DAIMON_LLM_COMMAND_OUTPUT` | sin definir | Cómo extraer el texto del asistente del stdout del comando: `text` (stdout crudo) o `json:<key>` (parsear JSON, leer `<key>`). |
| `DAIMON_LLM_COMMAND_INPUT` | `stdin` | Cómo llega el prompt al backend de comando: `stdin` (por tubería), `arg` (anexado como último elemento de argv) o `file:<flag>` (escrito a un archivo temporal, luego se anexa `<flag> <path>`). Un valor no reconocido registra una advertencia y cae a `stdin`. |

## Chunking del serializador

Las sesiones largas se serializan en chunks solapados cuyos checkpoints
parciales se fusionan jerárquicamente. Los defaults vienen de mediciones de
campo; solo importan si tus sesiones son rutinariamente muy largas.

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_CHUNK_LINES` | `1200` | Conteo de líneas del transcript renderizado sobre el cual la serialización cambia a modo chunked. |
| `DAIMON_CHUNK_OVERLAP` | `100` | Líneas de solapamiento entre chunks adyacentes, para que un ítem que cruza un borde sea visto entero por al menos un chunk. |
| `DAIMON_CHUNK_CONCURRENCY` | `4` | Llamadas LLM de serialización de chunks en paralelo. Mínimo 1 (secuencial). |
| `DAIMON_MERGE_GROUP_SIZE` | `3` | Máximo de checkpoints parciales fusionados por llamada de merge jerárquico. Mínimo 2. Bájalo a `2` si las llamadas de merge mueren en un gateway con techo de request del lado del servidor (los modelos de razonamiento generando merges de 3 vías pueden excederlo; subir `DAIMON_TIMEOUT` no ayuda — el kill es del lado del servidor). |
