---
description: "daimon serve abre una vista local de solo lectura de la memoria de un proyecto en tu navegador. Todo renderiza salida existente del motor y ninguna ruta modifica tu memoria."
---

# Visor (solo lectura)

`daimon serve` abre en tu navegador una vista local, de solo lectura, de la
memoria de un proyecto. Cada superficie renderiza la salida de un motor que ya
existe — el visor no tiene lógica propia con la que discrepar de la CLI, y
nada en él escribe.

```bash
daimon serve                      # enlaza 127.0.0.1:7717 y abre una pestaña
daimon serve --port 7800 --no-browser
```

Flags: `--data-dir` (directorio de checkpoints, y el almacén de todas las
rutas; por defecto el mismo que usa la CLI: `DAIMON_CHECKPOINT_DIR` del entorno
o de `~/.daimon/env`, y luego `~/.daimon/checkpoints`), `--project-dir`
(proyecto al que se limita, por defecto el directorio actual, resuelto a la
raíz del proyecto como lo hace la CLI con `--project`), `--port` (por defecto
7717), `--no-browser`.

## Qué ves

- **La búsqueda** es `daimon recall`, renderizado. El encabezado de resultados
  lo dice — lo que encuentres en el navegador es lo que recibiría un agente en
  su briefing.
- **Cada entrada tiene una página "why"**: el texto almacenado, su origen, la
  cita almacenada, una ventana de contexto del transcript (obtenida al leer,
  nunca almacenada), los ejes de evidencia detrás del item, un panel **Life**
  que muestra cómo cambió la entrada a través de los checkpoints, y un panel
  **History** que renderiza sus relaciones confirmadas por humanos (ver
  [relaciones](relations.md)).
- **Vistas hermanas** junto a la página de entrada: el ledger del proyecto,
  una página de sesión, una página de **Refutations** que lee el ledger de
  conocimiento negativo, un **Check strip**, un **Diff** entre checkpoints, y
  una **vista de impresión** que fija un checkpoint como registro impreso.

## Qué no muestra el visor

La lista de proyectos, las listas de checkpoints y de sesiones, un checkpoint,
el Diff, el feed de actividad, el ledger, el check strip, una página de sesión
y el panel Life de un item muestran solo lo que un lector del proyecto puede
ver. Un item que pusiste en cuarentena o que olvidaste no aparece en ellos, y
un tema retenido se lee como si no hubiera tema. Un valor en cuarentena dentro
de una nota o de un evento se lee como un marcador de retenido. Un valor
olvidado se lee como ausente, y también la clave que lo nombra: el feed de
actividad muestra un olvido como la palabra `forgotten`. Los conteos son
conteos de los items que puedes ver, así que un número nunca te dice que hay
algo oculto. Si el ledger de confianza no se puede leer, no se muestra ningún
item (las notas siguen legibles) y la página dice qué ledger falló.

El visor no lee ningún archivo de ledger por sí mismo: cada ruta que muestra
contenido del proyecto le pregunta a la misma vista de lectura que usa la CLI
(search, why, refutations y relations preguntan a sus propios motores).

El Diff, el ledger y el panel Life de un item cierran un item con la misma
regla: una resolución lo cierra, sea cual sea el estado que lo cerró (la regla
que usa `daimon diff`), salvo que un evento posterior lo reabriera. El Diff
lista un item que salió entre dos checkpoints como resuelto en ese caso, y
como descartado en cualquier otro.

## Solo lectura como compromiso

El servidor responde únicamente peticiones GET, y ninguna ruta escribe en tus
checkpoints, ledgers ni registros de confianza. Confirmar una relación,
resolver un loop u olvidar un item siguen viviendo en la CLI, donde la
terminal hace cumplir quién está hablando.

Hay una excepción, un caché derivado: `/api/recall` puede reconstruir el índice
de recall (`recall.db`) cuando ya no coincide con el almacén, y registra un
error de índice a su lado. El índice se reconstruye desde tus ledgers y no
contiene nada que el almacén no tenga.

En un home con alcance de tenant (`DAIMON_TENANT_SCOPED`) el visor lista y abre
solo el proyecto actual, la misma regla que siguen `daimon projects` y la
herramienta MCP de proyectos. Si una petición falla dentro del visor, responde
HTTP 500 con un error JSON genérico que nombra solo el tipo de la excepción.

## Postura solo-localhost

El servidor enlaza `127.0.0.1` y además rechaza cualquier petición cuyo header
`Host` no sea `127.0.0.1` o `localhost`. Nada queda expuesto a tu red y nada
se transmite a ningún lado — el visor lee los mismos archivos locales que lee
la CLI.
