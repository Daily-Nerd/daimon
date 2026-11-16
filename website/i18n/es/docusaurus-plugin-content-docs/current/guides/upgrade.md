---
sidebar_position: 6
description: "Qué correr después de actualizar daimon: refrescar hooks y skills, rearmar los checks, confirmar con status y la mudanza única de bucket para stores previos a la 0.42.0."
---

# Actualizar daimon

```sh
uv tool upgrade daimon-briefing
```

Los scripts de hooks y los archivos de skill instalados son copias de contenido que se mueve con la CLI, así que refrescalos después de actualizar.

1. Refrescá los scripts de hooks en cada host que los tenga instalados: `daimon hooks install <host>`. Sin host, daimon detecta lo que corre esta máquina. Un host servido por el plugin de daimon no necesita nada.
2. Revisá las skills con `daimon skill status`. Informa cada skill instalada como `CURRENT`, `STALE`, `MISSING` y así, y sale con código distinto de cero si hay deriva. Arreglá la deriva con `daimon skill install`.
3. Si usás checks de reglas, `daimon hooks install` y `daimon check sync` reconstruyen el manifiesto que lee un host. `daimon check sync --check` lo audita y no escribe nada.
4. Corré `daimon status`. Una línea `legacy:` significa que existe un bucket escrito con la regla de nombres previa a la 0.42.0 para este proyecto, y la sección de abajo lo mueve.

Leé la entrada de la versión en `CHANGELOG.md` antes de cambiar. daimon es pre-1.0, así que un cambio incompatible sale como versión menor y figura bajo `BREAKING CHANGES`.

## Actualizar a través de la 0.42.0


Antes de la 0.42.0 la biblioteca nombraba el bucket de un proyecto a partir de
la ruta literal que recibía. Desde la 0.42.0 cada entry point resuelve la ruta
primero: la vuelve absoluta, colapsa los symlinks y después la normaliza al
toplevel de git. Por eso hay dos formas de ruta que ahora nombran un bucket
distinto al de antes:

- una ruta con un **symlink en cualquier parte**, en macOS eso incluye todo lo
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
