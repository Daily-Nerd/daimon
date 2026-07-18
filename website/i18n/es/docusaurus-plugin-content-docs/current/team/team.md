# Memoria de equipo

La memoria de equipo refleja cada uno de tus checkpoints de sesión —
inmutables, un archivo por autor — a través de un repo git sidecar
compartido, así la memoria de todo un equipo converge sin fusionar las notas
de nadie con las de nadie más. Una vez habilitada, los temas activos y las
decisiones de tus compañeros aparecen atribuidos (nunca mezclados con los
tuyos) en `daimon brief --team`, y su historia es buscable junto a la tuya en
`daimon recall`.

## El repo compartido es la frontera de privacidad — lee esto primero

**El control de acceso del sidecar ES la frontera de membresía y privacidad.
Quien pueda leer ese repo git puede leer todo lo que las sesiones de cada
miembro produjeron, así que el remoto DEBE ser un repositorio privado.** No
hay una segunda puerta. Con la memoria de equipo activa, lo que una sesión
discutió se refleja al repo compartido *verbatim*, tras una pasada de
redacción de secretos basada en formas.

Esa redacción es deliberadamente estrecha. Atrapa secretos con una forma
concreta y reconocible — pares estilo asignación `api_key=…` / `token:…`,
bloques PEM de llaves y certificados, encabezados `Bearer`, URLs
`credential://user:pass@host`, y tokens de proveedor con prefijo fijo (AWS
`AKIA…`, Stripe `sk_live_…`, GitHub `ghp_…`, GitLab, Slack, Google, llaves de
OpenAI, JWTs). Cada coincidencia se reemplaza con un marcador visible
`[redacted:<kind>]`. **No** entiende significado: la prosa confidencial de
texto libre — el nombre de un cliente, un plan sin publicar, una URL interna
escrita como palabras comunes — no es una forma de secreto y se sincroniza
intacta. Trata el repo compartido como si cada miembro pudiera leer cada
palabra, porque pueden.

La identidad de autor se declara, no se autentica. Cualquier miembro del repo
puede escribir bajo cualquier nombre de autor; daimon cruza el nombre
sellado en cada archivo entrante contra el autor git que lo commiteó y
muestra la discrepancia como advertencia, pero nunca bloquea la escritura. El
control de acceso del repo es lo que deja fuera a los extraños — la etiqueta
de autor solo te dice de quién *afirma* ser un archivo.

## Configuración (dos personas)

Haz esto una vez por repo compartido, y luego una vez en cada máquina.

**1. Crea un repo git privado vacío** en el host que prefieras (GitHub,
GitLab, auto-hospedado — cualquier cosa que git pueda clonar por SSH o
HTTPS). Déjalo vacío; el primer `daimon team init` lo siembra. Asegúrate de
que sea **privado** y de que cada compañero tenga acceso de push.

**2. En cada máquina**, habilita la memoria de equipo y define tu nombre de
autor. Pon esto en `~/.daimon/env` (cargado por daimon) o expórtalo en tu
shell:

```sh
DAIMON_TEAM=1
DAIMON_AUTHOR="Ada Lovelace"   # opcional — mira abajo
```

`DAIMON_AUTHOR` es opcional. Sin definir, daimon cae a
`git config user.name`, luego a tu usuario del SO, y finalmente a `unknown`.
Defínelo explícitamente si tu identidad git no es el nombre que quieres que
vean tus compañeros.

**3. En cada máquina**, clona el sidecar:

```sh
daimon team init git@github.com:your-org/team-memory.git
```

Un remoto vacío está bien — la primera máquina en hacer init siembra un
commit raíz y lo pushea. Correr init una segunda vez contra un directorio que
ya existe es un error, no un re-clon.

**4. Verifica** en cada máquina:

```sh
daimon team status
```

Deberías ver el remoto listado con su frescura y los autores vistos hasta
ahora.

## Qué pasa, y cuándo

Al inicio de cada sesión, daimon dispara `daimon team sync` desacoplado en
segundo plano. Nunca bloquea tu briefing y nunca hace fallar tu sesión — si
algo sale mal, la sesión continúa exactamente como si la memoria de equipo
estuviera apagada. El lanzamiento está condicionado a que exista realmente un
remoto de equipo, así que las máquinas que nunca corrieron
`daimon team init` solo pagan un escaneo de directorio.

Una pasada de sync hace tres cosas, en orden:

- **Commitea y pushea solo tu propio directorio de autor.** Prepara y
  commitea archivos nuevos bajo `authors/<your-author-slug>/`, nada más —
  nunca archivos de otro autor, nunca algo que hayas dejado staged en el
  sidecar.
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
identidad de proyecto. Nunca se escriben punteros mutables al sidecar — como
cada autor anexa a una ruta disjunta y nada se reescribe, los merges son
libres de conflictos por construcción. Tus punteros locales de "último
checkpoint" permanecen privados en tu máquina y nunca se sincronizan.

`DAIMON_TEAM=1` regula **solo las escrituras**. Sin definirla, tus
checkpoints no se reflejan al sidecar — pero las lecturas del directorio de
equipo siempre están activas, así que puedes ver la memoria de tus compañeros
incluso antes de empezar a contribuir la tuya.

## Layout de proyectos: el árbol de squad escrito por el arquitecto

Los checkpoints en el sidecar se agrupan por **proyecto lógico**, así varios
repos pueden compartir un pool de memoria y el mismo repo mapea al mismo pool
en la máquina de cada compañero. La jerarquía es organizacional, no derivada
del forge: un arquitecto del equipo escribe `daimon-team.toml` en la raíz del
sidecar — el árbol de squad con los repos mapeados — y lo commitea como
cualquier otro archivo. **Daimon solo lee este archivo; los humanos lo
escriben y commitean.**

```toml
# daimon-team.toml — at the sidecar repo root, written by your team architect.
#
# One table per logical project. The key is the project's path in the squad
# tree (any depth); `repos` lists every repo that feeds that project's pool.
# ssh/https/scp spellings of the same repo are equivalent — the origin URL is
# normalized (scheme, credentials, `.git`, case) before matching.

[projects."core/cosmo/dusters/finance-1"]
repos = [
  "git@github.com:org/finance-svc.git",    # several repos → ONE shared pool
  "https://github.com/org/finance-web",
]

[projects."core/api-gateway"]
repos = ["git@github.com:org/gateway.git"]
```

En disco, los checkpoints de un repo mapeado aterrizan en
`projects/core/cosmo/dusters/finance-1/authors/<author-slug>/<session_id>.json`
— cada segmento de ruta se sanea para el filesystem, así una clave de config
nunca puede escapar del sidecar.

El proyecto lógico de una sesión se resuelve en este orden:

1. **`DAIMON_TEAM_PROJECT`** — una ruta relativa como `core/api-gateway`,
   definida por máquina. Intención local explícita; le gana al archivo de
   config.
2. **El mapeo de `daimon-team.toml`** — la URL `origin` del repo de la sesión
   se normaliza y se compara contra los `repos` de cada proyecto.
3. **Fallback derivado del origin** — la ruta de la URL de origin sin el host
   (`git@github.com:org/repo.git` → `projects/org/repo/…`). Los repos sin
   mapear igual obtienen identidad portátil, así el archivo de config es
   opcional e incremental — agrega mapeos a medida que el árbol de squad tome
   forma.
4. **Sin origin en absoluto** (sin git, sin remoto) — los archivos de
   checkpoint directamente bajo `authors/<author-slug>/`, exactamente como
   antes de que existiera este layout.

El archivo de config es opcional y puede agregarse o cambiarse en cualquier
momento — los repos sin mapear sincronizan bajo su ruta derivada del origin
desde el día uno. Remapear un repo nunca huérfana su historia previa: las
lecturas cubren también la ubicación anterior, así los checkpoints que ya
están bajo la ruta vieja siguen visibles tras la mudanza.

Un `daimon-team.toml` roto o imposible de parsear nunca bloquea una
escritura: el mapeo se trata como ausente (la resolución cae al fallback
derivado del origin) y el error de parseo se muestra como advertencia en
`daimon team status`. La membresía (abajo) es la única excepción — falla
**cerrada**, así un error de pegado nunca puede abrir el remoto a toda la
máquina.

**Nota de legado:** los sidecars escritos antes de este layout conservan sus
archivos planos `authors/<author-slug>/`. Esa era permanece legible para
siempre — las lecturas abarcan ambos layouts y no hay migración; los
checkpoints nuevos simplemente empiezan a aterrizar bajo `projects/` cuando
resuelve una identidad de proyecto.

## Qué proyectos sincronizan: la allowlist de alcance (cerrada por defecto)

`DAIMON_TEAM=1` es global a la máquina, pero un remoto sincronizado solo
acepta checkpoints de proyectos a los que **otorgó membresía**. Todo lo demás
se queda en el mirror local de la máquina (`<team_dir>/local/`) — retenido
del remoto, nunca perdido. Sin esta puerta, un remoto habilitado recibiría
cada proyecto de la máquina, incluidos los personales.

Un proyecto está en alcance para un remoto cuando se cumple cualquiera de
estas:

1. Su URL de origin está listada bajo la tabla `[scope]` de nivel superior
   del sidecar:

   ```toml
   [scope]
   repos = ["git@github.com:org/finance-svc.git"]
   ```

2. Su URL de origin está mapeada bajo cualquier tabla `[projects.*]` — un
   repo que el arquitecto colocó en el árbol de squad es miembro, así los
   sidecars mapeados existentes siguen sincronizando sin configuración
   nueva.
3. `DAIMON_TEAM_PROJECT` está definida en la máquina — intención local
   explícita.

`daimon team init` siembra un remoto fresco (vacío) con un
`daimon-team.toml` que limita el equipo al proyecto desde el que lo
corriste, así las configuraciones nuevas no necesitan pasos extra. Unirse a
un remoto establecido nunca escribe config — el arquitecto es dueño del
archivo después del nacimiento.

**Migrar un sidecar existente** (creado antes de que existiera el alcance):
agrega el bloque `[scope]` de arriba — una línea por repo que deba
sincronizar — commitea y pushea. Hasta entonces `daimon team status` muestra
`scope: none — this remote receives no checkpoints`. Si ya se acumularon
árboles de proyectos ajenos bajo `projects/`, elimínalos con un simple
`git rm -r projects/<path>` + push (reescribir la historia es opcional; sin
ella los archivos permanecen en la historia de git).

## Leyendo a tus compañeros

- **`daimon brief --team`** — tu briefing normal, más una sección Teammates:
  un bloque atribuido por compañero (excluyéndote), el más nuevo primero. Sin
  datos de equipo la sección simplemente no aparece.
- **`daimon recall <query>`** — búsqueda de texto completo (SQLite FTS5)
  sobre tu historia local *y* la del equipo.
- **`daimon team status`** — frescura por remoto, tu propio conteo de
  checkpoints sin pushear, los autores vistos en el sidecar, y la allowlist
  de alcance (qué repos pueden sincronizar a cada remoto).

Los checkpoints de compañeros se muestran dentro de una ventana de edad de
lectura controlada por `DAIMON_TEAM_RETENTION_DAYS` (default 365; `0`
significa conservar todo). Salir por edad es solo un filtro de lectura —
ningún archivo se borra físicamente de la rama compartida de solo-anexado.

## Referencia de entorno

| Variable | Default | Qué hace |
|---|---|---|
| `DAIMON_TEAM` | sin definir (off) | Ponla en `1` para reflejar tus checkpoints en el directorio de equipo. Regula solo escrituras; las lecturas siempre están activas. |
| `DAIMON_AUTHOR` | `git config user.name` → usuario del SO → `unknown` | El nombre de autor bajo el cual se archivan tus checkpoints. |
| `DAIMON_TEAM_DIR` | `~/.daimon/team` | Raíz del mirror local de equipo (un subdirectorio por clon de sidecar). |
| `DAIMON_TEAM_PROJECT` | sin definir | Ruta lógica de proyecto explícita (p. ej. `core/api-gateway`) para las sesiones de esta máquina. Le gana al mapeo de `daimon-team.toml` y al fallback derivado del origin. |
| `DAIMON_TEAM_RETENTION_DAYS` | `365` | Ventana de edad al leer los checkpoints de compañeros; `0` = conservar todos. |

Mira la [referencia de configuración](../getting-started/configuration.md)
para el entorno completo.

## Cuando algo sale mal

El sync es fail-open por diseño: cualquier resultado degradado te deja en el
último estado sincronizado y nunca rompe la sesión.

| Situación | Qué pasa |
|---|---|
| Sin conexión | El sync se pospone; conservas el último estado de equipo sincronizado. Tus checkpoints nuevos commitean localmente y quedan en cola para el siguiente push. |
| Credenciales git ausentes | Git corre no-interactivo (`GIT_TERMINAL_PROMPT=0`) — falla rápido en lugar de colgarse en un prompt de credenciales, y el sync se degrada a sin-conexión. |
| Pushes concurrentes | Un push rechazado es benigno (un compañero ganó la carrera). Daimon integra su cambio y reintenta, acotado a 3 intentos, y luego advierte si sigue rechazado. |
| Historia compartida reescrita | Advertencia fuerte; daimon deja tu copia local intacta y se niega a auto-reparar. Daimon nunca hace force-push — resuélvelo a mano con git y con tu equipo. |
| Git no instalado | El sync se omite y devuelve éxito (rc 0) — la memoria de equipo simplemente está inactiva. |

## Estado

La memoria de equipo es temprana. Está diseñada y validada a escala de 1–2
personas, donde el modelo de anexado libre de conflictos del sidecar git y la
frontera del repo privado se sostienen limpiamente. **No** es una frontera
multi-tenant defendida: el modelo de seguridad es "todos en el repo privado
confían en todos en el repo privado". Dimensiona la membresía del repo en
consecuencia.
