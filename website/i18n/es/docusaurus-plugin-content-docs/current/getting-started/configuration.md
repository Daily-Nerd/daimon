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
| `DAIMON_TEAM_APPLY_FORGET` | off | Consentimiento permanente para que el tombstone de olvido publicado por un COMPAÑERO reescriba los checkpoints propios de esta máquina. NO alcanza por sí solo — el borrado además exige el `daimon team sync --apply-forget` escrito a mano, porque un `daimon team sync` pelado se lanza en segundo plano al iniciar la sesión. Por defecto apagado: un tombstone ajeno siempre suprime el valor al leer y en el índice, pero borrar estado local a partir del hash de otra persona es una decisión que se toma a conciencia — la rama compartida es de solo-anexado, así que no hay vuelta atrás. |
| `DAIMON_LIVE_DELIVERY` | off | Cuando es verdadero, un request sin decidir dirigido a este proyecto se entrega a una sesión que ya estaba corriendo cuando llegó, en el siguiente límite de turno de esa sesión, en vez de esperar a su próximo briefing de SessionStart. Solo superficie de render: el ledger sigue siendo store-and-forward, el aviso entregado es el mismo registro pull-only que habría mostrado el próximo briefing, y los verbos que deciden siguen siendo solo humanos. Una vez por sesión y por revisión; un `request revise` vuelve a entregar el pedido afinado. Solo dentro del mismo daimon home. Por defecto apagado — solo-briefing es la postura correcta para sesiones cortas, así que un consumidor siempre-activo lo enciende a conciencia. |

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
| `DAIMON_WINDSURF_DIR` | `~/.daimon/windsurf` | Dónde guarda el adaptador de Windsurf los transcripts que acumula. Lo leen tanto el hook que los escribe como las rutas de `forget`/`heal` que los borran — cámbialo en un solo sitio, o el que escribe y el que borra dejan de coincidir. |
| `DAIMON_WINDSURF_STATE_DAYS` | `7` | Ventana de antigüedad para los transcripts de Windsurf que escribe daimon y los volcados `unparsed`, recogidos por `daimon heal`. Es un límite de privacidad: un valor olvidado no puede localizarse dentro de la prosa, así que esto acota cuánto tiempo permanece la conversación de origen entre ejecuciones de `forget`. Con mínimo 1 — a diferencia de los otros ajustes de Windsurf, `0` no lo desactiva. |

## Operación y diagnóstico

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_LOG_DIR` | `~/.daimon/logs` | Directorio de logs. `serialize-crash.log` respeta esta variable de los dos lados: los hooks que lanzan el proceso hijo de serialize la leen (del entorno y de este archivo) igual que el CLI, porque `daimon forget` borra ese archivo y quien escribe y quien borra tienen que coincidir en dónde está. `serialize.log` es la excepción: los hooks lo siguen escribiendo en `~/.daimon/logs` siempre, y este override solo cambia dónde lo busca el CLI (y los tests). |
| `DAIMON_CLAUDE_PROJECTS_DIR` | `~/.claude/projects` | Dónde viven los transcripts del host (`<slug>/<session>.jsonl`). Solo-lectura — la auditoría de re-verificación de citas los lee para re-revisar citas almacenadas contra su fuente. |
| `DAIMON_SCAR_HARVEST` | off | Cuando es verdadero, borra candidatos de scar (conocimiento negativo) desde el transcript al fin de sesión. |
| `DAIMON_LOG_STDOUT` | off | Cuando es verdadero, las líneas de resultado de serialize también llegan a stdout, el stream que recolecta un runtime de contenedores. El hook de fin de sesión deja que el proceso hijo desprendido herede stdout en vez de descartarlo, y el CLI también refleja ahí sus líneas `error:`, así que éxito, omisión y error caen todos en una sola superficie. Pensado para hosts en contenedores, donde una línea escrita solo en `serialize.log` sobre un volumen es invisible para la plataforma. Apagado por defecto: en una terminal esas líneas aparecen en tu shell minutos después de que terminó la sesión. No toca stderr, que queda reservado para `serialize-crash.log`. |

## Backend LLM

La serialización necesita un endpoint de LLM. `daimon configure` es la vía
prevista para definir estas variables. La URL, la key y el modelo caen cada
uno a una variable `LITELLM_*` si la forma `DAIMON_*` no está definida. El
backend `litellm` necesita **ambas** `DAIMON_LLM_MODEL` y
`DAIMON_LLM_API_KEY`; mientras falte alguna, `daimon configure` reporta
`backend: litellm — missing: ...` nombrando exactamente qué falta — esa línea
significa que la configuración está incompleta, no que el endpoint esté caído.

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_LLM_BACKEND` | `auto` | Transporte: `auto` (litellm si hay credenciales, si no un CLI de comando **nombrado** si alguno resuelve), `litellm`, `command` o `claude-cli`. `claude-cli` es la opción sin configuración: ejecuta un `claude` encontrado en el PATH con un preset incorporado y no necesita nada más. `command` requiere `DAIMON_LLM_COMMAND`. |
| `DAIMON_LLM_BASE_URL` | `http://localhost:4000` | URL del endpoint compatible con OpenAI (se recorta la barra final). Cae a `LITELLM_BASE_URL`. |
| `DAIMON_LLM_API_KEY` | sin definir | API key del endpoint. **Obligatoria para el backend `litellm`.** Cae a `LITELLM_API_KEY`. |
| `DAIMON_LLM_MODEL` | sin definir | Nombre de modelo a enviar. **Obligatorio para el backend `litellm`.** Cae a `LITELLM_MODEL`. |
| `DAIMON_LLM_TEMPERATURE` | `0.0` | Temperatura de muestreo de cada llamada de chat. `0.0` para extracción determinista; algunos upstreams rechazan cualquier valor que no sea uno fijo. |
| `DAIMON_LLM_FALLBACK` | on | Cuando el backend primario falla, cae automáticamente al comando de rescate (`DAIMON_LLM_COMMAND_FALLBACK`). Aplica tanto a un primario litellm como a uno `command`. Ponlo en `0` para desactivarlo. |
| `DAIMON_FALLBACK_MIN_SECONDS` | `DAIMON_TIMEOUT` | Presupuesto mínimo garantizado al comando de rescate al entrar. El primario pudo haber agotado el deadline compartido reintentando la misma falla que el rescate existe para resolver, lo que lo mataría al llegar; un presupuesto restante sano nunca se recorta. |
| `DAIMON_LLM_STREAM` | on | Transmite la respuesta de litellm para que el timeout del socket acote el intervalo entre frames y no la longitud total de la respuesta. Sin esto, una respuesta larga dispara el timeout y reintenta desde cero. Ponlo en `0` para desactivarlo. |
| `DAIMON_LLM_NO_CACHE` | off | Cuando es verdadero, evita el cache de respuestas del gateway por request — necesario cuando una respuesta mala cacheada fija una falla o cuando las corridas deben ser estadísticamente independientes. |
| `DAIMON_LLM_BRIEFING` | off | Cuando es verdadero, renderiza el briefing vía LLM en lugar de la plantilla determinista. |
| `DAIMON_LLM_COMMAND` | sin definir | Invocación completa del CLI para el backend `command` (binario + modelo + flags). Obligatoria para `command`, y obligatoria para que un `claude` en el PATH sea usado por cualquier backend que no sea `claude-cli`. |
| `DAIMON_LLM_COMMAND_OUTPUT` | sin definir | Cómo extraer el texto del asistente del stdout del comando: `text` (stdout crudo) o `json:<key>` (parsear JSON, leer `<key>`). |
| `DAIMON_LLM_COMMAND_INPUT` | `stdin` | Cómo llega el prompt al backend de comando: `stdin` (por tubería), `arg` (anexado como último elemento de argv) o `file:<flag>` (escrito a un archivo temporal, luego se anexa `<flag> <path>`). Un valor no reconocido registra una advertencia y cae a `stdin`. |
| `DAIMON_LLM_COMMAND_FALLBACK` | sin definir | El único CLI de rescate, usado cuando el backend primario falla. Sirve tanto para un primario litellm como para uno `command`, que antes no tenía ninguna dirección de rescate. Un solo fallback, nunca una cadena: si el primario y este fallan, la causa casi siempre es del entorno, y un tercer CLI gasta presupuesto para llegar al mismo error mientras hace que la instalación parezca más protegida de lo que está. Si no se define, un primario litellm sigue cayendo a `DAIMON_LLM_COMMAND` como antes. |
| `DAIMON_LLM_COMMAND_FALLBACK_OUTPUT` | sin definir | Especificación de salida del CLI de rescate, misma gramática que `DAIMON_LLM_COMMAND_OUTPUT`. Se lleva por separado porque el rescate es otro binario. |
| `DAIMON_LLM_COMMAND_FALLBACK_INPUT` | `stdin` | Especificación de entrada del CLI de rescate, misma gramática que `DAIMON_LLM_COMMAND_INPUT`. |

:::note[Qué proceso recibe tu transcripción]

Un binario `claude` que simplemente esté en el PATH **no** se adopta automáticamente. Serializar envía la transcripción completa de la sesión al CLI configurado, así que ese CLI debe nombrarse: o `DAIMON_LLM_COMMAND`, o `DAIMON_LLM_BACKEND=claude-cli` para optar por el preset incorporado.

Antes bastaba con dejar `DAIMON_LLM_COMMAND` sin definir y tener un `claude` en cualquier parte del PATH, tanto para instalaciones en `auto` como para la ruta de rescate de litellm. Si dependías de eso, definí una de las dos variables de arriba. `daimon configure` nombra el binario resuelto y su ruta para que puedas ver exactamente cuál está en uso.

:::

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
