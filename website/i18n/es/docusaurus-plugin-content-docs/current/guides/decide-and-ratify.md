---
sidebar_position: 3
description: "Trabajá la cola de decide: mirá qué solo puede resolver una persona, cerrá cada entrada con su comando y registrá que el estado de un item avanzó."
---

# Decidir y ratificar

Algunos cambios solo los puede hacer una persona en una terminal: aceptar una solicitud, ratificar una regla, una refutación o una enmienda. `daimon decide` es la cola de exactamente esos, y cada entrada trae el comando que la cierra. Por qué la cola se limita a verbos solo humanos está en [solicitudes y decisiones](../concepts/requests-and-decisions.md#decide-qué-está-esperando-por-vos).

## Ver qué está esperando

```sh
daimon decide
daimon decide --all-projects
```

`daimon decide` lee registros que ya existen y no escribe nada. Los otros proyectos aparecen como conteos. `--all-projects` los expande a texto completo, con cada comando ruteado con `--slug=<slug>` para que corra desde donde estás, y se rechaza en un home con alcance de inquilino (`DAIMON_TENANT_SCOPED`).

## Cerrar una entrada

| esperando por vos | decir que sí | decir que no |
| --- | --- | --- |
| una solicitud dirigida a vos | `daimon request accept <id>` | `daimon request reject <id> --note "<porqué>"` |
| una enmienda | `daimon amend ratify <id>` | `daimon amend reject <id> --note "<porqué>"` |
| una regla candidata | `daimon ruling ratify <id>` | `daimon ruling retire <id>` |
| una refutación candidata | `daimon refute ratify <id>` | `daimon refute overturn <id>` |

Cada una necesita una terminal interactiva, y `--by agent` se rechaza. `daimon ruling ratify` muestra el texto completo de la regla y pide una `y` explícita antes de activar nada.

## Registrar que un item avanzó

Una enmienda dice que el estado de un item del briefing avanzó mientras el item sigue abierto: "el PR se mergeó" o "se destrabó el bloqueo". La propone un agente y la ratifica una persona.

```sh
daimon amend propose <item-id> --change progressed \
  --evidence "se mergeó el PR" --by agent
daimon amend ratify a-7d41e0
daimon amend reject a-7d41e0 --note "el PR se mergeó pero el ítem es de otro repo"
daimon amend list
```

`--change` es `progressed`, `blocked` o `changed`. `--evidence` es una cita textual del transcript de la sesión, que daimon verifica byte a byte al terminar la sesión. Qué certifica y qué no esa verificación está en [solicitudes y decisiones](../concepts/requests-and-decisions.md#enmiendas-el-estado-avanzó-el-pendiente-sigue-abierto).
