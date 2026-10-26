# Arquitectura y decisiones

## Responsabilidades

`tmf_easor_base/core.py` contiene validación decimal, cliente HTTP, paginación,
comparación de identidad y comprobación de documentos. `engine.py` ejecuta una sola
etapa y recibe una función `persist` que debe ser duradera. No depende de Odoo ni
necesita un LLM.

`tmf_easor_invoicing/models.py` adapta el motor a ORM, ACL, interfaz, adjuntos y cron.
La conexión y equivalencias pertenecen a una compañía; una operación conserva una
instantánea, huella SHA-256, solicitante y autorizante. Solo la API pública española
recibe las peticiones. No se generan JWT internos ni se invocan endpoints privados.

## ADR-001: Easor es el único emisor

La emisión local se bloquea en `_post`, no solo ocultando botones. Una copia se
contabiliza exclusivamente después de verificar el resultado remoto. El permiso
interno usa identidad de un objeto Python: un contexto RPC `true` no lo suplanta.
La salida PDF de documentos gestionados utiliza el adjunto original. Las descargas
por lotes se rechazan en esta versión; no se regeneran versiones alternativas.

La localización fiscal emisora de Odoo y `account_edi` están fuera del piloto.
No se parchea Odoo core. La contabilización nativa mantiene las relaciones estándar
con Ventas, pero una integración avanzada con pedidos, anticipos y suscripciones
necesita su propia aceptación funcional.

## ADR-002: cola duradera sin reenvíos ciegos

```text
queued -> creating [COMMIT] -> POST borrador -> draft_sync -> GET -> review
review --persona autorizada--> issue_queued
issue_queued -> issuing [COMMIT] -> POST emisión -> issued_sync -> GET + PDF -> done
                                  | error/crash: unknown o marca sending persistida
```

El cron abre **su propio cursor**. Selecciona una fila con `FOR UPDATE SKIP LOCKED`,
comprueba el solicitante/autorizante y la instantánea y confirma la marca anterior
a cada escritura. No hace commit sobre la transacción de un botón de usuario.
Después de la marca no hay reintentos automáticos de POST, incluidos 429, 409, 5xx,
respuesta inválida o timeout. El servidor puede haber procesado una parte del lote;
por eso cada petición de emisión contiene un solo ID.

Una caída antes de la marca deja una operación repetible, porque aún no hubo POST.
Una caída después de ella deja una operación no seleccionable automáticamente.
Una caída después del POST y antes de guardar la respuesta requiere recuperación.
La indisponibilidad de la base puede impedir guardar “unknown”, pero no elimina la
marca ya confirmada. Esto limita intentos automáticos; no prueba exactamente-una-vez.

Las lecturas se reintentan con espera creciente. La creación no se reabre después
de una marca persistida. La recuperación con ID desconocido exige intervención,
verificación de origen y comparación completa del documento.

## ADR-003: conservar semántica, no adivinar

Céntimos y puntos básicos se convierten con Decimal. Se rechazan precios unitarios
con fracciones de céntimo, impuestos especiales, descuentos, unidades transformadas,
líneas sin producto y otras formas fuera del alcance. El tipo cero debe estar
marcado explícitamente como ordinario; no se interpreta como N1/N2 o suplido.

Antes de crear y emitir se verifican NIF de empresa, identidad del cliente,
descripciones/unidades/tipo de productos y régimen. Después se comparan cantidad,
precio, IVA, deducciones inesperadas y totales. No se “corrigen” céntimos en silencio.
La paginación exige un cierre coherente; una página truncada no es un catálogo vacío.

## ADR-004: límites de la copia contable

La copia local no se envía como asiento: hacerlo además del documento duplicaría
la operación. Bancos, cierres, amortizaciones y libros oficiales siguen en Easor.
El estado de pago se muestra por separado y no se escribe `payment_state` para
simular una conciliación. La API de exportadores no es una API de contabilización.
