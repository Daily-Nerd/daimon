---
sidebar_position: 2
description: "Configurá la memoria de equipo entre dos personas: creá el repo sidecar privado, apuntá cada máquina a él y comprobá que los checkpoints sincronizan."
---

# Configurar la memoria de equipo

Esta es la configuración de dos personas, paso a paso. Qué comparte la memoria de equipo, y por qué el repo compartido es la frontera de privacidad, está en [memoria de equipo](./team.md). El formato de `daimon-team.toml` y las variables de entorno están en [configuración del equipo](./configuration.md).

Haz esto una vez por repo compartido, y luego una vez en cada máquina.

**1. Crea un repo git privado vacío** en el host que prefieras (GitHub,
GitLab, auto-hospedado, cualquier cosa que git pueda clonar por SSH o
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

Un remoto vacío está bien, la primera máquina en hacer init siembra un
commit raíz y lo pushea. Correr init una segunda vez contra un directorio que
ya existe es un error, no un re-clon.

`team init` también escribe un `.gitattributes` en la raíz del clon con una
regla `merge=union` para los ledgers publicados (`authors/*/tombstones.jsonl`,
`authors/*/quarantines.jsonl` y sus formas bajo `projects/**/`, más una para el
propio archivo de atributos, así que las copias de las reglas de dos clones
mergean limpio). Ambos ledgers
son de solo agregar y cada autor escribe únicamente su propio archivo, así que
el único conflicto que dos máquinas de un mismo autor pueden producir es que
ambas agreguen una línea después de la misma base; la regla conserva ambas
líneas en vez de dejar marcadores de conflicto que hacen que el lector omita al
autor. Sobre un archivo existente solo se agregan las líneas que faltan.
`daimon team sync` escribe y commitea la regla antes de mergear, así que un
clon creado antes de que la regla existiera la recibe en su siguiente sync y
los clones del equipo convergen; no agregues las líneas a mano, una copia sin
trackear bloquea el merge que trae la copia commiteada de un compañero.
`daimon team status` nombra un clon cuyo archivo carece de la regla.

**4. Verifica** en cada máquina:

```sh
daimon team status
```

Deberías ver el remoto listado con su frescura y los autores vistos hasta
ahora.
