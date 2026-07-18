---
sidebar_position: 3
---

# Receipts y verificación offline

Los receipts son la respuesta de daimon a una pregunta que la mayoría de los
sistemas de memoria no puede tomarse en serio: *¿cómo sabes que el archivo de
memoria no fue editado después de escribirse?*

## Qué es un receipt

Con `DAIMON_RECEIPTS=1`, cada checkpoint se empareja con un receipt de
[vitni](https://github.com/Daily-Nerd/vitni) — una declaración firmada con
Ed25519, escrita en un archivo lateral `<session>.receipt`, que vincula:

- los **bytes exactos en disco** del checkpoint (`outputs_hash`), con
- su **transcript de origen** (`inputs_hash`).

Si alguien edita el archivo del checkpoint después — a mano, con un script,
con un job de limpieza bienintencionado — los bytes ya no coinciden con la
firma, y la edición es detectable.

Todo ocurre localmente. Los receipts se firman offline, se verifican offline,
y nada sale de la máquina. No hay servicio, ni autoridad de sellado de
tiempo, ni llamada de red.

## Verificar

```sh
daimon verify-receipt              # el último checkpoint de este proyecto
daimon verify-receipt <session-id> # uno específico
```

La verificación también está tejida en el camino del briefing: cuando a un
checkpoint de la era de receipts le falta el receipt, o el receipt ya no
coincide con los bytes del archivo, el briefing no le cree en silencio — las
etiquetas `✓ verbatim` afectadas se **degradan con una nota visible**. Una
afirmación verbatim vale lo que vale la integridad de los bytes que la
respaldan, y el briefing lo dice cuando esa integridad no puede demostrarse
(mira [clases de confianza](./trust-classes.md)).

## Fail-open por diseño

Los receipts nunca bloquean la memoria. Un CLI de vitni ausente, un timeout o
un paso de firma fallido registran una línea en el log y la serialización
continúa sin receipt — una falla de receipts no puede costarte un checkpoint
ni un briefing. Las llaves de firma se crean automáticamente en el primer
minteo bajo `~/.daimon/keys` (modo 0600).

Los receipts están **apagados por defecto** (cada minteo lanza un
subproceso); la lista completa de perillas — `DAIMON_RECEIPTS`,
`DAIMON_VITNI_CLI`, `DAIMON_KEYS_DIR` — está en la
[referencia de configuración](../getting-started/configuration#receipts).

## Receipts y borrado

El borrado se compone con los receipts en lugar de pelear contra ellos.
`daimon forget` elimina un ítem y **re-mintea el receipt sobre el checkpoint
posterior a la eliminación**, mientras un evento tombstone de solo-anexado
registra que hubo una eliminación — por hash del contenido, nunca por el
contenido. Editar un checkpoint a mano rompe su receipt; olvidar a través del
CLI deja un rastro firmado y demostrable. La página del
[ciclo de vida de los ítems](./lifecycle.md) cubre la mecánica.
