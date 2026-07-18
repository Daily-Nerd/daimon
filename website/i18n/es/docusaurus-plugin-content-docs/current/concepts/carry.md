---
sidebar_position: 2
---

# Arrastre y desactualización

Un checkpoint captura una sesión. El arrastre (carry) es lo que hace que la
memoria abarque muchas: los ítems que siguen abiertos — preguntas sin
resolver, decisiones vigentes, trabajo sin terminar — se arrastran desde
checkpoints anteriores al siguiente briefing, y siguen apareciendo hasta que
algo los cierra.

## El sufijo `[carried]`

Un ítem escrito por la sesión más reciente aparece sin marca. Un ítem que
viene de un checkpoint anterior lleva un sufijo visible:

```
- [~ inferred] The staging config drift needs an owner [carried]
```

`[carried]` significa: *ninguna sesión reciente re-confirmó esto — puede
estar desactualizado.* Cuanto más viejo es un ítem arrastrado, con más
escepticismo hay que leerlo. El arrastre deliberadamente nunca reformula
ítems — una cita verbatim arrastrada tiene los mismos bytes que el primer día
(mira [clases de confianza](./trust-classes.md)).

## El arrastre es acotado, no infinito

Los ítems sin cerrar no se acumulan para siempre:

- El peso de arrastre de cada ítem **decae** con el tiempo, graduado por
  importancia. Con el piso por defecto (`DAIMON_CARRY_FLOOR`), las decisiones
  expiran del arrastre en unas 5–6 semanas; las preguntas abiertas escaladas
  viven alrededor de 3–4 meses.
- Se arrastran como máximo `DAIMON_CARRY_MAX` ítems por tipo (por defecto:
  8), así el briefing se mantiene legible sin importar cuánto dure un
  proyecto.
- [Resolver](./lifecycle.md) un ítem termina su arrastre de inmediato — esa
  es la vía prevista de salida; el decaimiento es la red de seguridad.

Todas las perillas están en la
[referencia de configuración](../getting-started/configuration), incluido
`DAIMON_CARRY` (interruptor maestro, encendido por defecto).

## La advertencia de desactualización

El arrastre tiene un modo de falla del que daimon advierte explícitamente: un
ítem puede viajar, repetido briefing tras briefing, sin que nadie lo
re-verifique contra el mundo. Que dos artefactos del propio daimon coincidan
— el briefing y un checkpoint viejo — **no es corroboración**; son la misma
fuente repetida.

Por eso, cuando el sello de última verificación de un ítem arrastrado
envejece más allá de `DAIMON_STALE_DAYS` (por defecto: 7 días), el briefing
lo dice:

```
N carried item(s) unverified for >N days — world-check before repeating as true
```

La respuesta prevista es verificar el mundo — código, git, el issue
tracker — y luego o [resolver](./lifecycle.md) el ítem (ya está hecho o está
mal) o ejecutar `daimon reverify` con evidencia (sigue siendo cierto), lo
cual reinicia el reloj.

## Supersesión: arrastre que discute consigo mismo

Cuando una sesión nueva contradice un ítem arrastrado — el proyecto se
comprometió con X, y después con Y — el arrastre no descarta el ítem viejo en
silencio ni lo sigue inyectando como hecho. El briefing lo marca como
**candidato a supersesión**, presentando ambos lados con los comandos de
confirmar/rechazar en línea. Confirmar (`daimon resolve <id>`) oculta el lado
obsoleto de todos los briefings futuros; rechazar (`daimon reverify <id>`)
conserva el ítem y registra por qué. Nada se decide por ti — mira el
[ciclo de vida de los ítems](./lifecycle.md) para la mecánica completa.
