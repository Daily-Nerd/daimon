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
