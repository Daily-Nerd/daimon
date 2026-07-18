# Backends y modelos probados en campo

Qué combinaciones de modelo/backend funcionan de verdad con el serializador
de daimon — medidas en uso real, no asumidas. Una fila por combinación
(modelo, ruta de backend) que alguien haya corrido en el campo.

Dos reglas mantienen esto como fuente de verdad en lugar de una página de
sensaciones:

1. **Medido, no auto-reportado.** La columna de calidad es la *tasa de
   degradación verbatim*: la proporción de afirmaciones verbatim frescas cuya
   cita falló la verificación contra el transcript y fue degradada a
   inferida. La computa el verificador; la opinión del modelo sobre su propia
   salida nunca se acepta. Mira la receta abajo.
2. **Con fecha y versión, o no cuenta.** Cada fila lleva la versión de daimon
   y la fecha de prueba. Las filas viejas envejecen a la vista en lugar de
   mentir para siempre.

Las filas se contribuyen por PR — daimon no tiene telemetría por diseño, así
que nada de esto se recolecta automáticamente. Agrega tu combinación con la
receta de abajo.

## Matriz

| Modelo | Ruta de backend | daimon | Tasa de degradación (muestra) | Fecha | Notas |
|-------|--------------|--------|-------------------------|------|-------|
| MIXED — claude-haiku-4-5 (litellm proxy) + sesiones claude-cli, atribución por sesión perdida | ver notas | 0.13.0 | 29% de 51 afirmaciones verbatim frescas, 3 sesiones — rango por sesión 6%–77% | 2026-07-10 | máquina de desarrollo del maintainer. El backend cambió entre estas sesiones y los checkpoints no registran cuál serializó cada una, así que esta fila NO PUEDE separarse — se conserva como ejemplo práctico de por qué las muestras sin atribución son casi inútiles y de por qué el serializador necesita un sello de backend/modelo. Reemplázala con filas atribuidas ahora que el sellado existe. |
| _tu modelo_ | _anthropic / openai-compatible / claude-cli / command_ | | | | |

**Regla de atribución (aprendida llenando la primera fila):** una fila solo
es válida si cada checkpoint muestreado se sabe proveniente de ese par
exacto (modelo, backend). Antes de que los checkpoints llevaran sello de
serializador, eso significaba "el backend no cambió durante la ventana de la
muestra" — verifica antes de contar, o tu fila mezcla combinaciones. Los
checkpoints de 0.15.0+ llevan ese sello directamente: `llm_backend` (y
`llm_model`, cuando la config realmente conoce uno) se registra al
serializar, así que la atribución ya no tiene que reconstruirse de memoria
de cuándo cambió el backend.

Leyendo los números: una degradación es el **verificador atrapando una cita
incorrecta**, no pérdida de datos — el ítem sobrevive como `[~ inferred]` con
el sello del chequeo fallido. Más bajo es mejor; las señales interesantes son
el nivel *y* la varianza. Las tasas de una sola sesión sobre conteos chicos
de afirmaciones (< 20) son ruidosas — dilo en la fila.

## Receta para llenar una fila

La tasa de degradación queda sellada en cada checkpoint fresco:
`verify_quotes` marca cada afirmación verbatim fresca con
`quote_verified: true` (acierto) o la degrada a `trust: "inferred"` +
`quote_verified: false` (fallo). Cuenta ambos en tus checkpoints más nuevos —
nota que el filtro es sobre el *sello*, no sobre `trust` (los ítems
degradados ya no son `verbatim`, que es exactamente por qué filtrar por trust
los ocultaría). Agrupa por el par de sellos `(llm_backend, llm_model)`
(0.15.0+) para que un lote mixto de checkpoints nunca pueda mezclar
combinaciones por accidente — los checkpoints pre-0.15.0 no llevan sello y
caen a un bucket `(unstamped)`, que es una señal de verificar la atribución a
mano en lugar de una combinación citable en una fila:

```python
import json, sys
from collections import defaultdict

def items(c):
    w, e = c.get("working_context", {}), c.get("epistemic_snapshot", {})
    for k in ("open_questions", "recent_decisions"):
        yield from (w.get(k) or [])
    if isinstance(w.get("active_topic"), dict):
        yield w["active_topic"]
    for k in ("strong_beliefs", "uncertainties", "contradictions_flagged"):
        yield from (e.get(k) or [])

groups = defaultdict(lambda: [0, 0])  # (backend, model) -> [claims, downgraded]
for path in sys.argv[1:]:
    cp = json.load(open(path))
    key = (cp.get("llm_backend") or "(unstamped)", cp.get("llm_model") or "(no model)")
    fresh = [i for i in items(cp) if isinstance(i, dict)
             and not i.get("carried_from") and i.get("quote_verified") is not None]
    bad = sum(1 for i in fresh if i["quote_verified"] is False)
    g = groups[key]
    g[0] += len(fresh)
    g[1] += bad

for (backend, model), (claims, bad) in sorted(groups.items()):
    print(f"{backend}/{model}: {claims} claims, {bad} downgraded")
```

Córrelo sobre `~/.daimon/checkpoints/<project>/latest.json` y sus hermanos
`prev-*.json`, suma varias sesiones (una sola es demasiado ruidosa), y abre
un PR con la fila — una fila por grupo impreso, nunca un total fusionado a
mano entre grupos. Solo los checkpoints de 0.13.0+ llevan sellos de cita
confiables por checkpoint (`quote_verified: false` se volvió una señal
solo-de-frescos entonces; los ítems arrastrados más viejos pueden cargar
sellos obsoletos).

La *confiabilidad* de la serialización (si la corrida completa siquiera
termina) es un eje distinto de la fidelidad de citas — si tu combinación
falla por completo, eso es un reporte de issue con
`~/.daimon/logs/serialize.log` adjunto, no una fila de la matriz.
