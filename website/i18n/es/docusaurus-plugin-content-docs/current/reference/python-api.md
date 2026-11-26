---
sidebar_position: 7
description: "Lo que puede llamar un proceso anfitrión: reglas desde un host, la promesa de estabilidad, la superficie git sellada y los lectores del briefing."
---

# API de Python

Un proceso anfitrión que embebe daimon llama a la biblioteca directamente. Esta página lista las llamadas con las que puede contar.

## Reglas desde un proceso anfitrión

La CLI emite solo dos canales: `cli-tty` (una terminal interactiva) y `cli-agent` (`--by agent`). Nunca va a tener una bandera para los otros dos canales humanos, `ui` y `signed`, porque una bandera que un agente pudiera pasar desde un shell sería un canal humano autodeclarado. Esos dos existen para un proceso anfitrión que tiene la autoridad por sí mismo, un operador que verificó por fuera, y se escriben por la librería, en el mismo proceso:

- `daimon_briefing.refutations.ratify(refutation_id, channel="signed", note="...", project_dir=...)` activa una regla propuesta. `channel` es el canal que el anfitrión observó, uno de `ui` o `signed`; cualquier otro nombre se rechaza. Citá la prueba de la acción del operador en la evidencia de la regla (`url:`, o `receipt:` cuando exista la firma). El `check_sha256=` opcional fija el cuerpo del check que el operador vio, igual que la clave del texto de la regla; una regla sin check se activa con ese campo vacío. Una regla que lleva un check se activa solo mediante un ratify que pase el pin del check que mostró; un revise en proceso desde un canal humano que entrega un check nuevo lo arma tal cual, así que la confirmación queda a cargo del anfitrión.
- `daimon_briefing.refutations.listing(states={"active"}, polarity="ruling", project_dir=...)` y `daimon_briefing.briefing.active_rulings(project_dir)` leen el conjunto activo, cada fila con `subject`, `scope`, `anchors`, `activation_channel`, `evidence` y el texto de la regla. Los `anchors` son cadenas libres que se fijan con `ruling propose --anchor`; un anfitrión que aplica reglas por mensaje compara contra ellos y decide por su cuenta qué pasa.
- Estos dos lectores ahora difieren. `listing` se queda en el propio bucket de este proyecto, y solo sus filas alimentan el manifiesto de checks que lee un hook del anfitrión, así que un check nunca se arma dos veces bajo el mismo proyecto. `active_rulings` es la vista combinada que renderiza un briefing: las propias filas de este proyecto más cada regla activa de una capa por encima de él, capas primero, la propia gana ante un id compartido, una fila heredada de política de pedidos se descarta en lugar de renderizarse, y cada fila de capa lleva `inherited_from` con el directorio absoluto de donde vino. `daimon ruling list --inherited` es la forma en CLI de esa lectura combinada: sin la bandera se queda solo en el propio bucket, y con ella las filas de capa se suman después de las propias, cada una llevando `inherited_from` también en `--json`; una fila propia nunca lleva esa clave.
- `daimon_briefing.briefing.rulings_read(project_dir)` es la función hermana para un anfitrión al que una respuesta vacía no le alcanza: `active_rulings` siempre devuelve una lista simple y convierte cada falla en una lista vacía, así que un proyecto que nunca se pudo identificar, una ruta mal resuelta, un ledger ilegible y un proyecto genuinamente sin reglas se leen todos exactamente igual. `rulings_read` nunca lanza una excepción y siempre devuelve uno de cuatro valores de `state`, junto con las mismas `rows` y el `path` al que se resolvió: `"unresolved"` (ni siquiera se pudo resolver la ruta del ledger, un problema de configuración; `path` es None solo en este caso), `"no-bucket"` (la ruta se resolvió, pero nunca se escribió nada desde este proyecto), `"unreadable"` (el bucket existe pero el ledger no se pudo leer), o `"read"` (una lectura normal, incluido un bucket vacío pero limpio). `active_rulings` está construida encima, tampoco lanza excepciones nunca, y no puede desviarse de ella.

El registro se muestra como `ratified (signed)` o `ratified (ui)`, nunca como ratificado por humano sin el nivel. Nada local es infalsificable: quien tiene acceso a la máquina puede manejar una UI o abrir una terminal. Lo que el canal gana es procedencia, no prueba; falsificar cuesta una suplantación deliberada en vez de una palabra, y el canal queda auditable después.

En las dos llamadas de arriba, y en los helpers de los ledgers de refutaciones, pedidos, enmiendas y relaciones que están detrás, `project_dir` se resuelve igual que la CLI resuelve `--project`: se vuelve absoluto, se colapsan los symlinks y después se normaliza al toplevel de git. Un anfitrión parado en un subdirectorio de un repositorio lee el mismo bucket que la CLI lee desde la raíz del repositorio. `daimon_briefing.config.resolve_project_dir(path)` es la función pública que devuelve el directorio al que rutea una ruta. El store de checkpoints resuelve igual, en cada entry point público que recibe un `project_dir`, así que un checkpoint y un ruling escritos desde el mismo directorio en el mismo proceso caen en un solo bucket. `daimon_briefing.store.project_bucket(path)` devuelve el nombre del bucket que va a usar el store, para un anfitrión que quiera chequear antes de escribir. La única función que sigue siendo literal es `store.project_slug`, la transformación de caracteres que imprime `daimon slug`, que tiene que responder para una ruta que daimon nunca vio.

### Con qué puede contar un host

daimon está antes de la 1.0. Una versión que rompa algo de lo descrito acá llega como un salto de versión **menor**, no mayor, así que el número de versión por sí solo no te va a avisar. Leé esa frase dos veces si estás acostumbrado al significado habitual de una versión menor.

La estabilidad no es la promesa que podemos sostener en esta etapa. La visibilidad sí. Cualquier cambio en las llamadas de arriba, en los nombres de canal aceptados, en los textos de activación que se muestran, o en los campos de fila que se listan abajo, sale como un commit que rompe compatibilidad, y por lo tanto aparece bajo `### ⚠ BREAKING CHANGES` en `CHANGELOG.md`, con prosa que dice qué se movió y qué hacer al respecto.

Entonces: fijá una versión exacta y leé esa sección de `CHANGELOG.md` antes de pasar a otra.

Los campos de fila que lee un host son el id del registro, su estado, asunto, alcance, anclas, la activación que se muestra y el canal por el que llegó, la lista de evidencia, y el texto de la regla. Los dos lectores de arriba devuelven el mismo conjunto.

Deliberadamente fuera de la promesa: el orden en que vuelven las filas, la redacción de diagnósticos y líneas de log, el texto del mensaje de un error lanzado (usá el tipo de excepción en su lugar), y todo lo que no esté nombrado en esta página. Que se pueda importar no es lo mismo que que esté documentado.

## Leer un briefing

`api.render_briefing(project_dir, now=None, loops_pointer=False)` devuelve el texto que `daimon_brief` devuelve por MCP: el batón, una advertencia de formato de versión, las reglas vigentes, el conteo de decisiones, los cuatro paneles de pedidos, el cuerpo y el pie de lo retenido, con el mismo compositor que usa `daimon brief`. Devuelve un `RenderedBriefing(text, state, notes, ..., printed)` y nunca lanza; un fallo es el estado `unavailable`. Los paneles leen los ledgers de pedidos del proyecto pedido.

Acá leer no está libre de registro. Una API de host no tiene un punto posterior, así que el retorno es el límite de entrega: `render_briefing` marca como vistas por quien las envió (una fila `surfaced` en el ledger de este proyecto) las tarjetas de pedidos y de decisiones que su texto imprimió completas, y registra las filas de evidencia del mundo que corrió cuando `DAIMON_WORLDCHECK` está activo. Una tarjeta que el presupuesto de bytes contrajo a un conteo no se marca. `api.preview_briefing` recibe los mismos argumentos, devuelve el mismo texto y las mismas tarjetas en `printed`, y no escribe nada, para quien lee un briefing sin entregárselo a un agente. `api.briefing_text` es el texto de `render_briefing`, así que también marca tarjetas.

`api.parse_briefing(text)` lee un briefing de vuelta (`api.BRIEFING_FORMAT` es su versión, 4). Además de `header`, `standing_rulings`, `items`, `warnings` y `handoff`, devuelve los pedidos, el conteo y el pie, cada campo `None` cuando el texto no tiene esa sección:

| Campo | Qué contiene |
|---|---|
| `requests`, `verdicts`, `owed`, `overdue` | Un `Panel` cada uno: `cards` (un `PanelCard` con el `request_id`, la `line` de la tarjeta y sus líneas `detail` con sangría), `more` (el conteo de `(+N more …)`), `cut` (tarjetas que un panel contraído perdió por el presupuesto) y `lines` (todas las líneas del bloque). |
| `decisions` | Un `DecisionCount(here, elsewhere)`. |
| `withheld` | Un `Withheld(resolved, quarantined)`. |

Un panel se lee como un bloque desde su título hasta la siguiente línea en blanco, por su título y nunca por una marca, así que la flecha de un batón y la de una tarjeta de pedido no se confunden, y el texto que un par escribió dentro de una tarjeta se queda en esa tarjeta. La advertencia de formato de versión es una de las `warnings`. Los bytes de `render_briefing` son estables bajo las condiciones que lista su docstring, que ahora incluyen los ledgers de pedidos del proyecto y de los proyectos con los que intercambia pedidos, y el `now` que fija las edades de lo vencido.

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
