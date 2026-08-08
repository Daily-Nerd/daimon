---
sidebar_position: 4
---

# Afirmaciones, con método para re-ejecutarlas

Cada número que daimon publica sobre sí mismo viene con el método para
reproducirlo. Los números de abajo describen **nuestro propio store** — cinco
meses de dogfooding en una sola máquina. Correr los mismos comandos en tu
instalación mide **tu** store; ese es el punto. Una afirmación que no podés
re-ejecutar es un testimonio, y esta página no lleva testimonios.

Una definición acompaña todo lo de acá: **verificado significa que daimon
encontró esta cita exacta en tu transcript** — comparada tras normalizar
mayúsculas, espacios y formato, con tramos elididos permitidos. Es una
comprobación mecánica, no una afirmación de verdad.

## Las afirmaciones

| Afirmación | Número publicado / garantía | Re-ejecutar con |
| --- | --- | --- |
| Las citas verbatim frescas fallan el chequeo a una tasa medible y publicada | **12.2%** de por vida (450 de 3703 citas, 160 sesiones, corte 2026-08-07) — [fila fechada + salvedades](./backends-tested.md) | snippet de abajo |
| Cada cita verbatim almacenada sigue siendo re-chequeable después | salida `0` probado / `1` discrepancia / `3` no se puede probar | `daimon audit quotes` |
| Un valor olvidado está probadamente ausente de cada superficie declarada | mismo contrato de salida, reporte solo-hash | `daimon audit privacy` |
| El borrado sobrevive a re-serializar la transcripción original | la lápida suprime el ítem al re-capturar | `daimon forget <id>`, después `daimon serialize <transcripción>`, después `daimon recall` del valor |
| La procedencia de un checkpoint se chequea offline (opt-in) | firma ed25519 que ata los bytes exactos a su transcripción | `daimon verify-receipt` |

## Re-ejecutar la tasa de degradación

La tasa se deriva de tu propio store de checkpoints — sin red, sin más
herramientas que Python. Las copias rotadas de punteros duplican sesiones en
disco, así que deduplicar por session id es obligatorio (un glob ingenuo
cuenta doble):

```python
python3 - <<'EOF'
import json, glob, os
def items(o):
    if isinstance(o, dict):
        if "quote_verified" in o: yield o
        for v in o.values(): yield from items(v)
    elif isinstance(o, list):
        for v in o: yield from items(v)
seen=set(); checked=downgraded=0
for p in glob.glob(os.path.expanduser("~/.daimon/checkpoints/**/*.json"), recursive=True):
    try: cp=json.load(open(p, encoding="utf-8"))
    except Exception: continue
    if not isinstance(cp, dict): continue
    sid=cp.get("session_id")
    if not sid or sid in seen: continue
    seen.add(sid)
    for it in items(cp):
        checked+=1
        if it.get("quote_verified") is False: downgraded+=1
print(f"{downgraded}/{checked} degradadas en {len(seen)} sesiones"
      + (f" = {downgraded/checked:.1%}" if checked else ""))
EOF
```

Qué cuenta: cada ítem que lleva un sello `quote_verified` — `false` significa
que la cita falló la re-verificación al capturar y el ítem fue degradado a
`inferred`. El 12.2% publicado es un **corte fechado**; el mismo comando sobre
el mismo store cuatro sesiones después ya lee 11.9% (460/3869, 164 sesiones).
Tu store va a leer tu número, desde el primer día.

## Qué prueban los auditores

`daimon audit quotes` re-chequea cada cita verbatim contra su transcripción de
origen, solo lectura. `daimon audit privacy` hashea cada campo con texto plano
en cada superficie declarada y lo intersecta con el registro de borrados. Los
dos comparten un [contrato de salida](./cli.md#comprobar): `0` es limpio
probado, `1` nombra el residuo por superficie y hash, y `3` significa que una
superficie no se pudo leer — nunca trates `3` como limpio. Ese último código
existe porque "no pude chequear" reportado como "todo limpio" es exactamente
cómo funciona el teatro de auditoría.

## Qué no está en esta página, a propósito

Todo lo medido una sola vez, sobre una muestra demasiado chica para afirmarse,
o todavía no reproducible con un comando que puedas correr. Cuando un número
gradúa a método, se muda acá con su fecha.
