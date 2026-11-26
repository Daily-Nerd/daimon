---
sidebar_position: 8
description: "Cómo leer las marcas y anotaciones de una línea del briefing."
---

# Formato del briefing

Cada línea del briefing está marcada. La historia completa de confianza está en [clases de confianza](../concepts/trust-classes.md).

- `[✓ verbatim]` / `[~ inferred]` / `[? untagged]`, cómo se capturó el ítem.
- `[carried]`: heredado de una sesión anterior, no contexto fresco.
- `(noted <hora UTC>, re-check before acting)`: en un ítem arrastrado que nombra un PR, rama, archivo o dependencia: cuándo se capturó la nota por primera vez. No dice nada del estado desde entonces; mira [arrastre](../concepts/carry.md).
- `[≈ corroborated ×N]`: N sesiones independientes atestiguaron la afirmación.
- `[✓ world-checked]`: una sonda en vivo coincidió con esta afirmación durante este brief.
- `HANDOFF (…)`: un batón de la sesión anterior; va arriba de todo. El encabezado dice quién lo escribió: una persona (`left deliberately by previous session`) o un agente (`written by an agent, unverified`). Todos los hosts lo muestran: `daimon brief`, la herramienta MCP, la API de host y el hook de Hermes.
- `— because …`: el razonamiento declarado de la decisión, capturado solo cuando la transcripción lo declara.

## Pedidos, el conteo de decisiones y el pie

Alrededor del cuerpo, un briefing lleva además lo que sigue. Es el mismo texto en todos los hosts, porque lo escribe un único compositor.

- `⚠ checkpoint format X != current Y …`: una línea de advertencia sobre el saludo. El checkpoint lo escribió otro formato de daimon, así que algunas secciones pueden renderizarse parcialmente.
- `N decisions waiting on you here (M elsewhere) - daimon decide`: cuántas decisiones esperan a este proyecto y, entre paréntesis, a otros proyectos. `daimon decide` las lista. La redacción es fija.
- Cuatro paneles de pedidos. Cada uno es un bloque que abre en su título y termina en la siguiente línea en blanco, y cada uno lee los ledgers de pedidos de este proyecto:
  - `Requests waiting on you (from other projects):` los pedidos que otros proyectos te dirigieron. Una tarjeta se lee `→ <id>  <titular>`, con las marcas `[info]`, `[blocking]` y `[done claimed]`, luego una línea `From:` y, cuando la severidad no es la de por defecto, una línea de severidad.
  - `Decisions on requests you sent:` lo que el destinatario decidió sobre tus pedidos: una marca, el estado, el id y el titular, luego `To:`, una `Note:`, la evidencia `Done:` y la última respuesta cuando las hay.
  - `Requests you accepted and still owe:` trabajo que aceptaste. Termina con la línea que dice que un agente puede cerrar uno con `daimon request done` sin preguntarle a una persona.
  - `Requests you sent that nobody is moving:` `! <id>  <titular>  [estado, edad en días, to <proyecto>]`.
- Un panel muestra como máximo tres tarjetas y termina con `(+N more …)` cuando hay más. Con un presupuesto de bytes ajustado, cada panel se contrae a su título y `(N cut for budget, see: …)`, y un panel contraído cuenta como no mostrado.
- `N resolved item(s) withheld` y `N quarantined item(s) withheld`: las últimas líneas de un briefing, cuántos ítems dejó afuera.

Todos los hosts con briefing imprimen todo lo anterior: `daimon brief` (así que los hooks de Claude Code, Codex, Kimi Code y Gemini CLI, que lo ejecutan), la herramienta MCP `daimon_brief` (la vía que usa Windsurf), `render_briefing` de la API de host y el hook de Hermes. Los paneles y el conteo pertenecen al briefing del propio proyecto: una lectura de otro proyecto (`--slug`, `--project` que nombra otro proyecto, o una ruta bajo el permiso entre proyectos) no muestra ninguno. La deriva de anclas y `--team` siguen siendo solo de la CLI.

Una tarjeta mostrada completa queda registrada una vez como vista por quien la envió (una marca `surfaced`), cuando el host ya la recibió: después de que la CLI vacía su salida, después de escribir la respuesta MCP, cuando vuelve `render_briefing`, cuando vuelve el hook de Hermes. Una tarjeta que el presupuesto contrajo nunca se marca. `api.preview_briefing` devuelve el mismo texto y no registra nada.
