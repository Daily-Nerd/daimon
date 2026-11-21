---
sidebar_position: 7
description: "Cuando no aparece un briefing o falló una captura: leer status, reintentar con heal, qué significa cada sesión salteada, un ledger que no se puede leer y fallas de sincronización de equipo."
---

# Resolución de problemas

Empezá con `daimon status`. Informa la salud de la captura con honestidad, fallas y saltos incluidos: el checkpoint más nuevo del proyecto y su antigüedad, el último resultado de serialize, y una línea por problema con qué hacer.

## Sin briefing, nada capturado

- Una sesión más corta que `DAIMON_MIN_MESSAGES` (10 mensajes por defecto) no se serializa a propósito. Bajalo con `echo 'DAIMON_MIN_MESSAGES=4' >> ~/.daimon/env` si estás probando con sesiones cortas.
- Comprobá que los hooks estén instalados con `daimon hooks status`, y las skills con `daimon skill status`.
- Comprobá que el backend responde con `daimon configure --test`.

## Una captura falló

El próximo inicio de sesión reintenta una vez, en segundo plano, la sesión fallida más nueva con `daimon heal`. Podés correrlo vos:

```sh
daimon heal --dry-run     # explica qué se repararía, y por qué no
daimon heal               # la falla más reciente
daimon heal --all         # cada sesión reparable de esta máquina
daimon heal --force       # gasta otro intento en una sesión que usó su reintento
```

`--project <dir>` lo limita a un proyecto y `--session <id>` a una sesión. Una sesión que heal saltea se nombra con su razón:

| razón | qué significa |
| --- | --- |
| `retry-exhausted` | el reintento ya corrió y la sesión sigue fallando; volvé a correr con `--force` |
| `unrecoverable` | no hay registro de lanzamiento, así que no se puede reparar |
| `hung` | el serialize arrancó y nunca informó resultado, y no se registró la ruta del transcript |
| `admission-refused` | el ledger de eventos del proyecto no se puede leer (mirá abajo) |
| `repairing` | ya está corriendo una reparación de ese ledger |
| `project-unknown` | un transcript archivado bajo un directorio de proyecto cuyo proyecto no se puede recuperar; corré `daimon serialize <transcript> --project <dir>` |
| `import` | un intento de importación, que heal nunca reintenta; volvé a correr `daimon import` para retomar |

Una falla sigue pendiente hasta que algo la resuelve: un éxito posterior, un skip final, un checkpoint que cubre la sesión, o que su transcript ya no exista. La cantidad de otras sesiones que escribieron en el log nunca termina una falla. Una sesión cuyo archivo de transcript ya no está no tiene nada más que capturar, así que `daimon status` la cuenta aparte (`lost: N (never captured, transcript gone)`), `heal` nunca la lista y la salud la ignora. El conteo se lee del disco cada vez, así que si el transcript vuelve, la sesión vuelve a estar pendiente. Status y heal leen los últimos 4 MB de `serialize.log`; cuando el log es más largo, `daimon status` avisa que las líneas más viejas no se leen.

## Un ledger no se puede leer

Cuando el `events.jsonl` de un proyecto no se puede leer (un marcador de conflicto, un byte malo, un error del SO), daimon se niega a escribir un checkpoint para él, antes de llamar al modelo, así que el rechazo no cuesta nada. `daimon status` nombra el ledger y la cura, y el próximo briefing dice cuántas sesiones esperan. Repará el ledger y dejá que heal las reintente:

```sh
daimon ledger repair events --dry-run
daimon ledger repair events
```

`ledger repair` reúne las filas partidas en varias líneas y mueve cada línea que no puede parsear a `<name>.quarantined-lines` junto al ledger. Un rechazo solo se pierde cuando su transcript ya no está.

## Una línea `legacy:` en status

Existe un bucket escrito antes de la 0.42.0 para este proyecto. `daimon bucket migrate` lo mueve: mirá [Actualizar daimon](./upgrade.md#actualizar-a-través-de-la-0420).

## Sincronización de equipo

El sync es fail-open por diseño: cualquier resultado degradado te deja en el
último estado sincronizado y nunca rompe la sesión.

| Situación | Qué pasa |
|---|---|
| Sin conexión | El sync se pospone; conservas el último estado de equipo sincronizado. Tus checkpoints nuevos commitean localmente y quedan en cola para el siguiente push. |
| Credenciales git ausentes | Git corre no-interactivo (`GIT_TERMINAL_PROMPT=0`), falla rápido en lugar de colgarse en un prompt de credenciales, y el sync se degrada a sin-conexión. |
| Pushes concurrentes | Un push rechazado es benigno (un compañero ganó la carrera). Daimon integra su cambio y reintenta, acotado a 3 intentos, y luego advierte si sigue rechazado. |
| Historia compartida reescrita | Advertencia fuerte; daimon deja tu copia local intacta y se niega a auto-reparar. Daimon nunca hace force-push, resuélvelo a mano con git y con tu equipo. |
| Git no instalado | El sync se omite y devuelve éxito (rc 0), la memoria de equipo simplemente está inactiva. |
