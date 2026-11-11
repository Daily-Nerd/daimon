---
sidebar_position: 2
description: "Todas las variables de entorno de daimon, como el entorno del proceso le gana al archivo en ~/.daimon/env, y por que los hooks necesitan ese archivo."
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
| `DAIMON_PROJECT_DIR` | sin definir | Directorio de trabajo de la sesión que se briefea o serializa, usado para enrutar checkpoints por proyecto. Los hooks pasan el cwd del host a través de ella; sin definir significa que daimon usa el directorio de trabajo. Un host sin identidad de proyecto (el hook en proceso de Hermes) no recibe briefing: un proyecto desconocido no lee nada. |
| `DAIMON_MODEL_CROSS_PROJECT` | off | Cuando es verdadero, las herramientas MCP pueden leer fuera del alcance propio del proyecto: `daimon_recall` y `daimon_brief` aceptan `slug`, `all_projects` y una ruta `project`, `daimon_status` y `requests_inbox` aceptan una ruta `project`, y `daimon_projects` lista todos los buckets. Apagado, cualquier pedido que salga del bucket propio se rechaza en voz alta con un mensaje que nombra esta variable, y `daimon_projects` lista solo el bucket propio; las descripciones de las herramientas entonces no ofrecen esos argumentos. Un `project` que resuelve al bucket propio siempre se responde. Se lee al arrancar el proceso, del entorno o del archivo env, nunca del contenido de un prompt. Bajo `DAIMON_TENANT_SCOPED` no tiene efecto y se mantiene el rechazo de tenant. Esta variable no gobierna la CLI: sin alcance de inquilino una shell siempre puede elegir su propio directorio. `DAIMON_TENANT_SCOPED` lo cierra para los verbos de la CLI. |
| `DAIMON_CAPTURE_HOST` | sin definir | Pista puesta por el host indicando qué agente produjo una captura (`claude-code`, `codex`, `windsurf`, `gemini`, `hermes`, `manual`), enviada por los hooks de captura instalados (#594). No es algo que definas a mano — un hook la mete en el entorno que le pasa al CLI, para los casos en que una ruta de transcript sola no alcanza para resolverlo. |
| `DAIMON_SESSION_SPEAKER` | sin definir | La única persona a la que pertenece esta sesión, declarada por el host. Llena el `stated_by` de un ítem cuando el transcript no trae un `said_by` por mensaje, y solo para ítems ligados por completo a mensajes del usuario; todo lo que cite un mensaje del asistente o de una herramienta queda sin atribuir. Es una etiqueta pura, nunca una clave, así que no toca el espacio de nombres de `DAIMON_AUTHOR`. Defínela solo donde una sesión sea una persona durante toda su vida. Un host que atiende a varias personas a través de una sola sesión debe dejarla sin definir. |
| `DAIMON_SPEAKER_LINE` | sin definir | El delimitador que un host escribe alrededor de su propia línea de hablante al inicio de cada mensaje del usuario, para un host que atiende a varias personas a través de una sola sesión pero no es autor del transcript. Caracteres literales o la grafía `U+E000`. Cuando está definido, una fila de usuario que empieza con `<delim>from=<id> name="<name>"...<delim>` recibe `said_by` como `name (id)` y la línea se corta del contenido antes de que nada lo lea; solo cuenta la posición cero, y solo las filas de usuario. El valor lo declara el host, con la misma honestidad que el `said_by` propio de una fila. El host debe quitar el delimitador del texto del usuario antes de anteponer su línea, o un usuario puede falsificar una. Sin definir, el contenido nunca se lee para atribuir. |
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

### Actualizar a través de la 0.42.0

Antes de la 0.42.0 la biblioteca nombraba el bucket de un proyecto a partir de
la ruta literal que recibía. Desde la 0.42.0 cada entry point resuelve la ruta
primero: la vuelve absoluta, colapsa los symlinks y después la normaliza al
toplevel de git. Por eso hay dos formas de ruta que ahora nombran un bucket
distinto al de antes:

- una ruta con un **symlink en cualquier parte** — en macOS eso incluye todo lo
  que se alcanza a través de `/tmp`, y cualquier punto de montaje que sea un
  enlace;
- una ruta **debajo de un toplevel de git**, cuando la escritura vino por la
  biblioteca y no por la línea de comandos.

La CLI ya resolvía las dos cosas antes de la 0.42.0, así que un bucket escrito
corriendo `daimon` en una terminal no está afectado. Un bucket escrito por un
proceso anfitrión que llamó a la biblioteca desde una ruta así queda con el
nombre viejo, y nada lo lee.

`daimon status` lo dice. Cada vez que existe un bucket escrito con la regla
vieja para la ruta donde estás parado, el bloque de salud trae una línea
`legacy:` que lo nombra. Movelo una sola vez:

```sh
daimon bucket migrate                                # este proyecto
daimon bucket migrate --project /ruta/al/proyecto
daimon bucket migrate --project /ruta/al/proyecto --dry-run
```

El movimiento renombra el directorio viejo cuando el nuevo todavía no existe, y
en caso contrario fusiona. Una fusión agrega cada línea de ledger que el bucket
nuevo no tenga ya, y suma los punteros del bucket viejo a la cadena del nuevo.
Los punteros que ya están en el bucket nuevo nunca se borran ni se desplazan:
son del proyecto vivo. Los punteros viejos entran en los espacios que deje
libres `DAIMON_CHECKPOINT_HISTORY`, del más nuevo al más viejo, salvo que una
copia vieja de una sesión a la que el bucket nuevo ya apunta reemplaza ese
único espacio cuando es la más nueva de las dos. Todo lo demás, un puntero
viejo sin espacio libre o un archivo que daimon no escribe, queda exactamente
donde está y se nombra en el reporte junto con qué hacer al respecto.

Correrlo dos veces es seguro: una segunda corrida sobre un estado sin cambios no
escribe nada nuevo y devuelve el código que corresponde al estado que encuentra.
`--dry-run` imprime el mismo plan y no escribe nada.

La salida 0 significa que el movimiento terminó y el bucket viejo ya no está.
La salida 1 significa que no, y stdout nombra cada cosa que quedó con su propio
remedio: subí `DAIMON_CHECKPOINT_HISTORY` al número que indica y volvé a correr
para los punteros sin espacio libre; arreglá o movés un archivo que no se pudo
leer; sacá del bucket viejo un archivo que daimon no escribe. Un movimiento
parcial no cuenta como migrado, así que las filas del bucket viejo todavía no se
leen como historia de este proyecto. Si terminás el trabajo por tu cuenta
limpiando el directorio viejo, la siguiente corrida lo registra y la migración
queda completa.

La salida 2 es un rechazo. Un `--project` con un componente `..` se rechaza
porque la regla vieja colapsa el `..` antes de seguir un symlink y la actual
sigue el symlink primero, así que las dos nombran directorios distintos; un
bucket escrito antes de 0.42.0 se escribió bajo el slug literal de la ruta ya
colapsada, así que pasá esa ruta. En un home con alcance de inquilino
(`DAIMON_TENANT_SCOPED`) un `--project` explícito se rechaza directamente y el
proyecto sale de `DAIMON_PROJECT_DIR`, o del directorio de trabajo: una
migración escribe un alias duradero entre dos buckets, y elegir cuáles dos es
elegir un alcance. La misma regla vale para todos los verbos: en un home con alcance de inquilino se rechaza un `--project` que nombre otro bucket, sea cual sea el verbo.

Cada movimiento completado agrega una línea a
`~/.daimon/checkpoints/migrations.jsonl`. Ese archivo es lo que permite que
`daimon recall` y la bandeja de pedidos sigan encontrando el historial bajo el
nombre viejo del bucket una vez que el directorio ya no está: los archivos de
checkpoint por sesión conservan el nombre con el que fueron sellados, porque sus
receipts atan sus bytes exactos. Cuando un bucket ya absorbió a otro, `daimon
status` muestra una línea `migrated: from <bucket viejo> on <fecha>` en lugar de
la advertencia.

## Arrastre (carry)

Arrastre determinista de ítems sin resolver entre sesiones.

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_CARRY` | on | Interruptor maestro del arrastre. Activo salvo que valga exactamente `0` (cualquier otro valor lo mantiene activo). |
| `DAIMON_CARRY_FLOOR` | `0.05` | Peso efectivo mínimo para que un ítem arrastrado siga arrastrándose. Con el default, las decisiones expiran en ~5–6 semanas (graduado por importancia) y las preguntas abiertas escaladas viven ~3–4 meses. |
| `DAIMON_CARRY_MAX` | `24` | Tope de ítems arrastrados por tipo (los ítems nativos nunca cuentan contra él ni se descartan). Mínimo 1. |

## Briefing

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_BRIEF_MAX_TOKENS` | `3000` | Presupuesto de tokens para el briefing inyectado, estimado a 4 bytes por token (sin dependencia de tokenizador). Se pliega en un único presupuesto de bytes, `min(DAIMON_BRIEF_MAX_BYTES, DAIMON_BRIEF_MAX_TOKENS * 4)`, que comparten todos los bloques de `daimon brief`. `0` = sin límite. |
| `DAIMON_BRIEF_MAX_BYTES` | `9728` | El presupuesto de bytes para el stdout COMPLETO de `daimon brief`, en bytes UTF-8. Los bytes UTF-8 nunca son menos que los caracteres o unidades UTF-16 que cuenta un host, así que un presupuesto en bytes queda siempre del lado seguro de cada límite (tabla abajo). La línea de `--label`, los avisos de fallback y de serialize en curso, el bloque de traspaso (handoff), las reglas vigentes, los paneles de solicitudes y decisiones, el cuerpo cognitivo, el bloque de desvío de código, la sección de compañeros de equipo y la nota de ítems ocultos cuentan contra él. Cuando se agota, el briefing suelta ítems completos en un único orden fijo (primero los arrastrados vencidos, las secciones de fondo antes que las accionables, lo arrastrado antes que lo de esta sesión) y cada sección dice qué ocultó. El saludo, las reglas vigentes, el traspaso y las decisiones más nuevas nunca se sueltan; si solo eso ya excede el presupuesto, el briefing dice por cuánto. `0` = sin límite. |
| `DAIMON_MAX_BRIEFING_DECISIONS` | `10` | Tope de decisiones mostradas en el briefing (solo vista de renderizado, el checkpoint las conserva todas). La nota de Decisiones cuenta lo que el tope recortó. Las `min(3, tope)` decisiones más nuevas de la última sesión siempre se muestran. `0` = sin límite. |
| `DAIMON_BRIEF_GLOBAL_FALLBACK` | solo encabezado | Controla el fallback al puntero global entre proyectos cuando un proyecto no tiene checkpoint propio. El default muestra solo un encabezado; ponlo en `full` (o `1`) para inyectar el cuerpo foráneo completo. Un proyecto sin identidad nunca lee el puntero global, y en un home con `DAIMON_TENANT_SCOPED` esta variable y `brief --global-fallback` no tienen efecto (el flag se rechaza). |
| `DAIMON_STALE_DAYS` | `7.0` | Umbral de edad (días) tras el cual el sello efectivo de última verificación de un ítem arrastrado (su `last_verified`, si no el último evento de resolutions.jsonl, si no `first_seen`) está lo bastante desactualizado para que `brief` lo advierta. `0` advierte en cada ítem arrastrado. Pasado el umbral, el ítem mismo se muestra como `[? unverified]` con su etiqueta guardada y su edad en un sufijo final, en lugar de la etiqueta de confianza guardada (solo al renderizar; `daimon reverify` restaura la etiqueta en el siguiente brief). |
| `DAIMON_REQUEST_OVERDUE_DAYS` | `7.0` | Días sin ningún movimiento (apertura, revisión, aceptación, needs-info, respuesta o reclamo de hecho) tras los cuales una solicitud que su remitente sigue teniendo abierta figura como `overdue` en `daimon request list` y en el brief del remitente. De reloj, se deriva al leer, independiente de las sesiones del destinatario. `0` marca vencida toda solicitud abierta; un valor mal formado vuelve a `7.0`. |
| `DAIMON_PLAIN` | off | Cuando es verdadero (sin distinción de mayúsculas), fuerza salida de texto plano — desactiva las tablas/paneles enriquecidos en `status`, `brief` y `--help`. |
| `NO_COLOR` | sin definir | Por presencia, según la [convención NO_COLOR](https://no-color.org/): si la variable está definida con *cualquier* valor (incluso vacío), la salida enriquecida se desactiva. |

### Límites de salida de los hosts detrás del valor por defecto de `DAIMON_BRIEF_MAX_BYTES`

Un hook de inicio de sesión captura todo el stdout de `daimon brief --label`. Estos son los límites medidos en cada host, por eso el valor por defecto es 9728 (9,5 KiB) y no más alto.

| Host | Límite medido | Qué pasa al superarlo |
|---|---|---|
| Claude Code 2.1.314 | 10.000 unidades UTF-16, después de recortar la salida | La salida se vuelca a un archivo y se reemplaza por una vista previa de 2 KB. El mayor briefing sin volcar fue de 8.920 caracteres, el menor volcado de 10.508. |
| Codex 0.157.0 | Unos 10.000 caracteres (la unidad no está confirmada; el aviso menciona tokens) | Se entregan unos 10.000 caracteres con un aviso "Warning: truncated output" y una línea final "Full hook output saved to". |
| Kimi Code 0.43.1 | Sin corte observado, resultados de hook completos hasta 25.728 caracteres | Ninguno observado. |
| Gemini | No medido | Desconocido. |
| Windsurf | Sin inyección de briefing (el briefing se lee desde una terminal) | No aplica. |

Subir `DAIMON_BRIEF_MAX_BYTES` por encima de 9728 puede llevar el briefing más allá de los límites de Claude Code y Codex.

## Worldcheck

Opt-in (#365): un chequeo puntual, al momento del briefing, de las
afirmaciones de estado de PR/issue arrastradas contra la realidad, vía
probes de solo-lectura con `gh`. Apagado por defecto — `daimon brief` corre
en la ruta de inyección crítica en latencia del hook SessionStart (un
presupuesto de subproceso de 8s dentro de un presupuesto de hook de 10s),
así que incluso probes de red acotados en presupuesto ahí deben activarse a
propósito. Con el flag apagado no corre ningún probe y no se escribe nada. El
render igual lee el texto de cada nota arrastrada, y la que nombra un PR, una
rama, un archivo o una dependencia recibe el sufijo
`(noted <fecha>, re-check before acting)`, así que la salida no es byte-idéntica a una build sin
worldcheck.

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_WORLDCHECK` | off | Cuando es verdadero, chequea las afirmaciones de estado de PR/issue arrastradas contra la realidad vía probes de solo-lectura con `gh` al momento del briefing. |

Con worldcheck habilitado, las contradicciones detectadas sobre la existencia
de archivos y ramas o el estado de PR/issues también bajan la prioridad del
ítem en las búsquedas y sugerencias de recall. El ítem sigue visible. Una
confirmación posterior solo levanta la contradicción de esa misma afirmación;
un recibo válido no levanta una contradicción por un archivo ausente. Los
chequeos omitidos o no disponibles no invalidan ni levantan contradicciones.
Los chequeos de versiones de dependencias siguen anotando solo el briefing.
La evidencia la registra la ruta de briefing que corrió el chequeo (la CLI, la
herramienta MCP, la API de host o el hook de Hermes), por un commit compartido;
el renderizador en sí no escribe nada.

| Superficie | Recolección de evidencia con `DAIMON_WORLDCHECK=1` |
|---|---|
| Claude Code, Codex, Gemini CLI, Kimi Code | Soportada mediante el hook existente de `daimon brief` de cada host. |
| Windsurf | Soportada cuando el agente ejecuta `daimon brief --team`; sin inyección automática al iniciar sesión. |
| MCP `daimon_brief` (proyecto propio, no una lectura por `slug`), hook de briefing en proceso de Hermes, API de host `render_briefing` | Soportada. Estas rutas ejecutan los mismos chequeos y registran la misma evidencia que la CLI. El costo es de unos 3 s en el peor caso por briefing con el flag activo: los chequeos de archivos y ramas son lecturas de disco, los de PR corren `gh` con un plazo de 0,8 s, y `git remote -v` tiene un timeout de 2 s. El bucle de MCP es de un solo hilo, así que ese briefing bloquea las demás llamadas MCP del host por ese tiempo, el mismo límite que ya paga el hook de la CLI. Una llamada a `daimon_brief` por `slug` nunca ejecuta worldcheck, porque un slug nombra un bucket y no la ruta de un repositorio. |
| Recall por CLI y MCP, sugerencias proactivas | Consumen la evidencia registrada mediante el índice compartido de recall. |

## Recall

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_RECALL_DB` | por store (ver la descripción) | Solo para pruebas: fija un archivo explícito de índice de recall y omite el nombre por store. Déjala sin definir. El índice derivado de recall (SQLite FTS) nunca es fuente de verdad, así que es seguro borrarlo en cualquier momento. El store por defecto conserva `~/.daimon/recall.db`; cualquier otro store (`DAIMON_CHECKPOINT_DIR`, `--data-dir`) obtiene su propio `~/.daimon/recall/<hash>.db`. `daimon heal` elimina los índices por store cuyo store ya no existe o que pasaron 30 días sin uso. |
| `DAIMON_RECALL_SEEN_DIR` | `~/.daimon/recall_seen` | Estado de enfriamiento de sugerencias por sesión para que un tema repetido nunca se re-inyecte. Desechable — borrarlo solo reinicia los enfriamientos. |

## Alcance de inquilino

Para un host que corre un solo home de daimon con un directorio de proyecto por persona. La máquina de una sola persona nunca necesita ninguna de las dos variables. Ambas se leen al iniciar el proceso, desde el entorno del proceso o el archivo env, nunca desde el contenido de un prompt, así que un modelo no puede definirlas.

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_TENANT_SCOPED` | off | Cuando es verdadero, quien llama no puede elegir un alcance. `--slug` y `--all-projects` en `recall`, `brief`, `why` y `decide`, `--all` en `audit privacy`, `--global-fallback` en `brief`, y un `--project` que nombre otro bucket en cualquier verbo se rechazan con un error claro, y `DAIMON_BRIEF_GLOBAL_FALLBACK` se ignora. `daimon decide` no muestra conteos de otros proyectos, `daimon status` no muestra puntero global, y una lectura nunca cae al checkpoint de otro proyecto. Las herramientas MCP rechazan de la misma forma cualquier `slug`, `all_projects` o ruta `project` fuera del bucket propio de quien llama (`DAIMON_MODEL_CROSS_PROJECT` no puede levantarlo), y `daimon projects` lista solo el bucket propio de quien llama. Se rechaza, nunca se reduce en silencio: quien pidió un alcance y recibió el suyo en su lugar leería la respuesta como completa. |
| `DAIMON_GIT_POSTURE` | `ambient` | Cómo consulta daimon a git sobre el directorio de un proyecto. `ambient` deja que git responda como siempre. `sealed` es para hosts cuyos directorios de proyecto son escritos por alguien de menor confianza: daimon encuentra el checkout por su cuenta bajo un techo (el home por defecto), fija el directorio git, lee solo el archivo de config propio del checkout con los includes apagados, toma la rama de `HEAD`, nunca le pregunta a git por `user.name`, y rechaza un `.git` que sea un archivo, un symlink o un puntero a otro directorio. Un checkout cuya config tenga algo fuera de una lista corta de claves permitidas (nada de `include`, `filter`, `diff`, `merge`, `alias`, `core.sshCommand` y similares), o que tenga un archivo `config.worktree` o `objects/info/alternates`, se rechaza antes de que git corra sobre él. Un host puede declarar por su cuenta el slug y la URL de origen de un proyecto con `declare` en vez de dejar que daimon los derive; la raíz declarada igual pasa las verificaciones de arriba. `daimon status` y el briefing consultado por ruta muestran un proyecto rechazado, y las escrituras hacia él se rechazan. |
| `DAIMON_EXTRA_READ_SLUGS` | sin definir | Slugs separados por comas que esta sesión puede leer junto al suyo, de forma ambiental, sin que intervenga ningún argumento de quien llama. Alcanza por igual a `recall`, `why` y a la ruta de sugerencias proactivas. Solo lectura: ninguna ruta de escritura acepta un slug y una sesión nunca escribe en un alcance listado. Un acierto de un alcance listado nombra su proyecto de origen. Pensada para un alcance compartido cuyo contenido ya era visible para todos en él; defínela solo donde una sesión sea una persona. |

`DAIMON_MODEL_CROSS_PROJECT`, la contraparte del lado MCP, está en la tabla de Núcleo de arriba.

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
| `DAIMON_TEAM_APPLY_FORGET` | off | Consentimiento permanente para que el tombstone de olvido publicado por un COMPAÑERO reescriba los checkpoints propios de esta máquina. NO alcanza por sí solo — el borrado además exige el `daimon team sync --apply-forget` escrito a mano, porque un `daimon team sync` pelado se lanza en segundo plano al iniciar la sesión. Por defecto apagado: un tombstone ajeno siempre suprime el valor al leer y en el índice, pero borrar estado local a partir del hash de otra persona es una decisión que se toma a conciencia — la rama compartida es de solo-anexado, así que no hay vuelta atrás. Sin ella, `daimon team sync --apply-forget` se niega con código de salida 2 antes de sincronizar nada. Con la variable activada, `daimon team status` y la línea de equipo de `daimon status` lo reportan como armado. |
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
| `DAIMON_CODEX_SERIALIZE_ON_SESSION_END` | on | Si el hook `SessionEnd` de Codex serializa en absoluto — el fin real de una sesión, disparado una sola vez en el cierre ordenado con el transcript completo, deliberadamente sin throttle. Activo salvo que valga `0`, `false`, `no` u `off` (sin distinción de mayúsculas). No reemplaza a `Stop`, que sigue como seguro ante un SIGKILL o una terminal cerrada que nunca llega a `SessionEnd`. |
| `DAIMON_CODEX_MIN_SERIALIZE_INTERVAL` | `300` | Segundos mínimos entre lanzamientos de serialización de Codex. `0` serializa en cada `Stop`. |
| `DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL` | `300` | Segundos mínimos entre lanzamientos de serialización de Windsurf (Windsurf no tiene evento de fin de sesión, así que la captura corre con este throttle). `0` serializa cada turno. |
| `DAIMON_WINDSURF_FINALIZER_QUIET_SECONDS` | `600` | Periodo de silencio tras la última actividad de Windsurf antes de que un finalizador con debounce serialice el estado final del transcript de la trayectoria — cubre sesiones cuyos últimos turnos caen dentro de la ventana del throttle. Acepta valores fraccionarios; `0` desactiva el finalizador. |
| `DAIMON_WINDSURF_DIR` | `~/.daimon/windsurf` | Dónde guarda el adaptador de Windsurf los transcripts que acumula. Lo leen tanto el hook que los escribe como las rutas de `forget`/`heal` que los borran — cámbialo en un solo sitio, o el que escribe y el que borra dejan de coincidir. |
| `DAIMON_WINDSURF_STATE_DAYS` | `7` | Ventana de antigüedad para los transcripts de Windsurf que escribe daimon y los volcados `unparsed`, recogidos por `daimon heal`. Es un límite de privacidad: un valor olvidado no puede localizarse dentro de la prosa, así que esto acota cuánto tiempo permanece la conversación de origen entre ejecuciones de `forget`. Con mínimo 1 — a diferencia de los otros ajustes de Windsurf, `0` no lo desactiva. |

## Hosts que embeben daimon: la superficie git sellada

Un programa que embebe daimon y trabaja en directorios escritos por alguien de
menor confianza elige la postura sellada (`DAIMON_GIT_POSTURE=sealed`, o
`hostgit.configure(posture="sealed", ceiling=...)` antes de la primera lectura
de proyecto) y hace todo su git a través de `daimon_briefing.hostgit`. Estas
llamadas existen solo bajo esa postura, salvo `project`, que responde en ambas.

| Llamada | Qué hace |
|---|---|
| `project(path)` | El checkout al que pertenece `path`, o un código de rechazo. Corre la verificación de forma y la compuerta de la lista de claves de config antes de devolver un checkout. |
| `run(checkout, *args)` | Una llamada a git sobre un checkout que devolvió `project`, de una tabla fija de subcomandos (`diff`, `show`, `log`, `diff-tree`, `diff-index`, `diff-files`, `status`, `init`, `rev-parse`, `ls-files`, `ls-tree`, `cat-file`, `symbolic-ref`, `for-each-ref`, `rev-list`, `add`). La compuerta corre de nuevo en cada llamada. Un checkout rechazado responde con código 125, un cuelgue con 124. |
| `init(path)` | Crea un repositorio con un directorio de plantillas vacío y devuelve `project(path)`. |
| `fetch_local(lead, member, branch, member_name)` | Trae la rama del clon del miembro al lead como `refs/members/<member_name>/<branch>`, solo transporte local, sin tags ni submódulos. Ambos repositorios pasan la compuerta. |
| `declare(root, slug=..., origin_url=...)` | Declara la identidad de un proyecto una vez por proceso. También expuesto como `api.declare_project`. Es un dato: la raíz igual pasa la compuerta. |

Códigos de rechazo: `not-a-dir`, `outside-ceiling`, `no-git-below-ceiling`,
`gitfile`, `symlink`, `empty-gitdir`, `commondir`, `unreadable`,
`worktree-config`, `alternates`, `config-unreadable`, `config-key:<key>` (una
clave de config fuera de la lista permitida), `checkout-changed`, `init-failed`.

La lista permite `branch.<name>.vscode-merge-base`, que VS Code usa como
metadato para seleccionar una rama. Otras claves del editor requieren su propia
revisión.

El briefing del proyecto muestra el motivo de un rechazo de checkout sellado,
incluso cuando todavía no tiene checkpoint. El diagnóstico corresponde al
proyecto y a la respuesta actuales. También aparece en `daimon status`. Las
verificaciones de estado que necesitan Git conservan el motivo de omisión
`git-refused` cuando se rechaza el checkout.

| Host o superficie | Entrega del diagnóstico de rechazo |
|---|---|
| Claude Code, Codex, Gemini CLI | Briefing al iniciar sesión mediante la CLI compartida. |
| Kimi Code | Briefing del primer prompt mediante la CLI compartida. |
| Hermes | Preparación y renderizado compartidos en proceso. |
| API de Python, MCP | Briefing compartido para consumidores automáticos. |
| Windsurf | Briefing por CLI o MCP. La inyección automática del briefing por el hook de captura no está soportada. |

Para mantener las llamadas a git en un solo lugar, el censo se corre sobre el
propio árbol del host desde un test:

```python
from daimon_briefing import testing

testing.assert_single_git_owner(host_module_paths, allowlist)
```

`testing.git_spawn_sites(paths)` devuelve los sitios que encontró, como
`{(módulo, función, primitiva): números de línea}`.

## Cosecha de scars

Redacción opcional de conocimiento negativo (#76). Al final de la sesión, daimon
escanea el transcript en busca de lecciones con forma de scar (callejones sin
salida: "probamos X, se rompió"; rarezas que parecen intencionales y vale la
pena cercar; acoplamientos no obvios) y redacta archivos *candidatos* en
`<project_root>/.scars/candidates/`. El escaneo no usa LLM (coincidencia de
marcadores con stdlib pura, en inglés y español) y está anclado a rutas: un
acierto que no nombra ningún archivo ni directorio real en su propia oración se
descarta. Precisión sobre recall: un sistema de scars muere por ruido, no por
una lección perdida.

El límite con el proyecto [Scar](https://github.com/Daily-Nerd/Scar), dicho
claramente: **daimon solo redacta candidatos.** El lint, la inyección previa a
la edición y la promoción a scars activos son trabajo de Scar; instálalo en el
repo de destino para que los candidatos sirvan. daimon nunca escribe dentro de
`.scars/` por sí mismo, nunca sobrescribe un candidato existente (un humano pudo
haberlo editado) y limita cuántos candidatos puede emitir una sesión.

Límites honestos: los candidatos son borradores heurísticos y requieren revisión
humana antes de la promoción. La cosecha solo corre en repos que han optado por
entrar al tener un directorio `.scars/`; sin uno es un no-op silencioso aun con
la bandera activa. El texto de los candidatos pasa por la misma redacción de
secretos que los checkpoints, así que los archivos candidatos se pueden commitear.

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_SCAR_HARVEST` | off | Cuando es verdadero, redacta candidatos de scar (conocimiento negativo) al fin de sesión en `.scars/candidates/`; solo en repos con un directorio `.scars/`. |

## Escalamiento de heal

Opt-in (#360): el reintento por defecto de `daimon heal` vuelve a correr la
misma forma de extracción que ya había fallado. El escalamiento serializa de
nuevo desde varias perspectivas distintas en su lugar, fusionadas por el paso
de merge de siempre — lo que multiplica el costo de tokens del LLM
aproximadamente por la cantidad de perspectivas en tu propio backend, razón
por la cual sale apagado por defecto. Regula solo la ruta de heal: la
serialización por defecto de fin de sesión nunca escala, con o sin el flag.

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_HEAL_ESCALATION` | off | Cuando es verdadero, `daimon heal` serializa de nuevo desde varias perspectivas en vez de reintentar la misma forma de extracción. Solo en la ruta de heal. |

## Captura mientras un ledger no se puede leer

Cuando el `events.jsonl` de un proyecto no se puede leer (un marcador de
conflicto, un byte malo, un error del sistema), daimon no escribe un checkpoint
para él: la compuerta de olvidos tendría que adivinar, y un valor olvidado
podría volver. La sesión se rechaza antes de llamar al modelo, lo que no cuesta
nada. El rechazo es un serialize fallido común. `daimon status` lo cuenta desde
todo el log y nombra el remedio, el próximo briefing del proyecto dice cuántas
sesiones esperan, y `daimon heal` las reintenta de a una por inicio de sesión
cuando el ledger vuelve a leerse, sin gastar el único reintento. Un rechazo solo
se pierde cuando su transcript ya no está.

| Host | Captura rechazada |
|---|---|
| Claude Code, Codex, Gemini CLI, Kimi Code | Soportado: se cuenta, se avisa en el briefing y `daimon heal` lo reintenta mientras el host conserve el transcript. |
| Windsurf | Soportado, con un límite: sigue aplicando la limpieza propia de daimon a los transcripts de Windsurf a los 7 días (`DAIMON_WINDSURF_STATE_DAYS`), así que una sesión rechazada por más tiempo se pierde y se cuenta como irrecuperable. |
| Captura en proceso de Hermes | Soportado solo cuando el host pasa una ruta de transcript que existe. Sin ella se cuenta como irrecuperable y no se puede reintentar. |
| Pods de anamnesis | Soportado, por el mismo CLI. |

## Scene traces

Experimento opt-in (#317): el serializador pide un `scene` por ítem, una o
dos oraciones de contexto episódico, junto con el resto de la extracción.
Depende de que el harness de LongMemEval lo confirme como ganancia neta
antes de poder volverse default.

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_SCENE_TRACES` | off | Cuando es verdadero, le pide al serializador que también produzca el campo `scene` de cada ítem. |

## Operación y diagnóstico

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_LOG_DIR` | `~/.daimon/logs` | Directorio de logs. Los hooks lo resuelven exactamente igual que el CLI (entorno del proceso y después este archivo), así que `serialize.log`, los heartbeats y `serialize-crash.log` se mueven juntos: lo que un hook escribe en un directorio redirigido es lo que `daimon status`, `daimon heal` y `daimon forget` leen y purgan ahí, y nada queda en el directorio por defecto. |
| `DAIMON_CLAUDE_PROJECTS_DIR` | `~/.claude/projects` | Dónde viven los transcripts del host (`<slug>/<session>.jsonl`). Solo-lectura — la auditoría de re-verificación de citas los lee para re-revisar citas almacenadas contra su fuente. |
| `DAIMON_LOG_STDOUT` | off | Cuando es verdadero, las líneas de resultado de serialize también llegan a stdout, el stream que recolecta un runtime de contenedores. El hook de fin de sesión deja que el proceso hijo desprendido herede stdout en vez de descartarlo, y el CLI también refleja ahí sus líneas `error:`, así que éxito, omisión y error caen todos en una sola superficie. Pensado para hosts en contenedores, donde una línea escrita solo en `serialize.log` sobre un volumen es invisible para la plataforma. Apagado por defecto: en una terminal esas líneas aparecen en tu shell minutos después de que terminó la sesión. No toca stderr, que queda reservado para `serialize-crash.log`. |

## Backend LLM

La serialización necesita un endpoint de LLM. `daimon configure` es la vía
prevista para definir estas variables, y `daimon configure --init` corre el asistente guiado: backend, timeout, una prueba inmediata del backend, un recorrido opcional de memoria de equipo (autor + remoto, conectado vía `daimon team init`), y termina con el resumen de `daimon status`. Cada pregunta tiene una bandera de escape (`--backend`, `--timeout`, `--author`, `--team-remote`) para que scripts y CI lo corran sin interacción. La URL, la key y el modelo caen cada
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
| `DAIMON_RESAMPLE_MIN_SECONDS` | `DAIMON_TIMEOUT` | #553: presupuesto mínimo garantizado al remuestreo de validación al entrar — la misma falla que #341 arregló para el fallback de comando, un paso más allá. El deadline compartido puede agotarlo el intento cuya salida acaba de ser rechazada, lo que dejaría al remuestreo sin nada con qué trabajar. Cae a `timeout_seconds()` por la misma razón que `DAIMON_FALLBACK_MIN_SECONDS`. |
| `DAIMON_LLM_STREAM` | on | Transmite la respuesta de litellm para que el timeout del socket acote el intervalo entre frames y no la longitud total de la respuesta. Sin esto, una respuesta larga dispara el timeout y reintenta desde cero. Ponlo en `0` para desactivarlo. |
| `DAIMON_LLM_NO_CACHE` | off | Cuando es verdadero, evita el cache de respuestas del gateway por request — necesario cuando una respuesta mala cacheada fija una falla o cuando las corridas deben ser estadísticamente independientes. |
| `DAIMON_LLM_BRIEFING` | off | Cuando es verdadero, renderiza el briefing vía LLM en lugar de la plantilla determinista. |
| `DAIMON_LLM_COMMAND` | sin definir | Invocación completa del CLI para el backend `command` (binario + modelo + flags). Obligatoria para `command`, y obligatoria para que un `claude` en el PATH sea usado por cualquier backend que no sea `claude-cli`. |
| `DAIMON_LLM_COMMAND_OUTPUT` | sin definir | Cómo extraer el texto del asistente del stdout del comando: `text` (stdout crudo) o `json:<key>` (parsear JSON, leer `<key>`). |
| `DAIMON_LLM_COMMAND_INPUT` | `stdin` | Cómo llega el prompt al backend de comando: `stdin` (por tubería), `arg` (anexado como último elemento de argv) o `file:<flag>` (escrito a un archivo temporal, luego se anexa `<flag> <path>`). Un valor no reconocido registra una advertencia y cae a `stdin`. `arg` está limitado a 100.000 bytes por el sistema operativo. Para el primario es un tope duro sobre cada prompt: uno más grande falla antes de llamar a ningún backend, nombrando esta variable. `daimon configure` nombra un backend en modo `arg`. |
| `DAIMON_LLM_COMMAND_FALLBACK` | sin definir | El único CLI de rescate, usado cuando el backend primario falla. Sirve tanto para un primario litellm como para uno `command`, que antes no tenía ninguna dirección de rescate. Un solo fallback, nunca una cadena: si el primario y este fallan, la causa casi siempre es del entorno, y un tercer CLI gasta presupuesto para llegar al mismo error mientras hace que la instalación parezca más protegida de lo que está. Si no se define, un primario litellm sigue cayendo a `DAIMON_LLM_COMMAND` como antes. |
| `DAIMON_LLM_COMMAND_FALLBACK_OUTPUT` | sin definir | Especificación de salida del CLI de rescate, misma gramática que `DAIMON_LLM_COMMAND_OUTPUT`. Se lleva por separado porque el rescate es otro binario. |
| `DAIMON_LLM_COMMAND_FALLBACK_INPUT` | `stdin` | Especificación de entrada del CLI de rescate, misma gramática que `DAIMON_LLM_COMMAND_INPUT`. El límite de 100.000 bytes de `arg` también aplica al rescate, pero solo como límite blando: los merges se ajustan a él (un árbol de merge más profundo) y un prompt de chunk o de perspectiva más grande corre en el primario sin rescate, con una advertencia que nombra esta variable. |

:::note[Qué proceso recibe tu transcripción]

Un binario `claude` que simplemente esté en el PATH **no** se adopta automáticamente. Serializar envía la transcripción completa de la sesión al CLI configurado, así que ese CLI debe nombrarse: o `DAIMON_LLM_COMMAND`, o `DAIMON_LLM_BACKEND=claude-cli` para optar por el preset incorporado.

Antes bastaba con dejar `DAIMON_LLM_COMMAND` sin definir y tener un `claude` en cualquier parte del PATH, tanto para instalaciones en `auto` como para la ruta de rescate de litellm. Si dependías de eso, definí una de las dos variables de arriba. `daimon configure` nombra el binario resuelto y su ruta para que puedas ver exactamente cuál está en uso.

El preset `claude-cli` corre su hijo `claude -p` con `--strict-mcp-config`, así que no carga ningún servidor MCP. Sin esa bandera, el lanzador propio del hijo sale temprano bajo el entorno de aislamiento que define el serializador, y Claude Code lo registra como una falla de conexión, así que toda sesión nueva de la máquina saltea el servidor MCP de daimon durante los siguientes 15 minutos (#1077). Si apuntás un `DAIMON_LLM_COMMAND` a `claude` vos mismo, agregale `--strict-mcp-config` también: el serializador solo convierte texto en JSON y nunca necesita una herramienta MCP.

El preset también pasa `--no-session-persistence` cuando tu `claude` lo lista en `claude --help` (se verifica una vez por proceso), así que una llamada al modelo no deja ninguna sesión en el almacén de Claude Code; `daimon configure` avisa si tu `claude` es demasiado viejo para eso. Las llamadas hechas antes quedaron guardadas en `~/.claude/projects/`, en directorios cuyo nombre contiene `daimon-cli-`; borrarlas queda a tu criterio.

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
| `DAIMON_MERGE_GROUP_SIZE` | `3` | Máximo de checkpoints parciales fusionados por llamada de merge jerárquico. Mínimo 2. Un grupo se divide más si su request excedería el límite de entrada en modo `arg` del primario o del rescate. Bájalo a `2` si las llamadas de merge mueren en un gateway con techo de request del lado del servidor (los modelos de razonamiento generando merges de 3 vías pueden excederlo; subir `DAIMON_TIMEOUT` no ayuda — el kill es del lado del servidor). |
| `DAIMON_CHUNK_CACHE` | on | #48: cache de contenido-direccionado de la salida de extracción por chunk, indexada por los bytes del propio chunk, para que un reintento de heal o retry vuelva a pagar una llamada solo cuando el chunk de origen cambió de verdad. Interruptor de apagado — activo salvo que valga `0`. |
| `DAIMON_CHUNK_CACHE_DAYS` | `3` | Ventana de rotación del cache de chunks, en días. La salida cacheada es previa a la redacción (lo exige el orden de verificación de citas de #125), así que la ventana es tanto un límite de privacidad como de disco — 3 días cubre la ventana de heal/retry manteniendo la exposición corta. |
