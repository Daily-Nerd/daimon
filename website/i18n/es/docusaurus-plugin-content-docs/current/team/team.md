---
description: "Compartir memoria de agentes en un equipo a traves de un repo sidecar de git. Los checkpoints quedan inmutables y atribuidos, y el repo es el limite de privacidad."
---

# Memoria de equipo

La memoria de equipo refleja cada uno de tus checkpoints de sesión , 
inmutables, un archivo por autor, a través de un repo git sidecar
compartido, así la memoria de todo un equipo converge sin fusionar las notas
de nadie con las de nadie más. Una vez habilitada, los temas activos y las
decisiones de tus compañeros aparecen atribuidos (nunca mezclados con los
tuyos) en `daimon brief --team`, y su historia es buscable junto a la tuya en
`daimon recall`.

## El repo compartido es la frontera de privacidad, lee esto primero

**El control de acceso del sidecar ES la frontera de membresía y privacidad.
Quien pueda leer ese repo git puede leer todo lo que las sesiones de cada
miembro produjeron, así que el remoto DEBE ser un repositorio privado.** No
hay una segunda puerta. Con la memoria de equipo activa, lo que una sesión
discutió se refleja al repo compartido *verbatim*, tras una pasada de
redacción de secretos basada en formas.

Esa redacción es deliberadamente estrecha. Atrapa secretos con una forma
concreta y reconocible, pares estilo asignación `api_key=…` / `token:…`,
bloques PEM de llaves y certificados, encabezados `Bearer`, URLs
`credential://user:pass@host`, y tokens de proveedor con prefijo fijo (AWS
`AKIA…`, Stripe `sk_live_…`, GitHub `ghp_…`, GitLab, Slack, Google, llaves de
OpenAI, JWTs). Cada coincidencia se reemplaza con un marcador visible
`[redacted:<kind>]`. **No** entiende significado: la prosa confidencial de
texto libre, el nombre de un cliente, un plan sin publicar, una URL interna
escrita como palabras comunes, no es una forma de secreto y se sincroniza
intacta. Trata el repo compartido como si cada miembro pudiera leer cada
palabra, porque pueden.

La identidad de autor se declara, no se autentica. Cualquier miembro del repo
puede escribir bajo cualquier nombre de autor; daimon cruza el nombre
sellado en cada archivo entrante contra el autor git que lo commiteó y
muestra la discrepancia como advertencia, pero nunca bloquea la escritura. El
control de acceso del repo es lo que deja fuera a los extraños, la etiqueta
de autor solo te dice de quién *afirma* ser un archivo.

## Qué pasa, y cuándo

Al inicio de cada sesión, daimon dispara `daimon team sync` desacoplado en
segundo plano. Nunca bloquea tu briefing y nunca hace fallar tu sesión, si
algo sale mal, la sesión continúa exactamente como si la memoria de equipo
estuviera apagada. El lanzamiento está condicionado a que exista realmente un
remoto de equipo, así que las máquinas que nunca corrieron
`daimon team init` solo pagan un escaneo de directorio.

Una pasada de sync hace tres cosas, en orden:

- **Commitea y pushea tu propio directorio de autor, más las reglas de
  merge.** Prepara y commitea archivos nuevos bajo
  `authors/<your-author-slug>/`: nunca archivos de otro autor, nunca algo que
  hayas dejado staged en el sidecar. La única otra ruta que commitea es el
  `.gitattributes` de la raíz del clon, cuando le faltan las reglas
  `merge=union` para los ledgers publicados (y para el propio archivo de
  atributos); el contenido de esas líneas es fijo, así que dos clones que las
  commitean mergean limpio.
- **Trae las actualizaciones de tus compañeros**, pero solo cuando el remoto
  realmente cambió. Un probe `ls-remote` de solo-refs compara hashes primero;
  los objetos se transfieren solo ante una discrepancia, así un sync sin
  cambios cuesta un viaje de red liviano.
- Deja todo lo demás en paz.

Solo archivos de checkpoint inmutables por autor se sincronizan. En disco
viven bajo `~/.daimon/team/<remote-slug>/`; dentro del repo compartido la
ruta es
`projects/<logical/path>/authors/<author-slug>/<session_id>.json` (mira la
sección de layout de proyectos abajo), o
`authors/<author-slug>/<session_id>.json` cuando no resuelve ninguna
identidad de proyecto. Nunca se escriben punteros mutables al sidecar, como
cada autor anexa a una ruta disjunta y nada se reescribe, los merges son
libres de conflictos por construcción. Tus punteros locales de "último
checkpoint" permanecen privados en tu máquina y nunca se sincronizan.

`DAIMON_TEAM=1` regula **solo las escrituras**. Sin definirla, tus
checkpoints no se reflejan al sidecar, pero las lecturas del directorio de
equipo siempre están activas, así que puedes ver la memoria de tus compañeros
incluso antes de empezar a contribuir la tuya.

## Configurar

- [Configurar la memoria de equipo](./setup.md): la configuración de dos personas, paso a paso.
- [Configuración del equipo](./configuration.md): `daimon-team.toml`, la allowlist de alcance y las variables de entorno.
- [Resolución de problemas](../guides/troubleshooting.md): qué pasa cuando la sincronización no llega al remoto.

## Leyendo a tus compañeros

- **`daimon brief --team`**, tu briefing normal, más una sección Teammates:
  un bloque atribuido por compañero (excluyéndote), el más nuevo primero. Sin
  datos de equipo la sección simplemente no aparece.
- **`daimon recall <query>`**, búsqueda de texto completo (SQLite FTS5)
  sobre tu historia local *y* la del equipo.
- **`daimon team status`**, frescura por remoto, tu propio conteo de
  checkpoints sin pushear, los autores vistos en el sidecar, la allowlist
  de alcance (qué repos pueden sincronizar a cada remoto), una advertencia
  cuando el `.gitattributes` del clon carece de la regla `merge=union` para
  los ledgers publicados, y una línea para el proyecto actual cuando sus
  cuarentenas activas aún no están publicadas (ver "Las cuarentenas también
  viajan" abajo).

Los checkpoints de compañeros se muestran dentro de una ventana de edad de
lectura controlada por `DAIMON_TEAM_RETENTION_DAYS` (default 365; `0`
significa conservar todo). Salir por edad es solo un filtro de lectura , 
ningún archivo se borra físicamente de la rama compartida de solo-anexado.

### La compuerta de entrada

Toda política que daimon aplica a tus propios checkpoints al salir se vuelve a
aplicar sobre el checkpoint de un compañero al entrar, en el momento de lectura
o indexado (en memoria: los archivos del sidecar y la historia de git nunca se
reescriben):

- **Alcance**: el contenido de un remoto que no pasa la verificación de alcance
  de arriba nunca llega al briefing ni al índice de recall.
- **Redacción**: tus patrones locales de secretos vuelven a depurar el texto
  entrante antes de mostrarlo o indexarlo. Un compañero con un daimon más viejo
  y menos patrones no puede sembrar una copia en claro duradera en tu máquina.
- **Forget**: los valores que marcaste con tombstone usando `daimon forget`
  también se descartan de los checkpoints entrantes: la copia de un compañero no
  puede hacer reaparecer algo que borraste localmente.
- **Confianza**: un ítem `verbatim` de un compañero se guarda y se puntúa como
  `inferred`, porque su receipt solo se puede verificar en la máquina que lo
  capturó. En la sección Teammates se muestra con una etiqueta visible:
  `[teammate claims verbatim — unverifiable here]`, que declara ambos hechos: la
  afirmación, y que no se puede comprobar localmente. Tus propios ítems no se
  ven afectados.

### Las cuarentenas también viajan

Una cuarentena es la decisión de una persona de que un valor no es seguro para
actuar sobre él (`daimon trust propose`, confirmada o liberada por un humano).
Cuando activas una, o la levantas, y hay un equipo habilitado, daimon agrega
una fila solo con hashes a tu propio `quarantines.jsonl`, junto a tus
tombstones, en cada sidecar al que el proyecto enruta: el tipo, un hash del
valor, `active` o `released`, un sello de orden e ids. Nunca el texto, la razón
ni la evidencia. El candidato de un agente y un descarte no publican nada. Una
liberación también llega a todo sidecar tuyo que aún conserve la afirmación,
lo enrute o no el proyecto ahora.

- **Alcance.** La cuarentena de un compañero retiene ese valor, de ese tipo, en
  toda tu máquina, en todos los proyectos, como lo hace un forget. Ves
  `[withheld: quarantine]` sin id, en todos los canales, terminal incluida.
  `daimon forget`, `daimon resolve` y `daimon reverify` sobre ese id exacto se
  rechazan ("a teammate quarantined this; they release it"). Solo ese
  compañero la libera.
- **Las tuyas siguen siendo tuyas.** Tus cuarentenas propias siguen operando por
  proyecto desde tu ledger local. La misma persona en una segunda máquina debe
  proponerla también allí.
- **Verlas.** `daimon trust list` termina con un conteo de las cuarentenas que
  los compañeros tienen vigentes. `daimon trust list --team [--json]` muestra
  tipo y conteo en cualquier canal, y el directorio del autor y la hora en una
  terminal.
- **Reenviar.** `daimon trust republish` (una persona en una terminal, nunca
  `--by agent`) reenvía sin cambios las cuarentenas vigentes del proyecto y la
  última liberación de cada una liberada. Úsalo tras una publicación fallida
  (`trust` sale con 4 y dice qué sidecar), tras habilitar el equipo, tras recibir
  acceso a un sidecar, o tras `daimon ledger repair trust`. Varios autores se
  combinan por unión: la liberación de un compañero solo levanta su propia
  afirmación.
- **Ver lo que sigue local.** `daimon team status` termina con una línea para
  el proyecto desde el que lo corres cuando su ledger tiene una cuarentena
  activa cuya última fila falta en un clon al que el proyecto rutea:
  `<project>: 2 active quarantine(s) not yet published; run daimon trust
  republish`. La presencia se verifica por el id de la fila, igual que lo hace
  el publicador, y el comando no escribe nada. Una cuarentena liberada nunca
  cuenta. Cuando el ledger de un clon no se puede leer, la línea lo dice en vez
  de reportar cero. Con `--json` la fila de cada remoto ruteado lleva el mismo
  conteo bajo `unpublished_quarantines`.
- **Autoridad.** El acceso de escritura al sidecar es la frontera de confianza,
  igual que para checkpoints y tombstones. Las filas publicadas son
  afirmaciones declaradas, no autenticadas. Un miembro con acceso de escritura
  puede sobre-retener publicando una fila `active`, y puede levantar la
  cuarentena de un compañero con una fila `released` o borrando líneas (borrar
  un tombstone ya vuelve a exponer un valor olvidado). El sync avisa cuando un
  commit de alguien distinto del dueño del directorio cambió un ledger
  publicado. Es higiene, no prueba, y no detecta una fila falsificada. Las filas publicadas
  son de versión 1; un lector que encuentra una versión de fila que no conoce
  omite a ese autor hasta actualizarse.
- **Un olvido no se retracta.** Una fila de tombstone publicada no tiene
  retractación porque las filas de tombstone no llevan versión ni un gancho que
  falle cerrado; esa compuerta existe solo para las filas de cuarentena.
  `daimon unforget` levanta un olvido en tu propia máquina y deja la fila que
  publicaste: la fila que tus compañeros ya integraron se queda, y siguen
  reteniendo el valor hasta que la quiten ellos.
- **Un ledger dañado omite al autor.** Una línea basura o una última línea
  cortada en cualquiera de los ledgers publicados de un compañero significa que
  su afirmación más nueva no se puede probar, así que sus checkpoints no se
  admiten hasta que el archivo esté limpio, y recibes la nota `author-skipped`.
  Las filas buenas anteriores siguen contando. Cualquiera con acceso de
  escritura al sidecar puede hacerle esto a cualquier autor, para todo el
  equipo, con una sola línea dañada en el archivo de ese autor; la cura es que
  el dueño arregle el archivo en git.
- **Lo que no hace.** El mirror sigue llevando el texto de un checkpoint, y el
  historial de git lo conserva. Una cuarentena retiene al leer y nunca
  reescribe; `daimon forget` es la herramienta de borrado.

## Estado

La memoria de equipo es temprana. Está diseñada y validada a escala de 1–2
personas, donde el modelo de anexado libre de conflictos del sidecar git y la
frontera del repo privado se sostienen limpiamente. **No** es una frontera
multi-tenant defendida: el modelo de seguridad es "todos en el repo privado
confían en todos en el repo privado". Dimensiona la membresía del repo en
consecuencia.
