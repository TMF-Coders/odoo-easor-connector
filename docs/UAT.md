# Aceptación funcional y evidencia

## Niveles de evidencia

- Tests puros: decimales, validación, HTTP, paginación, comparación y estado/fences.
- Tests ORM: instalar addons y ejercitar permisos, documentos, adjuntos y copia
  nativa en Odoo 19 con API simulada explícitamente.
- UAT externo: empresa de sandbox autorizada, API desplegada y verificación humana.

Ningún nivel sustituye al siguiente. Los tests ORM mantienen la transacción del
framework de pruebas y suprimen commit(); no son una prueba física de crash y
recuperación de PostgreSQL. No se ha certificado emisión en un entorno real por
el hecho de tener un workflow o una respuesta simulada.

## Guion en sandbox, nunca en una compañía operativa

| Caso | Resultado exigido |
| --- | --- |
| Acceso y NIF | Empresa correcta; un ID de otra compañía se rechaza. |
| Dos líneas ordinarias | Líneas, importes, idioma, notas y vencimiento iguales en ambos sistemas. |
| Preparar dos veces | Una operación local y un solo borrador remoto. |
| Preparar sin permiso de emisor | Crea borrador, pero no permite autorizar la emisión. |
| Emitir | Un número Easor, documento no borrador, PDF original y comprobación de su efecto en libros. |
| Copia operativa | Número y total idénticos; las cantidades facturadas del pedido deben verificarse con Ventas. |
| PDF y portal | Descarga original, acceso autorizado y denegación a otra empresa; validar el portal de la instalación antes de prometerlo. |
| Error tras creación | Inyectar pérdida de respuesta; no debe aparecer otro borrador al reiniciar. |
| Error tras emisión | Interrumpir trabajador después del POST; recuperar por lectura sin emitir de nuevo. |
| Doble trabajador | Dos procesos compiten por la misma cola; una sola marca y un único intento remoto. |
| Contenido cambiado | Diferencia de un céntimo, cliente, unidad o descripción bloquea la continuación. |
| Periodo cerrado | Easor debe rechazar según su estado; nunca reabrirlo automáticamente. |
| Permiso revocado | Un emisor desactivado después de autorizar no provoca una nueva escritura. |
| API/clave ausente | Error visible y recuperable, sin éxito simulado ni fallback privado. |
| Restauración de backup | Arranque con escrituras desactivadas y reconciliación del estado remoto. |

Registre versión de Odoo/imagen, revisión del addon, entorno y versión de Easor,
caso, actor, ID local/remoto y resultado. Los IDs de documentos reales y PDFs no
pertenecen a una issue pública. Guarde evidencia privada con acceso restringido.

## Criterio de salida del piloto

Revisión independiente de código, CI de ambas suites verde, UAT de éxito y fallos,
reconciliación de un periodo de prueba, procedimientos de recuperación ensayados
y aceptación del responsable de facturación. No habilitar escritura general antes.
