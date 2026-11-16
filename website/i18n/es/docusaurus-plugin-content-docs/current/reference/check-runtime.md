---
sidebar_position: 6
description: "Cómo una regla sobre tus repositorios obliga a los proyectos de abajo, cómo corre un check armado antes de un comando de shell y qué entrega cada host."
---

# Reglas heredadas y checks en ejecución

## Reglas heredadas

Una regla propuesta y ratificada en un directorio simple por encima de tus repositorios ata a cada proyecto debajo de él: corré `daimon ruling propose` con el texto de la regla y sus banderas de siempre, después `daimon ruling ratify <id> --project ~/work` desde una terminal interactiva, y arma ahí y en todo lo que está debajo. Una capa es cualquier directorio así, a la altura de tu directorio home o por debajo, que no tenga `.git` en ningún punto por encima de él y que ya tenga un bucket de daimon.

El briefing de un proyecto hijo renderiza estas reglas heredadas primero, cada una etiquetada `[from ~/work]`, y cuentan contra el propio tope de 7 del hijo: la ceremonia en la capa avisa a cuáles de sus descendientes una activación empujaría por encima de ese tope. `retire`, `revise` y `ratify` sobre un id heredado se rechazan adentro del hijo y apuntan a `--project ~/work` en su lugar, porque solo el propio bucket de la capa puede actuar sobre esa regla. `daimon ruling list --inherited` suma la vista combinada a la lista propia de un proyecto, y cada fila que agrega lleva `inherited_from`, también en `--json`. Un check adjunto a una regla de capa se arma en cada repositorio debajo la próxima vez que corre `daimon hooks install` o `daimon check sync` ahí.

## Checks en ejecución

Ratificar una regla que lleva un check escribe dos cosas bajo `~/.daimon/checks`: un manifiesto que nombra cada check armado y el directorio de proyecto al que pertenece, y un archivo por check con los bytes exactos que la regla guarda. Cada escritura en el ledger que puede armar o desarmar un check los reconstruye; `daimon check sync` los reconstruye a pedido, y también lo hace `daimon hooks install`, que imprime lo que encontró. Una regla con el patrón o el cuerpo de su check retenido no arma nada, y ambos comandos imprimen una línea con cuántas reglas se salteó por ese motivo.

En un anfitrión con el hook de pre-acción instalado, un comando de shell que coincide corre sus checks armados antes de ejecutarse. El hook lee el manifiesto, se queda con los checks cuyo directorio de proyecto contiene el directorio de trabajo de la acción, y corre aquellos cuyo patrón coincide con la cadena del comando. Siempre sale con 0 y escribe o bien un objeto JSON o bien nada: el rechazo es una decisión que el hook toma deliberadamente, nunca un código de salida que un crash podría producir por accidente.

## Qué recibe un check en cada anfitrión

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

Las rutas relativas resuelven contra el directorio de trabajo. Un archivo de más de 1 MiB es `file-oversize`, uno que no es texto UTF-8 es `file-binary`, y uno que falta o no se puede leer es `file-missing` o `file-unreadable`. Una ruta que necesita que el shell la expanda (`$f`, `$(mktemp)`, un backtick, `~/x` o un glob como `*.md`) es `shell-expansion`: daimon lee el comando antes de que corra el shell, así que pasá una ruta literal. Un solo argumento que daimon no puede leer deja todo el sujeto sin resolver, digan lo que digan los demás.

Cada corrida termina en exactamente uno de tres resultados, y nunca se mezclan:

| resultado | qué significa |
| --- | --- |
| `clean` | el check corrió sobre el sujeto completo y salió con 0 |
| `violation` | el check corrió sobre el sujeto completo y salió con 1, su propia primera línea de stderr es la razón |
| `unresolved` | daimon no pudo probar que el sujeto estuviera limpio: un argumento que no pudo leer, un check que se rompió o se pasó de presupuesto, un cuerpo que ya no hashea a lo que se ratificó, o un anfitrión sin `sh` |

`unresolved` nunca se muestra como limpio y nunca se cuenta como violación.

El cuerpo corre con un entorno mínimo: `PATH`, `HOME`, `LANG`, las variables `LC_*` y `TMPDIR`, más las tres de arriba. No es un sandbox, un check que ratificaste corre como vos, y podría hacer cualquier cosa que vos puedas. Recortar el entorno solo evita que a un script cuyo trabajo es leer un archivo se le entregue cada token de la sesión.

El runner lleva su propio presupuesto, `DAIMON_CHECK_TIMEOUT`, cinco segundos por defecto contra un timeout de hook del anfitrión de diez. Ese presupuesto no es opcional: en los anfitriones medidos hasta ahora, un hook que llega al timeout del propio anfitrión no bloquea y la acción sigue, así que un check sin presupuesto propio convierte un cuelgue en un permiso silencioso. Pasarse mata el check y todo su grupo de procesos, y reporta `unresolved`.

Cada corrida agrega una fila a `~/.daimon/logs/checks.jsonl`: ids, resultados, causas, duraciones, anfitrión y modo. Nada del texto del comando, ninguna ruta, nada del sujeto. La razón que ve el agente puede nombrar una ruta; el log no.

El archivo tiene un tope de 256 KiB. Pasado eso el escritor conserva los últimos 64 KiB y descarta las filas más viejas, en el mismo lugar, así un hook que tenga el archivo abierto sigue escribiendo en el mismo. Los conteos que reportan las superficies de abajo son conteos sobre lo que queda, y cada una nombra la marca de tiempo donde arranca esa ventana.

Leé ese log para saber si el check está vivo, no para saber si se cumplió. Una fila prueba que el check CORRIÓ. Solo `decision_emitted: deny` bajo `enforce` cierra la distancia entre un check que corrió y un check que fue respetado.

Tres superficies lo leen por vos. `daimon ruling checks` lo pliega por regla y por anfitrión, `daimon stats` reporta una línea sobre todos los anfitriones, y `daimon ruling show` agrega una línea `Fired:` a una sola regla. Las tres reportan una marca de tiempo y conteos sobre la ventana conservada, nunca el último resultado: un log que solo agrega está ordenado por el agregado, así que la última fila no es el estado actual. Un check armado sin ninguna fila se lee como `never fired`, que es una respuesta distinta de cero violaciones. Un log que daimon no puede abrir es una tercera respuesta más: cada superficie dice que el log no se puede leer, en lugar de afirmar que no corrió nada.

Dos convenciones que conviene sostener. Armá un check nuevo con intención `warn` primero, así un patrón que agarra más de lo que pensabas cuesta una advertencia y no una acción bloqueada. Y corré `daimon ruling check try` antes de ratificar, para eso está.

| variable | por defecto | qué guarda |
| --- | --- | --- |
| `DAIMON_CHECKS_DIR` | `~/.daimon/checks` | el manifiesto y los cuerpos materializados |
| `DAIMON_CHECK_TIMEOUT` | `5` | el presupuesto del runner en segundos, con piso de 0.5 |
| `DAIMON_LOG_DIR` | `~/.daimon/logs` | contiene `checks.jsonl` |
