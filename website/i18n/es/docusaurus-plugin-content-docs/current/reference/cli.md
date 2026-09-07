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
| `daimon hooks install <host>` | Instala los hook scripts del host (Windsurf, Codex) desde el paquete. `list` / `status` inspeccionan; `status` también audita el manifiesto de checks de este proyecto contra su ledger, y sale distinto de cero ante cualquiera de las dos derivas. |
| `daimon skill install <host>` | Instala la skill de agente de daimon en el directorio de skills del host. Volvé a correrlo después de cada upgrade. |
| `daimon check sync` | Reconstruye `~/.daimon/checks` — el manifiesto de checks armados que lee un hook del anfitrión, y un archivo por cada cuerpo de check — desde el ledger de este proyecto. Cada ratify, revise, retire y forget ya lo hace, así que correlo solo cuando el manifiesto se dañó por fuera. Es seguro repetirlo: un manifiesto que no cambió no se reescribe. Imprime cuántos checks quedaron armados, cero incluido. `--check` audita en lugar de reconstruir y no escribe nada: salida 0 si está al día, 1 si hay deriva, 3 si el manifiesto existe y no se puede leer. `--json` para máquinas. |
| `daimon heal` | Re-serializa la última sesión fallida cuando es seguro hacerlo. |
| `daimon mcp serve` | Sirve las herramientas de daimon por MCP (stdio). |

## Brief

| comando | qué hace |
| --- | --- |
| `daimon brief` | Renderiza el briefing del último checkpoint — dónde quedaste, con etiquetas de confianza. `--team` suma lo último del equipo; `--slug <s>` lee el bucket de otro proyecto explícitamente. El briefing también lleva una línea con el conteo de decisiones que están esperando por vos, por ejemplo `3 decisions waiting on you here (2 elsewhere) - daimon decide`, que apunta a `daimon decide`. |
| `daimon recall "consulta"` | Búsqueda full-text sobre el historial local + de equipo. `--json` para filas, `--all-projects` para ampliar. |
| `daimon handoff "Hacé X primero. Ojo con Y."` | Deja un batón autoral para la próxima sesión — se renderiza arriba de todas las secciones del briefing y nunca compite con ítems rankeados. `--clear` lo retira; uno nuevo reemplaza al anterior. |

## Comprobar

| comando | qué hace |
| --- | --- |
| `daimon why <item-id>` | El inspector de confianza: muestra cada eje de evidencia detrás de un ítem — captura independiente, procedencia, fuente, integridad de bytes, soporte actual, resultado del chequeo de citas, ciclo de vida, corroboración. `--source` agrega una ventana de fuente acotada y redactada; `--json` para máquinas. Los ids de ítem salen de `daimon recall` o `daimon loops`. |
| `daimon verify-receipt` | Verifica el recibo firmado de procedencia de un checkpoint (chequeo criptográfico completo vía el CLI de vitni). |
| `daimon reverify <id>` | Afirma que un ítem arrastrado sigue siendo cierto — exige evidencia y reinicia su reloj de vencimiento. También es la mitad de rechazo de un candidato a supersesión. |
| `daimon audit quotes` | Re-verifica cada cita verbatim almacenada contra su transcripción de origen y reporta discrepancias. Solo lectura — nunca reescribe etiquetas. Salida 0 si verificó y salió limpio, 1 si hay discrepancia, 3 si no había nada verificable. Siempre reporta cuántos ítems tuvo que saltear, y cuando son todos lo dice en lugar de imprimir una tasa sobre nada. `--json` para máquinas, y lista todas las discrepancias, mientras que las líneas impresas se cortan en `--top`. |
| `daimon audit privacy` | Prueba el contrato de borrado: hashea cada campo con texto plano en cada superficie (checkpoints, punteros rotados, el registro de eventos, el espejo de equipo, el índice de recall y sus snapshots huérfanos) y reporta todo valor olvidado que haya sobrevivido. Solo lectura. |
| `daimon refute list\|show\|search\|guard` | Lee el ledger de conocimiento negativo sin decaimiento. `guard` emite solo matches activos por ancla exacta o frase de sujeto; es consultivo y nunca bloquea un comando. `search` devuelve ambas polaridades, etiquetadas; `list` y `guard` quedan solo para refutaciones. Sumá `--json` para integraciones de deliberación. |
| `daimon ruling list\|show` | Lee las reglas vigentes: restricciones positivas ratificadas por humanos en el mismo ledger, que nunca decaen ni se re-extraen. `show` incluye propuestas de agentes pendientes. Un `list` que no encuentra nada sale con 1 y nombra el bucket en stderr cuando el proyecto nunca fue escrito, y sale con 0 cuando el proyecto tiene un bucket sin reglas. |
| `daimon ruling checks` | Qué tiene armado este proyecto: una fila por cada regla que lleva un check, cruzada con cada anfitrión. Cada fila nombra el ciclo de vida del check, la intención que pidió su autor, el modo que ese anfitrión entrega de verdad, cuándo corrió por última vez, y sus conteos de clean / violation / unresolved sobre la ventana que el log todavía conserva. Una fila que no pudo correr termina después de su modo, porque un check propuesto o desarmado no arma nada y un anfitrión que no puede entregarlo no tiene canal por donde correrlo. Las líneas de encabezado reportan el estado del manifiesto, el estado del propio log de corridas, la marca de tiempo donde arranca la ventana conservada, y cada anfitrión donde el hook alguna vez corrió. Esas líneas de anfitrión llevan la etiqueta `(any project)`: las filas detrás de ellas no llevan proyecto, así que que el hook esté vivo es un hecho de esta máquina, nunca de este proyecto. Es de solo lectura, y una respuesta vacía sale con 0. `--json` para máquinas. |
| `daimon ruling check try <id> --command "<cmd>"` | Corre el check de esa regla contra una línea de comando que vos nombrás e imprime el resultado. No arma nada, no registra nada, y materializa el cuerpo fuera del directorio de checks. `--cwd` fija el directorio de trabajo contra el que resuelven las rutas relativas; `--proposed` corre el cuerpo de una revisión de agente pendiente en lugar del armado. Solo vía humana, como `ratify`: ejecuta el cuerpo, así que hace falta una terminal interactiva y `--by agent` se rechaza. Mismo contrato de salida que los auditores de abajo. |
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
| `daimon ruling propose\|ratify\|revise\|retire` | Gestiona reglas vigentes en el mismo ledger, con un ciclo más estricto: `ratify` muestra el texto completo, avisa que va a renderizarse en cada sesión futura y ata la activación al texto mostrado; un humano que revisa una regla activa confirma el cambio y la regla sigue activa; los revise y retire de agentes registran propuestas mientras el texto queda en pie; la activación se rechaza pasado el tope (`DAIMON_RULING_CAP`, por defecto 7). Retirar no exige cita de evidencia. `propose` y `revise` pueden adjuntar un check con `--check-body-file`, `--check-match` y `--check-intent`: un script cuyos bytes quedan guardados en la regla (una ruta se rechaza), un patrón sobre la línea de comando que elige las acciones antes de las cuales corre, y qué le pide a cada anfitrión (`warn` por defecto). El check de una candidata se lee como propuesto, no armado, y ningún anfitrión lo ejecuta. `ratify` muestra el check y ata la activación al cuerpo que mostró, por hash, y lo materializa para que un anfitrión pueda correrlo. Un cuerpo que nombra una ruta local del anfitrión en cualquier línea se rechaza, porque el check viaja con la regla y una ruta fuera de él no existe en ninguna otra máquina. Ver [Checks en ejecución](#checks-en-ejecución). |

### Checks en ejecución

Ratificar una regla que lleva un check escribe dos cosas bajo `~/.daimon/checks`: un manifiesto que nombra cada check armado y el directorio de proyecto al que pertenece, y un archivo por check con los bytes exactos que la regla guarda. Cada escritura en el ledger que puede armar o desarmar un check los reconstruye; `daimon check sync` los reconstruye a pedido, y también lo hace `daimon hooks install`, que imprime lo que encontró.

En un anfitrión con el hook de pre-acción instalado, un comando de shell que coincide corre sus checks armados antes de ejecutarse. El hook lee el manifiesto, se queda con los checks cuyo directorio de proyecto contiene el directorio de trabajo de la acción, y corre aquellos cuyo patrón coincide con la cadena del comando. Siempre sale con 0 y escribe o bien un objeto JSON o bien nada: el rechazo es una decisión que el hook toma deliberadamente, nunca un código de salida que un crash podría producir por accidente.

### Qué recibe un check en cada anfitrión

Lo que pidió el autor es una intención. Lo que entrega un anfitrión es un modo, y es el más débil de los dos.

| intención | Claude Code | Codex | Windsurf |
| --- | --- | --- | --- |
| `enforce` | `enforce` | `enforce` | `unsupported` |
| `warn` | `warn` | `record-only` | `unsupported` |
| `record-only` | `record-only` | `record-only` | `unsupported` |

`enforce` devuelve el rechazo estructurado del anfitrión y el comando no corre. `warn` permite el comando y muestra la razón. `record-only` no le dice nada al anfitrión y deja la corrida en el registro. `unsupported` es lo que recibe un anfitrión del que daimon no midió cómo entrega una decisión: Cascade documenta un evento `pre_run_command`, pero nada medido dice qué hace con una, así que toda la columna de Windsurf queda en `unsupported` en lugar de reclamar una imposición que nadie vio entregada.

Codex es la celda interesante. No documenta ningún canal para una advertencia, así que `warn` degrada allí a `record-only`: el check igual corre y el registro igual lo dice, y daimon no dice haberle mostrado al autor algo que el anfitrión nunca mostró.

Cuando más de un check armado coincide con un comando, decide el modo más fuerte de los que FALLARON. Un check `enforce` que pasó no bloquea el comando por un check `warn` que no, y el mensaje nombra cada check que falló en ese modo o por encima. La razón de un check más débil queda en el registro: `record-only` pidió el registro y nada más, y un vecino que falló más fuerte no la saca en su nombre.

Un check corre contra un **sujeto**: la línea de comando, un separador, y después el contenido de cada argumento de archivo que daimon pudo resolver, cada uno bajo un encabezado que nombra la bandera de la que salió. El sujeto va a un archivo temporal con modo 600 y se borra después de la corrida. Su ruta llega en `DAIMON_CHECK_SUBJECT`, junto con `DAIMON_CHECK_COMMAND` y `DAIMON_CHECK_RULING`; el directorio de trabajo es el de la acción, y la entrada estándar es `/dev/null`.

Qué lee el resolutor:

| forma en el comando | qué hace daimon |
| --- | --- |
| `--body-file <ruta>`, `--body-file=<ruta>`, `-F <ruta>`, `-F<ruta>` | lee el archivo |
| `--notes-file <ruta>`, `--notes-file=<ruta>` | lee el archivo |
| `-F clave=@<ruta>`, `--field clave=@<ruta>`, `-Fclave=@<ruta>` | lee el archivo |
| `<bandera> -` con exactamente un heredoc en el MISMO comando | lee el texto del heredoc |
| `<bandera> -` alimentada por un pipe | sin resolver, causa `stdin-pipe` |
| `<bandera> -` cuyo propio comando no trae heredoc | sin resolver, causa `arg-form-unparsed` |
| un comando con más de un heredoc o más de una `<bandera> -` | sin resolver, causa `arg-form-unparsed` |
| cualquier otro `@<ruta>` o `<bandera> -` | sin resolver, causa `arg-form-unparsed` |

Un valor pegado a una bandera corta es el mismo comando que uno separado, así que `-Fbody.md` se lee igual que `-F body.md`.

Un heredoc pertenece al comando al que está pegado, y a ningún otro. daimon parte la línea en `&&`, `||`, `;`, `|` y saltos de línea, y un heredoc en uno de esos pedazos solo puede leerse para un argumento de ese mismo pedazo. Así que `cat <<EOF > note.txt ... EOF` seguido de `gh pr create -F -` queda sin resolver, en vez de comprobarse contra el texto que recibió `cat`. Dentro de un mismo comando las cuentas siguen teniendo que ser uno a uno: cuál heredoc alimenta a cuál argumento no es algo que la línea de comando responda, y una respuesta equivocada armaría el sujeto con texto que la acción nunca envía.

Un comando de más de 64 KiB, una vez apartados los cuerpos de sus heredocs, es `arg-form-unparsed`: tokenizar un solo argumento enorme cuesta más que todo el presupuesto del hook, y un resolutor al que se le adelanta el timeout del anfitrión deja pasar la acción sin ningún registro. Un heredoc largo no cuenta para ese límite, así que un cuerpo de PR grande sigue resolviendo.

Las rutas relativas resuelven contra el directorio de trabajo. Un archivo de más de 1 MiB es `file-oversize`, uno que no es texto UTF-8 es `file-binary`, y uno que falta o no se puede leer es `file-missing` o `file-unreadable`. Un solo argumento que daimon no puede leer deja todo el sujeto sin resolver, digan lo que digan los demás.

Cada corrida termina en exactamente uno de tres resultados, y nunca se mezclan:

| resultado | qué significa |
| --- | --- |
| `clean` | el check corrió sobre el sujeto completo y salió con 0 |
| `violation` | el check corrió sobre el sujeto completo y salió con 1 — su propia primera línea de stderr es la razón |
| `unresolved` | daimon no pudo probar que el sujeto estuviera limpio: un argumento que no pudo leer, un check que se rompió o se pasó de presupuesto, un cuerpo que ya no hashea a lo que se ratificó, o un anfitrión sin `sh` |

`unresolved` nunca se muestra como limpio y nunca se cuenta como violación.

El cuerpo corre con un entorno mínimo: `PATH`, `HOME`, `LANG`, las variables `LC_*` y `TMPDIR`, más las tres de arriba. No es un sandbox — un check que ratificaste corre como vos, y podría hacer cualquier cosa que vos puedas. Recortar el entorno solo evita que a un script cuyo trabajo es leer un archivo se le entregue cada token de la sesión.

El runner lleva su propio presupuesto, `DAIMON_CHECK_TIMEOUT`, cinco segundos por defecto contra un timeout de hook del anfitrión de diez. Ese presupuesto no es opcional: en los anfitriones medidos hasta ahora, un hook que llega al timeout del propio anfitrión no bloquea y la acción sigue, así que un check sin presupuesto propio convierte un cuelgue en un permiso silencioso. Pasarse mata el check y todo su grupo de procesos, y reporta `unresolved`.

Cada corrida agrega una fila a `~/.daimon/logs/checks.jsonl`: ids, resultados, causas, duraciones, anfitrión y modo. Nada del texto del comando, ninguna ruta, nada del sujeto. La razón que ve el agente puede nombrar una ruta; el log no.

El archivo tiene un tope de 256 KiB. Pasado eso el escritor conserva los últimos 64 KiB y descarta las filas más viejas, en el mismo lugar, así un hook que tenga el archivo abierto sigue escribiendo en el mismo. Los conteos que reportan las superficies de abajo son conteos sobre lo que queda, y cada una nombra la marca de tiempo donde arranca esa ventana.

Leé ese log para saber si el check está vivo, no para saber si se cumplió. Una fila prueba que el check CORRIÓ. Solo `decision_emitted: deny` bajo `enforce` cierra la distancia entre un check que corrió y un check que fue respetado.

Tres superficies lo leen por vos. `daimon ruling checks` lo pliega por regla y por anfitrión, `daimon stats` reporta una línea sobre todos los anfitriones, y `daimon ruling show` agrega una línea `Fired:` a una sola regla. Las tres reportan una marca de tiempo y conteos sobre la ventana conservada, nunca el último resultado: un log que solo agrega está ordenado por el agregado, así que la última fila no es el estado actual. Un check armado sin ninguna fila se lee como `never fired`, que es una respuesta distinta de cero violaciones. Un log que daimon no puede abrir es una tercera respuesta más: cada superficie dice que el log no se puede leer, en lugar de afirmar que no corrió nada.

Dos convenciones que conviene sostener. Armá un check nuevo con intención `warn` primero, así un patrón que agarra más de lo que pensabas cuesta una advertencia y no una acción bloqueada. Y corré `daimon ruling check try` antes de ratificar — para eso está.

| variable | por defecto | qué guarda |
| --- | --- | --- |
| `DAIMON_CHECKS_DIR` | `~/.daimon/checks` | el manifiesto y los cuerpos materializados |
| `DAIMON_CHECK_TIMEOUT` | `5` | el presupuesto del runner en segundos, con piso de 0.5 |
| `DAIMON_LOG_DIR` | `~/.daimon/logs` | contiene `checks.jsonl` |

### Reglas desde un proceso anfitrión

La CLI emite solo dos canales: `cli-tty` (una terminal interactiva) y `cli-agent` (`--by agent`). Nunca va a tener una bandera para los otros dos canales humanos, `ui` y `signed`, porque una bandera que un agente pudiera pasar desde un shell sería un canal humano autodeclarado. Esos dos existen para un proceso anfitrión que tiene la autoridad por sí mismo, un operador que verificó por fuera, y se escriben por la librería, en el mismo proceso:

- `daimon_briefing.refutations.ratify(refutation_id, channel="signed", note="...", project_dir=...)` activa una regla propuesta. `channel` es el canal que el anfitrión observó, uno de `ui` o `signed`; cualquier otro nombre se rechaza. Citá la prueba de la acción del operador en la evidencia de la regla (`url:`, o `receipt:` cuando exista la firma). El `check_sha256=` opcional fija el cuerpo del check que el operador vio, igual que la clave del texto de la regla; una regla sin check se activa con ese campo vacío. Una regla que lleva un check se activa solo mediante un ratify que pase el pin del check que mostró; un revise en proceso desde un canal humano que entrega un check nuevo lo arma tal cual, así que la confirmación queda a cargo del anfitrión.
- `daimon_briefing.refutations.listing(states={"active"}, polarity="ruling", project_dir=...)` y `daimon_briefing.briefing.active_rulings(project_dir)` leen el conjunto activo, cada fila con `subject`, `scope`, `anchors`, `activation_channel`, `evidence` y el texto de la regla. Los `anchors` son cadenas libres que se fijan con `ruling propose --anchor`; un anfitrión que aplica reglas por mensaje compara contra ellos y decide por su cuenta qué pasa.

El registro se muestra como `ratified (signed)` o `ratified (ui)`, nunca como ratificado por humano sin el nivel. Nada local es infalsificable: quien tiene acceso a la máquina puede manejar una UI o abrir una terminal. Lo que el canal gana es procedencia, no prueba; falsificar cuesta una suplantación deliberada en vez de una palabra, y el canal queda auditable después.

En las dos llamadas de arriba, y en los helpers de los ledgers de refutaciones, pedidos, enmiendas y relaciones que están detrás, `project_dir` se resuelve igual que la CLI resuelve `--project`: se vuelve absoluto, se colapsan los symlinks y después se normaliza al toplevel de git. Un anfitrión parado en un subdirectorio de un repositorio lee el mismo bucket que la CLI lee desde la raíz del repositorio. `daimon_briefing.config.resolve_project_dir(path)` es la función pública que devuelve el directorio al que rutea una ruta. El store de checkpoints resuelve igual, en cada entry point público que recibe un `project_dir`, así que un checkpoint y un ruling escritos desde el mismo directorio en el mismo proceso caen en un solo bucket. `daimon_briefing.store.project_bucket(path)` devuelve el nombre del bucket que va a usar el store, para un anfitrión que quiera chequear antes de escribir. La única función que sigue siendo literal es `store.project_slug`, la transformación de caracteres que imprime `daimon slug`, que tiene que responder para una ruta que daimon nunca vio.

#### Con qué puede contar un host

daimon está antes de la 1.0. Una versión que rompa algo de lo descrito acá llega como un salto de versión **menor**, no mayor, así que el número de versión por sí solo no te va a avisar. Leé esa frase dos veces si estás acostumbrado al significado habitual de una versión menor.

La estabilidad no es la promesa que podemos sostener en esta etapa. La visibilidad sí. Cualquier cambio en las llamadas de arriba, en los nombres de canal aceptados, en los textos de activación que se muestran, o en los campos de fila que se listan abajo, sale como un commit que rompe compatibilidad, y por lo tanto aparece bajo `### ⚠ BREAKING CHANGES` en `CHANGELOG.md`, con prosa que dice qué se movió y qué hacer al respecto.

Entonces: fijá una versión exacta y leé esa sección de `CHANGELOG.md` antes de pasar a otra.

Los campos de fila que lee un host son el id del registro, su estado, asunto, alcance, anclas, la activación que se muestra y el canal por el que llegó, la lista de evidencia, y el texto de la regla. Los dos lectores de arriba devuelven el mismo conjunto.

Deliberadamente fuera de la promesa: el orden en que vuelven las filas, la redacción de diagnósticos y líneas de log, el texto del mensaje de un error lanzado (usá el tipo de excepción en su lugar), y todo lo que no esté nombrado en esta página. Que se pueda importar no es lo mismo que que esté documentado.

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
| `daimon status` | Presencia y edad del checkpoint, resultado del último serialize, avisos de salud. Una línea reporta cuántos checks tiene armados este proyecto, cuántos siguen propuestos, y hace cuánto corrió alguno por última vez, más un puntero a `daimon check sync` cuando el manifiesto se fue de tema. Aparece solo si este proyecto tiene algún check, y nunca mueve el código de salida. `--suppressed` lista los ítems resueltos retenidos. |
| `daimon stats` | Agregados locales de uso y captura — nada se transmite; compartir la salida es un pegado deliberado. Incluye una línea de sondeos de recibo con totales acumulados (intentados, elegibles, confirmados, contradichos, omitidos, curados) cuando los recibos están configurados. También una línea de checks en tres redacciones: nada armado, armado pero nunca corrió, o totales (corridas, clean, violation, unresolved, denegadas) sumados sobre todos los anfitriones y sobre la ventana que conserva el log con tope, ventana que la línea nombra. `--json` para máquinas. |
| `daimon log --text "…"` | Agrega un evento libre a la línea de tiempo del proyecto — cero LLM, solo rastro de auditoría. |
| `daimon loops` | Lista los loops abiertos direccionables con sus ids — la contraparte de lectura del camino de escritura de `resolve`. |
| `daimon decide` | Lista lo que está esperando por VOS, cada cosa con el único comando que la cierra — el espejo del lado humano de `loops`. Lee registros que ya existen y no escribe nada, así que abrirlo nunca cambia lo que el agente ve. Acotado a este proyecto; los demás proyectos llegan como conteos. `--all-projects` agrega la cola de cada otro proyecto como texto, compuesta por proyecto, con cada comando enrutado con `--slug=<slug>` para que corra desde acá. Los diez verbos solo para humanos (`request accept`, `reject`, `needs-info`, `suppress`; `amend ratify`, `reject`; `ruling ratify`, `retire`; `refute ratify`, `overturn`) toman esa bandera solo como ruteo. Ambos se rechazan mientras `DAIMON_TENANT_SCOPED` esté activo. El briefing ya muestra ese conteo en una sola línea, que apunta de vuelta a este comando. |
| `daimon projects` | Lista cada proyecto con checkpoint, con un adelanto del tema. |
| `daimon slug <path>` | Imprime el nombre de directorio de checkpoint que daimon deriva de una ruta de proyecto. Sin acceso a store, config ni ledger, así que responde incluso para una ruta a la que daimon nunca escribió. |
| `daimon team init\|sync\|status` | Memoria de equipo compartida vía repo sidecar — ruteo cerrado por defecto, redacción por forma antes de sincronizar nada. |

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
