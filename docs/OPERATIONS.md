# Operación y recuperación

## Recorrido normal

Prepare una factura ordinaria en el diario Easor. Seleccione conscientemente forma
de pago e idioma. Revise cliente, descripciones, unidades, cantidades y fecha.
«Preparar en Easor» congela la versión local y encola la creación; no emite.

La tarea programada procesa una etapa cada vez. Crea el borrador, recupera sus
líneas y totales y solo entonces llega a «Pendiente de aprobación». Un usuario con
permiso de emisión debe pulsar «Autorizar emisión en Easor». El trabajador vuelve
a comprobar los datos antes de enviar la petición.

Un HTTP 2xx no cierra la operación: el trabajador consulta el documento, exige
estado emitido y número, compara otra vez líneas/importes y descarga el PDF
original. El resultado «Emisión verificada» **no representa aceptación tributaria**;
ese detalle no está disponible en el contrato de lectura que utiliza este módulo.

La copia nativa de Odoo sigue siendo borrador hasta «Contabilizar copia operativa».
Esta acción requiere el PDF verificado y aplica el número externo sin una segunda
llamada de emisión. Si falla por cuentas, fecha bloqueada, duplicidad o formato de
serie local, el documento remoto sigue emitido: nunca vuelva a emitir para resolver
un problema de la copia. Una nueva pulsación sobre una copia ya contabilizada no
crea otro asiento.

El estado de cobro del panel proviene de Easor; no se fabrican pagos, movimientos
bancarios ni conciliaciones en Odoo. Una copia operativa de ventas no es un balance
completo de la empresa. La actualización posterior es manual mediante «Recuperar /
actualizar sin duplicar»; no se promete un feed de cambios inexistente.

## Estados que requieren intervención

| Estado | Significado y actuación |
| --- | --- |
| Bloqueado antes de cualquier escritura | Corrija permisos, conexión o maestros remotos; la recuperación segura puede volver a hacer la preparación. No se cambia la instantánea aprobada. |
| Creación iniciada / Emisión iniciada | Hay una marca persistida antes de la llamada. Puede haber una petición en curso o un trabajador interrumpido. Nunca se reenvía automáticamente. |
| Resultado remoto desconocido | El servidor puede haber aplicado la operación. No pulse crear en otro sistema para “arreglarlo”. |
| Error de lectura con ID conocido | El cron reintenta lecturas, hasta ocho intentos y con espera creciente. Después requiere recuperación explícita. |
| PDF remoto cambiado | Se conserva el original y se bloquea la sustitución. Investigue antes de alterar la evidencia. |

Si se pierde la respuesta de creación y no se conoce el ID, localice el documento
exacto en Easor y use el asistente de vinculación. Debe explicar cómo comprobó su
origen; cliente e importe iguales no bastan. El asistente solo consulta y compara,
no crea ni emite. También admite vincular un documento que ya se encuentre emitido,
con permiso de emisor y verificación de contenido. Una restricción SQL impide
vincular el mismo documento remoto a dos facturas de la misma conexión.

La espera mínima de tres minutos evita intervenir sobre una petición reciente;
**no acredita que una operación asíncrona haya terminado**. La recuperación sigue
siendo de lectura y no habilita un nuevo POST de emisión.

No hay botón para “reintentar todo” ni para borrar trazas. Un caso incierto sin
resolución verificable se escala a Easor. No edite `state`, `remote_id` ni las tablas
con SQL para forzar el flujo. El código tampoco afirma exactamente-una-vez remota:
para esa garantía hacen falta claves de idempotencia de servidor.

## Observabilidad y mantenimiento

El panel muestra empresa, factura, ID remoto, último tipo de escritura, marca
previa a la llamada, intentos, próximo intento y error seguro. El historial guarda
usuario/acción/fecha sin copiar cuerpos de error remotos ni credenciales. La
instantánea y el PDF quedan en registros protegidos por compañía.

Supervise antigüedad de `creating`/`issuing`, número de `unknown`/`blocked`, fallos del
cron, tamaño de adjuntos y permisos de los usuarios que prepararon/aprobaron.
Al restaurar un backup, deshabilite escrituras antes de arrancar: el estado remoto
puede estar más adelantado que la copia restaurada. Reconciliar viene antes de
reenviar. Una restauración no revierte una factura emitida.

Las rectificativas deben hacerse mediante el circuito de Easor hasta incorporar
el adaptador correspondiente. Se bloquean las reversiones nativas de Odoo para
no crear una corrección fiscal independiente. Documente cualquier corrección manual
y no use la copia local como libro oficial.
