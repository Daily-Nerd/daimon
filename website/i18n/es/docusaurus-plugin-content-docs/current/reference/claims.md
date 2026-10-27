---
sidebar_position: 4
description: "Cada numero que daimon publica sobre si mismo viene con el comando para reproducirlo en tu propio store. Un dato que no podes correr de nuevo es un testimonio."
---

# Afirmaciones, con método para re-ejecutarlas

Cada número que daimon publica sobre sí mismo viene con el método para
reproducirlo. Los números de abajo describen **nuestro propio store** — dos
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
| Las citas verbatim frescas fallan el chequeo a una tasa medible y publicada | **11.3%**, solo ítems frescos, sobre cada sesión que el store tenía el 2026-10-26 (1819 de 16118 en 500 sesiones tras deduplicar). Corte por mes abajo, como contexto. [Fila fechada + salvedades](./backends-tested.md) | snippet de abajo |
| Cada cita verbatim almacenada sigue siendo re-chequeable después | salida `0` probado / `1` discrepancia / `3` no se puede probar | `daimon audit quotes` |
| Un valor olvidado está probadamente ausente de cada superficie declarada | mismo contrato de salida, reporte solo-hash | `daimon audit privacy` |
| El borrado sobrevive a re-serializar la transcripción original | la lápida suprime el ítem al re-capturar | `daimon forget <id>`, después `daimon serialize <transcripción>`, después `daimon recall` del valor |
| La procedencia de un checkpoint se chequea offline (opt-in) | firma ed25519 que ata los bytes exactos a su transcripción | `daimon verify-receipt` |

## Re-ejecutar la tasa de degradación

La tasa se deriva de tu propio store de checkpoints — sin red, sin más
herramientas que Python. Dos filtros son obligatorios. Las copias rotadas de
punteros duplican sesiones en disco, así que deduplicá por session id (un
glob ingenuo cuenta doble). Y un checkpoint tiene dos clases de ítem: los
frescos, extraídos de la transcripción de esta sesión, y las copias
arrastradas (`carried_from`), traídas de una sesión anterior. Una copia
arrastrada conserva un sello `true` que ganó cuando era fresca, pero el
arrastre le quita un sello `false`. Así que contar las copias arrastradas
suma ítems verificados al total y casi ninguna falla, lo que baja la tasa
sin medir nada nuevo. Solo los ítems frescos responden la pregunta que hace
la afirmación:

```python
python3 - <<'EOF'
import json, glob, os
from collections import defaultdict
def items(o):
    if isinstance(o, dict):
        if "quote_verified" in o: yield o
        for v in o.values(): yield from items(v)
    elif isinstance(o, list):
        for v in o: yield from items(v)
seen=set(); checked=downgraded=0; month=defaultdict(lambda: [0, 0])
for p in glob.glob(os.path.expanduser("~/.daimon/checkpoints/**/*.json"), recursive=True):
    try: cp=json.load(open(p, encoding="utf-8"))
    except Exception: continue
    if not isinstance(cp, dict): continue
    sid=cp.get("session_id")
    if not sid or sid in seen: continue
    seen.add(sid)
    m=(cp.get("created") or "")[:7]
    for it in items(cp):
        if it.get("carried_from"): continue  # una copia arrastrada conserva un sello true y pierde uno false
        checked+=1; month[m][0]+=1
        if it.get("quote_verified") is False: downgraded+=1; month[m][1]+=1
print(f"{downgraded}/{checked} ítems frescos degradados en {len(seen)} sesiones"
      + (f" = {downgraded/checked:.1%}" if checked else ""))
for m in sorted(month):
    c, d = month[m]; print(f"  {m or 'sin fecha'}: {d}/{c} = {d/c:.1%}")
EOF
```

Qué cuenta: cada ítem fresco que lleva un sello `quote_verified`. `false`
significa que la cita falló la re-verificación al capturar y el ítem fue
degradado a `inferred`. Sobre nuestro store, el 2026-10-26, esto lee 11.3%:
1819 de 16118 ítems frescos en 500 sesiones. Eso es cada sesión que el store
todavía tenía ese día, no una vida entera: por defecto daimon conserva los
100 checkpoints por sesión más nuevos (`DAIMON_CHECKPOINT_KEEP`), más los
que un puntero todavía referencia o los que fija un ítem de importancia 9 o
más, así que un store viejo lee una ventana retenida y sus meses más viejos
son las sesiones que sobrevivieron la poda. El mismo store contado a la
manera vieja, copias arrastradas incluidas, lee 7.3% (1827 de 25035). Ese es
el número del método viejo, diluido por las copias arrastradas. Una
reformulación fresca de algo que una
sesión anterior también tenía conserva esa sesión anterior como
`origin_session`; excluir esas también lo mueve a 11.4% (1770 de 15567), así
que ese filtro no cambia nada que merezca una línea en el snippet.

El corte por mes se publica como contexto. No afirma ninguna tendencia.
Julio 17.1% (n=1697), agosto 12.9% (n=3658), septiembre 12.7% (n=5669),
octubre 6.5% (n=5084). Los hosts, los backends y el tipo de trabajo cambian
de un mes a otro, y el corte no separa eso de un cambio en el verificador o
en los modelos. El titular es el número sobre todo el store. Tu
store va a leer tu número, desde el primer día.

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
