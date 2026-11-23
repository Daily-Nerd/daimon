---
sidebar_position: 9
description: "Mover daimon a una máquina nueva, a otro usuario o a una carpeta renombrada sin perder el historial de un proyecto: qué copiar, qué dejar y los comandos en orden."
---

# Mover daimon a una máquina o carpeta nueva

La memoria de un proyecto vive en un bucket: un directorio bajo `checkpoints/` cuyo nombre nunca cambia. El bucket registra la carpeta donde vive su proyecto, en un archivo llamado `root`. Mover un proyecto es decirle a daimon la carpeta nueva. No se renombra nada y no se reescribe ningún archivo firmado, así que el historial viaja con vos.

Esta página cubre tres mudanzas:

- una máquina nueva, o un usuario nuevo en la misma (el camino largo, abajo);
- la carpeta de un proyecto renombrada o movida (andá a [Se movió una carpeta](#se-movió-una-carpeta));
- una máquina donde ya corriste daimon (leé primero [La máquina nueva tiene que empezar vacía](#la-máquina-nueva-tiene-que-empezar-vacía)).

Si el briefing de un proyecto dice que no hay memoria asociada a su carpeta, o `daimon bucket doctor` informa un bucket cuya carpeta ya no existe, esta es la página a la que apuntan.

## Antes de copiar

Cada directorio que usa daimon se puede mover con una variable, así que los comandos de abajo leen la variable y caen al valor por defecto. Si guardás ajustes en `~/.daimon/env`, mirá ahí también: `rg DAIMON_ ~/.daimon/env`.

En la máquina vieja, sin ninguna sesión corriendo y con el editor o el agente cerrado:

```sh
OLD_CKPT="${DAIMON_CHECKPOINT_DIR:-$HOME/.daimon/checkpoints}"
OLD_TEAM="${DAIMON_TEAM_DIR:-$HOME/.daimon/team}"
OLD_KEYS="${DAIMON_KEYS_DIR:-$HOME/.daimon/keys}"
OLD_ENV="${DAIMON_ENV_FILE:-$HOME/.daimon/env}"
```

Una copia tomada mientras un hook escribe puede separar un checkpoint de su recibo, y por eso no tiene que haber nada corriendo.

## En la máquina vieja

1. Reintentá cualquier sesión que no se haya guardado. `heal` lee logs que no se copian, así que tiene que correr acá:

   ```sh
   daimon heal --dry-run
   daimon heal --all
   ```

2. Registrá dónde vive cada proyecto, para que la máquina nueva sepa qué bucket es cuál. Esto solo escribe archivos de identidad chicos y nunca toca un checkpoint firmado. Apuntá `--scan` a las carpetas que contienen tus repositorios. El primer comando imprime un plan, el segundo lo escribe:

   ```sh
   daimon bucket stamp --scan ~/code
   daimon bucket stamp --scan ~/code --apply
   ```

3. Si usás un repositorio de equipo y tenés checkpoints locales sin subir, sincronizalos primero, para que la copia de los clones del equipo esté al día:

   ```sh
   daimon team sync
   ```

4. Copiá. `rsync -a` conserva las fechas de modificación, y la retención las lee. Poné en las variables `DEST_` el lugar donde van los archivos en la máquina nueva (`vos@maquina-nueva:.daimon/checkpoints`, o una carpeta en un disco externo), y creá allá `~/.daimon/team` y `~/.daimon/keys` antes, porque `rsync` no crea carpetas padre.

   ```sh
   DEST_CKPT=vos@maquina-nueva:.daimon/checkpoints
   DEST_TEAM=vos@maquina-nueva:.daimon/team
   DEST_KEYS=vos@maquina-nueva:.daimon/keys
   DEST_ENV=vos@maquina-nueva:.daimon/env
   ```

   Los checkpoints, sin las cachés ni los archivos a medio escribir:

   ```sh
   rsync -a \
     --exclude '.chunk-cache/' --exclude '.partials/' --exclude '.birth/' \
     --exclude '*.tmp' --exclude '.pointer.lock' \
     "$OLD_CKPT/" "$DEST_CKPT/"
   ```

   Tu propio espejo de equipo (puede sobrevivir a los archivos planos):

   ```sh
   rsync -a --exclude '*.tmp' --exclude '.pointer.lock' "$OLD_TEAM/local/" "$DEST_TEAM/local/"
   ```

   El archivo de entorno, una sola vez. Editalo después en la máquina nueva, porque tiene ajustes y secretos de cada máquina:

   ```sh
   rsync -a "$OLD_ENV" "$DEST_ENV"
   ```

   Las claves de firma, solo si la máquina vieja se retira. Son una sola identidad, así que copiá los dos archivos o ninguno:

   ```sh
   rsync -a "$OLD_KEYS/" "$DEST_KEYS/"
   ```

## Qué copiar y qué dejar

| Copiar | Por qué |
| --- | --- |
| `checkpoints/` (menos `.chunk-cache`, `.partials`, `.birth`, `*.tmp`, `.pointer.lock`) | La fuente de verdad: cada bucket, cada `<session>.json` firmado con su `.receipt`. Mantené juntos cada par. |
| `team/local/` | Tu propio espejo. `team/<clone>/` solo después de `daimon team sync`, y se puede volver a bajar. |
| `keys/` como par, o nada | `signing.seed` y `signing.pub.json` son una sola identidad. Copiá los dos y todos los recibos verifican, con una identidad de firma viviendo en dos máquinas. No copies ninguno y la máquina nueva firma con una clave propia: los recibos nuevos verifican y los viejos, firmados con la clave vieja, no verifican acá. Nunca copies solo el archivo público: cada recibo nuevo fallaría la verificación. Esto sale de leer el código; no se corrió de punta a punta. |
| `env`, una vez | Ajustes de cada máquina; editalo después de copiar. |

| Dejar | Por qué |
| --- | --- |
| `recall.db`, `recall/` | Derivados. Se reconstruyen en el primer uso. |
| `recall_seen/`, `checks/`, `hooks/` | Enfriamientos, checks generados y copias instaladas. Reinstalá y regenerá (`daimon hooks install`, `daimon check sync`). |
| `codex/`, `kimi/`, `windsurf/`, `probe/` | Estado de sesión por host de la máquina vieja. |
| `logs/` | `serialize.log` dice qué sesiones se capturaron. Copiarlo hace que `heal` busque transcripts que no están acá. |

## La máquina nueva tiene que empezar vacía

Instalá daimon y pará ahí: no abras todavía una sesión en ningún proyecto. La copia va a un `checkpoints/` vacío. Un store que ya tiene sesiones no está soportado, porque daimon no tiene forma de fusionar dos stores enteros. Si la máquina nueva tiene historial que vale la pena guardar, mové ese store a un costado y apuntá `DAIMON_CHECKPOINT_DIR` ahí cuando quieras leerlo.

Cloná o copiá tus proyectos a sus carpetas nuevas. Después corré todo lo que sigue en la máquina nueva.

## En la máquina nueva

1. Reasociá los buckets. Home viejo a home nuevo, para cada proyecto que ya existe en su lugar nuevo. El primer comando es una simulación y lista lo que cambiaría; leelo y después aplicalo:

   ```sh
   daimon bucket remap --from /Users/alice --to /Users/bob
   daimon bucket remap --from /Users/alice --to /Users/bob --apply
   ```

   Un bucket cuya carpeta nueva todavía no existe (un proyecto que no clonaste) se saltea y se lista, y está bien: corré el mismo comando de nuevo después de clonarlo. Un bucket cuya carpeta nueva ya tiene un bucket propio, porque corrió una sesión ahí antes de este paso, se lista con el comando exacto `daimon bucket merge` que une los dos, el nuevo dentro del viejo. Revisá los nombres, corrélo y después corré `remap` otra vez:

   ```sh
   daimon bucket merge --from=BUCKET --into=BUCKET --force
   daimon bucket merge --from=BUCKET --into=BUCKET --force --apply
   ```

   El comando lleva `--force` porque la carpeta existe, y `merge` si no se niega a eliminar un bucket cuya carpeta sigue ahí (una sesión en esa carpeta lo crearía otra vez). Acá es seguro, porque `remap` asocia la carpeta al bucket viejo justo después. No abras una sesión en ese proyecto entre los dos comandos.

2. Un proyecto cuya carpeta no es simplemente la vieja bajo un home nuevo (un renombre, otra estructura) se asocia de a uno. `doctor` encuentra el bucket por el origen y el primer commit del repositorio e imprime el comando:

   ```sh
   daimon bucket doctor --path .
   daimon bucket adopt --bucket=BUCKET --path . --apply
   ```

3. Revisá el store. Reparás un ledger solo si `daimon status` lo informa degradado; la simulación no cambia nada:

   ```sh
   daimon ledger repair events --dry-run --project .
   daimon bucket doctor
   ```

4. Regenerá lo que no copiaste:

   ```sh
   daimon check sync --project .
   ```

5. Comprobá el resultado desde adentro de un proyecto movido:

   ```sh
   daimon status --project .
   daimon brief --project .
   ```

   El briefing es donde lo dejaste en la máquina vieja.

6. El nombre de autor del equipo decide qué checkpoints son tuyos. Conservá el viejo: poné `DAIMON_AUTHOR` en `~/.daimon/env` con el valor viejo, o hacé que `git config user.name` sea el mismo en las dos máquinas.

7. Opcional: traé también los transcripts viejos. Copiá `~/.claude/projects/` e importá lo que todavía no tiene checkpoint. Los transcripts registran la carpeta vieja, e `import` los acepta una vez que `remap` o `adopt` la registró como raíz anterior del proyecto:

   ```sh
   daimon import --project . --plan
   ```

## Se movió una carpeta

En la misma máquina, la carpeta de un proyecto se renombró o se movió. No hay nada que copiar. Encontrá el bucket desde adentro de la carpeta nueva y asocialo:

```sh
daimon bucket doctor --path .
daimon bucket adopt --bucket=BUCKET --path . --apply
```

`adopt` se niega cuando otro bucket ya registra la carpeta nueva (uno nuevo que nació antes de que lo corrieras). Unilo con el comando `bucket merge` que imprime `doctor` y después adoptá.

## Cambio de usuario en la misma máquina

Igual que una máquina nueva, sin la copia: corré los comandos `remap` del paso 1 con el home viejo y el nuevo.

## Windows

Los comandos son los mismos. `%USERPROFILE%\.daimon` es donde daimon guarda sus archivos por defecto, y `remap` acepta rutas con letra de unidad:

```sh
daimon bucket remap --from C:\Users\a --to C:\Users\b
```

Las rutas se comparan sin distinguir mayúsculas ni `/` contra `\`, y una mudanza entre sistemas funciona (`C:\Users\a` a `/Users/b`). Elegí tu propia herramienta de copia y conservá las fechas de modificación. Esto se comprobó con las reglas de rutas de Windows en tests, no en una máquina Windows.

## Si algo se ve mal

- El briefing dice que no hay memoria asociada: la carpeta no estaba cubierta por el `remap` que corriste. Corré `daimon bucket doctor --path .`.
- `remap` listó un bucket como `collision`: corré el `bucket merge` que imprimió y después `remap` otra vez.
- `remap` salteó un bucket como `live-source`: su carpeta vieja todavía existe en esta máquina, así que puede ser un clon con historial propio. `--force` lo mueve igual.
- `daimon bucket doctor` informa un bucket `unstamped`: no registra ninguna carpeta. Hacele `adopt` hacia la carpeta correcta.
