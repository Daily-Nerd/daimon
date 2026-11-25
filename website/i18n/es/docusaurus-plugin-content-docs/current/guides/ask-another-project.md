---
sidebar_position: 2
description: "Abrí una solicitud a otro proyecto, seguila y respondé una dirigida a vos: revise, withdraw, severity, accept, done y reply."
---

# Pedirle algo a otro proyecto

Usá esto cuando el trabajo de un proyecto depende de otro. La solicitud vive en el bucket de tu proyecto, y el otro proyecto responde en el suyo. El modelo detrás está en [solicitudes y decisiones](../concepts/requests-and-decisions.md).

## Abrir una solicitud

```sh
daimon request open --to <directorio> --ask "…" --why "…" --title "…"
```

`--to` toma el directorio del proyecto destinatario, validado contra los
proyectos que daimon ya conoce, con una sugerencia si hay un typo parecido.
`--title` es opcional: unas pocas palabras, 80 caracteres como máximo, que
dicen de qué trata el pedido. Lo encabeza donde se lista, y el ask conserva
el texto completo. Sin título, lo reemplaza la primera oración del ask.

El ask es también el texto que una persona firma cuando acepta, rechaza o da
por terminado el pedido, así que tiene que entrar en la pantalla de firma: como
máximo 2048 bytes de UTF-8 y 32 líneas tal como las muestra el firmador. Una
línea que la pantalla no puede contener sigue en la siguiente, y cada byte
fuera del ASCII simple (una letra con tilde son dos) se muestra como cuatro
caracteres, así que el texto en otros idiomas llega antes al límite. Un ask más
largo se rechaza al abrirlo o revisarlo, con su tamaño y el límite que pasó.
Los demás campos conservan su límite de 2000 caracteres.

`--severity` dice cuánto importa el resultado para quien pide, en una escala
fija: `critical`, `high`, `normal` y `low`. `normal` es el default y no muestra
nada. Cualquier otro nivel necesita `--severity-reason`, una línea de 80
caracteres como máximo (más largo se rechaza, no se corta), y se muestra junto
al nivel: `High: the release waits on this`. No es `--blocking`: la severidad
es cuánto importa, `--blocking` es que estás frenado. Los paneles de decisión y
de pendientes del destinatario, `request inbox`, `request list` y `daimon
decide` ordenan por eso, y dentro de un nivel conservan el orden por
antigüedad; los paneles siguen mostrando como máximo tres filas: la severidad
cambia cuáles tres. `request inbox --min-severity high` (y el argumento
`min_severity` de MCP) muestra ese nivel y los más severos. Cualquier canal
puede fijarla, porque no baja ninguna barra de aprobación. Un pedido que
reemplaza a otro con `--supersedes` no hereda la severidad.

Después de registrar el ask, `request open` puede imprimir una nota más.
Aparece cuando en el bucket del destinatario no se guardó ni se mostró nada
durante 14 días o más antes de que un bucket vecino estuviera activo: las dos
carpetas cuelgan del mismo padre y tienen nombres con prefijo en común, como
`Acme` y `AcmeV2`. Un bucket cuenta como visto cuando una sesión guardó un
checkpoint ahí o el brief o el hook de prompt le mostró un pedido. Las
decisiones tomadas desde esa carpeta no cuentan. La nota da los dos slugs y las
dos fechas y pregunta si son un mismo proyecto. Si lo son, pedile al dueño que
corra `daimon bucket merge`, que es lo que hace que los pedidos lleguen a una
sola bandeja. Si no lo son, ignorala. Nunca bloquea: el ask se escribe igual
que antes, y puede equivocarse cuando dos repos distintos tienen nombres
parecidos. Nunca se consideran los buckets sin carpeta registrada, los pares ya
fusionados ni los buckets de otros tenants.

## Como remitente

- **`revise`** responde un needs-info, o afina el pedido, hasta tres veces
  por registro; al cuarto intento se abre una solicitud nueva con
  `--supersedes` en vez de reescribir en silencio, así que el linaje queda
  visible.
- **`withdraw`** es la forma en que un remitente retira su propio pedido:
  `daimon request withdraw <id> --reason "<por qué>"`, desde cualquier canal,
  porque retirar tu propio pedido es un acto del remitente y no una decisión del destinatario.
  Funciona sobre un pedido abierto, en needs-info, aceptado o con un reclamo
  pendiente, y lo deja como `withdrawn`, con el motivo en `note`; no se
  completa ningún campo de decisión. Una solicitud ya terminada, rechazada o
  retirada queda como está, y el comando dice por qué. Es definitivo para ese
  registro: para volver a pedir, usá `--supersedes`. Un destinatario que ya
  había aceptado el pedido recibe un aviso de una línea, una sola vez, por la
  entrega en vivo. Solo el remitente puede retirarlo, y un remitente ya no
  puede hacer `reject`, `accept`, `needs-info`, `suppress` ni `done` sobre su
  propio pedido: cada evento de una solicitud pertenece a un rol, y una fila
  escrita desde el otro lado queda inerte.
- **`severity`** la cambia después: `daimon request severity <id> <nivel>
  --reason "<por qué>"`, desde cualquier canal, mientras el pedido está
  abierto, en needs-info o aceptado (`normal` la borra). Es un evento propio y
  no una revisión: no mueve el estado, no gasta ninguna de las tres
  revisiones, no descarta un reclamo de finalización y no vuelve a enviar nada.
  El nuevo nivel aparece en el próximo brief del destinatario, en `inbox` y en
  `daimon decide`. Solo el remitente puede cambiarla, y un pedido terminado,
  rechazado o retirado conserva el nivel que tenía.

## Como destinatario

- **`accept` / `reject` / `needs-info`** son la decisión del destinatario, tomada en una terminal interactiva. Cuándo puede un agente aceptar sin una persona, y por qué, está en [Quién decide](../concepts/requests-and-decisions.md#quién-decide). El rechazo es final para ese registro: el remitente abre una solicitud nueva en vez de pedir de nuevo.
- **`suppress`** saca un pedido del panel de briefing propio del
  destinatario, solo atención, solo humano; el registro sigue totalmente
  visible en `list` e `inbox`, y cualquier decisión posterior revierte la
  supresión.
- **`done`** reporta el pedido como satisfecho, desde cualquiera de los dos
  canales (humano o agente), por el proyecto al que se envió el pedido. El reclamo de un agente se renderiza sin verificar hasta que el
  próximo fin de sesión del destinatario verifica byte a byte la cita de
  evidencia contra su propia transcripción; un `done` humano se renderiza
  como está. En una solicitud `work` que nadie aceptó todavía, el `done` de
  un agente registra el reclamo pero deja abierta la cola de decisión, a la
  espera de `accept` o `reject`.
- **`reply`** le manda al remitente una nota de avance sobre una solicitud
  que aceptaste y no terminaste. No mueve ningún estado, así que la
  solicitud sigue pendiente hasta el `done`. La nota de un agente llega
  marcada como un reclamo de agente sin verificar. Cualquier otro estado la
  rechaza y nombra el verbo que corresponde. Una respuesta tardía se
  muestra una vez, aunque la decisión ya haya salido del panel.

## Ver qué está abierto

```sh
daimon request list    # las solicitudes que este proyecto envió
daimon request inbox   # las solicitudes dirigidas A este proyecto
```

Las dos aceptan `--json`. El propio briefing lleva cuatro paneles chicos con
los mismos datos: "solicitudes esperando por vos" y "solicitudes que aceptaste
y todavía debés" del lado del destinatario, y "decisiones sobre solicitudes que
enviaste" y "solicitudes que enviaste y nadie mueve" del lado del emisor, cada uno
con un tope y una línea de exceso bien visible en vez de un descarte
silencioso. El [servidor MCP](../reference/mcp.md) expone la vista del
destinatario en modo solo lectura, como la herramienta `requests_inbox`;
ningún verbo que escriba una solicitud es alcanzable por MCP, y
`daimon_brief` en sí nunca lleva contenido de solicitudes.
