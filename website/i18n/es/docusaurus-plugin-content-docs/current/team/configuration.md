---
sidebar_position: 3
description: "El árbol de squad de daimon-team.toml, la allowlist de alcance que decide qué proyectos sincronizan y las variables de entorno del equipo."
---

# Configuración del equipo

Referencia de los archivos y variables que controlan la memoria de equipo. Para configurarla desde cero, seguí [configurar la memoria de equipo](./setup.md). Las demás variables están en la [referencia de configuración](../reference/configuration.md).

## Layout de proyectos: el árbol de squad escrito por el arquitecto

Los checkpoints en el sidecar se agrupan por **proyecto lógico**, así varios
repos pueden compartir un pool de memoria y el mismo repo mapea al mismo pool
en la máquina de cada compañero. La jerarquía es organizacional, no derivada
del forge: un arquitecto del equipo escribe `daimon-team.toml` en la raíz del
sidecar, el árbol de squad con los repos mapeados, y lo commitea como
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
,  cada segmento de ruta se sanea para el filesystem, así una clave de config
nunca puede escapar del sidecar.

El proyecto lógico de una sesión se resuelve en este orden:

1. **`DAIMON_TEAM_PROJECT`**, una ruta relativa como `core/api-gateway`,
   definida por máquina. Intención local explícita; le gana al archivo de
   config.
2. **El mapeo de `daimon-team.toml`**, la URL `origin` del repo de la sesión
   se normaliza y se compara contra los `repos` de cada proyecto.
3. **Fallback derivado del origin**, la ruta de la URL de origin sin el host
   (`git@github.com:org/repo.git` → `projects/org/repo/…`). Los repos sin
   mapear igual obtienen identidad portátil, así el archivo de config es
   opcional e incremental, agrega mapeos a medida que el árbol de squad tome
   forma.
4. **Sin origin en absoluto** (sin git, sin remoto), los archivos de
   checkpoint directamente bajo `authors/<author-slug>/`, exactamente como
   antes de que existiera este layout.

El archivo de config es opcional y puede agregarse o cambiarse en cualquier
momento, los repos sin mapear sincronizan bajo su ruta derivada del origin
desde el día uno. Remapear un repo nunca huérfana su historia previa: las
lecturas cubren también la ubicación anterior, así los checkpoints que ya
están bajo la ruta vieja siguen visibles tras la mudanza.

Un `daimon-team.toml` roto o imposible de parsear nunca bloquea una
escritura: el mapeo se trata como ausente (la resolución cae al fallback
derivado del origin) y el error de parseo se muestra como advertencia en
`daimon team status`. La membresía (abajo) es la única excepción, falla
**cerrada**, así un error de pegado nunca puede abrir el remoto a toda la
máquina.

**Nota de legado:** los sidecars escritos antes de este layout conservan sus
archivos planos `authors/<author-slug>/`. Esa era permanece legible para
siempre, las lecturas abarcan ambos layouts y no hay migración; los
checkpoints nuevos simplemente empiezan a aterrizar bajo `projects/` cuando
resuelve una identidad de proyecto.

**Segmento reservado.** `authors` nombra el nivel de directorio por autor, así
que no puede aparecer como segmento de la ruta lógica de un proyecto.
`daimon team init` rechaza un proyecto cuya ruta lo contenga, y el resolvedor
descarta esa ruta (venga de `DAIMON_TEAM_PROJECT`, del fallback derivado del
origin o de una tabla `[projects."…"]`, que `daimon team status` reporta).
Migración: renombra el segmento en `daimon-team.toml` o en
`DAIMON_TEAM_PROJECT`. Los archivos que ya estén bajo una ruta que contenga
`authors` no se leen hasta renombrarla. Leer menos no es la dirección segura para
los tombstones: hasta el renombrado, un valor que alguien olvidó bajo esa ruta
no se suprime en tu máquina y puede reaparecer.

## Qué proyectos sincronizan: la allowlist de alcance (cerrada por defecto)

`DAIMON_TEAM=1` es global a la máquina, pero un remoto sincronizado solo
acepta checkpoints de proyectos a los que **otorgó membresía**. Todo lo demás
se queda en el mirror local de la máquina (`<team_dir>/local/`), retenido
del remoto, nunca perdido. Sin esta puerta, un remoto habilitado recibiría
cada proyecto de la máquina, incluidos los personales.

La misma allowlist regula también las **lecturas**: `brief --team` y el índice de
recall solo admiten el contenido de un remoto sincronizado cuando pasa la
verificación de alcance. Un remoto al que este proyecto nunca se le otorgó no
aporta nada al briefing ni al índice de búsqueda de este proyecto: los archivos
quedan intactos en el clon sidecar, simplemente no entran a las superficies
locales. El mirror local de la máquina (`<team_dir>/local/`, tus propias
escrituras retenidas) no necesita otorgamiento.

Un proyecto está en alcance para un remoto cuando se cumple cualquiera de
estas:

1. Su URL de origin está listada bajo la tabla `[scope]` de nivel superior
   del sidecar:

   ```toml
   [scope]
   repos = ["git@github.com:org/finance-svc.git"]
   ```

2. Su URL de origin está mapeada bajo cualquier tabla `[projects.*]`, un
   repo que el arquitecto colocó en el árbol de squad es miembro, así los
   sidecars mapeados existentes siguen sincronizando sin configuración
   nueva.
3. `DAIMON_TEAM_PROJECT` está definida en la máquina, intención local
   explícita.

`daimon team init` siembra un remoto fresco (vacío) con un
`daimon-team.toml` que limita el equipo al proyecto desde el que lo
corriste, así las configuraciones nuevas no necesitan pasos extra. Unirse a
un remoto establecido nunca escribe config, el arquitecto es dueño del
archivo después del nacimiento.

**Migrar un sidecar existente** (creado antes de que existiera el alcance):
agrega el bloque `[scope]` de arriba, una línea por repo que deba
sincronizar, commitea y pushea. Hasta entonces `daimon team status` muestra
`scope: none — this remote receives no checkpoints`. Si ya se acumularon
árboles de proyectos ajenos bajo `projects/`, elimínalos con un simple
`git rm -r projects/<path>` + push (reescribir la historia es opcional; sin
ella los archivos permanecen en la historia de git).

## Referencia de entorno

Las variables de entorno de la memoria de equipo están en la [sección Memoria de equipo de la referencia de configuración](../reference/configuration.md#memoria-de-equipo), que es su único lugar.
