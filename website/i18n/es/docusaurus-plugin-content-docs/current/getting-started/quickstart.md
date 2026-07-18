---
sidebar_position: 1
---

# Inicio rápido

De la instalación a tu primer briefing. Cuatro pasos, uno opcional.

## 1. Instala el CLI

```sh
uv tool install 'daimon-briefing[pretty]'
```

`pipx install 'daimon-briefing[pretty]'` funciona igual. El extra `[pretty]`
agrega tablas y paneles enriquecidos a `status` y `brief`; sin él, la salida
es texto plano.

## 2. Conecta un LLM

La serialización — convertir una sesión terminada en un checkpoint — necesita
un endpoint de LLM. Ejecuta:

```sh
daimon configure
```

Si el CLI `claude` está en tu PATH, esto imprime `✓ ready` y ya está — cero
configuración. Si no, apunta daimon a cualquier endpoint compatible con
OpenAI:

```sh
daimon configure --backend litellm \
  --base-url https://generativelanguage.googleapis.com/v1beta/openai \
  --api-key <TU-KEY> --model gemini-2.5-flash
```

Después verifica el backend de punta a punta antes de confiar en él:

```sh
daimon configure --test
```

Esto envía un prompt mínimo por el backend resuelto y reporta si pasó o
falló. La configuración se escribe en `~/.daimon/env` — mira
[Configuración](./configuration.md) para todas las variables, y la
[matriz de backends](../reference/backends-tested.md) para combinaciones de
modelos medidas en uso real.

## 3. Conecta tu host

Los hooks son lo que captura tus sesiones. Para Claude Code, instala el
plugin — registra los hooks de sesión por sí solo:

```
/plugin marketplace add Daily-Nerd/daimon
/plugin install daimon@daimon
```

Para Windsurf, Codex o Gemini CLI, sigue la página de tu host en
[Hosts](../hosts/index.md) — cada host expone una superficie de hooks
distinta, y las guías por host cubren los pasos exactos de registro y sus
particularidades.

## 4. Enséñale el protocolo a tu agente (opcional, recomendado)

Los hooks capturan sesiones; la skill le enseña al agente del otro lado cómo
*usar* el briefing — leerlo al inicio de sesión, tratar los ítems `verbatim`
como citas inmutables, verificar afirmaciones que parezcan desactualizadas
antes de repetirlas:

```sh
daimon skill install claude
```

`daimon skill list` muestra los destinos de instalación para otros hosts.

## 5. Termina una sesión, inicia la siguiente

Ese es todo el ciclo:

1. Trabaja una sesión normal en tu agente.
2. Termínala. El hook de fin de sesión serializa un checkpoint en segundo
   plano.
3. Inicia la siguiente sesión. El briefing se inyecta al arrancar:

```
While you were away — here's where we left off.

VERIFY BEFORE TRUSTING (state may have changed outside this session):
- [✓ verbatim] PR #212 state — you said you'd merge it yourself from the UI  — "I'll merge it after the demo"

Open loops:
- [✓ verbatim] Retry policy for the payments webhook — exponential or fixed?  — "don't ship the retry loop until we pick a policy"

Decisions made:
- [✓ verbatim] Postgres advisory locks over Redis locks for the scheduler  — "let's not add a Redis dependency for this"

Active topic: Migrating the scheduler off cron to the new worker pool
```

También puedes leerlo en una terminal en cualquier momento con
`daimon brief`.

:::note Las sesiones cortas se omiten a propósito
Una sesión con menos mensajes que `DAIMON_MIN_MESSAGES` (por defecto: 10) no
se serializa — no hay nada que valga la pena recordar en un intercambio de
dos mensajes. Si estás evaluando daimon y quieres capturar tus sesiones
cortas de prueba, baja el umbral por ahora:

```sh
echo 'DAIMON_MIN_MESSAGES=4' >> ~/.daimon/env
```
:::

## Confirma que funciona

```sh
daimon status
```

Status reporta la salud de captura con honestidad — fallas, omisiones y
crashes incluidos. Las líneas que importan en una instalación fresca:

```
project checkpoint: <session id>, written <n>m ago
last serialize result: success — wrote checkpoint: ...
```

Si la última serialización falló o una sesión nunca se capturó, una captura
fallida se auto-repara al inicio de la siguiente sesión — o ejecuta
`daimon heal` para reintentar de inmediato.

## Adónde seguir

- [Configuración](./configuration.md) — todas las variables de entorno,
  incluido el interruptor de apagado `DAIMON_DISABLE`.
- [Hosts](../hosts/index.md) — detalle de configuración por host y
  limitaciones conocidas.
- [Memoria de equipo](../team/team.md) — comparte checkpoints con tu equipo
  a través de un remoto git privado (opt-in).
