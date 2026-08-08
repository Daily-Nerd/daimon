---
sidebar_position: 0
sidebar_label: Resumen
slug: /
---

# daimon

Tu agente olvida todo entre sesiones. daimon escribe un **checkpoint** pequeño
cuando una sesión termina y lo convierte en un **briefing** cuando arranca la
siguiente — así el agente retoma desde un estado previo fiel en lugar de una
suposición confiada:

```
While you were away — here's where we left off.

VERIFY BEFORE TRUSTING (state may have changed outside this session):
- [✓ verbatim] PR #212 state — you said you'd merge it yourself from the UI  — "I'll merge it after the demo"

Open loops:
- [✓ verbatim] Retry policy for the payments webhook — exponential or fixed?  — "don't ship the retry loop until we pick a policy"
- [~ inferred] The staging config drift needs an owner [carried]

Decisions made:
- [✓ verbatim] Postgres advisory locks over Redis locks for the scheduler  — "let's not add a Redis dependency for this"

Active topic: Migrating the scheduler off cron to the new worker pool
```

Tres sustantivos cubren todo el sistema. Una **sesión** es una conversación
con tu agente. Un **checkpoint** es el registro local y firmado que una sesión
deja al terminar. Un **briefing** es la versión legible de un vistazo del
último checkpoint, inyectada cuando arranca la siguiente sesión.

## Por qué es distinto

La mayoría de las herramientas de memoria guardan lo que un modelo *escribió
sobre* lo que pasó — y cuando ese texto está mal, nada te avisa. daimon marca
cada línea con cómo llegó a existir: las líneas `[✓ verbatim]` son citas
exactas que un verificador determinista encontró en la transcripción de la
sesión (operaciones de strings puras, sin LLM — case, espacios y formato
plegados, con tramos elididos permitidos); las
líneas `[~ inferred]` son conclusiones propias del modelo, honestas sobre ser
derivaciones. Una "cita" que falla la verificación se degrada a inferida en el
acto, así que una cita alucinada nunca puede llevar la insignia verbatim. Con
[receipts](concepts/receipts.md) activados, los bytes exactos del checkpoint
se firman — todo lo de arriba es verificable offline, sin confiar en daimon,
en el modelo, ni en esta página.

## El ciclo

1. **Una sesión termina** — un hook del host serializa la transcripción a un
   checkpoint. JSON local en tu disco; sin servidor, sin telemetría.
2. **La siguiente sesión arranca** — un hook inyecta el briefing como
   contexto, así el agente responde "¿dónde quedamos?" antes de que
   preguntes.
3. **Verificas, resuelves o corriges** — los ítems abiertos se arrastran
   hasta cerrarse, y envejecen a la vista en lugar de mentir para siempre.

**Empieza por el [Inicio rápido](getting-started/quickstart.md)** — de la
instalación al primer briefing en cinco pasos. La configuración por host vive
en [Hosts](hosts/index.md); el sistema de confianza se explica en
[Clases de confianza](concepts/trust-classes.md).
