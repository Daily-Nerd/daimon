---
sidebar_position: 10
description: "Comportamiento de los comandos de daimon más allá de sus flags: rechazos, códigos de salida, qué escribe cada comando y cómo se comportan los paneles de pedidos."
---

# Notas de comandos

Lo que hace cada comando más allá de sus flags. Los flags, valores por defecto y uso de cada comando están en las [páginas de grupo generadas](index.md).

## Preparar

### `daimon configure`

Detecta el backend LLM resuelto y completa los huecos en `~/.daimon/env`. `--test` corre un round-trip real.

### `daimon hooks install [host]`

Instala los hook scripts del host (Windsurf, Codex, Kimi) desde el paquete. Sin host, daimon detecta qué corre esta máquina, imprime una línea por host con el canal que lo atiende, e instala: en silencio si hay uno solo, con una confirmación si hay varios, y nunca sin terminal salvo que pases `--all`. También rearma cada capa de reglas por encima de este proyecto, así que un check ratificado en una capa por encima arma acá también, incluso en una máquina que nunca sincronizó esa capa directamente; la línea de una capa se imprime solo cuando esa capa armó algo. `remove [host]` desregistra lo que daimon registró, y `--all` cubre todos los hosts detectados. `list` / `status` inspeccionan; `status` también audita el manifiesto de checks de este proyecto contra su ledger, y sale distinto de cero ante cualquiera de las dos derivas.

### `daimon skill install [host]`

Instala la skill de agente de daimon en el directorio de skills del host. Con la misma detección que `hooks install` cuando no nombrás un host, y el mismo `--all`. Volvé a correrlo después de cada upgrade.

### `daimon skill status`

Audita cada skill instalada contra lo que este CLI escribiría ahora: CURRENT, STALE, MISSING, BROKEN, UNREADABLE, NOT INSTALLED, PLUGIN o LEFTOVER, por host y alcance, tanto para la skill generada `daimon` como para la empaquetada `daimon-end`. Sale distinto de cero ante deriva, `--json` para máquinas. La skill es una copia estática de contenido que se mueve con el CLI, así que así te enterás de que faltó re-correr install después de un upgrade. Un host atendido por el plugin de daimon reporta PLUGIN y no necesita nada; un archivo que quedó abajo de uno reporta LEFTOVER, y su reparación es `uninstall`, no `install`.

### `daimon check sync`

Reconstruye `~/.daimon/checks`, el manifiesto de checks armados que lee un hook del anfitrión, y un archivo por cada cuerpo de check, desde el ledger de este proyecto, y desde cada capa de reglas por encima de él, así que el check de una capa rearma acá sin que un humano tenga que correr este comando desde adentro del proyecto de esa capa en esta máquina. Cada ratify, revise, retire y forget ya lo hace, así que correlo solo cuando el manifiesto se dañó por fuera. Es seguro repetirlo: un manifiesto que no cambió no se reescribe. Imprime cuántos checks quedaron armados, cero incluido, con una línea más por cada capa que armó algo o que no pudo sincronizar. `--check` audita en lugar de reconstruir y no escribe nada, calificando cada capa por encima de este proyecto de la misma forma: salida 0 si está al día, 1 si hay deriva (de este proyecto o de una capa), 3 si un manifiesto existe y no se puede leer. `--json` para máquinas, con una clave `layers` presente solo cuando alguna capa aportó algo.

### `daimon heal`

Re-serializa la última sesión fallida cuando es seguro hacerlo. Una sesión rechazada porque el ledger de eventos de su proyecto no se pudo leer queda retenida, con el motivo y qué ejecutar, y no gasta su único reintento. Cuando ese ledger vuelve a leerse, cada ejecución toma una de ellas. Una sesión cuyo proyecto no se puede recuperar, para una transcripción archivada bajo un directorio de proyecto, se omite con ese motivo en lugar de guardar su checkpoint en el directorio global; `daimon serialize <transcript> --project <dir>` la archiva. Un reintento escribe su proyecto y su transcripción en su propia línea del log, así que un log muy activo ya no los pierde.

### `daimon import --project <dir> [--sessions N] [--transcript RUTA ...]`

Serializa transcripciones pasadas de un proyecto en una sola corrida acotada, el primer llenado de un proyecto que daimon todavía no capturó. Sin `--transcript` descubre las transcripciones del proyecto en disco bajo las raíces de Claude Code y Codex, comparando el directorio de trabajo que cada archivo registró (el nombre del directorio de Claude Code es un slug con pérdida y nunca es la coincidencia), y se queda con las N más nuevas por fin de sesión (`--sessions N`, por defecto 5). Los saltos que se nombran abajo salen de esas N y ninguna más vieja ocupa su lugar, así que el mismo comando reconstruye el mismo plan y podés leer qué archivos toca una corrida; el plan da después cuenta de cada transcripción que leyó, una línea por host (`claude-code: 5 of 14 transcripts under ~/.claude/projects recorded this project (7 recorded another project, 2 recorded no directory)`, o `codex: no transcripts under ~/.codex/sessions, ~/.codex/archived_sessions`), solo conteos y nunca el nombre de otro proyecto, así que un plan vacío dice si el proyecto nunca se registró, si el host borró los archivos o si los archivos registran otro directorio; cuando hay coincidencias más allá de las N agrega `window: 3 newest of 41 matched planned or skipped; raise --sessions to reach older history`, y `window: all 3 newest of 41 matched already captured; raise --sessions to reach older history` cuando las N ya están capturadas. Una raíz de host que el usuario no puede listar es una línea del plan (`codex root unreadable: <ruta>`) y las otras raíces siguen contando. El plan nombra después los hosts a los que el descubrimiento no llega, en una línea: `not discovered: windsurf, kimi (records no project, pass --transcript); gemini (no registered root); hermes (no transcript file)`. Windsurf y Kimi no registran proyecto, así que esos entran por `--transcript`. Gemini CLI escribe una transcripción pero daimon no registra una raíz para ella, y Hermes no escribe archivo de transcripción, así que ninguno de los dos se puede importar todavía, ni por descubrimiento ni por `--transcript`; un archivo de Gemini dado a mano se rechaza por nombre, con salida 2. Dar `--transcript` y `--sessions` juntos es un error de uso. Imprime el plan antes de cualquier llamada al modelo: cada sesión con su fecha, cantidad de mensajes y aproximadamente cuántas llamadas costará (una por fragmento de la transcripción renderizada, más las fusiones; un remuestreo por validación o un rescate en el backend de respaldo agrega llamadas que el plan no puede ver), el total, una nota cuando la sesión termina antes de la última captura en vivo del proyecto (se archiva como historia, `latest` no cambia, nada se arrastra) o cuando el proyecto vino de la bandera, y cada transcripción dejada afuera con su motivo: ya tiene checkpoint, ya fue capturada (una línea de éxito en `serialize.log`, que sobrevive a un checkpoint podado), la sesión en vivo o un archivo modificado en los últimos diez minutos, demasiado corta, ausente, ilegible. `--plan` se detiene ahí. Los rechazos salen con 2 antes de gastar: una transcripción cuyas filas registran otro directorio de trabajo, una que no registra ninguno y está fuera de la raíz de Claude Code del proyecto (una transcripción de Windsurf o Kimi no registra ninguno y se acepta solo cuando `--project` se escribe en la línea de comandos, porque nada en el archivo puede confirmarlo), dos archivos que nombran una misma sesión, el interruptor de apagado, un ledger de eventos que no se puede leer. La corrida va de la más vieja a la más nueva, escribe un marcador `import:` en `serialize.log` antes de cada serialize, y se detiene antes de la sesión cuyas llamadas planificadas llevarían el gasto por encima de `--max-calls` (por defecto: el total planificado; el gasto es lo que dice el sello `capture_cost.calls` de cada checkpoint escrito, así que una corrida supera el número impreso solo por los reintentos y el rescate de una sola sesión), con salida 0 y una pista para reanudar, o en el primer serialize fallido de cualquier tipo, con salida 1 y la pista. Cada línea informa los tokens que registró el sello, o `tokens unreported`, las citas que la verificación degradó, y el marcador de respaldo; el resumen se imprime en cada salida después de gastar, con las llamadas gastadas junto a las planificadas, y nombra cada sesión que produjo el respaldo. Volver a correr el mismo comando reconstruye el plan y salta las sesiones ya hechas sin costo; una sesión se paga dos veces solo si `serialize.log` se borra o `DAIMON_LOG_DIR` cambia mientras su checkpoint también desapareció. Los checkpoints llevan `capture_door: "import"` y sus líneas de éxito terminan en `[import]`; `daimon heal` nunca reintenta una importación, aunque el barrido de inicio de sesión todavía puede lanzar una importación de Claude Code o Codex que murió, dentro de su ventana de 14 días, una vez que su latido envejeció.

### `daimon mcp serve`

Sirve las herramientas de daimon por MCP (stdio).

## Brief

### `daimon brief`

Renderiza el briefing del último checkpoint, dónde quedaste, con etiquetas de confianza. `--team` suma lo último del equipo; `--slug <s>` lee el bucket de otro proyecto explícitamente. El briefing también lleva una línea con el conteo de decisiones que están esperando por vos, por ejemplo `3 decisions waiting on you here (2 elsewhere) - daimon decide`, que apunta a `daimon decide`.

### `daimon recall "consulta"`

Búsqueda full-text sobre el historial local + de equipo. `--json` para filas, `--all-projects` para ampliar.

### `daimon handoff "Hacé X primero. Ojo con Y."`

Deja un batón autoral para la próxima sesión, se renderiza arriba de todas las secciones del briefing y nunca compite con ítems rankeados. `--clear` lo retira; uno nuevo reemplaza al anterior.

## Comprobar

### `daimon why <item-id>`

El inspector de confianza: muestra cada eje de evidencia detrás de un ítem: captura independiente, procedencia, fuente, integridad de bytes, soporte actual, resultado del chequeo de citas, ciclo de vida, corroboración. `--source` agrega una ventana de fuente acotada y redactada, y se rechaza cuando algo fue olvidado en la máquina, este proyecto tiene una cuarentena o el registro de confianza no se puede leer; `--json` para máquinas; `lifecycle_event` trae el `event_id` de la fila en la que se asentó el pliegue del ciclo de vida, el mismo id que imprime `resolve`, así un host distingue su propia escritura de una anterior sobre el mismo ítem (una fila escrita antes de que existieran los ids da `null`). Un id que el lector no puede ver imprime `[withheld: quarantine …]`, `[withheld: forgotten]` o `[withheld: trust ledger unreadable]` en lugar de su valor. Los ids de ítem salen de `daimon recall` o `daimon loops`.

### `daimon diff`

Qué cambió entre dos checkpoints retenidos de este proyecto: ítems agregados, reformulados, re-etiquetados, y cada ítem que la cadena dejó de arrastrar con el motivo que da el registro (resuelto, reemplazado, o descartado cuando nada lo explica). Un ítem retenido por una cuarentena o por un registro de confianza ilegible tiene una fila `withheld` que dice en qué generación está retenido; un ítem olvidado no tiene fila. Por defecto usa el último par legible; `--from N --to M` cuenta generaciones hacia atrás, donde 0 es `latest.json`. `--json` para máquinas. Es solo lectura y no hay restauración: un checkpoint registra lo que hizo una sesión, así que restaurar uno sería afirmar un pasado distinto. Para decir que algo cambió, usá `resolve`, `reverify` o `forget`.

### `daimon blame <item-id>`

Cómo llegó hasta acá un ítem: la sesión que lo enunció primero, cada arrastre desde entonces, y cada evento que cambió su estado, en orden. `daimon why` responde de dónde vino una afirmación; este responde cómo llegó. Un ítem olvidado o en cuarentena imprime su marcador y nunca su texto. `--json` para máquinas.

### `daimon verify-receipt`

Verifica el recibo firmado de procedencia de un checkpoint (chequeo criptográfico completo vía el CLI de vitni).

### `daimon reverify <id>`

Afirma que un ítem arrastrado sigue siendo cierto: exige evidencia y reinicia su reloj de vencimiento. También es la mitad de rechazo de un candidato a supersesión. Solo id exacto, y es un comando de una persona. Un id que el briefing retiene porque una persona puso su valor en cuarentena se asocia solo desde la terminal de una persona, exige `--evidence` e imprime el marcador en lugar del texto. Un id retenido porque el ledger de confianza no se puede leer se rechaza con la cura (`daimon trust repair`, salida 2), y un id olvidado no se encuentra. Rechaza con salida 2 mientras el ledger de eventos del proyecto no se puede leer.

### `daimon audit quotes`

Re-verifica cada cita verbatim almacenada contra su transcripción de origen y reporta discrepancias. Solo lectura: nunca reescribe etiquetas. Audita lo que un lector puede ver, así que un ítem en cuarentena u olvidado no se verifica, no se cuenta y no se imprime, y mientras el ledger de confianza no se puede leer no verifica nada. Salida 0 si verificó y salió limpio, 1 si hay discrepancia, 3 si no había nada verificable. Siempre reporta cuántos ítems tuvo que saltear, y cuando son todos lo dice en lugar de imprimir una tasa sobre nada. `--all` recorre todos los proyectos que podés leer. `--json` para máquinas, y lista todas las discrepancias, mientras que las líneas impresas se cortan en `--top`.

### `daimon audit privacy`

Prueba el contrato de borrado: hashea cada campo con texto plano en cada superficie (checkpoints, punteros rotados, el registro de eventos, el espejo de equipo, el índice de recall y sus snapshots huérfanos) y reporta todo valor olvidado que haya sobrevivido. Solo lectura. Un valor que solo un compañero de equipo olvidó y que todavía está en texto plano en esta máquina se reporta como `SUPPRESSED-PRESENT`, y esta clase nunca cambia el código de salida, porque limpiarlo es opcional a través de `DAIMON_TEAM_APPLY_FORGET`. Cuando el ledger de eventos de algún proyecto no se puede leer, el conjunto de olvidos puede estar incompleto: la verificación del índice de recall no puede probar su resultado, la auditoría lo dice y sale con 3.

### `daimon refute list|show|search|guard`

Lee el ledger de conocimiento negativo sin decaimiento. `guard` emite solo matches activos por ancla exacta o frase de sujeto; es consultivo y nunca bloquea un comando. `search` devuelve ambas polaridades, etiquetadas, y solo coincide con valores que el lector puede ver; `list` y `guard` quedan solo para refutaciones. Sumá `--json` para integraciones de deliberación.

### `daimon ruling list|show`

Lee las reglas vigentes: restricciones positivas ratificadas por humanos en el mismo ledger, que nunca decaen ni se re-extraen. `show` incluye propuestas de agentes pendientes, y también resuelve contra una capa de reglas por encima de este proyecto, así que un id heredado muestra la regla y nombra su capa en lugar de leerse como desconocido. `list` se queda en el propio bucket de este proyecto salvo que pases `--inherited`, que suma las reglas activas heredadas de una capa por encima (cada fila en `--json` lleva `inherited_from`); se rechaza bajo `DAIMON_TENANT_SCOPED`, igual que cualquier otro alcance entre proyectos elegido por quien llama. Un `list` que no encuentra nada sale con 1 y nombra el bucket en stderr cuando el proyecto nunca fue escrito, y sale con 0 cuando el proyecto tiene un bucket sin reglas.

### `daimon ruling checks`

Qué tiene armado este proyecto: una fila por cada regla que lleva un check, cruzada con cada anfitrión, incluido el check activo de una regla heredada de una capa por encima de este proyecto (con la etiqueta de esa capa). Cada fila nombra el ciclo de vida del check, la intención que pidió su autor, el modo que ese anfitrión entrega de verdad, cuándo corrió por última vez, y sus conteos de clean / violation / unresolved sobre la ventana que el log todavía conserva. Una fila que no pudo correr termina después de su modo, porque un check propuesto o desarmado no arma nada y un anfitrión que no puede entregarlo no tiene canal por donde correrlo. Las líneas de encabezado reportan el estado del manifiesto, el estado del propio log de corridas, la marca de tiempo donde arranca la ventana conservada, y cada anfitrión donde el hook alguna vez corrió. Esas líneas de anfitrión llevan la etiqueta `(any project)`: las filas detrás de ellas no llevan proyecto, así que que el hook esté vivo es un hecho de esta máquina, nunca de este proyecto. Es de solo lectura, y una respuesta vacía sale con 0. `--json` para máquinas.

### `daimon ruling check try <id> --command "<cmd>"`

Corre el check de esa regla contra una línea de comando que vos nombrás e imprime el resultado. También resuelve un id heredado de una capa por encima de este proyecto, y en ese caso nombra la capa. No arma nada, no registra nada, y materializa el cuerpo fuera del directorio de checks. `--cwd` fija el directorio de trabajo contra el que resuelven las rutas relativas; `--proposed` corre el cuerpo de una revisión de agente pendiente en lugar del armado. Solo vía humana, como `ratify`: ejecuta el cuerpo, así que hace falta una terminal interactiva y `--by agent` se rechaza. Mismo contrato de salida que los auditores de abajo.

### `daimon serve`

Abre el [visor local de solo lectura](../viewer.md) en localhost, búsqueda como recall, páginas "why" por entrada, refutaciones, diff, check strip, vista de impresión. Nada escribe.

### `daimon relations list|show|confirm|reject|retract`

El [ledger de relaciones tipadas](../relations.md): las máquinas proponen, solo una persona confirma, y decidir necesita una terminal interactiva. Los candidatos nunca se renderizan en la superficie de una entrada. `list` y `show` imprimen el texto de cada extremo, o su marcador de retenido cuando una persona lo puso en cuarentena o el ledger de confianza no se puede leer, y omiten sin contarla una arista que toca un ítem olvidado.

## Corregir

### `daimon resolve <id o texto>`

Marca un ítem como resuelto con un evento append-only; el ítem deja de arrastrarse. El evento guarda el id y el estado, nunca el texto del ítem. `--dry-run` previsualiza el match; `--by agent --evidence "<cita>"` reclama un cierre que se verifica byte a byte al final de la sesión. Una consulta de texto se asocia solo cuando coincide exactamente un ítem visible y ningún ítem retenido coincide también. Si no, lista los candidatos visibles y una línea que cuenta los retenidos (`N withheld item(s) also match; use the exact id`, y `daimon status --suppressed` los lista). Un ítem olvidado nunca es un destino. Un ítem en cuarentena se asocia por id exacto solo desde la terminal de una persona y nunca se repite en pantalla; a un agente se le rechaza (salida 2). Mientras el ledger de confianza no se puede leer se rechaza todo id con la cura (salida 2), y un ledger de eventos ilegible rechaza la escritura antes de buscar nada. Imprime `event: <id>` de la fila que escribió, o con `--json` imprime `{item_id, status, event_id}`.

### `daimon trust propose|confirm|dismiss|release|republish|list|show|repair`

Pone en cuarentena un valor del checkpoint que no es seguro usar como base para actuar; mirá [cuarentena](../../concepts/trust-classes.md#cuarentena). `propose --text "…" --kind <belief\|contradiction\|decision\|question\|topic\|uncertainty> --reason "…" --evidence <tipo:ref>` desde un agente (`--by agent`) queda como candidata, y desde una persona en una terminal queda activa de inmediato. `confirm` y `dismiss` deciden una candidata, `release` levanta una cuarentena activa y `republish` vuelve a enviar las cuarentenas de este proyecto al sidecar del equipo: todos son solo para humanos y rechazan `--by agent`. `list` muestra primero las candidatas (`--team` lista lo que publicaron tus compañeros, `--json` para máquinas), `show <tr-id>` imprime un registro y `repair` es el alias de `daimon ledger repair trust`.

### `daimon anchor <archivo> <símbolo>`

Ancla un ítem cognitivo a un símbolo de código; los briefings avisan cuando el código anclado cambió. `--attach <texto>` busca solo entre los ítems visibles (el tema incluido) e ignora por completo los retenidos, así que una aguja que coincide con un ítem en cuarentena no nombra ni cuenta nada. La reescritura conserva cada otro ítem byte a byte.

### `daimon refute add|ratify|revise|overturn`

Gestiona conocimiento negativo con alcance en su propio ledger append-only. Las escrituras de agentes quedan como candidatas; solo una ratificación humana explícita activa un guard, y `ratify` exige la vía humana: una terminal interactiva y `--by` omitido. Las revisiones exigen una cita de evidencia tipada nueva, cuya forma se valida pero nunca se resuelve ni se verifica, y devuelven una refutación activa a candidata hasta que se vuelva a ratificar. Los overturns de agentes siguen siendo propuestas.

### `daimon ruling propose|ratify|revise|retire`

Gestiona reglas vigentes en el mismo ledger, con un ciclo más estricto: `ratify` muestra el texto completo, avisa que va a renderizarse en cada sesión futura y ata la activación al texto mostrado; un humano que revisa una regla activa confirma el cambio y la regla sigue activa; los revise y retire de agentes registran propuestas mientras el texto queda en pie; la activación se rechaza pasado el tope (`DAIMON_RULING_CAP`, por defecto 7). Retirar no exige cita de evidencia. `propose` y `revise` pueden adjuntar un check con `--check-body-file`, `--check-match` y `--check-intent`: un script cuyos bytes quedan guardados en la regla (una ruta se rechaza), un patrón sobre la línea de comando que elige las acciones antes de las cuales corre, y qué le pide a cada anfitrión (`warn` por defecto). El check de una candidata se lee como propuesto, no armado, y ningún anfitrión lo ejecuta. `ratify` muestra el check y ata la activación al cuerpo que mostró, por hash, y lo materializa para que un anfitrión pueda correrlo. Un cuerpo que nombra una ruta local del anfitrión en cualquier línea se rechaza, porque el check viaja con la regla y una ruta fuera de él no existe en ninguna otra máquina. Ver [Checks en ejecución](../check-runtime.md). `propose` y `revise` también pueden llevar un permiso `--request-policy`, en una de dos formas (repetible, las cuatro claves son obligatorias, según el verbo, y mezclar las dos se rechaza): `--request-policy sender=<slug> kind=work verb=accept by=agent` deja al agente de ese proyecto emisor registrar `accept` sobre una petición de tipo `work` que este proyecto le debe; `--request-policy to=<slug> kind=info verb=open by=agent` deja que el propio agente de ESTE proyecto abra una petición hacia ese destinatario como `kind=info`, sin que ninguna persona la toque (sin comodín en `to`). `ratify` ata la activación al permiso que mostró, igual que hace con un check, y `revise --no-request-policy` lo borra.

### `daimon amend propose <item-id> --change progressed|blocked|changed --evidence "<cita>"`

Registra una transición de estado con evidencia sobre un ítem abierto del briefing: el pendiente sigue abierto, lo que cambió es su estado. Un ítem olvidado nunca es un destino. Un ítem en cuarentena, o cualquier ítem mientras el ledger de confianza no se puede leer, se rechaza por todos los canales (`<id> is withheld; a human decides`, salida 2). `--evidence` es una cita textual de la transcripción, verificada byte a byte contra la transcripción de esta sesión al final de la sesión, y nada más. La propuesta de un agente queda invisible en el briefing hasta que pasa esa verificación, y desde ahí se renderiza marcada y sin confirmar, con su propio par confirmar/rechazar; solo un `ratify` humano la renderiza como asentada. `ratify`/`reject` toman uno o más ids, todo o nada si alguno es desconocido o está en el estado equivocado. `list` lista cada enmienda de este proyecto, primero las candidatas.

## Olvidar

### `daimon forget <id o texto>`

Elimina el contenido de un ítem del disco y del índice, dejando una lápida de solo-hash. La eliminación sobrevive a re-serializar la transcripción original. Primero comprueba que el ledger de eventos del proyecto se puede leer y sale con 2 sin tocar nada cuando no, porque el tombstone es lo que hace que el olvido valga en todos los demás lugares. Después de limpiar lista cada superficie que no pudo garantizar (un ledger con texto plano y líneas rotas o bytes ilegibles, un sidecar de cuarentena, un ledger que no pudo reescribir o una publicación al equipo que falló) y sale con 4: limpiado, con superficies sin alcanzar. Un ledger que no guarda texto plano solo se anota. La salida 1 sigue siendo para las fallas previas a escribir algo. Los candidatos se revisan como en cualquier otra lectura. Una consulta de texto se asocia a un único valor visible y a nada más. Un candidato en cuarentena o retenido por confianza ilegible es una línea de conteo que apunta al id exacto, uno olvidado no se lista, y una consulta sin resultado imprime un puntero en lugar de listar todo lo que hay. Un id exacto de un ítem en cuarentena se asocia solo desde la terminal de una persona; mientras el ledger de confianza no se puede leer, un id exacto sigue funcionando por todos los canales, porque la promesa de borrado pesa más que la falla. `--dry-run` imprime `would forget <id>: <texto>` para un destino visible y el marcador para uno retenido. Cualquier superficie que no pudo alcanzar se lista como `unreached:` con su cura, y la ejecución sale con 4: un ledger con texto plano, un sidecar de cuarentena, una caché, un log, las copias del checkpoint, el mirror del equipo, el índice de recall o una publicación al equipo que falló. `daimon forget --resume` no lleva destino (darle uno es un error de uso, salida 2) y necesita una terminal. Termina todas las fases de cada olvido ya registrado, escribe la fila de clave que le falta a un olvido hecho antes de esta versión e imprime una línea con la cuenta, y sale con 4 por lo que aún no puede alcanzar. `--only <fase>` ejecuta una sola fase y `--dry-run` muestra una vista previa sin escribir. Una copia del checkpoint o del mirror del equipo que no se puede interpretar mantiene `forget` en salida 4 hasta que se arregle el archivo a mano, y `--resume` lo informa en cada ejecución. `daimon forget --republish` es su fase de publicación, `--resume --only publish`: publica cada olvido local que le falte al sidecar del equipo, una vez cada uno, y sale con 4 cuando ese sidecar no se puede leer o supera su tope de tamaño.

### `daimon unforget <id>`

Levanta un olvido en esta máquina, solo desde una terminal (no tiene canal de agente). Toma el id exacto que imprimió el recibo del olvido y levanta cada referencia que lleva la clave en un solo append. No restaura nada; una captura nueva de la afirmación se admite de nuevo. Sale con 0 solo cuando nada más retiene el valor; si no, sale con 4 nombrando la clase (otro proyecto de esta máquina, una fila publicada de un compañero, o un ledger que no pudo probar). La fila que publicaste se queda, porque una fila de tombstone no tiene retractación. `--reason` se registra en las filas del levantamiento y `--dry-run` cuenta las referencias sin escribir.

## Coordinar

Una solicitud vive en el bucket del proyecto que la envía; el destinatario
responde con filas de decisión en su propio bucket. El registro combinado
es un join en tiempo de lectura, nadie escribe jamás en el ledger de otro
proyecto.

### `daimon request open --to <dir> --ask "…" --why "…" [--title "…"]`

Pide algo a otro proyecto. `--to` toma el **directorio** del proyecto destinatario, no su slug (un slug real empieza con `-`, que argparse lee como una opción, `--to=<slug>` también funciona). Se valida contra `daimon projects`, con sugerencias por parecido ante un typo; `--anyway` registra el pedido igual contra un proyecto que nunca serializó en esta máquina. `--blocking` y `--to-human` son flags del registro. `--title` es un titular opcional de hasta 80 caracteres (si es más largo se rechaza, no se corta) que encabeza el pedido donde se lista; el ask sigue siendo el texto completo, y un pedido sin título muestra la primera oración de su ask. `--kind work\|info` fija el requisito de aprobación: `work` (el default) espera una decisión de una persona, `info` se puede responder con los propios artefactos del destinatario y no debe un accept. Solo un canal humano puede abrirla como `info`. El resto, cualquier canal. `--severity critical\|high\|low` junto con `--severity-reason "…"` (una línea, 80 caracteres como máximo, se rechaza si se pasa y nunca se corta; obligatorio con cualquier nivel salvo el default) dice cuánto importa el resultado para quien pide, así que las listas y los paneles del destinatario ordenan por eso. No es `--blocking`, que dice que estás frenado. Si se omite es `normal`, que no muestra nada. Cualquier canal.

### `daimon request revise <id> [--ask] [--why] [--evidence] [--title]`

Responde un needs-info, o afina una solicitud abierta. Revisar conserva el título salvo que se pase `--title`; `--title ""` lo quita. Cualquier canal; tope de 3 revisiones por registro, superado el tope, se abre una nueva solicitud con `--supersedes <id>` para mantener visible el linaje.

### `daimon request withdraw <id> --reason "<por qué>"`

Retira una solicitud que envió este proyecto. Cualquier canal, porque es un acto propio del remitente y no una decisión del destinatario. Pasa una solicitud abierta, en needs-info, aceptada o con un reclamo pendiente al estado terminal `withdrawn`, con `withdrawn_by`, `withdrawn_at` y el motivo en `note`. Sobre una solicitud ya terminada, rechazada o retirada no cambia nada y dice por qué. Es definitivo para ese registro; abrí una solicitud nueva con `--supersedes <id>`. Un destinatario que había aceptado el pedido ve una línea, `withdrawn by the sender: <motivo>`, por la entrega en vivo. Solo el remitente puede correrlo, y el `reject`, `accept`, `needs-info`, `suppress` y `done` del remitente sobre su propio pedido se rechazan: cada evento pertenece a un rol, y una fila del lado equivocado queda inerte.

### `daimon request severity <id> <nivel> --reason "<por qué>"`

Cambia cuánto importa para quien pidió una solicitud que envió este proyecto (`critical`, `high`, `normal`, `low`; `normal` borra un nivel elevado y no lleva motivo). Cualquier canal. Es un evento propio, no una revisión: no mueve el estado, no gasta ninguna de las 3 revisiones, no descarta un reclamo de finalización y no manda aviso en vivo. El nuevo nivel aparece en el próximo brief del destinatario, en `inbox` y en `daimon decide`. Funciona mientras la solicitud está abierta, en needs-info o aceptada, y se rechaza sobre una terminada, rechazada o retirada, y cuando no cambiaría nada. Solo el remitente puede ejecutarlo.

### `daimon request accept|reject|needs-info <id> [--note]`

Registra una decisión. Solo humano: requiere una terminal interactiva. Un agente puede correr `accept --by agent` para una solicitud dirigida (`info` o `work`) que sigue abierta o en needs-info solo si una regla vigente en este proyecto (`verb=accept`) cubre a ese remitente puntual y ese tipo, y entonces el registro nombra la regla. Sin una regla así, el accept lo hace una persona, tanto para `info` como para `work`. `reject` es definitivo para ese registro; el remitente reemplaza con una nueva solicitud en vez de volver a pedir.

### `daimon request suppress <id> [--note]`

Saca una solicitud del panel de briefing propio del destinatario. Solo humano; el registro sigue en `list`/`inbox`, y cualquier decisión posterior lo revierte.

### `daimon request done <id> --evidence "<cita>"`

Reporta la solicitud como satisfecha. Cualquier canal; el reclamo de un agente se renderiza como `done (claimed, unverified)` hasta que el próximo fin de sesión del destinatario verifica byte a byte la cita de evidencia contra su transcripción. Un `done` humano se renderiza sin más. En una solicitud `work` que nadie aceptó todavía, el `done` de un agente registra el reclamo y deja la solicitud en la cola de decisión, a la espera de `accept` o `reject`, en lugar de cerrarla. Una solicitud que se le ofreció a este proyecto y que todavía no aceptó está dirigida a él, así que ese reclamo se retiene por diseño, no se rechaza. Un id que no es una solicitud dirigida a este proyecto (un error de tipeo, o una solicitud pensada para otro proyecto) se rechaza con salida 1 y no se escribe nada; una persona sí puede decidir desde cualquier directorio una solicitud dirigida a otro, salvo en un home con alcance por tenant. Cuando no se puede leer el ledger de un remitente, el rechazo lo dice (corré `daimon status`) en vez de llamar desconocido al id. Imprime `event: <id>` de la fila que escribió, o con `--json` imprime `{request_id, event, event_id}`.

### `daimon request reply <id> --note "<texto>" [--evidence "<puntero>"]`

Le manda al remitente una nota de avance sobre una solicitud que aceptaste, sin cerrarla. Cualquier canal; la nota de un agente le llega marcada como `Reply (agent, unverified)`. No mueve ningún estado y solo funciona sobre una solicitud aceptada; en cualquier otro estado se rechaza con el verbo que corresponde (`accept --note`, `needs-info --note`, `done`). Ejecutado desde el proyecto que envió la solicitud, se rechaza y lo dice: solo responde el destinatario. El remitente ve la última respuesta en el briefing y en el aviso en vivo, incluso cuando la decisión ya salió del panel. Igual que `done`, imprime `event: <id>` o, con `--json`, `{request_id, event, event_id}`.

### `daimon request list`

Las solicitudes enviadas por este proyecto, primero las que siguen sin decidir. Un destinatario cuyo ledger de solicitudes no se puede leer se omite y se cuenta en una línea de aviso (por stderr con `--json`). Cada fila muestra cuándo se movió por última vez y `OVERDUE` cuando nada la movió durante `DAIMON_REQUEST_OVERDUE_DAYS` días (por defecto 7). `--json` para máquinas, y cada fila suma `last_movement_at`, `age_seconds` y `overdue`, además de `updated_event_id`, el id de la fila que la movió por última vez (el id que imprimió el verbo de escritura; `null` en un registro cuya última fila es anterior a los ids). `suppress` y `severity` también imprimen un id, pero nunca mueven el registro, así que ese id queda en la fila del ledger y en `row_effects` del registro, no en `updated_event_id`. `--min-severity <nivel>` conserva ese nivel y los más severos; dentro de un estado, las filas se ordenan por severidad antes que por antigüedad.

### `daimon request inbox`

Solicitudes que otros proyectos dirigieron a este, de cualquier remitente, primero las que siguen sin decidir, incluidas las que el panel de briefing dejó fuera de la atención. Un remitente cuyo ledger de solicitudes no se puede leer se omite y se cuenta en una línea de aviso (por stderr con `--json`); el panel del briefing y la herramienta MCP `requests_inbox` dicen lo mismo. `--json` para máquinas, y cada fila trae `updated_event_id`, el id de la fila que la movió por última vez (`null` si es anterior a los ids; un `suppress` nunca lo mueve). `--min-severity <nivel>` conserva ese nivel y los más severos; dentro de un estado, las filas se ordenan por severidad antes que por antigüedad.

### Pedidos: paneles, stale, overdue y pedidos info

Cuatro paneles viajan solo con el `brief` de CLI del mismo proyecto, nunca con
`--slug`, el fallback al puntero global, ni por MCP. El destinatario ve
"Requests waiting on you" y "Requests you accepted and still owe"; el remitente
ve "Decisions on requests you sent" y "Requests you sent that nobody is moving".
Cada uno tiene un tope de 3 tarjetas con una línea de desborde bien visible
(`+N more …`) que nombra el comando para ver el resto, nunca un descarte
silencioso. La supresión es solo atención del lado del destinatario: el
panel del remitente sigue mostrando una solicitud suprimida como publicada
y sin decidir. Una solicitud sin responder pasa a `stale` después de 3
sesiones del destinatario sin decisión; una ya decidida sale del panel del
remitente después de 2 sesiones del remitente. La atención decae, los
registros nunca se eliminan, y ambos siguen totalmente visibles en
`list`/`inbox`.

`stale` cuenta sesiones del destinatario, así que una solicitud a un proyecto
que nadie abre nunca llega ahí. El remitente tiene una segunda señal, de
reloj: una solicitud que sigue abierta de su lado (open, needs-info, o
accepted sin `done`) pasa a `overdue` cuando nada la movió durante
`DAIMON_REQUEST_OVERDUE_DAYS` días. Moverla es abrirla, revisarla,
aceptarla, pedir más información, responderle o reclamarla como hecha; un
brief que solo mostró la tarjeta no cuenta. Se deriva al leer, nunca se
escribe, y no necesita ninguna sesión del lado del destinatario. El brief
del remitente lista las solicitudes vencidas en un panel titulado "Requests
you sent that nobody is moving", con tope de 3 y una línea de desborde
contada, y omite las que el panel "Decisions on requests you sent" ya
muestra. Nada corre en un temporizador: un script que quiera perseguirlas
puede consultar `daimon request list --json` y actuar sobre `overdue`.

Una solicitud abierta con `--kind info` no debe un accept, así que nunca
aparece en `daimon decide` ni en el panel "Requests waiting on you" del
destinatario de arriba: no hay nada ahí para que una persona decida.
Sigue llevando una marca `[info]` en el panel "Requests you accepted and
still owe" del destinatario y en la línea de entrega en vivo que la
inyecta a mitad de sesión. Una solicitud `work`, el default, no lleva
marca en ninguna de las dos.

Solo un canal humano puede abrir una solicitud como `kind=info`. Un agente puede hacerlo únicamente bajo una regla `verb=open` activa que su propio proyecto ratificó para ese destinatario; el registro entonces muestra `Kind: info (opened by agent under r-...)` en `list`, `show` e `inbox`, así que una solicitud clasificada por regla nunca se ve como una que abrió una persona.

`daimon status` agrega un resumen de una línea, `requests: N open sent, M
awaiting you`, silencioso cuando los dos son cero.

El [servidor MCP](../mcp.md) expone la vista del lado del destinatario como la
herramienta de solo lectura `requests_inbox`. `daimon_brief` nunca lleva
contenido de solicitudes, y ningún verbo de escritura de solicitudes es
alcanzable por MCP.

## Estado

### `daimon status`

Presencia y edad del checkpoint, resultado del último serialize, avisos de salud. Una línea reporta cuántos checks tiene armados este proyecto, contando también un check activo heredado de una capa por encima, cuántos siguen propuestos, y hace cuánto corrió alguno por última vez, más un puntero a `daimon check sync` cuando el manifiesto se fue de tema. Aparece solo si este proyecto tiene algún check, y nunca mueve el código de salida. También revisa los archivos de ledger de este proyecto sin modificarlos: una línea cuando todos están bien; si no, una línea por cada ledger con líneas rotas, partidas o ilegibles, o que todavía conserva un valor que olvidaste (un conteo, nunca el valor), y una línea con cualquier archivo `.jsonl` que daimon no reconoce. Cada una de esas líneas termina con qué hacer y dónde está el archivo: reintentar ante una falla transitoria, revisar permisos ante un error del sistema, o `daimon ledger repair <nombre>` para líneas rotas o basura (`daimon trust repair` para el ledger de confianza). Otra línea nombra cada proyecto del alcance cuyo ledger de eventos no se puede leer, porque el conjunto de olvidos puede estar sin uno de sus tombstones; un home con alcance de tenant no nombra ninguno. Una línea por cada proyecto del alcance cuenta las sesiones rechazadas porque su ledger de eventos no se puede leer (`admission refused`), con el motivo, qué ejecutar y dónde está el archivo. Mientras todas las sesiones fallidas sean rechazos de ese tipo, no se muestra el aviso de captura silenciosa. Cuando la revisión de valores olvidados no puede correr, lo dice en lugar de informar cero. `--json` y la herramienta de estado de MCP llevan el mismo resultado en un campo `ledgers`. Nada de esto mueve el código de salida. `--suppressed` lista lo que el briefing retiene: los loops resueltos con el evento que los cerró, y los valores en cuarentena por id de ítem y de registro, nunca su texto. Un valor que olvidaste no se lista. Cuando no se puede leer el ledger de confianza, dice cuántos ítems quedan retenidos en lugar de informar que no hay ninguno, y cuando no se puede armar la lista imprime una línea de error y sale con 2.

### `daimon stats`

Agregados locales de uso y captura. Nada se transmite; compartir la salida es un pegado deliberado. Incluye una línea de sondeos de recibo con totales acumulados (intentados, elegibles, confirmados, contradichos, omitidos, curados) cuando los recibos están configurados. También una línea de checks en tres redacciones: nada armado, armado pero nunca corrió, o totales (corridas, clean, violation, unresolved, denegadas) sumados sobre todos los anfitriones y sobre la ventana que conserva el log con tope, ventana que la línea nombra; el conteo de armados incluye un check activo heredado de una capa por encima de este proyecto. También una sección de worldcheck: por clase de afirmación, cuántas notas de este proyecto fueron contradichas al menos una vez sobre las notas verificadas al menos una vez, junto a los conteos por chequeo de confirmadas y contradichas del mismo proyecto. Ambos conteos cubren la misma ventana, que abre en el primer resultado registrado y que la sección nombra; las líneas de chequeo escritas antes de que el log nombrara un proyecto se cuentan aparte como `unscoped`, para ninguno; una clase sin resultados registrados dice `none recorded`, nunca `0 of 0`. También una línea de tokens en la sección store: los tokens que costaron las capturas de los últimos 14 días (total, prompt, completion), sumados desde el sello `capture_cost` de cada checkpoint, junto a cuántas de esas capturas no reportaron nada (el backend no imprimió uso), cuántas son anteriores al sello y cuántos registros se escribieron sin ninguna llamada a un modelo (`daimon write-checkpoint`); una cifra en dólares aparece solo cuando al menos una captura reportó un costo y cada captura que reportó traía uno, cosa que hoy solo imprime el backend `claude-cli`. También una línea de roles de cita: de quién son las palabras de las citas verificadas frescas de este proyecto, por rol de mensaje, leído de la lista `binding.roles` que el recibo registra al verificar (solo user, solo assistant, con tool, mixtas, con conteos y porcentajes), junto a las citas que no pueden responder: previas al campo (un recibo vinculado a mensajes y escrito antes de que existiera la lista de roles) y sin vínculo (un recibo de escaneo de la transcripción sin lista de roles: escrito antes del campo, o capturado sin transcripción por mensaje). Una cita vinculada a mensajes toma los roles de esos mensajes; una cita verificada por escaneo toma los canales que pueden dar cuenta de ella, y así es como la salida de herramientas y las citas de una corrida de `daimon serialize <archivo>` (un solo mensaje con rol user, sin ids de mensaje) reciben un rol. Una cita que ambos canales dijeron palabra por palabra se lee como mixta en un recibo de escaneo, porque cualquiera de los dos puede dar cuenta de ella. Las copias arrastradas quedan afuera, así las palabras de cada sesión pesan una sola vez. El rol es el canal del mensaje, nunca una persona. `--json` para máquinas.

### `daimon log --text "…"`

Agrega un evento libre a la línea de tiempo del proyecto, cero LLM, solo rastro de auditoría.

### `daimon loops`

Lista los loops abiertos direccionables con sus ids y edades, con una marca `stale` en los ítems arrastrados que pasaron el presupuesto de vencimiento. La contraparte de lectura del camino de escritura de `resolve`. `--stale` lista solo esos ítems arrastrados vencidos, el conjunto que el briefing puede soltar.

### `daimon decide`

Lista lo que está esperando por VOS, cada cosa con el único comando que la cierra, el espejo del lado humano de `loops`. Lee registros que ya existen y no escribe nada, así que abrirlo nunca cambia lo que el agente ve. Acotado a este proyecto; los demás proyectos llegan como conteos. `--all-projects` agrega la cola de cada otro proyecto como texto, compuesta por proyecto, con cada comando enrutado con `--slug=<slug>` para que corra desde acá. Los diez verbos solo para humanos (`request accept`, `reject`, `needs-info`, `suppress`; `amend ratify`, `reject`; `ruling ratify`, `retire`; `refute ratify`, `overturn`) toman esa bandera solo como ruteo. Ambos se rechazan mientras `DAIMON_TENANT_SCOPED` esté activo. El briefing ya muestra ese conteo en una sola línea, que apunta de vuelta a este comando.

### `daimon projects`

Lista cada proyecto con checkpoint, con un adelanto del tema. Un tema en cuarentena, olvidado o que no se puede verificar se muestra como `?` (`null` en `--json`), igual que un proyecto sin tema. Un proyecto cuyo ledger de confianza no se puede leer se cuenta en una línea de aviso después de la tabla (con `--json` stdout sigue siendo un arreglo JSON y el aviso va por stderr; un home con alcance de tenant lo dice sin el conteo). Cuando no se puede armar la lista imprime una línea de error y sale con 2.

### `daimon slug <path>`

Toma la entrada de forma literal e imprime el nombre de directorio de checkpoint que daimon deriva de esa cadena exacta. Para ver el bucket que un directorio usa de verdad, corré `daimon status --project <path> --json` y mirá `identity`. Sin acceso a store, config ni ledger, así que responde incluso para una ruta a la que daimon nunca escribió. Cuando la entrada parece una ruta pero no es absoluta y normalizada (`.`, `dir/`, `a/b`), una línea de aviso por stderr lo dice y nombra ese comando; stdout y el código de salida no cambian.

### `daimon bucket migrate`

Mueve el bucket que un daimon anterior a 0.42.0 escribió para este proyecto al que lee este daimon. Hay dos formas de ruta afectadas: un symlink en cualquier parte de la ruta, y un subdirectorio de un repositorio git escrito por la biblioteca y no por la CLI. Renombra cuando el bucket nuevo todavía no existe y fusiona línea por línea cuando ya existe. Un puntero que ya está en el bucket nuevo nunca se borra ni se desplaza; los punteros viejos llenan los espacios libres. Se puede correr dos veces sin problema. La salida 1 significa que el movimiento no terminó y nombra cada cosa que quedó con su remedio; la salida 2 rechaza un `--project` que contenga `..`, y rechaza `--project` directamente en un home con alcance de inquilino. `--dry-run` imprime el plan y no escribe nada; `--json` imprime el registro que agrega a `~/.daimon/checkpoints/migrations.jsonl`, o `{"refused": "..."}`.

### Worktrees enlazados

Una sesión abierta en un worktree enlazado usa el bucket de su repositorio principal, así que el historial, las reglas y los pedidos de un proyecto quedan en un solo lugar. La autoridad es la lista de worktrees del propio repositorio principal: daimon ejecuta `git worktree list --porcelain -z` (git 2.36 o más nuevo; con un git anterior el worktree conserva su bucket propio) y enlaza solo cuando esa lista nombra exactamente esta carpeta. Un archivo `.git` que apunta al directorio de administración de otro repositorio no enlaza nada. Los repositorios comunes nunca ejecutan ese comando. No corre bajo SEALED, que sigue rechazando un `.git` que sea archivo (un host que necesite un worktree ahí llama a `declare`), ni en un home con alcance de tenant. Hay dos límites documentados y no mitigados: una entrada vieja de la lista, que queda al mover un worktree (hasta `git worktree prune`), sigue nombrando su ruta anterior, y un worktree borrado antes de que corra un `heal` tardío vuelve a un bucket propio, que `bucket doctor` lista. Como el bucket es compartido, el último checkpoint del principal puede venir de una sesión en la rama de un worktree. Un worktree que ya tenía un bucket de antes conserva ese bucket hasta que lo fusiones con `bucket merge`; su brief lo avisa en una línea fija que nombra `daimon bucket doctor`.

### `daimon bucket merge --from=BUCKET --into=BUCKET`

Fusiona un bucket en otro, una sola vez y a propósito: la reparación para una división que ya existe (un bucket de worktree, un bucket fantasma, un bucket nuevo nacido antes de que reasignaras). Las líneas de ledger que el destino no tiene se agregan. La sesión más nueva de `--from` pasa a ser la última de `--into` cuando es más reciente, igual que lo haría una sesión más en `--into`, lo que puede sacar de su cadena el puntero más viejo de `--into`. Un puntero que no cabe en ningún slot se descarta y se lista por id de sesión; los archivos de sesión firmados conservan sus bytes. Luego `--from` se elimina y se registra un alias para que los lectores lo sigan. Se rechaza (salida 2) mientras la raíz registrada de `--from` siga existiendo en disco o cuando no tiene ninguna (nada prueba entonces que su proyecto desapareció), salvo con `--force` (un worktree enlazado vivo cuyas sesiones ya usan `--into` no se rechaza, porque nada puede volver a crear `--from`); también cuando `--into` no existe, o cuando los dos nombres son el mismo directorio o un symlink. Salida 1 significa que quedó algo y lo nombra. Es un ensayo salvo con `--apply`. Los nombres de bucket suelen empezar con `-`, así que escribí `--from=NOMBRE`. Solo para humanos: se rechaza en un home con alcance de tenant. `--json` imprime el registro.

### `daimon bucket doctor [--path DIR]`

Relevamiento de solo lectura de todos los buckets: raíces registradas que ya no existen, buckets sin raíz, dos buckets con una misma raíz, buckets vacíos y fantasma, raíces con pinta de worktree, buckets que dejó una fusión sin terminar, registros de identidad que no coinciden con la raíz. Con `--path` también lista los buckets colgados cuya URL de origin o commit raíz registrados coinciden con esa carpeta, rotulados como evidencia y no como prueba. Los nombres de bucket, los candidatos y las sugerencias de fusión se imprimen solo en una terminal; en cualquier otro lado recibís conteos. Cuando la carpeta de un bucket ya no existe o no hay ninguna registrada, termina con una línea que apunta a [Mover daimon a una máquina o carpeta nueva](../../guides/move-to-a-new-machine.md). A un bucket cuya raíz registrada es un worktree enlazado vivo se le sugiere fusionarse en el bucket que usa su repositorio principal; una raíz que solo parece un worktree (una carpeta `.claude/worktrees` que ningún repositorio lista) no recibe sugerencia, porque su carpeta viva volvería a crear el bucket fusionado. Se rechaza en un home con alcance de tenant y bajo SEALED. `--json` sigue la misma regla.

### `daimon bucket stamp [--scan DIR ...]`

Correlo en la máquina vieja antes de copiar el store. Cada bucket con raíz recibe un registro de identidad (URL de origin sin credenciales, commit raíz, nombre de carpeta). Un bucket sin raíz la recibe solo cuando exactamente un repositorio bajo un directorio `--scan` tiene el nombre de ese bucket; dos coincidencias se rechazan y no escriben nada. Una raíz registrada nunca se reescribe y los checkpoints firmados nunca se tocan. Es un ensayo salvo con `--apply`. Salida 1 cuando un bucket necesitó a una persona (dos coincidencias, una carrera perdida). Se rechaza en un home con alcance de tenant.

### `daimon bucket adopt --bucket=BUCKET --path=DIR`

Asocia un bucket con la carpeta donde vive ahora su proyecto: la respuesta explícita cuando un proyecto se movió y `remap` no lo cubre. El bucket conserva su nombre y todos sus archivos firmados; solo cambia su raíz registrada, y la anterior queda en su historial de identidad. Se rechaza (salida 2) cuando `--path` no es un directorio, cuando está dentro de un proyecto (la carpeta que abre una sesión es la raíz de git, y el error la nombra: adoptá esa), cuando otro bucket ya registra esa carpeta (el bucket nuevo nacido antes de que reasignaras: fusionalo primero con `bucket merge`, o la carpeta respondería por dos buckets), cuando la raíz registrada del bucket todavía existe en disco (un clon que conserva su propia historia) salvo con `--force`, cuando el bucket propio de la carpeta no tiene raíz registrada y guarda historia que la reasignación dejaría atrás (fusionalo primero con `bucket merge`, o `--force`), cuando no se puede leer su registro de identidad y cuando no se puede bloquear el store o el bucket. Dos adopciones sobre una misma carpeta no pueden ganar las dos: la colisión se vuelve a revisar bajo un lock del store. Adoptar lo que ya está adoptado no hace nada. Se rechaza bajo SEALED, igual que en un home con alcance de tenant. Es un ensayo salvo con `--apply`. En una terminal también muestra la raíz que el bucket dejó y una comparación de sus anclas registradas con la carpeta (evidencia, no prueba); en cualquier otro lado se omiten. Se rechaza en un home con alcance de tenant. `--json` imprime el registro.

### `daimon bucket remap --from=DIR --to=DIR`

Después de un cambio de carpeta o de usuario, reescribe la raíz registrada de cada bucket que está bajo `--from` al mismo lugar bajo `--to`. La coincidencia se corta en un separador, así que `/Users/a` nunca coincide con `/Users/abc`; se descarta un separador final de cualquiera de los dos lados; funcionan rutas entre sistemas (`C:\Users\a` a `/Users/b`) porque los separadores de la cola siguen a `--to`. Un bucket cuya carpeta nueva todavía no existe (un proyecto que no clonaste en esta máquina) se omite y se lista, y eso no es un error. Un bucket cuya carpeta nueva ya pertenece a otro bucket se omite y se lista con el `bucket merge` que fusiona los dos (lleva `--force`, porque esa carpeta existe y `merge` si no se niega a eliminar un bucket cuya carpeta está viva; corré `remap` de nuevo después), y también uno cuya carpeta nueva no es la que abre una sesión (`not-a-root`, por ejemplo un subdirectorio de un repositorio) y uno cuya raíz vieja todavía existe (`live-source`, un clon que se conserva: `--force` lo mueve igual). `--from` puede darse a través de un symlink. Se rechaza (salida 2) `--from /`, una unidad suelta, un valor vacío, un `--to` relativo y un `--to` dentro de `--from`. Cada bucket se reescribe bajo su propio lock y se vuelve a leer antes; uno bloqueado o cambiado mientras tanto se lista y la corrida sale con 1, igual que ante una colisión. Se puede correr dos veces sin problema. Los buckets sin raíz registrada quedan fuera de alcance; `bucket doctor` los lista. Es un ensayo salvo con `--apply`. Los nombres de bucket y las raíces se imprimen solo en una terminal, y en cualquier otro lado solo conteos. Se rechaza en un home con alcance de tenant. `--json` sigue la misma regla.

### `daimon ledger repair <nombre>`

Repara un ledger del bucket (`events`, `trust`, `refutations`, `amendments`, `requests`, ...) que `daimon status` informa como degradado o ilegible. Reúne las filas partidas en varias líneas, mueve cada línea rota o no interpretable a `<nombre>.quarantined-lines` junto al ledger (una fila JSON por línea, los bytes no decodificables se conservan como escapes `\xNN`), y luego vuelve a ejecutar forget para cada clave olvidada del proyecto, lo que alcanza valores que un forget no vio mientras su fila estaba partida. Se puede correr dos veces sin problema; un ledger ya sano imprime `nothing to repair`. Imprime cuántas filas tiene ahora el sidecar, porque un valor fusionado dentro de una fila rota necesita revisión manual de ese archivo, y después de reparar `events` dice cuántas sesiones rechazadas esperan a `daimon heal`, que toma una por ejecución. Un forget posterior también purga de ese archivo las filas que coinciden, y los fragmentos no relacionados se quedan ahí. Salida 1 cuando el ledger es transitorio o no se puede reescribir (no se cambia nada), salida 2 para un nombre desconocido. `--dry-run` informa los mismos conteos y no escribe nada. `daimon trust repair` es el mismo verbo para `trust`.

### `daimon team init|sync|status`

Memoria de equipo compartida vía repo sidecar, ruteo cerrado por defecto, redacción por forma antes de sincronizar nada.

### La regla del slug

La regla del slug, dicha con precisión: primero se recorta el espacio en
blanco al principio y al final, y después cada carácter que no sea un
carácter de palabra Unicode ni `-` se convierte en `-`. Los guiones bajos, las
letras acentuadas y las escrituras no latinas sobreviven al pliegue; una
entrada vacía o de solo espacios no tiene slug. Ejemplo: `/Users/x/my.proj` se
convierte en `-Users-x-my-proj`. Este no es el esquema que usa Claude Code
para `~/.claude/projects`. Los dos coinciden en barras, puntos y espacios, y
difieren en `_` (daimon lo conserva, Claude Code lo pliega a `-`). Un host que
prefiera reimplementar la regla puede chequear su copia contra `daimon slug`
directamente. Una ruta que empieza con `-` necesita `--` antes (`daimon slug
-- -Users-x`), el mismo escape que necesita cualquier argumento posicional con
guion inicial.

La regla es una transformación de caracteres que se aplica DESPUÉS de la resolución. `daimon slug` imprime la transformación de la cadena literal que le pasás, y por eso responde para una ruta a la que daimon nunca escribió. Para ver la raíz y el slug a los que una ruta rutea de verdad, con los pasos de symlink y toplevel de git ya aplicados, corré `daimon status --project <path> --json` y mirá `identity`.

## Internos

### `daimon serialize <transcripción>`

Convierte un archivo de transcripción en checkpoint, lo llaman los hooks de fin de sesión; a mano, rellena uno.

### `daimon write-checkpoint`

Almacena un checkpoint recibido como JSON por stdin, el camino de introspección en sesión. La confianza la fija el código: nada en este camino puede reclamar `verbatim`, porque no hay transcripción contra la cual verificar.

### `daimon recall-inject`

El backend de sugerencias por prompt detrás del hook de recall: prompt por stdin, de cero a dos líneas de trabajo previo, exit 0 siempre.

### `daimon action-recall`

El backend de recall previo a una acción de shell: un comando de shell por stdin, cero o una línea de trabajo previo, exit 0 siempre. Es un proceso separado del check previo a la acción que sí puede denegar un comando, así que una falla acá nunca hace desaparecer una denegación.

### `daimon request-inject`

El backend de entrega en vivo por prompt: imprime las solicitudes entre proyectos sin decidir que esta sesión todavía no vio, exit 0 siempre.
