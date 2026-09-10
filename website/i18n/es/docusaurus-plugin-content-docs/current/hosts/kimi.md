---
description: "Configura daimon en Kimi Code. Briefing solo por UserPromptSubmit, captura con Stop throttled como via del modo print, y los checks de pre-accion todavia sin conectar."
---

# Kimi Code

El soporte de Kimi Code está medido sobre una sola sesión en vivo de 0.42.0
(2026-09-09): la instalación, los tres registros de hooks y el hueco del
modo print de más abajo salen de esa corrida, no de una flota. Los checks de
pre-acción de las rulings todavía no están conectados para este host.

## Instalación

Agrega el ciclo de captura -> inyección de daimon a Kimi Code desde el
paquete publicado:

```sh
daimon hooks install kimi
```

Esto copia tres scripts de hook, más los módulos que comparten, al
directorio de configuración de Kimi, y luego agrega entradas `[[hooks]]` a
`~/.kimi-code/config.toml` (o `$KIMI_CODE_HOME/config.toml` cuando esa
variable está definida). El archivo de configuración se respalda antes de
que daimon escriba en él. El escritor solo agrega o quita sus propios
bloques. Nunca reescribe las tablas de proveedor o modelo.

Re-ejecuta `daimon hooks install kimi` después de cada `uv tool upgrade
daimon-briefing`, igual que en cualquier otro host, para que los scripts
instalados coincidan con la versión del CLI. Los hooks se cargan una sola
vez, al inicio de la sesión: después de instalar, iniciá una sesión nueva de
Kimi. La que ya está corriendo no va a tomar el cambio.

Requiere el CLI `daimon` en el `PATH`:

```sh
uv tool install 'daimon-briefing[pretty]'
```

## Qué hace cada script

- **`daimon-kimi-user-prompt-submit.py`**: hook `UserPromptSubmit`. Es el
  único canal que Kimi le da a un hook para meter texto en la sesión, así
  que es el que lleva el briefing. Kimi no tiene un punto de inyección al
  inicio de sesión (mirá Limitaciones más abajo). El modelo ve el briefing
  como un mensaje de usuario envuelto en una etiqueta `hook_result`,
  adjunto a tu primer prompt.
- **`daimon-kimi-session-end.py`**: hook `SessionEnd`. Serializa la sesión
  terminada cuando una sesión interactiva de Kimi se cierra, incluidos los
  turnos posteriores a la última captura de `Stop`. Los bytes que `Stop` ya
  capturó no se vuelven a serializar: la CLI compara la transcripción con el
  último checkpoint antes de cualquier llamada al modelo.
- **`daimon-kimi-stop.py`**: hook `Stop`. Corre una captura con throttle
  después de cada turno. Es un seguro contra crashes para una sesión
  interactiva, y es la única vía de captura en modo print, donde
  `SessionEnd` nunca se dispara (mirá Limitaciones).

## Limitaciones

- **Sin briefing al inicio de sesión.** El hook `SessionStart` de Kimi es
  solo de observación, su stdout se descarta. El briefing llega recién con
  tu primer prompt, a través del hook `UserPromptSubmit` de arriba.
- **El modo print nunca cierra.** Las sesiones de `kimi -p` quedan
  resumibles y nunca disparan `SessionEnd`, medido dos veces en una sesión
  en vivo. El hook `Stop` es lo que las captura. En una salida interactiva,
  `SessionEnd` corre su propia captura aunque haya un `Stop` reciente, así
  que los turnos posteriores al último Stop no se pierden; la CLI compara la
  transcripción con el último checkpoint antes de nada, así que los bytes que
  Stop ya capturó cuestan una lectura de archivo, no una segunda llamada al
  modelo.
- **Una sesión resumida no recibe el briefing de nuevo.** El marcador del
  briefing se indexa por id de sesión, así que `kimi -r` sobre una sesión
  que ya lo recibió obtiene la inyección de recall por prompt pero no un
  segundo briefing. El resume en sí no se midió en una sesión en vivo.
- **Todavía sin checks de pre-acción.** El canal de rechazo de Kimi no se
  midió en una sesión en vivo, así que esta versión no trae un perfil de
  checks para este host.
- **Los hooks se cargan solo al inicio de sesión.** Instalar no llega a una
  sesión que ya está corriendo. Iniciá una nueva para que tome el cambio.
- **macOS registra la ruta resuelta.** Kimi guarda el directorio de trabajo
  al que resuelve, así que una sesión iniciada bajo `/tmp/x` queda
  registrada como `/private/tmp/x`. Tené esto en cuenta si buscás sesiones
  por directorio.

## Transcripts

Kimi escribe el transcript de cada sesión en
`~/.kimi-code/sessions/<workspace>/<session id>/agents/main/wire.jsonl`. El
payload del hook lleva el id de sesión, no una ruta, así que daimon resuelve
el transcript buscando ese id de sesión dentro del directorio de sesiones.
Los transcripts de subagentes no se combinan con el transcript principal en
esta versión.

## MCP

Kimi no necesita un adaptador MCP propio de daimon. Lee un `.mcp.json` en
la raíz del proyecto con el mismo formato que usa Claude Code, así que una
entrada MCP de daimon ya existente funciona sin cambios.

## Enseñale el protocolo al agente

```sh
daimon skill install kimi              # ~/.kimi-code/skills/daimon/SKILL.md
daimon skill install kimi --project    # <repo>/.kimi-code/skills/daimon/SKILL.md
```

Install entrega dos skills: `daimon` (el protocolo) y `daimon-end`, el flujo de checkpoint en sesión `/daimon-end`, escrita al lado como `~/.kimi-code/skills/daimon-end/SKILL.md`.

Kimi escanea las dos ubicaciones en busca de skills. La prueba midió dónde
busca, no cómo prioriza entre las dos cuando ambas tienen una skill de
daimon, así que instalá en un solo alcance. Volvé a correr install después
de actualizar `daimon` para refrescar el contenido.

## Desinstalar

```sh
daimon hooks remove kimi
```

Saca las entradas `[[hooks]]` de daimon del `config.toml` y deja el resto
del archivo como estaba, finales de línea incluidos. La única excepción: un
archivo cuya última línea no tenía salto de línea gana uno, y remove no
puede distinguirlo de uno que escribiste vos. Los scripts instalados quedan donde
están, inertes una vez desregistrados. Los checkpoints en `~/.daimon/` no se
tocan.

## Verificar

```sh
daimon status
```

`daimon status` reporta la salud de captura de Kimi igual que en cualquier
otro host. Una captura hecha por el throttle de `Stop` cuenta igual que una
hecha por `SessionEnd`; nada en el reporte distingue las sesiones en modo print.
